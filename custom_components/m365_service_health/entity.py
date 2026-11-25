"""Base entity for M365 Service Health."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ADMIN_CENTER_URL, DOMAIN
from .coordinator import M365ServiceHealthCoordinator


class M365Entity(CoordinatorEntity[M365ServiceHealthCoordinator]):
    """One device per tenant, all entities attached to it."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: M365ServiceHealthCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id))},
            name=entry.title,
            manufacturer="Microsoft",
            model="Microsoft 365 service health",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=ADMIN_CENTER_URL,
        )
