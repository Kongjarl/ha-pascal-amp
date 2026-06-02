"""Media player platform exposing each amplifier zone."""

from __future__ import annotations

import logging
import re

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PascalConfigEntry
from .api import PascalError
from .const import (
    DEFAULT_GAIN_MAX,
    DEFAULT_GAIN_MIN,
    MIX_SOURCE_BASE,
    REG_STATE,
    SOURCE_OFF_ID,
    SOURCE_OFF_NAME,
    STATE_ON,
    VOLUME_STEP_DB,
)
from .coordinator import PascalCoordinator
from .entity import PascalEntity
from .util import safe_bool, safe_float, safe_int, unquote

_LOGGER = logging.getLogger(__name__)

_ZONE_NAME_RE = re.compile(r"^ZONE-([A-Za-z])\.NAME$")
_INPUT_NAME_RE = re.compile(r"^IN-(\d+)\.NAME$")
_MIX_NAME_RE = re.compile(r"^MIX-(\d+)\.NAME$")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PascalConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a media player entity for each discovered zone."""
    coordinator = entry.runtime_data
    cache = coordinator.client.cache

    zone_ids = sorted(
        {
            match.group(1).upper()
            for key in cache
            if (match := _ZONE_NAME_RE.match(key))
        }
    )
    entities = [PascalZone(coordinator, zid) for zid in zone_ids]
    if not entities:
        _LOGGER.warning(
            "No zones discovered on Pascal amplifier; nothing to add as media players"
        )
    async_add_entities(entities)


class PascalZone(PascalEntity, MediaPlayerEntity):
    """A single amplifier zone presented as a media player.

    * volume_level maps to the zone gain between its GAIN_MIN/GAIN_MAX bounds
    * mute maps to ZONE-x.MUTE
    * source maps to ZONE-x.PRIMARY_SRC
    * turn on/off mutes/unmutes the zone (the whole-amp power is a switch)
    """

    _attr_device_class = MediaPlayerDeviceClass.SPEAKER

    def __init__(self, coordinator: PascalCoordinator, zone_id: str) -> None:
        """Initialise the zone entity."""
        super().__init__(coordinator)
        self._zone_id = zone_id
        self._attr_unique_id = f"{coordinator.unique_id}_zone_{zone_id}"
        self._attr_translation_placeholders = {"zone": zone_id}

    # -- register helpers ------------------------------------------------ #
    def _z(self, suffix: str) -> str:
        return f"ZONE-{self._zone_id}.{suffix}"

    @property
    def _gain_min(self) -> float:
        return safe_float(self._reg(self._z("GAIN_MIN")), DEFAULT_GAIN_MIN)

    @property
    def _gain_max(self) -> float:
        return safe_float(self._reg(self._z("GAIN_MAX")), DEFAULT_GAIN_MAX)

    @property
    def _has_external_vc(self) -> bool:
        """True when a GPIO volume control owns the gain (it is then read-only)."""
        return (safe_int(self._reg(self._z("GPIO_VC")), 0) or 0) != 0

    # -- naming ---------------------------------------------------------- #
    @property
    def name(self) -> str | None:
        """Return the zone's configured name (falls back to 'Zone X')."""
        name = unquote(self._reg(self._z("NAME")))
        return name or f"Zone {self._zone_id}"

    # -- state ----------------------------------------------------------- #
    @property
    def state(self) -> MediaPlayerState | None:
        """Return ON unless the amp is in standby or the zone is muted."""
        amp_state = unquote(self._reg(REG_STATE))
        if amp_state != STATE_ON:
            return MediaPlayerState.OFF
        if safe_bool(self._reg(self._z("MUTE"))):
            return MediaPlayerState.OFF
        return MediaPlayerState.ON

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Return supported features (volume set drops out under GPIO control)."""
        features = (
            MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.SELECT_SOURCE
            | MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
        )
        if not self._has_external_vc:
            features |= (
                MediaPlayerEntityFeature.VOLUME_SET
                | MediaPlayerEntityFeature.VOLUME_STEP
            )
        return features

    # -- volume ---------------------------------------------------------- #
    @property
    def volume_level(self) -> float | None:
        """Map zone gain (dB) onto a 0..1 volume."""
        gain = safe_float(self._reg(self._z("GAIN")))
        if gain is None:
            return None
        lo, hi = self._gain_min, self._gain_max
        if hi <= lo:
            return 0.0
        return max(0.0, min(1.0, (gain - lo) / (hi - lo)))

    @property
    def is_volume_muted(self) -> bool | None:
        """Return whether the zone is muted."""
        value = self._reg(self._z("MUTE"))
        return safe_bool(value) if value is not None else None

    # -- source ---------------------------------------------------------- #
    def _source_map(self) -> dict[str, int]:
        """Build a {display name -> source id} map from the current cache.

        Honours per-zone ``SRC-{SID}.ENABLED`` so disabled inputs are hidden.
        """
        data = self.coordinator.data or {}
        sources: dict[str, int] = {SOURCE_OFF_NAME: SOURCE_OFF_ID}
        for key, raw in data.items():
            sid: int | None = None
            if match := _INPUT_NAME_RE.match(key):
                sid = int(match.group(1))
            elif match := _MIX_NAME_RE.match(key):
                sid = MIX_SOURCE_BASE + int(match.group(1)) - 1
            if sid is None:
                continue
            # Respect the per-zone source allow-list when present.
            enabled = data.get(self._z(f"SRC-{sid}.ENABLED"))
            if enabled is not None and not safe_bool(enabled, True):
                continue
            name = unquote(raw) or f"Source {sid}"
            sources[name] = sid
        return sources

    @property
    def source_list(self) -> list[str]:
        """Return selectable source names for this zone."""
        return list(self._source_map().keys())

    @property
    def source(self) -> str | None:
        """Return the current source name."""
        sid = safe_int(self._reg(self._z("PRIMARY_SRC")))
        if sid is None:
            return None
        if sid == SOURCE_OFF_ID:
            return SOURCE_OFF_NAME
        for name, value in self._source_map().items():
            if value == sid:
                return name
        return None

    # -- commands -------------------------------------------------------- #
    async def async_set_volume_level(self, volume: float) -> None:
        """Set the zone gain from a 0..1 volume."""
        if self._has_external_vc:
            _LOGGER.warning(
                "Zone %s volume is controlled by an external GPIO control; ignoring",
                self._zone_id,
            )
            return
        lo, hi = self._gain_min, self._gain_max
        gain = lo + max(0.0, min(1.0, volume)) * (hi - lo)
        await self._async_command(self.client.async_set(self._z("GAIN"), f"{gain:.2f}"))

    async def async_volume_up(self) -> None:
        """Increase the zone gain by a fixed step."""
        if self._has_external_vc:
            return
        await self._async_command(
            self.client.async_inc(self._z("GAIN"), VOLUME_STEP_DB)
        )

    async def async_volume_down(self) -> None:
        """Decrease the zone gain by a fixed step."""
        if self._has_external_vc:
            return
        await self._async_command(
            self.client.async_inc(self._z("GAIN"), -VOLUME_STEP_DB)
        )

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute the zone."""
        await self._async_command(
            self.client.async_set(self._z("MUTE"), "1" if mute else "0")
        )

    async def async_select_source(self, source: str) -> None:
        """Select a primary source for the zone."""
        sid = self._source_map().get(source)
        if sid is None:
            _LOGGER.warning("Unknown source %r for zone %s", source, self._zone_id)
            return
        await self._async_command(
            self.client.async_set(self._z("PRIMARY_SRC"), str(sid))
        )

    async def async_turn_on(self) -> None:
        """Ensure the amp is on and the zone is unmuted."""
        if unquote(self._reg(REG_STATE)) != STATE_ON:
            await self._async_command(self.client.async_power_on())
        await self._async_command(self.client.async_set(self._z("MUTE"), "0"))

    async def async_turn_off(self) -> None:
        """Mute the zone (whole-amp power is the dedicated switch entity)."""
        await self._async_command(self.client.async_set(self._z("MUTE"), "1"))

    async def _async_command(self, coro) -> None:
        """Await a client command and refresh state, swallowing link errors."""
        try:
            await coro
        except PascalError as err:
            _LOGGER.warning("Command failed for zone %s: %s", self._zone_id, err)
            return
        self._refresh()

    @callback
    def _refresh(self) -> None:
        """Publish the optimistic cache change and update entity state."""
        self.coordinator.async_set_updated_data(dict(self.client.cache))
