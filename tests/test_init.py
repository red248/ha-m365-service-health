"""Tests for setup, entities and events."""

from __future__ import annotations

import json

import aiohttp
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.m365_service_health.const import (
    CONF_EXCLUDED_SERVICES,
    DOMAIN,
    EVENT_ISSUE,
)
from custom_components.m365_service_health.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_SCAN_INTERVAL, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import CLIENT_ID, CLIENT_SECRET, TENANT_ID, MockGraph, load_fixture


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, platform: str, key: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{TENANT_ID}_{key}"
    )


async def test_setup_and_states(
    hass: HomeAssistant, mock_graph: MockGraph, config_entry: MockConfigEntry
) -> None:
    """States come from open issues, not from Microsoft's overview status."""
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    exchange_id = _entity_id(hass, "sensor", "service_Exchange")
    assert exchange_id == "sensor.microsoft_365_exchange_online"
    exchange = hass.states.get(exchange_id)
    # Degraded according to Microsoft, but no open issue: operational.
    assert exchange.state == "operational"
    assert exchange.attributes["microsoft_status"] == "serviceDegradation"
    assert exchange.attributes["incidents"] == []

    teams = hass.states.get(_entity_id(hass, "sensor", "service_microsoftteams"))
    assert teams.state == "advisory"
    assert [i["id"] for i in teams.attributes["advisories"]] == ["TM100001"]

    sharepoint = hass.states.get(_entity_id(hass, "sensor", "service_SharePoint"))
    assert sharepoint.state == "incident"
    incident = sharepoint.attributes["incidents"][0]
    assert incident["id"] == "SP100002"
    assert incident["url"].endswith("/alerts/SP100002")

    clipchamp = hass.states.get(
        _entity_id(hass, "sensor", "service_ClipchampCommercial")
    )
    assert clipchamp.name == "Microsoft 365 Microsoft Clipchamp"

    # The "Microsoft 365 suite" advisory has no overview entry: counted globally.
    assert hass.states.get("sensor.microsoft_365_active_incidents").state == "1"
    advisories = hass.states.get("sensor.microsoft_365_active_advisories")
    assert advisories.state == "2"
    assert [i["id"] for i in advisories.attributes["issues"]] == [
        "TM100001",
        "MO100003",
    ]
    assert hass.states.get("binary_sensor.microsoft_365_incident").state == STATE_ON


async def test_issue_events(
    hass: HomeAssistant, mock_graph: MockGraph, config_entry: MockConfigEntry
) -> None:
    """New, updated and resolved issues fire events; startup does not."""
    events = async_capture_events(hass, EVENT_ISSUE)
    await _setup(hass, config_entry)
    assert events == []

    issues = load_fixture("issues.json")
    teams, _sharepoint, suite = issues
    teams["classification"] = "incident"  # an advisory escalated
    exchange = {
        **suite,
        "id": "EX100004",
        "service": "Exchange Online",
        "classification": "incident",
        "title": "Users may be unable to send email",
    }
    mock_graph(issues=[teams, suite, exchange])
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert {(e.data["action"], e.data["id"]) for e in events} == {
        ("updated", "TM100001"),
        ("resolved", "SP100002"),
        ("new", "EX100004"),
    }
    new = next(e for e in events if e.data["action"] == "new")
    assert new.data["classification"] == "incident"
    assert new.data["service"] == "Exchange Online"
    assert new.data["tenant"] == "Microsoft 365"
    assert new.data["config_entry_id"] == config_entry.entry_id

    state = hass.states.get("sensor.microsoft_365_exchange_online")
    assert state.state == "incident"
    assert (
        hass.states.get("sensor.microsoft_365_sharepoint_online").state == "operational"
    )
    assert hass.states.get("sensor.microsoft_365_active_incidents").state == "2"


async def test_excluded_services(hass: HomeAssistant, mock_graph: MockGraph) -> None:
    """Excluded services have no entity and their issues are ignored."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Microsoft 365",
        unique_id=TENANT_ID,
        data={
            "tenant_id": TENANT_ID,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        options={CONF_SCAN_INTERVAL: 5, CONF_EXCLUDED_SERVICES: ["SharePoint"]},
    )
    await _setup(hass, entry)
    assert _entity_id(hass, "sensor", "service_SharePoint") is None
    assert hass.states.get("sensor.microsoft_365_active_incidents").state == "0"
    assert hass.states.get("binary_sensor.microsoft_365_incident").state == STATE_OFF


async def test_new_service_is_added(
    hass: HomeAssistant, mock_graph: MockGraph, config_entry: MockConfigEntry
) -> None:
    """A service published later by Microsoft gets its entity."""
    await _setup(hass, config_entry)
    assert _entity_id(hass, "sensor", "service_Planner") is None

    overviews = load_fixture("health_overviews.json")
    overviews.append(
        {"id": "Planner", "service": "Planner", "status": "serviceOperational"}
    )
    mock_graph(overviews=overviews)
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    entity_id = _entity_id(hass, "sensor", "service_Planner")
    assert entity_id == "sensor.microsoft_365_planner"
    assert hass.states.get(entity_id).state == "operational"


async def test_service_disappears(
    hass: HomeAssistant, mock_graph: MockGraph, config_entry: MockConfigEntry
) -> None:
    """A service no longer listed becomes unavailable."""
    await _setup(hass, config_entry)
    overviews = [
        o for o in load_fixture("health_overviews.json") if o["id"] != "Exchange"
    ]
    mock_graph(overviews=overviews)
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert (
        hass.states.get("sensor.microsoft_365_exchange_online").state == "unavailable"
    )


async def test_expired_secret_starts_reauth(
    hass: HomeAssistant, mock_graph: MockGraph, config_entry: MockConfigEntry
) -> None:
    """When the secret expires, Home Assistant asks for a new one."""
    await _setup(hass, config_entry)
    mock_graph(
        graph_status=401,
        token_status=401,
        token_body={"error": "invalid_client", "error_codes": [7000222]},
    )
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH
    assert (
        hass.states.get("binary_sensor.microsoft_365_incident").state == "unavailable"
    )


async def test_setup_auth_error(
    hass: HomeAssistant, mock_graph: MockGraph, config_entry: MockConfigEntry
) -> None:
    """Invalid credentials at startup: setup error and reauth."""
    mock_graph(
        token_status=401,
        token_body={"error": "invalid_client", "error_codes": [7000215]},
    )
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        f["context"]["source"] == SOURCE_REAUTH
        for f in hass.config_entries.flow.async_progress()
    )


async def test_setup_retry(
    hass: HomeAssistant, mock_graph: MockGraph, config_entry: MockConfigEntry
) -> None:
    """Network errors at startup are retried."""
    mock_graph(exc=aiohttp.ClientError())
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload(
    hass: HomeAssistant, mock_graph: MockGraph, config_entry: MockConfigEntry
) -> None:
    """The entry unloads cleanly."""
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_diagnostics_are_redacted(
    hass: HomeAssistant, mock_graph: MockGraph, config_entry: MockConfigEntry
) -> None:
    """Diagnostics never contain credentials or the tenant ID."""
    await _setup(hass, config_entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    dumped = json.dumps(diagnostics, default=str)
    for secret in (CLIENT_SECRET, CLIENT_ID, TENANT_ID):
        assert secret not in dumped
    assert {s["id"]: s["state"] for s in diagnostics["services"]}[
        "SharePoint"
    ] == "incident"
