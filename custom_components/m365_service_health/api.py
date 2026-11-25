"""Small async client for the Microsoft Graph service health API.

Only aiohttp is used, so the integration has no extra requirements.
Authentication uses the OAuth 2.0 client credentials flow of an Entra ID
app registration with the ServiceHealth.Read.All *application* permission.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime
import json
import logging
import time
from typing import Any
from urllib.parse import quote

import aiohttp

_LOGGER = logging.getLogger(__name__)

TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
GRAPH_ORIGIN = "https://graph.microsoft.com/"
GRAPH_BASE = "https://graph.microsoft.com/v1.0/admin/serviceAnnouncement"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
REQUIRED_ROLE = "ServiceHealth.Read.All"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
TOKEN_EXPIRY_MARGIN = 300  # refresh the token 5 minutes before it expires
MAX_PAGES = 20

_OVERVIEW_FIELDS = "id,service,status"
# $select keeps the payload small: without it every issue carries its full
# message history ("posts"), about 90 KB for ten open issues.
_ISSUE_FIELDS = ",".join(
    (
        "id",
        "title",
        "service",
        "feature",
        "featureGroup",
        "classification",
        "status",
        "origin",
        "impactDescription",
        "isResolved",
        "startDateTime",
        "lastModifiedDateTime",
    )
)

# AADSTS error codes returned by the token endpoint, mapped to a reason.
_AUTH_ERROR_REASONS: dict[int, str] = {
    90002: "invalid_tenant",  # tenant not found
    900023: "invalid_tenant",  # tenant identifier is not a GUID or domain
    700016: "invalid_client_id",  # application not found in the tenant
    7000215: "invalid_client_secret",  # wrong secret (often the secret ID)
    7000222: "expired_client_secret",
    53003: "blocked",  # blocked by Conditional Access
}


class M365Error(Exception):
    """Base error for the service health client."""


class M365ConnectionError(M365Error):
    """Network error, throttling or Microsoft-side failure: retry later."""


class M365AuthError(M365Error):
    """The credentials were rejected by Entra ID."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        """Store the reason (one of the _AUTH_ERROR_REASONS values or unknown)."""
        super().__init__(message or reason)
        self.reason = reason


class M365PermissionError(M365Error):
    """The app lacks ServiceHealth.Read.All or its admin consent."""


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _decode_claims(token: str) -> dict[str, Any]:
    """Read the JWT payload (no signature check: only used for tid/roles)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}
    return claims if isinstance(claims, dict) else {}


@dataclass(frozen=True, slots=True)
class ServiceHealth:
    """Health overview of one Microsoft 365 service."""

    id: str
    name: str
    status: str

    @classmethod
    def from_graph(cls, item: dict[str, Any]) -> ServiceHealth:
        """Build from a Graph serviceHealth object."""
        return cls(
            id=item["id"],
            name=(item.get("service") or item["id"]).strip(),
            status=item.get("status") or "unknown",
        )


@dataclass(frozen=True, slots=True)
class ServiceIssue:
    """An incident or advisory published by Microsoft."""

    id: str
    title: str
    service: str
    classification: str
    status: str
    feature: str | None
    feature_group: str | None
    impact_description: str | None
    origin: str | None
    start: datetime | None
    last_modified: datetime | None

    @property
    def is_incident(self) -> bool:
        """Return True for incidents (anything else counts as an advisory)."""
        return self.classification == "incident"

    @classmethod
    def from_graph(cls, item: dict[str, Any]) -> ServiceIssue:
        """Build from a Graph serviceHealthIssue object."""
        return cls(
            id=item["id"],
            title=item.get("title") or item["id"],
            service=(item.get("service") or "").strip(),
            classification=item.get("classification") or "advisory",
            status=item.get("status") or "unknown",
            feature=item.get("feature"),
            feature_group=item.get("featureGroup"),
            impact_description=item.get("impactDescription"),
            origin=item.get("origin"),
            start=_parse_datetime(item.get("startDateTime")),
            last_modified=_parse_datetime(item.get("lastModifiedDateTime")),
        )


class M365ServiceHealthClient:
    """Read-only client for /admin/serviceAnnouncement."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._tenant_id = tenant_id.strip()
        self._client_id = client_id.strip()
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expires = 0.0
        self._token_lock = asyncio.Lock()
        self.tenant_guid: str | None = None

    async def async_get_token(self, *, force_refresh: bool = False) -> str:
        """Return a valid access token, requesting a new one when needed."""
        async with self._token_lock:
            if (
                not force_refresh
                and self._token is not None
                and time.monotonic() < self._token_expires
            ):
                return self._token
            token, expires_in = await self._async_request_token()
            claims = _decode_claims(token)
            # The tenant may have been entered as a domain name: the token
            # tells us its GUID, which is what we use as unique ID.
            self.tenant_guid = claims.get("tid") or self.tenant_guid
            roles = claims.get("roles")
            if claims and REQUIRED_ROLE not in (roles or []):
                raise M365PermissionError(
                    f"The access token has no {REQUIRED_ROLE} role: add this "
                    "application permission and grant admin consent"
                )
            self._token = token
            self._token_expires = time.monotonic() + max(
                expires_in - TOKEN_EXPIRY_MARGIN, 60
            )
            return token

    async def _async_request_token(self) -> tuple[str, int]:
        url = TOKEN_URL.format(tenant=quote(self._tenant_id, safe=""))
        form = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "scope": GRAPH_SCOPE,
        }
        try:
            async with self._session.post(
                url, data=form, timeout=REQUEST_TIMEOUT
            ) as resp:
                status = resp.status
                try:
                    body = await resp.json(content_type=None)
                except ValueError:
                    body = None
        except (aiohttp.ClientError, TimeoutError) as err:
            raise M365ConnectionError(
                f"Cannot reach the Microsoft sign-in service: {err}"
            ) from err

        if not isinstance(body, dict):
            body = {}
        if status == 200 and isinstance(body.get("access_token"), str):
            try:
                expires_in = int(body.get("expires_in", 3600))
            except (TypeError, ValueError):
                expires_in = 3600
            return body["access_token"], expires_in
        if status == 429 or status >= 500:
            raise M365ConnectionError(f"Microsoft sign-in returned HTTP {status}")

        codes = [c for c in body.get("error_codes") or [] if isinstance(c, int)]
        reason = next(
            (_AUTH_ERROR_REASONS[c] for c in codes if c in _AUTH_ERROR_REASONS),
            "unknown",
        )
        # Only the error name and AADSTS code: the description may echo IDs.
        raise M365AuthError(
            reason,
            f"Token request rejected: {body.get('error', f'HTTP {status}')}"
            f" (AADSTS{codes[0] if codes else '?'})",
        )

    async def _async_get(self, url: str) -> dict[str, Any]:
        for attempt in range(2):
            token = await self.async_get_token(force_refresh=attempt > 0)
            try:
                async with self._session.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                    timeout=REQUEST_TIMEOUT,
                ) as resp:
                    status = resp.status
                    if status == 200:
                        body = await resp.json(content_type=None)
                        if not isinstance(body, dict):
                            raise M365ConnectionError("Unexpected Graph response")
                        return body
                    retry_after = resp.headers.get("Retry-After")
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                raise M365ConnectionError(
                    f"Error while calling Microsoft Graph: {err}"
                ) from err

            if status == 401 and attempt == 0:
                continue  # token revoked or expired early: get a fresh one
            if status == 401:
                raise M365AuthError("unknown", "Microsoft Graph rejected the token")
            if status == 403:
                raise M365PermissionError(
                    f"Microsoft Graph refused access: check the {REQUIRED_ROLE} "
                    "application permission and its admin consent"
                )
            if status == 429 or status >= 500:
                hint = f", retry after {retry_after}s" if retry_after else ""
                raise M365ConnectionError(
                    f"Microsoft Graph returned HTTP {status}{hint}"
                )
            raise M365Error(f"Unexpected HTTP {status} from Microsoft Graph")
        raise M365Error("Unreachable")  # pragma: no cover

    async def _async_get_collection(self, url: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = url
        for _ in range(MAX_PAGES):
            if next_url is None:
                break
            body = await self._async_get(next_url)
            items.extend(i for i in body.get("value") or [] if isinstance(i, dict))
            next_url = body.get("@odata.nextLink")
            if next_url is not None and not next_url.startswith(GRAPH_ORIGIN):
                # Never send the token anywhere but Microsoft Graph.
                _LOGGER.warning("Ignoring unexpected pagination link")
                break
        return items

    async def async_get_health_overviews(self) -> list[ServiceHealth]:
        """Return the health overview of every service of the tenant."""
        items = await self._async_get_collection(
            f"{GRAPH_BASE}/healthOverviews?$select={_OVERVIEW_FIELDS}"
        )
        return [ServiceHealth.from_graph(i) for i in items if i.get("id")]

    async def async_get_open_issues(self) -> list[ServiceIssue]:
        """Return the incidents and advisories that are not resolved yet."""
        items = await self._async_get_collection(
            f"{GRAPH_BASE}/issues?$filter=isResolved%20eq%20false"
            f"&$select={_ISSUE_FIELDS}"
        )
        return [ServiceIssue.from_graph(i) for i in items if i.get("id")]
