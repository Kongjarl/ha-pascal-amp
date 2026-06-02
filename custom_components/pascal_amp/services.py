"""Services for advanced amplifier control (EQ bands, raw register set)."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .api import PascalError
from .const import (
    DOMAIN,
    EQ_FREQ_MAX,
    EQ_FREQ_MIN,
    EQ_Q_MAX,
    EQ_Q_MIN,
    INPUT_EQ_GAIN_MAX,
    INPUT_EQ_GAIN_MIN,
    INPUT_EQ_TYPES,
    SERVICE_SET_INPUT_EQ_BAND,
    SERVICE_SET_REGISTER,
)
from .coordinator import PascalCoordinator

_LOGGER = logging.getLogger(__name__)

ATTR_DEVICE_ID = "device_id"

_EQ_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Required("input"): vol.Coerce(int),
        vol.Required("band"): vol.All(vol.Coerce(int), vol.Range(min=1, max=15)),
        vol.Optional("type"): vol.In(INPUT_EQ_TYPES),
        vol.Optional("gain"): vol.All(
            vol.Coerce(float), vol.Range(min=INPUT_EQ_GAIN_MIN, max=INPUT_EQ_GAIN_MAX)
        ),
        vol.Optional("frequency"): vol.All(
            vol.Coerce(float), vol.Range(min=EQ_FREQ_MIN, max=EQ_FREQ_MAX)
        ),
        vol.Optional("q"): vol.All(
            vol.Coerce(float), vol.Range(min=EQ_Q_MIN, max=EQ_Q_MAX)
        ),
        vol.Optional("bypass"): cv.boolean,
    }
)

_SET_REGISTER_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Required("register"): cv.string,
        vol.Required("value"): cv.string,
    }
)


def _coordinators(hass: HomeAssistant, device_ids: list[str]) -> list[PascalCoordinator]:
    """Resolve target device ids to loaded Pascal coordinators."""
    dev_reg = dr.async_get(hass)
    coordinators: list[PascalCoordinator] = []
    seen: set[str] = set()
    for device_id in device_ids:
        device = dev_reg.async_get(device_id)
        if device is None:
            raise HomeAssistantError(f"Unknown device id: {device_id}")
        matched = False
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is None or entry.domain != DOMAIN:
                continue
            coordinator = getattr(entry, "runtime_data", None)
            if coordinator is None or entry_id in seen:
                continue
            seen.add(entry_id)
            coordinators.append(coordinator)
            matched = True
        if not matched:
            raise HomeAssistantError(
                f"Device {device_id} is not a Pascal amplifier (or not loaded)"
            )
    return coordinators


async def _apply(coordinator: PascalCoordinator, ops: list[tuple[str, str, bool]]) -> None:
    """Apply (register, value, is_string) operations to one amplifier."""
    for register, value, is_string in ops:
        try:
            if is_string:
                await coordinator.client.async_set_string(register, value)
            else:
                await coordinator.client.async_set(register, value)
        except PascalError as err:
            raise HomeAssistantError(
                f"Amplifier rejected {register}={value}: {err}"
            ) from err
    coordinator.async_set_updated_data(dict(coordinator.client.cache))


def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration services (idempotent)."""

    async def _handle_set_eq_band(call: ServiceCall) -> None:
        iid = call.data["input"]
        band = call.data["band"]
        prefix = f"IN-{iid}.EQ-{band}"
        ops: list[tuple[str, str, bool]] = []
        if "type" in call.data:
            ops.append((f"{prefix}.TYPE", call.data["type"], False))
        if "gain" in call.data:
            ops.append((f"{prefix}.GAIN", f"{call.data['gain']:.2f}", False))
        if "frequency" in call.data:
            ops.append((f"{prefix}.FREQ", f"{call.data['frequency']:.2f}", False))
        if "q" in call.data:
            ops.append((f"{prefix}.Q", f"{call.data['q']:.3f}", False))
        if "bypass" in call.data:
            ops.append((f"{prefix}.BYPASS", "1" if call.data["bypass"] else "0", False))
        if not ops:
            raise HomeAssistantError(
                "Provide at least one of: type, gain, frequency, q, bypass"
            )
        for coordinator in _coordinators(hass, call.data[ATTR_DEVICE_ID]):
            await _apply(coordinator, ops)

    async def _handle_set_register(call: ServiceCall) -> None:
        register = call.data["register"].strip()
        value = call.data["value"]
        for coordinator in _coordinators(hass, call.data[ATTR_DEVICE_ID]):
            await _apply(coordinator, [(register, value, True)])

    if not hass.services.has_service(DOMAIN, SERVICE_SET_INPUT_EQ_BAND):
        hass.services.async_register(
            DOMAIN, SERVICE_SET_INPUT_EQ_BAND, _handle_set_eq_band, schema=_EQ_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SET_REGISTER):
        hass.services.async_register(
            DOMAIN, SERVICE_SET_REGISTER, _handle_set_register, schema=_SET_REGISTER_SCHEMA
        )


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove integration services (call when the last entry unloads)."""
    for service in (SERVICE_SET_INPUT_EQ_BAND, SERVICE_SET_REGISTER):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
