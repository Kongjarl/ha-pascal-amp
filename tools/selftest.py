#!/usr/bin/env python3
"""Standalone self-test for the Pascal client (no Home Assistant required).

Loads ``const.py``, ``util.py`` and ``api.py`` from the integration as a synthetic
package, spins up a mock amplifier over a real TCP socket, and exercises:

* GET / SET / power commands and response correlation,
* interleaved subscription updates,
* malformed lines and ``#error`` replies (must not crash),
* a mid-session disconnect followed by automatic reconnect + re-sync.

Run:  python tools/selftest.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

PKG = "pascal_amp_selftest"
COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "pascal_amp"


def _load_package() -> types.ModuleType:
    """Load const/util/api under a synthetic package so relative imports work."""
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(COMPONENT)]
    sys.modules[PKG] = pkg
    for name in ("const", "util", "api"):
        spec = importlib.util.spec_from_file_location(
            f"{PKG}.{name}", COMPONENT / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{PKG}.{name}"] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        setattr(pkg, name, module)
    return sys.modules[f"{PKG}.api"]


api = _load_package()


class MockAmp:
    """Minimal stand-in for the amplifier's line protocol."""

    def __init__(self) -> None:
        self.registers = {
            "SYSTEM.STATUS.STATE": '"STANDBY"',
            "ZONE-A.NAME": '"Bar"',
            "ZONE-A.GAIN": "-20.000",
            "ZONE-A.MUTE": "0",
        }
        self.server: asyncio.AbstractServer | None = None
        self.port = 0
        self.connections = 0
        self.kill_next = False  # drop the next client to test reconnect
        self._writers: list[asyncio.StreamWriter] = []

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.append(writer)
        if self.kill_next:
            self.kill_next = False
            writer.close()
            return
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                line = raw.decode().rstrip("\r\n")
                await self._dispatch(line, writer)
        except (ConnectionError, OSError):
            pass

    async def _dispatch(self, line: str, writer: asyncio.StreamWriter) -> None:
        parts = line.split(" ")
        cmd = parts[0]
        if cmd == "GET" and parts[1] == "*":
            for reg, val in self.registers.items():
                writer.write(f"+{reg} {val}\n".encode())
            # Inject a malformed line and a stray no-prefix update to prove
            # the client tolerates them.
            writer.write(b"this is garbage with no prefix or register\n")
            writer.write(b"+\n")  # empty register
            writer.write(f"*{line}\n".encode())
        elif cmd == "GET":
            reg = parts[1]
            if reg in self.registers:
                writer.write(f"+{reg} {self.registers[reg]}\n".encode())
            writer.write(f"*{line}\n".encode())
        elif cmd == "SET":
            reg, val = parts[1], " ".join(parts[2:])
            self.registers[reg] = val
            writer.write(f"+{reg} {val}\n".encode())
            writer.write(f"*{line}\n".encode())
        elif cmd in ("POWER_ON", "POWER_OFF"):
            self.registers["SYSTEM.STATUS.STATE"] = (
                '"ON"' if cmd == "POWER_ON" else '"STANDBY"'
            )
            writer.write(f"*{cmd}\n".encode())
        elif cmd == "SUBSCRIBE":
            writer.write(f"*{line}\n".encode())
            # Push a dynamic update after subscribing.
            writer.write(b"+ZONE-A.DYN.SIGNAL -42.5\n")
        elif cmd == "BADCMD":
            writer.write(b"#Unknown command\n")
        else:
            writer.write(f"*{line}\n".encode())
        await writer.drain()


def check(cond: bool, label: str) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        raise SystemExit(f"Self-test failed: {label}")


async def run() -> None:
    amp = MockAmp()
    await amp.start()
    print(f"Mock amplifier listening on 127.0.0.1:{amp.port}\n")

    client = api.PascalClient("127.0.0.1", amp.port)
    synced = asyncio.Event()

    async def on_connect() -> None:
        await client.async_get_all()
        await client.async_subscribe()
        client.mark_ready()
        synced.set()

    await client.async_start(on_connect)

    print("1. Initial connect + GET * (with garbage lines mixed in)")
    await asyncio.wait_for(client.wait_ready(), timeout=5)
    check(client.connected, "client reports connected")
    check(client.cache.get("ZONE-A.NAME") == "Bar", "string register unquoted")
    check(client.cache.get("ZONE-A.GAIN") == "-20.000", "numeric register cached")
    check("ZONE-A.DYN.SIGNAL" in client.cache, "subscription update received")

    print("\n2. SET command round-trip")
    await client.async_set("ZONE-A.GAIN", "-10.0")
    check(client.cache.get("ZONE-A.GAIN") == "-10.0", "SET updated cache")

    print("\n3. Power command")
    await client.async_power_on()
    val = await client.async_get("SYSTEM.STATUS.STATE")
    check(api.unquote(val) == "ON", "POWER_ON reflected in STATE")

    print("\n4. Error reply does not crash, raises PascalCommandError")
    raised = False
    try:
        await client._command("BADCMD")
    except api.PascalCommandError:
        raised = True
    check(raised, "error reply raised PascalCommandError")
    check(client.connected, "still connected after error")

    print("\n5. Disconnect mid-session triggers automatic reconnect + re-sync")
    synced.clear()
    amp.kill_next = True
    first_conn_count = amp.connections
    # Force the current socket closed by killing it server-side: open a probe
    # that the server drops, then drop the live one too.
    for w in list(amp._writers):
        try:
            w.close()
        except Exception:  # noqa: BLE001
            pass
    await asyncio.wait_for(synced.wait(), timeout=10)
    check(amp.connections > first_conn_count, "client reconnected")
    check(client.connected, "connected again after reconnect")
    check(client.cache.get("ZONE-A.NAME") == "Bar", "cache re-synced after reconnect")

    print("\n6. Clean shutdown")
    await client.async_close()
    check(not client.connected, "disconnected after close")

    await amp.stop()
    print("\nAll self-tests passed.")


if __name__ == "__main__":
    asyncio.run(run())
