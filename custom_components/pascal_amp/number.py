"""Number platform: input and output gain."""

from __future__ import annotations

import re

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PascalConfigEntry
from .const import (
    GAIN_STEP,
    GEN_GAIN_MAX,
    GEN_GAIN_MIN,
    GENERATOR_IID,
    IN_GAIN_MAX,
    IN_GAIN_MIN,
    OUT_GAIN_MAX,
    OUT_GAIN_MIN,
)
from .coordinator import PascalCoordinator
from .entity import PascalEntity
from .util import safe_float, unquote

_INPUT_NAME_RE = re.compile(r"^IN-(\d+)\.NAME$")
_OUTPUT_NAME_RE = re.compile(r"^OUT-(\d+)\.NAME$")

DB_UNIT = "dB"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PascalConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up input/output gain number entities."""
    coordinator = entry.runtime_data
    cache = coordinator.client.cache
    entities: list[NumberEntity] = []

    for key in cache:
        if match := _INPUT_NAME_RE.match(key):
            iid = int(match.group(1))
            if iid == GENERATOR_IID:
                lo, hi = GEN_GAIN_MIN, GEN_GAIN_MAX
            else:
                lo, hi = IN_GAIN_MIN, IN_GAIN_MAX
            entities.append(
                PascalGainNumber(
                    coordinator,
                    register=f"IN-{iid}.GAIN",
                    name_register=key,
                    key=f"in_{iid}_gain",
                    fallback_name=f"Input {iid} gain",
                    minimum=lo,
                    maximum=hi,
                )
            )
        elif match := _OUTPUT_NAME_RE.match(key):
            oid = int(match.group(1))
            entities.append(
                PascalGainNumber(
                    coordinator,
                    register=f"OUT-{oid}.GAIN",
                    name_register=key,
                    key=f"out_{oid}_gain",
                    fallback_name=f"Output {oid} gain",
                    minimum=OUT_GAIN_MIN,
                    maximum=OUT_GAIN_MAX,
                )
            )

    async_add_entities(entities)


class PascalGainNumber(PascalEntity, NumberEntity):
    """A gain (dB) control backed by a float register."""

    _attr_native_unit_of_measurement = DB_UNIT
    _attr_native_step = GAIN_STEP
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: PascalCoordinator,
        *,
        register: str,
        name_register: str,
        key: str,
        fallback_name: str,
        minimum: float,
        maximum: float,
    ) -> None:
        """Initialise the gain control."""
        super().__init__(coordinator)
        self._register = register
        self._name_register = name_register
        self._fallback_name = fallback_name
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum
        self._attr_unique_id = f"{coordinator.unique_id}_{key}"

    @property
    def name(self) -> str:
        """Return '<channel name> gain'."""
        channel = unquote(self._reg(self._name_register))
        if channel:
            return f"{channel} gain"
        return self._fallback_name

    @property
    def native_value(self) -> float | None:
        """Return the current gain in dB."""
        return safe_float(self._reg(self._register))

    async def async_set_native_value(self, value: float) -> None:
        """Set the gain in dB."""
        clamped = max(self._attr_native_min_value, min(self._attr_native_max_value, value))
        await self._async_send(self.client.async_set(self._register, f"{clamped:.2f}"))
