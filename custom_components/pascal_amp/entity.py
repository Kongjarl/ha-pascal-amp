"""Base entity for the Pascal IP Amplifier integration."""

from __future__ import annotations

import logging
from collections.abc import Awaitable

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import PascalClient, PascalError
from .coordinator import PascalCoordinator

_LOGGER = logging.getLogger(__name__)


class PascalEntity(CoordinatorEntity[PascalCoordinator]):
    """Common base wiring entities to the coordinator and amplifier."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PascalCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self.client: PascalClient = coordinator.client

    @property
    def device_info(self) -> DeviceInfo:
        """Return shared device info."""
        return self.coordinator.device_info

    @property
    def available(self) -> bool:
        """Entity is available only while connected and last sync succeeded."""
        return (
            super().available
            and self.client.connected
            and self.coordinator.data is not None
        )

    def _reg(self, register: str) -> str | None:
        """Read a register from the latest coordinator snapshot."""
        data = self.coordinator.data
        if data is None:
            return None
        return data.get(register)

    async def _async_send(self, coro: Awaitable) -> bool:
        """Run a client command, swallow link errors, and push fresh state.

        Returns True on success. Never raises, so a transient amplifier issue
        cannot bubble up and break the UI.
        """
        try:
            await coro
        except PascalError as err:
            _LOGGER.warning("Command failed on %s: %s", self.entity_id, err)
            return False
        self.coordinator.async_set_updated_data(dict(self.client.cache))
        return True
