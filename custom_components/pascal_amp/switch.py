"""Switch platform for whole-amplifier power."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PascalConfigEntry
from .api import PascalError
from .const import REG_STATE, STATE_FAULT, STATE_ON
from .coordinator import PascalCoordinator
from .entity import PascalEntity
from .util import unquote

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PascalConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the amplifier power switch."""
    async_add_entities([PascalPowerSwitch(entry.runtime_data)])


class PascalPowerSwitch(PascalEntity, SwitchEntity):
    """Controls amplifier power via POWER_ON / POWER_OFF (standby)."""

    _attr_translation_key = "power"
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, coordinator: PascalCoordinator) -> None:
        """Initialise the power switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_id}_power"

    @property
    def is_on(self) -> bool | None:
        """Return True when the amplifier reports the ON state."""
        state = unquote(self._reg(REG_STATE))
        if state is None:
            return None
        return state == STATE_ON

    @property
    def icon(self) -> str:
        """Show a warning icon while the amplifier is in a fault state."""
        if unquote(self._reg(REG_STATE)) == STATE_FAULT:
            return "mdi:power-plug-off"
        return "mdi:power"

    async def async_turn_on(self, **kwargs) -> None:
        """Power the amplifier on."""
        await self._run(self.client.async_power_on())

    async def async_turn_off(self, **kwargs) -> None:
        """Put the amplifier into standby."""
        await self._run(self.client.async_power_off())

    async def _run(self, coro) -> None:
        try:
            await coro
        except PascalError as err:
            _LOGGER.warning("Power command failed: %s", err)
            return
        self.coordinator.async_set_updated_data(dict(self.client.cache))
