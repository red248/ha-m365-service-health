"""Config flow for M365 Service Health."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_NAME,
    CONF_SCAN_INTERVAL,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    M365AuthError,
    M365ConnectionError,
    M365Error,
    M365PermissionError,
    M365ServiceHealthClient,
)
from .const import (
    CONF_EXCLUDED_SERVICES,
    CONF_TENANT_ID,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import M365ConfigEntry

_LOGGER = logging.getLogger(__name__)

_GUID_RE = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$", re.IGNORECASE)
_AUTH_ERRORS = {
    "invalid_tenant",
    "invalid_client_id",
    "invalid_client_secret",
    "expired_client_secret",
    "blocked",
}

_TEXT = TextSelector()
_SECRET = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default=DEFAULT_NAME): _TEXT,
        vol.Required(CONF_TENANT_ID): _TEXT,
        vol.Required(CONF_CLIENT_ID): _TEXT,
        vol.Required(CONF_CLIENT_SECRET): _SECRET,
    }
)
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_CLIENT_SECRET): _SECRET})
RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CLIENT_ID): _TEXT,
        vol.Required(CONF_CLIENT_SECRET): _SECRET,
    }
)


async def _async_validate(
    hass: HomeAssistant, tenant_id: str, client_id: str, client_secret: str
) -> str:
    """Check the credentials end to end and return the tenant GUID."""
    client = M365ServiceHealthClient(
        async_get_clientsession(hass), tenant_id, client_id, client_secret
    )
    await client.async_get_health_overviews()
    return (client.tenant_guid or tenant_id).lower()


def _error_key(err: M365Error) -> str:
    if isinstance(err, M365AuthError):
        return err.reason if err.reason in _AUTH_ERRORS else "invalid_auth"
    if isinstance(err, M365PermissionError):
        return "missing_permission"
    if isinstance(err, M365ConnectionError):
        return "cannot_connect"
    return "unknown"


class M365ServiceHealthConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1
    MINOR_VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: M365ConfigEntry,
    ) -> M365ServiceHealthOptionsFlow:
        """Return the options flow."""
        return M365ServiceHealthOptionsFlow()

    async def _async_try_validate(
        self,
        errors: dict[str, str],
        tenant_id: str,
        client_id: str,
        client_secret: str,
    ) -> str | None:
        try:
            return await _async_validate(self.hass, tenant_id, client_id, client_secret)
        except M365Error as err:
            _LOGGER.debug("Validation failed: %s", err)
            errors["base"] = _error_key(err)
        except Exception:
            _LOGGER.exception("Unexpected error while validating credentials")
            errors["base"] = "unknown"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the app registration details."""
        errors: dict[str, str] = {}
        if user_input is not None:
            tenant_id = user_input[CONF_TENANT_ID].strip()
            client_id = user_input[CONF_CLIENT_ID].strip()
            client_secret = user_input[CONF_CLIENT_SECRET].strip()
            if not (_GUID_RE.match(tenant_id) or _DOMAIN_RE.match(tenant_id)):
                errors[CONF_TENANT_ID] = "invalid_tenant_format"
            elif not _GUID_RE.match(client_id):
                errors[CONF_CLIENT_ID] = "invalid_client_id_format"
            elif tenant_guid := await self._async_try_validate(
                errors, tenant_id, client_id, client_secret
            ):
                await self.async_set_unique_id(tenant_guid)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME].strip() or DEFAULT_NAME,
                    data={
                        CONF_TENANT_ID: tenant_guid,
                        CONF_CLIENT_ID: client_id,
                        CONF_CLIENT_SECRET: client_secret,
                    },
                    options={
                        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                        CONF_EXCLUDED_SERVICES: [],
                    },
                )

        suggested = {
            k: v for k, v in (user_input or {}).items() if k != CONF_CLIENT_SECRET
        }
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, suggested),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication (expired or revoked secret)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new client secret."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            client_secret = user_input[CONF_CLIENT_SECRET].strip()
            if tenant_guid := await self._async_try_validate(
                errors,
                entry.data[CONF_TENANT_ID],
                entry.data[CONF_CLIENT_ID],
                client_secret,
            ):
                await self.async_set_unique_id(tenant_guid)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_CLIENT_SECRET: client_secret}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the app registration (client ID and secret) of the tenant."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            client_id = user_input[CONF_CLIENT_ID].strip()
            client_secret = user_input[CONF_CLIENT_SECRET].strip()
            if not _GUID_RE.match(client_id):
                errors[CONF_CLIENT_ID] = "invalid_client_id_format"
            elif tenant_guid := await self._async_try_validate(
                errors, entry.data[CONF_TENANT_ID], client_id, client_secret
            ):
                await self.async_set_unique_id(tenant_guid)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_CLIENT_ID: client_id,
                        CONF_CLIENT_SECRET: client_secret,
                    },
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                RECONFIGURE_SCHEMA,
                {CONF_CLIENT_ID: (user_input or entry.data)[CONF_CLIENT_ID]},
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )


class M365ServiceHealthOptionsFlow(OptionsFlowWithReload):
    """Polling interval and services to ignore."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_EXCLUDED_SERVICES: sorted(
                        user_input.get(CONF_EXCLUDED_SERVICES, [])
                    ),
                }
            )

        options = self.config_entry.options
        excluded: list[str] = list(options.get(CONF_EXCLUDED_SERVICES, []))
        labels = {sid: sid for sid in excluded}
        if self.config_entry.state is ConfigEntryState.LOADED:
            labels.update(
                {
                    sid: service.name
                    for sid, service in self.config_entry.runtime_data.data.all_services.items()
                }
            )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Optional(CONF_EXCLUDED_SERVICES, default=excluded): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=sid, label=name)
                            for sid, name in sorted(
                                labels.items(), key=lambda kv: kv[1].casefold()
                            )
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
