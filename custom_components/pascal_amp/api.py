"""Async client for the Pascal line-based amplifier API (TCP port 7621).

Design notes
------------
The amplifier exposes a single, line-delimited TCP stream that mixes two kinds
of traffic:

* command responses (``+REG VALUE`` data lines, terminated by a ``*CMD`` echo
  or a ``#error`` line), and
* asynchronous subscription updates (also ``+REG VALUE`` lines).

To keep this robust we:

* run a single background reader that parses *every* line into a register cache
  and notifies listeners -- it never raises out of the loop;
* serialise outgoing commands with a lock so exactly one command is in flight,
  letting us correlate the next ``*``/``#`` line with that command;
* read command results from the cache once the ``*`` echo arrives, instead of
  trying to fish specific ``+`` lines out of a stream that may be interleaved
  with subscription updates;
* supervise the connection and reconnect with exponential backoff, re-running
  an ``on_connect`` callback so the cache + subscriptions are re-established.

Every public coroutine either succeeds or raises a :class:`PascalError`
subclass; callers are expected to handle those. Nothing here logs at a level
that would spam, and no parsing path can crash Home Assistant.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from .const import (
    COMMAND_TIMEOUT,
    CONNECT_TIMEOUT,
    DEFAULT_PORT,
    GET_ALL_TIMEOUT,
    RECONNECT_MAX_DELAY,
    RECONNECT_MIN_DELAY,
    SUBSCRIBE_DYN_FREQ,
)
from .util import quote_if_needed, unquote

_LOGGER = logging.getLogger(__name__)

RegisterListener = Callable[[str, str], None]
ConnectionListener = Callable[[bool], None]
OnConnect = Callable[[], Awaitable[None]]


class PascalError(Exception):
    """Base error for the Pascal amplifier client."""


class PascalConnectionError(PascalError):
    """Raised when the amplifier cannot be reached or the link drops."""


class PascalCommandError(PascalError):
    """Raised when the amplifier replies with an error (``#message``)."""


class PascalClient:
    """A resilient connection to one Pascal amplifier."""

    def __init__(self, host: str, port: int = DEFAULT_PORT) -> None:
        """Initialise the client for *host*:*port*."""
        self._host = host
        self._port = port

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._supervisor: asyncio.Task | None = None

        self._command_lock = asyncio.Lock()
        self._pending: asyncio.Future[str] | None = None

        self._connected = False
        self._closing = False
        self._ready_event = asyncio.Event()

        self._on_connect: OnConnect | None = None
        self._reg_listeners: list[RegisterListener] = []
        self._conn_listeners: list[ConnectionListener] = []

        #: Latest known value for every register the amplifier has reported.
        self.cache: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Properties / listeners
    # ------------------------------------------------------------------ #
    @property
    def host(self) -> str:
        """Return the amplifier host."""
        return self._host

    @property
    def connected(self) -> bool:
        """Return True while the TCP link is up."""
        return self._connected

    def add_register_listener(self, callback: RegisterListener) -> Callable[[], None]:
        """Register a callback invoked as ``callback(register, value)`` on change."""
        self._reg_listeners.append(callback)

        def _remove() -> None:
            with contextlib.suppress(ValueError):
                self._reg_listeners.remove(callback)

        return _remove

    def add_connection_listener(
        self, callback: ConnectionListener
    ) -> Callable[[], None]:
        """Register a callback invoked as ``callback(connected: bool)``."""
        self._conn_listeners.append(callback)

        def _remove() -> None:
            with contextlib.suppress(ValueError):
                self._conn_listeners.remove(callback)

        return _remove

    async def wait_ready(self) -> None:
        """Wait until the first successful connect + initial sync completes."""
        await self._ready_event.wait()

    def mark_ready(self) -> None:
        """Signal that the initial connect/sync has finished."""
        self._ready_event.set()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def async_start(self, on_connect: OnConnect | None = None) -> None:
        """Start the supervised connection that auto-reconnects forever."""
        self._on_connect = on_connect
        self._closing = False
        if self._supervisor is None or self._supervisor.done():
            self._supervisor = asyncio.create_task(self._supervise())

    async def async_close(self) -> None:
        """Tear the connection down permanently."""
        self._closing = True
        if self._supervisor:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._supervisor
            self._supervisor = None
        await self._close_connection()

    async def _supervise(self) -> None:
        """Connect, sync, then reconnect with backoff whenever the link drops."""
        delay = RECONNECT_MIN_DELAY
        while not self._closing:
            try:
                await self._open_connection()
                delay = RECONNECT_MIN_DELAY
                if self._on_connect is not None:
                    try:
                        await self._on_connect()
                    except PascalError as err:
                        _LOGGER.warning("Initial sync failed for %s: %s", self._host, err)
                    except Exception:  # noqa: BLE001 - never kill the supervisor
                        _LOGGER.exception("Unexpected error during initial sync")
                # Block until the reader stops (i.e. the connection dropped).
                if self._read_task is not None:
                    with contextlib.suppress(Exception):
                        await self._read_task
            except asyncio.CancelledError:
                raise
            except PascalConnectionError as err:
                _LOGGER.debug("Connection attempt to %s failed: %s", self._host, err)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected supervisor error for %s", self._host)

            if self._closing:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _open_connection(self) -> None:
        """Open the socket and launch the reader task."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=CONNECT_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise PascalConnectionError(
                f"Cannot connect to {self._host}:{self._port}: {err}"
            ) from err

        self._connected = True
        self._read_task = asyncio.create_task(self._read_loop())
        _LOGGER.debug("Connected to amplifier at %s:%s", self._host, self._port)
        self._notify_connection(True)

    async def _close_connection(self) -> None:
        """Cancel the reader and close the socket (idempotent)."""
        if self._read_task:
            self._read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._read_task
            self._read_task = None
        await self._handle_disconnect()

    async def _handle_disconnect(self) -> None:
        """Mark the link down and fail any in-flight command."""
        was_connected = self._connected
        self._connected = False

        if self._pending is not None and not self._pending.done():
            self._pending.set_exception(
                PascalConnectionError("Connection lost before response")
            )

        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
                # Give the transport a chance to close cleanly, but never hang.
                await asyncio.wait_for(self._writer.wait_closed(), timeout=2.0)
        self._writer = None
        self._reader = None

        if was_connected:
            self._notify_connection(False)

    # ------------------------------------------------------------------ #
    # Reader / line parsing
    # ------------------------------------------------------------------ #
    async def _read_loop(self) -> None:
        """Continuously read lines; resilient to any per-line failure."""
        assert self._reader is not None
        try:
            while True:
                try:
                    line = await self._reader.readline()
                except (OSError, asyncio.IncompleteReadError) as err:
                    _LOGGER.debug("Read error from %s: %s", self._host, err)
                    break
                if not line:  # EOF -> peer closed
                    break
                try:
                    text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                    self._handle_line(text)
                except Exception:  # noqa: BLE001 - one bad line must not stop us
                    _LOGGER.exception("Failed to handle line: %r", line)
        except asyncio.CancelledError:
            raise
        finally:
            await self._handle_disconnect()

    def _handle_line(self, line: str) -> None:
        """Dispatch a single received line."""
        if not line:
            return
        prefix = line[0]
        if prefix == "+":
            self._handle_update(line[1:])
        elif prefix == "*":
            self._resolve_pending(result=line[1:])
        elif prefix == "#":
            self._resolve_pending(error=line[1:].strip() or "Unknown amplifier error")
        else:
            # Some firmware emits dynamic updates without the leading '+'.
            self._handle_update(line)

    def _handle_update(self, body: str) -> None:
        """Store a ``REG VALUE`` update and notify listeners on change."""
        register, _, raw = body.partition(" ")
        register = register.strip()
        if not register:
            return
        value = unquote(raw.strip()) or ""
        old = self.cache.get(register)
        self.cache[register] = value
        if old != value:
            for callback in list(self._reg_listeners):
                try:
                    callback(register, value)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Register listener raised for %s", register)

    def _resolve_pending(self, *, result: str | None = None, error: str | None = None) -> None:
        """Complete the in-flight command future (if any)."""
        future = self._pending
        if future is None or future.done():
            return
        if error is not None:
            future.set_exception(PascalCommandError(error))
        else:
            future.set_result(result or "")

    def _notify_connection(self, connected: bool) -> None:
        for callback in list(self._conn_listeners):
            try:
                callback(connected)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Connection listener raised")

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #
    async def _command(self, command: str, timeout: float = COMMAND_TIMEOUT) -> str:
        """Send a command and wait for its ``*``/``#`` completion line."""
        if not self._connected or self._writer is None:
            raise PascalConnectionError("Not connected to amplifier")

        async with self._command_lock:
            if not self._connected or self._writer is None:
                raise PascalConnectionError("Not connected to amplifier")
            loop = asyncio.get_running_loop()
            future: asyncio.Future[str] = loop.create_future()
            self._pending = future
            try:
                self._writer.write((command + "\n").encode("utf-8"))
                await self._writer.drain()
                return await asyncio.wait_for(future, timeout)
            except asyncio.TimeoutError as err:
                raise PascalConnectionError(
                    f"Timed out waiting for response to: {command}"
                ) from err
            except (OSError, ConnectionError) as err:
                raise PascalConnectionError(f"Write failed for: {command}: {err}") from err
            finally:
                self._pending = None

    async def async_get(self, register: str) -> str | None:
        """GET a single register and return its (cached) value."""
        await self._command(f"GET {register}")
        return self.cache.get(register)

    async def async_get_all(self) -> dict[str, str]:
        """GET every register to (re)populate the cache."""
        await self._command("GET *", timeout=GET_ALL_TIMEOUT)
        return dict(self.cache)

    async def async_set(self, register: str, value: str) -> None:
        """SET a register to a numeric/enum *value* (already formatted)."""
        await self._command(f"SET {register} {value}")
        # Optimistically reflect the intended value; a subscription update will
        # correct it if the amplifier clamped/changed it.
        self.cache[register] = unquote(value) or ""

    async def async_set_string(self, register: str, value: str) -> None:
        """SET a string register, quoting the value if it contains whitespace."""
        await self._command(f"SET {register} {quote_if_needed(value)}")
        self.cache[register] = value

    async def async_inc(self, register: str, amount: float) -> str | None:
        """INC a register by *amount* (positive or negative)."""
        await self._command(f"INC {register} {amount}")
        return self.cache.get(register)

    async def async_power_on(self) -> None:
        """Power the amplifier on."""
        await self._command("POWER_ON")

    async def async_power_off(self) -> None:
        """Put the amplifier into standby."""
        await self._command("POWER_OFF")

    async def async_subscribe(self) -> None:
        """Subscribe to register changes and (slow) dynamic level updates."""
        await self._command("SUBSCRIBE REG")
        # Dynamic (meter) updates are subscription-only; keep the rate modest.
        await self._command(f"SUBSCRIBE DYN {SUBSCRIBE_DYN_FREQ}")

    # ------------------------------------------------------------------ #
    # One-shot helper used by the config flow for validation
    # ------------------------------------------------------------------ #
    async def async_fetch_info(self) -> dict[str, str]:
        """Open a throwaway connection, read identity registers, then close.

        Used by the config flow to validate connectivity without starting the
        long-lived supervisor.
        """
        await self._open_connection()
        try:
            for register in (
                "SYSTEM.DEVICE.SERIAL",
                "SYSTEM.DEVICE.MODEL_NAME",
                "SYSTEM.DEVICE.VENDOR_NAME",
                "SYSTEM.DEVICE.MAC",
                "SETUP.SYSTEM.DEVICE_NAME",
                "API_VERSION",
            ):
                with contextlib.suppress(PascalError):
                    await self.async_get(register)
            return dict(self.cache)
        finally:
            await self._close_connection()
