"""The Pascal IP Amplifier integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .api import PascalClient
from .const import DEFAULT_PORT, DOMAIN
from .coordinator import PascalCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.MEDIA_PLAYER,
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]

type PascalConfigEntry = ConfigEntry[PascalCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: PascalConfigEntry) -> bool:
    """Set up Pascal IP Amplifier from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)

    client = PascalClient(host, port)
    coordinator = PascalCoordinator(hass, entry, client)

    # Starts the supervised connection and waits for the first sync, raising
    # ConfigEntryNotReady if the amplifier cannot be reached.
    await coordinator.async_setup()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PascalConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = entry.runtime_data
        await coordinator.async_shutdown()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: PascalConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
