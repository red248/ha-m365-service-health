"""Tests for the config, reauth, reconfigure and options flows."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.m365_service_health.const import (
    CONF_EXCLUDED_SERVICES,
    CONF_TENANT_ID,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from .conftest import (
    CLIENT_ID,
    CLIENT_SECRET,
    OVERVIEWS_URL,
    TENANT_ID,
    MockGraph,
    make_token,
)

USER_INPUT = {
    CONF_NAME: "Contoso",
    CONF_TENANT_ID: TENANT_ID,
    CONF_CLIENT_ID: CLIENT_ID,
    CONF_CLIENT_SECRET: f" {CLIENT_SECRET} ",
}
SETUP_PATCH = "custom_components.m365_service_health.async_setup_entry"


async def test_user_flow(hass: HomeAssistant, mock_graph: MockGraph) -> None:
    """A valid app registration creates an entry keyed by the tenant GUID."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(SETUP_PATCH, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Contoso"
    assert result["result"].unique_id == TENANT_ID
    assert result["data"] == {
        CONF_TENANT_ID: TENANT_ID,
        CONF_CLIENT_ID: CLIENT_ID,
        CONF_CLIENT_SECRET: CLIENT_SECRET,
    }
    assert result["options"] == {CONF_SCAN_INTERVAL: 5, CONF_EXCLUDED_SERVICES: []}


async def test_user_flow_with_domain(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A tenant domain is stored as the tenant GUID."""
    aioclient_mock.post(
        "https://login.microsoftonline.com/contoso.onmicrosoft.com/oauth2/v2.0/token",
        json={"access_token": make_token(), "expires_in": 3599},
    )
    aioclient_mock.get(OVERVIEWS_URL, json={"value": []})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(SETUP_PATCH, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**USER_INPUT, CONF_TENANT_ID: "contoso.onmicrosoft.com"},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TENANT_ID] == TENANT_ID


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        (CONF_TENANT_ID, "not a tenant", "invalid_tenant_format"),
        (CONF_CLIENT_ID, "my-app", "invalid_client_id_format"),
    ],
)
async def test_user_flow_format_errors(
    hass: HomeAssistant, mock_graph: MockGraph, field: str, value: str, error: str
) -> None:
    """Obviously wrong identifiers are rejected before calling Microsoft."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, field: value}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: error}


@pytest.mark.parametrize(
    ("graph", "error"),
    [
        (
            {
                "token_status": 401,
                "token_body": {"error": "invalid_client", "error_codes": [7000215]},
            },
            "invalid_client_secret",
        ),
        (
            {
                "token_status": 401,
                "token_body": {"error": "invalid_client", "error_codes": [7000222]},
            },
            "expired_client_secret",
        ),
        (
            {
                "token_status": 400,
                "token_body": {"error": "invalid_request", "error_codes": [90002]},
            },
            "invalid_tenant",
        ),
        (
            {
                "token_status": 400,
                "token_body": {"error": "unauthorized_client", "error_codes": [700016]},
            },
            "invalid_client_id",
        ),
        (
            {
                "token_status": 400,
                "token_body": {"error": "invalid_request", "error_codes": [12345]},
            },
            "invalid_auth",
        ),
        ({"token": make_token(roles=())}, "missing_permission"),
        ({"graph_status": 403}, "missing_permission"),
        ({"token_status": 503, "token_body": {}}, "cannot_connect"),
        ({"exc": aiohttp.ClientError()}, "cannot_connect"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, mock_graph: MockGraph, graph: dict[str, Any], error: str
) -> None:
    """Errors are shown, and the user can fix them."""
    mock_graph(**graph)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_graph()
    with patch(SETUP_PATCH, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_graph: MockGraph, config_entry: MockConfigEntry
) -> None:
    """The same tenant cannot be added twice."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, mock_graph: MockGraph, config_entry: MockConfigEntry
) -> None:
    """An expired secret is replaced through the reauth flow."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_graph(
        token_status=401,
        token_body={"error": "invalid_client", "error_codes": [7000222]},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CLIENT_SECRET: "still-old"}
    )
    assert result["errors"] == {"base": "expired_client_secret"}

    mock_graph()
    with patch(SETUP_PATCH, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CLIENT_SECRET: "brand-new"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_CLIENT_SECRET] == "brand-new"


async def test_reauth_other_tenant(
    hass: HomeAssistant, mock_graph: MockGraph, config_entry: MockConfigEntry
) -> None:
    """Credentials of another tenant are refused."""
    config_entry.add_to_hass(hass)
    mock_graph(token=make_token(tenant_id="99999999-9999-9999-9999-999999999999"))
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CLIENT_SECRET: "brand-new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert config_entry.data[CONF_CLIENT_SECRET] == CLIENT_SECRET


async def test_reconfigure(
    hass: HomeAssistant, mock_graph: MockGraph, config_entry: MockConfigEntry
) -> None:
    """The app registration can be changed."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    new_client = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    with patch(SETUP_PATCH, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_CLIENT_ID: new_client, CONF_CLIENT_SECRET: "other-secret"},
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_CLIENT_ID] == new_client
    assert config_entry.data[CONF_CLIENT_SECRET] == "other-secret"


async def test_options_flow(
    hass: HomeAssistant, mock_graph: MockGraph, config_entry: MockConfigEntry
) -> None:
    """Excluding a service removes its entity after the automatic reload."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    unique_id = f"{TENANT_ID}_service_Exchange"
    assert registry.async_get_entity_id("sensor", DOMAIN, unique_id)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"].schema
    selector = next(v for k, v in schema.items() if k == CONF_EXCLUDED_SERVICES)
    labels = {o["value"]: o["label"] for o in selector.config["options"]}
    assert labels["Exchange"] == "Exchange Online"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL: 10, CONF_EXCLUDED_SERVICES: ["Exchange"]},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {
        CONF_SCAN_INTERVAL: 10,
        CONF_EXCLUDED_SERVICES: ["Exchange"],
    }
    assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None
    assert config_entry.runtime_data.update_interval.total_seconds() == 600
