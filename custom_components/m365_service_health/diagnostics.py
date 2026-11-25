"""Diagnostics for M365 Service Health."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET
from homeassistant.core import HomeAssistant

from .const import CONF_TENANT_ID
from .coordinator import M365ConfigEntry

TO_REDACT = {CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_TENANT_ID, "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: M365ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics without any credential or tenant identifier."""
    data = entry.runtime_data.data
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "services": [
            {
                "id": state.service.id,
                "name": state.service.name,
                "microsoft_status": state.service.status,
                "state": state.state,
                "incidents": [i.id for i in state.incidents],
                "advisories": [i.id for i in state.advisories],
            }
            for state in data.services.values()
        ],
        "excluded_services": sorted(entry.runtime_data.excluded),
        "issues": [asdict(issue) for issue in data.issues.values()],
    }
