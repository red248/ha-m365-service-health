"""Fixtures for M365 Service Health tests."""

from __future__ import annotations

import base64
from collections.abc import Callable
import json
from pathlib import Path
from typing import Any

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
from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_SCAN_INTERVAL

TENANT_ID = "11111111-2222-3333-4444-555555555555"
CLIENT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CLIENT_SECRET = "abc8Q~not-a-real-secret"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
OVERVIEWS_URL = (
    "https://graph.microsoft.com/v1.0/admin/serviceAnnouncement/healthOverviews"
)
ISSUES_URL = "https://graph.microsoft.com/v1.0/admin/serviceAnnouncement/issues"

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    """Load a JSON fixture."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_token(
    tenant_id: str = TENANT_ID, roles: tuple[str, ...] = ("ServiceHealth.Read.All",)
) -> str:
    """Build an unsigned JWT with the claims the client reads."""

    def b64(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'tid': tenant_id, 'roles': list(roles)})}.sig"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations in all tests."""


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry for the test tenant."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Microsoft 365",
        unique_id=TENANT_ID,
        data={
            CONF_TENANT_ID: TENANT_ID,
            CONF_CLIENT_ID: CLIENT_ID,
            CONF_CLIENT_SECRET: CLIENT_SECRET,
        },
        options={CONF_SCAN_INTERVAL: 5, CONF_EXCLUDED_SERVICES: []},
    )


MockGraph = Callable[..., None]


@pytest.fixture
def mock_graph(aioclient_mock: AiohttpClientMocker) -> MockGraph:
    """Register Microsoft responses; call again to change them."""

    def _register(
        *,
        overviews: list[dict[str, Any]] | None = None,
        issues: list[dict[str, Any]] | None = None,
        token_status: int = 200,
        token_body: dict[str, Any] | None = None,
        graph_status: int = 200,
        token: str | None = None,
        exc: Exception | None = None,
    ) -> None:
        aioclient_mock.clear_requests()
        if exc is not None:
            aioclient_mock.post(TOKEN_URL, exc=exc)
            return
        aioclient_mock.post(
            TOKEN_URL,
            status=token_status,
            json=token_body
            if token_body is not None
            else {
                "token_type": "Bearer",
                "expires_in": 3599,
                "access_token": token or make_token(),
            },
        )
        aioclient_mock.get(
            OVERVIEWS_URL,
            status=graph_status,
            json={
                "value": overviews
                if overviews is not None
                else load_fixture("health_overviews.json")
            },
        )
        aioclient_mock.get(
            ISSUES_URL,
            status=graph_status,
            json={
                "value": issues if issues is not None else load_fixture("issues.json")
            },
        )

    _register()
    return _register
