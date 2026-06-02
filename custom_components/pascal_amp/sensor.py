"""Sensor platform: amplifier status, identity and signal levels."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PascalConfigEntry
from .const import (
    REG_API_VERSION,
    REG_FIRMWARE,
    REG_FIRMWARE_DATE,
    REG_LAN,
    REG_SERIAL,
    REG_SIGNAL_IN,
    REG_SIGNAL_OUT,
    REG_STATE,
    REG_WIFI,
)
from .coordinator import PascalCoordinator
from .entity import PascalEntity
from .util import safe_float, unquote

_LOGGER = logging.getLogger(__name__)

_INPUT_NAME_RE = re.compile(r"^IN-(\d+)\.NAME$")
_OUTPUT_NAME_RE = re.compile(r"^OUT-(\d+)\.NAME$")

# Decibels are not a first-class HA unit; use a plain string label.
DB_UNIT = "dB"

STATE_OPTIONS = ["INIT", "STANDBY", "ON", "FAULT"]
SIGNAL_IN_OPTIONS = ["OFF", "NO_SIGNAL", "SIGNAL", "CLIP"]
SIGNAL_OUT_OPTIONS = ["OFF", "NO_SIGNAL", "SIGNAL", "CLIP", "FAULT"]


@dataclass(frozen=True, kw_only=True)
class PascalSensorDescription(SensorEntityDescription):
    """Describes a register-backed sensor."""

    register: str
    value_fn: Callable[[str | None], str | float | None] = staticmethod(unquote)


STATUS_SENSORS: tuple[PascalSensorDescription, ...] = (
    PascalSensorDescription(
        key="state",
        translation_key="state",
        register=REG_STATE,
        device_class=SensorDeviceClass.ENUM,
        options=STATE_OPTIONS,
    ),
    PascalSensorDescription(
        key="signal_in",
        translation_key="signal_in",
        register=REG_SIGNAL_IN,
        device_class=SensorDeviceClass.ENUM,
        options=SIGNAL_IN_OPTIONS,
    ),
    PascalSensorDescription(
        key="signal_out",
        translation_key="signal_out",
        register=REG_SIGNAL_OUT,
        device_class=SensorDeviceClass.ENUM,
        options=SIGNAL_OUT_OPTIONS,
    ),
)

DIAGNOSTIC_SENSORS: tuple[PascalSensorDescription, ...] = (
    PascalSensorDescription(
        key="firmware",
        translation_key="firmware",
        register=REG_FIRMWARE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    PascalSensorDescription(
        key="firmware_date",
        translation_key="firmware_date",
        register=REG_FIRMWARE_DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    PascalSensorDescription(
        key="api_version",
        translation_key="api_version",
        register=REG_API_VERSION,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    PascalSensorDescription(
        key="serial",
        translation_key="serial",
        register=REG_SERIAL,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    PascalSensorDescription(
        key="lan_ip",
        translation_key="lan_ip",
        register=REG_LAN,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    PascalSensorDescription(
        key="wifi_ip",
        translation_key="wifi_ip",
        register=REG_WIFI,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PascalConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up status, diagnostic and per-channel signal-level sensors."""
    coordinator = entry.runtime_data
    cache = coordinator.client.cache

    entities: list[SensorEntity] = [
        PascalRegisterSensor(coordinator, desc)
        for desc in (*STATUS_SENSORS, *DIAGNOSTIC_SENSORS)
    ]

    # Per-input signal level meters (subscription-only; disabled by default).
    for key in cache:
        if match := _INPUT_NAME_RE.match(key):
            iid = match.group(1)
            entities.append(
                PascalSignalSensor(
                    coordinator,
                    register=f"IN-{iid}.DYN.SIGNAL",
                    key=f"in_{iid}_signal",
                    name_register=key,
                    fallback_name=f"Input {iid} level",
                )
            )
        elif match := _OUTPUT_NAME_RE.match(key):
            oid = match.group(1)
            entities.append(
                PascalSignalSensor(
                    coordinator,
                    register=f"OUT-{oid}.DYN.SIGNAL",
                    key=f"out_{oid}_signal",
                    name_register=key,
                    fallback_name=f"Output {oid} level",
                )
            )

    async_add_entities(entities)


class PascalRegisterSensor(PascalEntity, SensorEntity):
    """A sensor backed directly by a single register."""

    entity_description: PascalSensorDescription

    def __init__(
        self, coordinator: PascalCoordinator, description: PascalSensorDescription
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.unique_id}_{description.key}"

    @property
    def native_value(self) -> str | float | None:
        """Return the parsed register value."""
        raw = self._reg(self.entity_description.register)
        try:
            return self.entity_description.value_fn(raw)
        except Exception:  # noqa: BLE001 - never let a bad value crash HA
            _LOGGER.debug("Failed to parse %s value %r", self.entity_description.key, raw)
            return None


class PascalSignalSensor(PascalEntity, SensorEntity):
    """A subscription-driven signal level meter (dB) for an input/output."""

    _attr_native_unit_of_measurement = DB_UNIT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False
    _attr_suggested_display_precision = 1

    def __init__(
        self,
        coordinator: PascalCoordinator,
        *,
        register: str,
        key: str,
        name_register: str,
        fallback_name: str,
    ) -> None:
        """Initialise the level sensor."""
        super().__init__(coordinator)
        self._register = register
        self._name_register = name_register
        self._fallback_name = fallback_name
        self._attr_unique_id = f"{coordinator.unique_id}_{key}"

    @property
    def name(self) -> str:
        """Return '<channel name> level'."""
        channel = unquote(self._reg(self._name_register))
        if channel:
            return f"{channel} level"
        return self._fallback_name

    @property
    def native_value(self) -> float | None:
        """Return the current signal level in dB (None if no data)."""
        value = safe_float(self._reg(self._register))
        if value is None:
            return None
        # The amplifier reports -144 dB to mean "no signal".
        return value
