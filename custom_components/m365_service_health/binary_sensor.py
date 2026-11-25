"""Binary sensor for M365 Service Health."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import M365ConfigEntry, M365ServiceHealthCoordinator
from .entity import M365Entity

PARALLEL_UPDATES = 0  # the coordinator does all the polling


async def async_setup_entry(
    hass: HomeAssistant,
    entry: M365ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensor."""
    async_add_entities([M365IncidentBinarySensor(entry.runtime_data)])


class M365IncidentBinarySensor(M365Entity, BinarySensorEntity):
    """On while at least one incident is open on a monitored service."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "incident"

    def __init__(self, coordinator: M365ServiceHealthCoordinator) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, "incident")

    @property
    def is_on(self) -> bool:
        """Return True if an incident is open."""
        return bool(self.coordinator.data.incidents)
