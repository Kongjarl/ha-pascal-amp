"""Select platform: input sensitivity."""

from __future__ import annotations

import re

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PascalConfigEntry
from .const import SENS_OPTIONS
from .coordinator import PascalCoordinator
from .entity import PascalEntity
from .util import unquote

_SENS_RE = re.compile(r"^IN-(\d+)\.SENS$")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PascalConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up an input-sensitivity select for each input that supports it."""
    coordinator = entry.runtime_data
    cache = coordinator.client.cache

    entities = [
        PascalSensitivitySelect(coordinator, int(match.group(1)))
        for key in cache
        if (match := _SENS_RE.match(key))
    ]
    async_add_entities(entities)


class PascalSensitivitySelect(PascalEntity, SelectEntity):
    """Input sensitivity (14DBU / 4DBU / -10DBV / MIC)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = SENS_OPTIONS
    _attr_icon = "mdi:tune-vertical"

    def __init__(self, coordinator: PascalCoordinator, iid: int) -> None:
        """Initialise the sensitivity select."""
        super().__init__(coordinator)
        self._iid = iid
        self._register = f"IN-{iid}.SENS"
        self._name_register = f"IN-{iid}.NAME"
        self._attr_unique_id = f"{coordinator.unique_id}_in_{iid}_sens"

    @property
    def name(self) -> str:
        """Return '<channel name> sensitivity'."""
        channel = unquote(self._reg(self._name_register))
        if channel:
            return f"{channel} sensitivity"
        return f"Input {self._iid} sensitivity"

    @property
    def current_option(self) -> str | None:
        """Return the current sensitivity, if it is a known option."""
        value = unquote(self._reg(self._register))
        return value if value in SENS_OPTIONS else None

    async def async_select_option(self, option: str) -> None:
        """Set the input sensitivity."""
        if option not in SENS_OPTIONS:
            return
        # Quote the value: it can start with '-' (e.g. -10DBV).
        await self._async_send(self.client.async_set(self._register, f'"{option}"'))
