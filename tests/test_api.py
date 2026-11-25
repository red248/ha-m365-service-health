"""Tests for the Microsoft Graph client."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.m365_service_health.api import (
    M365AuthError,
    M365ConnectionError,
    M365PermissionError,
    M365ServiceHealthClient,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import (
    CLIENT_ID,
    CLIENT_SECRET,
    ISSUES_URL,
    OVERVIEWS_URL,
    TENANT_ID,
    TOKEN_URL,
    MockGraph,
    make_token,
)


def _client(hass: HomeAssistant, tenant: str = TENANT_ID) -> M365ServiceHealthClient:
    return M365ServiceHealthClient(
        async_get_clientsession(hass), tenant, CLIENT_ID, CLIENT_SECRET
    )


def _calls(aioclient_mock: AiohttpClientMocker, url: str) -> int:
    return sum(1 for call in aioclient_mock.mock_calls if str(call[1]).startswith(url))


async def test_token_is_cached(
    hass: HomeAssistant, mock_graph: MockGraph, aioclient_mock: AiohttpClientMocker
) -> None:
    """One token request serves several Graph calls."""
    client = _client(hass)
    services = await client.async_get_health_overviews()
    issues = await client.async_get_open_issues()

    assert _calls(aioclient_mock, TOKEN_URL) == 1
    assert client.tenant_guid == TENANT_ID
    assert [s.id for s in services][:2] == ["Exchange", "microsoftteams"]
    assert services[-1].name == "Microsoft Clipchamp"  # trailing space removed
    assert {i.id for i in issues} == {"TM100001", "SP100002", "MO100003"}
    sharepoint = next(i for i in issues if i.id == "SP100002")
    assert sharepoint.is_incident
    assert sharepoint.start is not None and sharepoint.start.minute == 30


async def test_issues_request_filters_and_selects(
    hass: HomeAssistant, mock_graph: MockGraph, aioclient_mock: AiohttpClientMocker
) -> None:
    """Only open issues are requested, without their message history."""
    await _client(hass).async_get_open_issues()
    url = next(
        c[1] for c in aioclient_mock.mock_calls if str(c[1]).startswith(ISSUES_URL)
    )
    assert url.query["$filter"] == "isResolved eq false"
    assert "posts" not in url.query["$select"]
    assert "classification" in url.query["$select"]


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        (7000215, "invalid_client_secret"),
        (7000222, "expired_client_secret"),
        (90002, "invalid_tenant"),
        (700016, "invalid_client_id"),
        (53003, "blocked"),
        (99999, "unknown"),
    ],
)
async def test_auth_error_reasons(
    hass: HomeAssistant, mock_graph: MockGraph, code: int, reason: str
) -> None:
    """AADSTS codes are mapped to a reason, without echoing credentials."""
    mock_graph(
        token_status=401,
        token_body={
            "error": "invalid_client",
            "error_description": f"AADSTS{code}: something for {CLIENT_ID}",
            "error_codes": [code],
        },
    )
    with pytest.raises(M365AuthError) as err:
        await _client(hass).async_get_health_overviews()
    assert err.value.reason == reason
    assert CLIENT_ID not in str(err.value)


async def test_missing_role(hass: HomeAssistant, mock_graph: MockGraph) -> None:
    """A token without ServiceHealth.Read.All is reported clearly."""
    mock_graph(token=make_token(roles=("User.Read.All",)))
    with pytest.raises(M365PermissionError):
        await _client(hass).async_get_health_overviews()


@pytest.mark.parametrize(
    ("graph_status", "error"),
    [
        (403, M365PermissionError),
        (429, M365ConnectionError),
        (503, M365ConnectionError),
    ],
)
async def test_graph_errors(
    hass: HomeAssistant, mock_graph: MockGraph, graph_status: int, error: type
) -> None:
    """Graph errors are mapped to the right exception."""
    mock_graph(graph_status=graph_status)
    with pytest.raises(error):
        await _client(hass).async_get_health_overviews()


async def test_token_endpoint_unavailable(
    hass: HomeAssistant, mock_graph: MockGraph
) -> None:
    """A Microsoft-side error on sign-in is a connection error, not an auth error."""
    mock_graph(token_status=503, token_body={})
    with pytest.raises(M365ConnectionError):
        await _client(hass).async_get_health_overviews()


async def test_401_refreshes_token_once(
    hass: HomeAssistant, mock_graph: MockGraph, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 from Graph triggers one new token, then the call succeeds."""
    responses = iter([401, 200])

    async def _side_effect(
        method: str, url: URL, data: object
    ) -> AiohttpClientMockResponse:
        return AiohttpClientMockResponse(
            method, url, status=next(responses), json={"value": []}
        )

    aioclient_mock.clear_requests()
    aioclient_mock.post(
        TOKEN_URL, json={"access_token": make_token(), "expires_in": 3599}
    )
    aioclient_mock.get(OVERVIEWS_URL, side_effect=_side_effect)

    assert await _client(hass).async_get_health_overviews() == []
    assert _calls(aioclient_mock, TOKEN_URL) == 2


async def test_pagination_stays_on_graph(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Next links are followed, but never outside Microsoft Graph."""
    aioclient_mock.post(
        TOKEN_URL, json={"access_token": make_token(), "expires_in": 3599}
    )
    aioclient_mock.get(
        f"{OVERVIEWS_URL}?$skiptoken=page3",
        json={"value": [{"id": "C", "service": "C", "status": "serviceOperational"}]},
    )
    aioclient_mock.get(
        f"{OVERVIEWS_URL}?$skiptoken=page2",
        json={
            "value": [{"id": "B", "service": "B", "status": "serviceOperational"}],
            "@odata.nextLink": "https://evil.example.com/steal?$skiptoken=page3",
        },
    )
    aioclient_mock.get(
        OVERVIEWS_URL,
        json={
            "value": [{"id": "A", "service": "A", "status": "serviceOperational"}],
            "@odata.nextLink": f"{OVERVIEWS_URL}?$skiptoken=page2",
        },
    )

    services = await _client(hass).async_get_health_overviews()
    assert [s.id for s in services] == ["A", "B"]
    assert not any("evil" in str(c[1]) for c in aioclient_mock.mock_calls)


async def test_tenant_domain_is_resolved(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A tenant entered as a domain gets its GUID from the token."""
    aioclient_mock.post(
        "https://login.microsoftonline.com/contoso.onmicrosoft.com/oauth2/v2.0/token",
        json={"access_token": make_token(), "expires_in": 3599},
    )
    aioclient_mock.get(OVERVIEWS_URL, json={"value": []})
    client = _client(hass, "contoso.onmicrosoft.com")
    await client.async_get_health_overviews()
    assert client.tenant_guid == TENANT_ID
