"""Data update coordinator for the Pascal IP Amplifier."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PascalClient, PascalError
from .const import (
    CONNECT_TIMEOUT,
    DOMAIN,
    GET_ALL_TIMEOUT,
    HEARTBEAT_INTERVAL,
    PUSH_DEBOUNCE,
    REG_DEVICE_NAME,
    REG_FIRMWARE,
    REG_MAC,
    REG_MODEL,
    REG_SERIAL,
    REG_STATE,
    REG_VENDOR,
)
from .util import unquote

_LOGGER = logging.getLogger(__name__)


class PascalCoordinator(DataUpdateCoordinator[dict[str, str]]):
    """Owns the amplifier connection and a snapshot of its register cache.

    Updates are *pushed*: the client streams register changes which we coalesce
    into a single Home Assistant state write. A slow periodic heartbeat doubles
    as a liveness check so entities go unavailable if the link silently dies.
    """

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: PascalClient
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=HEARTBEAT_INTERVAL),
        )
        self.entry = entry
        self.client = client
        self._unique_id = entry.unique_id or entry.entry_id
        self._push_unsub: CALLBACK_TYPE | None = None
        self._remove_reg = client.add_register_listener(self._handle_register_change)
        self._remove_conn = client.add_connection_listener(self._handle_connection_change)

    async def async_setup(self) -> None:
        """Start the connection and wait for the first successful sync."""
        await self.client.async_start(self._on_connect)
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT + GET_ALL_TIMEOUT):
                await self.client.wait_ready()
        except (TimeoutError, PascalError) as err:
            await self.client.async_close()
            raise ConfigEntryNotReady(
                f"Could not connect to Pascal amplifier at {self.client.host}"
            ) from err

    async def async_shutdown(self) -> None:
        """Stop the connection and cancel pending work."""
        self._cancel_push()
        self._remove_reg()
        self._remove_conn()
        await self.client.async_close()
        await super().async_shutdown()

    async def _on_connect(self) -> None:
        """Re-sync the full cache and (re)subscribe after every (re)connect."""
        await self.client.async_get_all()
        await self.client.async_subscribe()
        self.client.mark_ready()
        # Publish the freshly synced snapshot immediately.
        self.async_set_updated_data(dict(self.client.cache))

    async def _async_update_data(self) -> dict[str, str]:
        """Heartbeat poll; also surfaces dynamic values into a state write."""
        if not self.client.connected:
            raise UpdateFailed("Amplifier is not connected")
        try:
            await self.client.async_get(REG_STATE)
        except PascalError as err:
            raise UpdateFailed(f"Amplifier did not respond: {err}") from err
        return dict(self.client.cache)

    # ------------------------------------------------------------------ #
    # Push handling
    # ------------------------------------------------------------------ #
    @callback
    def _handle_register_change(self, register: str, value: str) -> None:
        """Coalesce streamed register changes into one debounced state write."""
        if self._push_unsub is not None:
            return
        self._push_unsub = async_call_later(self.hass, PUSH_DEBOUNCE, self._do_push)

    @callback
    def _do_push(self, _now) -> None:
        self._push_unsub = None
        self.async_set_updated_data(dict(self.client.cache))

    @callback
    def _cancel_push(self) -> None:
        if self._push_unsub is not None:
            self._push_unsub()
            self._push_unsub = None

    @callback
    def _handle_connection_change(self, connected: bool) -> None:
        """Reflect link up/down in entity availability promptly."""
        if not connected:
            # Mark the last update as failed so entities report unavailable.
            self.async_set_update_error(UpdateFailed("Amplifier disconnected"))
        else:
            self.async_update_listeners()

    # ------------------------------------------------------------------ #
    # Device info
    # ------------------------------------------------------------------ #
    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry info derived from the cached identity."""
        cache = self.client.cache
        serial = unquote(cache.get(REG_SERIAL)) or None
        mac = unquote(cache.get(REG_MAC)) or None
        name = (
            unquote(cache.get(REG_DEVICE_NAME))
            or unquote(cache.get(REG_MODEL))
            or self.entry.title
        )

        connections = set()
        if mac:
            connections.add((CONNECTION_NETWORK_MAC, mac.lower()))

        return DeviceInfo(
            identifiers={(DOMAIN, self._unique_id)},
            connections=connections,
            name=name,
            manufacturer=unquote(cache.get(REG_VENDOR)) or "Pascal Audio",
            model=unquote(cache.get(REG_MODEL)),
            serial_number=serial,
            sw_version=unquote(cache.get(REG_FIRMWARE)),
            configuration_url=f"http://{self.client.host}",
        )

    @property
    def unique_id(self) -> str:
        """Return the stable unique id for this amplifier."""
        return self._unique_id
