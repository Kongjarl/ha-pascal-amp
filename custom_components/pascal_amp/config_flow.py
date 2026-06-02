"""Config flow for the Pascal IP Amplifier integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import PascalClient, PascalError
from .const import DEFAULT_NAME, DEFAULT_PORT, DOMAIN, REG_MODEL, REG_SERIAL
from .util import unquote

_LOGGER = logging.getLogger(__name__)


async def _async_probe(host: str, port: int) -> dict[str, str]:
    """Validate connectivity and return identity registers.

    Raises :class:`PascalError` on failure.
    """
    client = PascalClient(host, port)
    return await client.async_fetch_info()


class PascalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Pascal amplifiers."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise transient discovery state."""
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._serial: str | None = None
        self._model: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a manually initiated flow."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input.get(CONF_PORT, DEFAULT_PORT)
            try:
                info = await _async_probe(host, port)
            except PascalError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error probing %s", host)
                errors["base"] = "unknown"
            else:
                serial = unquote(info.get(REG_SERIAL)) or None
                model = unquote(info.get(REG_MODEL))
                unique_id = serial or f"{host}:{port}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: host, CONF_PORT: port}
                )
                title = model or DEFAULT_NAME
                return self.async_create_entry(
                    title=title,
                    data={CONF_HOST: host, CONF_PORT: port},
                )

        suggested = {
            CONF_HOST: (user_input or {}).get(CONF_HOST, ""),
            CONF_PORT: (user_input or {}).get(CONF_PORT, DEFAULT_PORT),
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=suggested[CONF_HOST]): str,
                vol.Optional(CONF_PORT, default=suggested[CONF_PORT]): int,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle discovery via mDNS (_pasconnect._tcp)."""
        host = discovery_info.host
        port = discovery_info.port or DEFAULT_PORT
        props = discovery_info.properties or {}

        # Only continue for Pascal amplifier services.
        device_type = props.get("device_type")
        if device_type and device_type != "PasAmpControl":
            return self.async_abort(reason="not_pascal_amp")

        serial = props.get("serial")
        model = props.get("model")
        unique_id = serial or host

        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: host, CONF_PORT: port}
        )

        self._host = host
        self._port = port
        self._serial = serial
        self._model = model
        self.context["title_placeholders"] = {"name": model or DEFAULT_NAME}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a discovered amplifier and validate it is reachable."""
        assert self._host is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await _async_probe(self._host, self._port)
            except PascalError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error probing %s", self._host)
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=self._model or DEFAULT_NAME,
                    data={CONF_HOST: self._host, CONF_PORT: self._port},
                )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={
                "name": self._model or DEFAULT_NAME,
                "host": self._host,
            },
            errors=errors,
        )
