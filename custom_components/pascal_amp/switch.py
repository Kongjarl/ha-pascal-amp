"""Switch platform: amplifier power, output mute, and input toggles."""

from __future__ import annotations

import logging
import re

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PascalConfigEntry
from .api import PascalError
from .const import REG_STATE, STATE_FAULT, STATE_ON
from .coordinator import PascalCoordinator
from .entity import PascalEntity
from .util import safe_bool, unquote

_LOGGER = logging.getLogger(__name__)

_INPUT_NAME_RE = re.compile(r"^IN-(\d+)\.NAME$")
_OUTPUT_NAME_RE = re.compile(r"^OUT-(\d+)\.NAME$")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PascalConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the power switch plus output mute and input toggle switches."""
    coordinator = entry.runtime_data
    cache = coordinator.client.cache
    entities: list[SwitchEntity] = [PascalPowerSwitch(coordinator)]

    for key in cache:
        if match := _OUTPUT_NAME_RE.match(key):
            oid = int(match.group(1))
            entities.append(
                PascalToggleSwitch(
                    coordinator,
                    register=f"OUT-{oid}.MUTE",
                    name_register=key,
                    key=f"out_{oid}_mute",
                    suffix="mute",
                    fallback=f"Output {oid} mute",
                    device_class=SwitchDeviceClass.SWITCH,
                    icon="mdi:volume-mute",
                    invert_icon=True,
                )
            )
        elif match := _INPUT_NAME_RE.match(key):
            iid = int(match.group(1))
            # Each toggle is created only if the amplifier exposes its register
            # for this input (HPF/stereo/EQ apply to a subset of channels).
            for reg_suffix, label, icon in (
                ("HPF_ENABLE", "high-pass filter", "mdi:filter"),
                ("STEREO", "stereo link", "mdi:link-variant"),
                ("EQ.BYPASS", "EQ bypass", "mdi:equalizer"),
            ):
                reg = f"IN-{iid}.{reg_suffix}"
                if reg not in cache:
                    continue
                entities.append(
                    PascalToggleSwitch(
                        coordinator,
                        register=reg,
                        name_register=key,
                        key=f"in_{iid}_{reg_suffix.lower().replace('.', '_')}",
                        suffix=label,
                        fallback=f"Input {iid} {label}",
                        entity_category=EntityCategory.CONFIG,
                        icon=icon,
                    )
                )

    async_add_entities(entities)


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


class PascalToggleSwitch(PascalEntity, SwitchEntity):
    """A boolean (1/0) register exposed as a switch."""

    def __init__(
        self,
        coordinator: PascalCoordinator,
        *,
        register: str,
        name_register: str,
        key: str,
        suffix: str,
        fallback: str,
        device_class: SwitchDeviceClass | None = None,
        entity_category: EntityCategory | None = None,
        icon: str | None = None,
        invert_icon: bool = False,
    ) -> None:
        """Initialise the toggle."""
        super().__init__(coordinator)
        self._register = register
        self._name_register = name_register
        self._suffix = suffix
        self._fallback = fallback
        self._base_icon = icon
        self._invert_icon = invert_icon
        self._attr_device_class = device_class
        self._attr_entity_category = entity_category
        self._attr_unique_id = f"{coordinator.unique_id}_{key}"

    @property
    def name(self) -> str:
        """Return '<channel name> <suffix>'."""
        channel = unquote(self._reg(self._name_register))
        if channel:
            return f"{channel} {self._suffix}"
        return self._fallback

    @property
    def is_on(self) -> bool | None:
        """Return the register's boolean state."""
        value = self._reg(self._register)
        return safe_bool(value) if value is not None else None

    @property
    def icon(self) -> str | None:
        """Return the icon (optionally reflecting on/off, e.g. mute)."""
        if self._base_icon is None:
            return None
        if self._invert_icon and not self.is_on:
            return "mdi:volume-high"
        return self._base_icon

    async def async_turn_on(self, **kwargs) -> None:
        """Set the register to 1."""
        await self._async_send(self.client.async_set(self._register, "1"))

    async def async_turn_off(self, **kwargs) -> None:
        """Set the register to 0."""
        await self._async_send(self.client.async_set(self._register, "0"))
