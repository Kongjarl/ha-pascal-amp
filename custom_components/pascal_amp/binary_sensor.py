"""Binary sensor platform: fault and clip indicators."""

from __future__ import annotations

import re

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PascalConfigEntry
from .const import REG_SIGNAL_IN, REG_SIGNAL_OUT, REG_STATE, STATE_FAULT
from .coordinator import PascalCoordinator
from .entity import PascalEntity
from .util import safe_bool, unquote

_INPUT_NAME_RE = re.compile(r"^IN-(\d+)\.NAME$")
_OUTPUT_NAME_RE = re.compile(r"^OUT-(\d+)\.NAME$")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PascalConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up fault and clip binary sensors."""
    coordinator = entry.runtime_data
    cache = coordinator.client.cache

    entities: list[BinarySensorEntity] = [
        PascalFaultSensor(coordinator),
        PascalSignalProblemSensor(
            coordinator,
            key="input_clip",
            translation_key="input_clip",
            register=REG_SIGNAL_IN,
        ),
        PascalSignalProblemSensor(
            coordinator,
            key="output_clip",
            translation_key="output_clip",
            register=REG_SIGNAL_OUT,
        ),
    ]

    # Per-channel clip indicators (subscription-only; disabled by default).
    for key in cache:
        if match := _INPUT_NAME_RE.match(key):
            iid = match.group(1)
            entities.append(
                PascalClipSensor(
                    coordinator,
                    register=f"IN-{iid}.DYN.CLIP",
                    key=f"in_{iid}_clip",
                    name_register=key,
                    fallback_name=f"Input {iid} clip",
                )
            )
        elif match := _OUTPUT_NAME_RE.match(key):
            oid = match.group(1)
            entities.append(
                PascalClipSensor(
                    coordinator,
                    register=f"OUT-{oid}.DYN.CLIP",
                    key=f"out_{oid}_clip",
                    name_register=key,
                    fallback_name=f"Output {oid} clip",
                )
            )

    async_add_entities(entities)


class PascalFaultSensor(PascalEntity, BinarySensorEntity):
    """On when the amplifier reports a fault state."""

    _attr_translation_key = "fault"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: PascalCoordinator) -> None:
        """Initialise the fault sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_id}_fault"

    @property
    def is_on(self) -> bool | None:
        """Return True when STATE is FAULT or output signal reports FAULT."""
        state = unquote(self._reg(REG_STATE))
        signal_out = unquote(self._reg(REG_SIGNAL_OUT))
        if state is None and signal_out is None:
            return None
        return state == STATE_FAULT or signal_out == "FAULT"


class PascalSignalProblemSensor(PascalEntity, BinarySensorEntity):
    """On when an aggregate signal status reports clipping."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: PascalCoordinator,
        *,
        key: str,
        translation_key: str,
        register: str,
    ) -> None:
        """Initialise the aggregate clip sensor."""
        super().__init__(coordinator)
        self._register = register
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{coordinator.unique_id}_{key}"

    @property
    def is_on(self) -> bool | None:
        """Return True when the signal status is CLIP."""
        value = unquote(self._reg(self._register))
        if value is None:
            return None
        return value == "CLIP"


class PascalClipSensor(PascalEntity, BinarySensorEntity):
    """Per-channel clip indicator driven by subscription dynamics."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: PascalCoordinator,
        *,
        register: str,
        key: str,
        name_register: str,
        fallback_name: str,
    ) -> None:
        """Initialise the per-channel clip sensor."""
        super().__init__(coordinator)
        self._register = register
        self._name_register = name_register
        self._fallback_name = fallback_name
        self._attr_unique_id = f"{coordinator.unique_id}_{key}"

    @property
    def name(self) -> str:
        """Return '<channel name> clip'."""
        channel = unquote(self._reg(self._name_register))
        if channel:
            return f"{channel} clip"
        return self._fallback_name

    @property
    def is_on(self) -> bool | None:
        """Return whether the channel is currently clipping."""
        value = self._reg(self._register)
        return safe_bool(value) if value is not None else None
