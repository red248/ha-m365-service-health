"""Sensors for M365 Service Health."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ServiceIssue
from .const import MAX_ATTRIBUTE_ISSUES, SERVICE_STATES
from .coordinator import (
    M365ConfigEntry,
    M365Data,
    M365ServiceHealthCoordinator,
    ServiceState,
    issue_summary,
)
from .entity import M365Entity

PARALLEL_UPDATES = 0  # the coordinator does all the polling


@dataclass(frozen=True, kw_only=True)
class M365CountSensorDescription(SensorEntityDescription):
    """Describes a sensor counting open issues."""

    issues_fn: Callable[[M365Data], list[ServiceIssue]]


COUNT_SENSORS: tuple[M365CountSensorDescription, ...] = (
    M365CountSensorDescription(
        key="active_incidents",
        translation_key="active_incidents",
        state_class=SensorStateClass.MEASUREMENT,
        issues_fn=lambda data: data.incidents,
    ),
    M365CountSensorDescription(
        key="active_advisories",
        translation_key="active_advisories",
        state_class=SensorStateClass.MEASUREMENT,
        issues_fn=lambda data: data.advisories,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: M365ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors, adding services that appear later on the fly."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _async_add_new_services() -> None:
        new = [sid for sid in coordinator.data.services if sid not in known]
        if not new:
            return
        known.update(new)
        async_add_entities(M365ServiceSensor(coordinator, sid) for sid in new)

    async_add_entities(
        M365CountSensor(coordinator, description) for description in COUNT_SENSORS
    )
    _async_add_new_services()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_services))


class M365CountSensor(M365Entity, SensorEntity):
    """Number of open incidents or advisories in the tenant."""

    entity_description: M365CountSensorDescription
    _unrecorded_attributes = frozenset({"issues"})

    def __init__(
        self,
        coordinator: M365ServiceHealthCoordinator,
        description: M365CountSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int:
        """Return the number of open issues."""
        return len(self.entity_description.issues_fn(self.coordinator.data))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the open issues (most recent first)."""
        issues = self.entity_description.issues_fn(self.coordinator.data)
        return {"issues": [issue_summary(i) for i in issues[:MAX_ATTRIBUTE_ISSUES]]}


class M365ServiceSensor(M365Entity, SensorEntity):
    """State of one Microsoft 365 service: operational, advisory or incident."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = SERVICE_STATES
    _attr_translation_key = "service_status"
    _unrecorded_attributes = frozenset({"incidents", "advisories"})

    def __init__(
        self, coordinator: M365ServiceHealthCoordinator, service_id: str
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, f"service_{service_id}")
        self._service_id = service_id
        self._attr_translation_placeholders = {
            "service": coordinator.data.services[service_id].service.name
        }

    @property
    def _service_state(self) -> ServiceState | None:
        return self.coordinator.data.services.get(self._service_id)

    @property
    def available(self) -> bool:
        """Unavailable if Microsoft stops listing the service."""
        return super().available and self._service_state is not None

    @property
    def native_value(self) -> str | None:
        """Return the simplified state."""
        if (state := self._service_state) is None:
            return None
        return state.state

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose Microsoft's raw status and the open issues."""
        if (state := self._service_state) is None:
            return None
        return {
            "service_id": self._service_id,
            "microsoft_status": state.service.status,
            "incidents": [
                issue_summary(i) for i in state.incidents[:MAX_ATTRIBUTE_ISSUES]
            ],
            "advisories": [
                issue_summary(i) for i in state.advisories[:MAX_ATTRIBUTE_ISSUES]
            ],
        }
