"""Data update coordinator for M365 Service Health."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    M365AuthError,
    M365Error,
    M365PermissionError,
    M365ServiceHealthClient,
    ServiceHealth,
    ServiceIssue,
)
from .const import (
    CONF_EXCLUDED_SERVICES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_ISSUE,
    ISSUE_URL,
    STATE_ADVISORY,
    STATE_INCIDENT,
    STATE_OPERATIONAL,
)

_LOGGER = logging.getLogger(__name__)

type M365ConfigEntry = ConfigEntry[M365ServiceHealthCoordinator]


def _sort_key(issue: ServiceIssue) -> str:
    return issue.start.isoformat() if issue.start else ""


def issue_summary(issue: ServiceIssue) -> dict[str, Any]:
    """Return the issue fields exposed in attributes."""
    return {
        "id": issue.id,
        "service": issue.service,
        "title": issue.title,
        "status": issue.status,
        "start": issue.start.isoformat() if issue.start else None,
        "url": ISSUE_URL.format(issue_id=issue.id),
    }


@dataclass(slots=True)
class ServiceState:
    """A monitored service with its open issues."""

    service: ServiceHealth
    incidents: list[ServiceIssue] = field(default_factory=list)
    advisories: list[ServiceIssue] = field(default_factory=list)

    @property
    def state(self) -> str:
        """Return operational, advisory or incident."""
        if self.incidents:
            return STATE_INCIDENT
        if self.advisories:
            return STATE_ADVISORY
        return STATE_OPERATIONAL


@dataclass(slots=True)
class M365Data:
    """Snapshot of the tenant service health."""

    all_services: dict[str, ServiceHealth]  # every service, by id
    services: dict[str, ServiceState]  # monitored services, by id
    issues: dict[str, ServiceIssue]  # open issues of monitored services

    @property
    def incidents(self) -> list[ServiceIssue]:
        """Open incidents, most recent first."""
        return sorted(
            (i for i in self.issues.values() if i.is_incident),
            key=_sort_key,
            reverse=True,
        )

    @property
    def advisories(self) -> list[ServiceIssue]:
        """Open advisories, most recent first."""
        return sorted(
            (i for i in self.issues.values() if not i.is_incident),
            key=_sort_key,
            reverse=True,
        )


def build_data(
    services: list[ServiceHealth],
    issues: list[ServiceIssue],
    excluded: set[str],
) -> M365Data:
    """Match issues to services (by name, as Graph does) and apply exclusions."""
    all_services = {s.id: s for s in services}
    excluded_names = {s.name.casefold() for s in services if s.id in excluded}
    states = {s.id: ServiceState(s) for s in services if s.id not in excluded}
    by_name = {state.service.name.casefold(): state for state in states.values()}

    kept: dict[str, ServiceIssue] = {}
    for issue in issues:
        key = issue.service.casefold()
        if key in excluded_names:
            continue
        # Issues of services missing from the overview still count globally.
        kept[issue.id] = issue
        if (state := by_name.get(key)) is not None:
            (state.incidents if issue.is_incident else state.advisories).append(issue)

    for state in states.values():
        state.incidents.sort(key=_sort_key, reverse=True)
        state.advisories.sort(key=_sort_key, reverse=True)
    return M365Data(all_services=all_services, services=states, issues=kept)


class M365ServiceHealthCoordinator(DataUpdateCoordinator[M365Data]):
    """Poll Microsoft Graph and fire events when issues change."""

    config_entry: M365ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: M365ConfigEntry,
        client: M365ServiceHealthClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(
                minutes=config_entry.options.get(
                    CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                )
            ),
        )
        self.client = client
        self.excluded: set[str] = set(
            config_entry.options.get(CONF_EXCLUDED_SERVICES, [])
        )
        self._known_issues: dict[str, ServiceIssue] | None = None

    async def _async_update_data(self) -> M365Data:
        try:
            services = await self.client.async_get_health_overviews()
            issues = await self.client.async_get_open_issues()
        except M365AuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="auth_failed",
                translation_placeholders={"reason": err.reason},
            ) from err
        except M365PermissionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="missing_permission",
            ) from err
        except M365Error as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        data = build_data(services, issues, self.excluded)
        self._async_fire_issue_events(data.issues)
        return data

    def _async_fire_issue_events(self, issues: dict[str, ServiceIssue]) -> None:
        previous, self._known_issues = self._known_issues, dict(issues)
        if previous is None:
            return  # first refresh after startup: baseline only, no events

        for issue_id, issue in issues.items():
            old = previous.get(issue_id)
            if old is None:
                self._async_fire("new", issue)
            elif (old.classification, old.status, old.title, old.last_modified) != (
                issue.classification,
                issue.status,
                issue.title,
                issue.last_modified,
            ):
                self._async_fire("updated", issue)
        for issue_id, old in previous.items():
            if issue_id not in issues:
                self._async_fire("resolved", old)

    def _async_fire(self, action: str, issue: ServiceIssue) -> None:
        self.hass.bus.async_fire(
            EVENT_ISSUE,
            {
                "config_entry_id": self.config_entry.entry_id,
                "tenant": self.config_entry.title,
                "action": action,
                **issue_summary(issue),
                "classification": issue.classification,
                "feature": issue.feature,
                "impact_description": issue.impact_description,
                "last_modified": (
                    issue.last_modified.isoformat() if issue.last_modified else None
                ),
            },
        )
