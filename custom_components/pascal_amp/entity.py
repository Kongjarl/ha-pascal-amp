"""Base entity for the Pascal IP Amplifier integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import PascalClient
from .coordinator import PascalCoordinator


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
