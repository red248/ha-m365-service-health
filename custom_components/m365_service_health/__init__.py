"""The M365 Service Health integration."""

from __future__ import annotations

from homeassistant.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import M365ServiceHealthClient
from .const import CONF_EXCLUDED_SERVICES, CONF_TENANT_ID
from .coordinator import M365ConfigEntry, M365ServiceHealthCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: M365ConfigEntry) -> bool:
    """Set up M365 Service Health from a config entry."""
    client = M365ServiceHealthClient(
        async_get_clientsession(hass),
        entry.data[CONF_TENANT_ID],
        entry.data[CONF_CLIENT_ID],
        entry.data[CONF_CLIENT_SECRET],
    )
    coordinator = M365ServiceHealthCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _async_remove_excluded_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: M365ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


@callback
def _async_remove_excluded_entities(
    hass: HomeAssistant, entry: M365ConfigEntry
) -> None:
    """Delete the entities of services excluded in the options."""
    excluded = set(entry.options.get(CONF_EXCLUDED_SERVICES, []))
    if not excluded:
        return
    registry = er.async_get(hass)
    prefix = f"{entry.unique_id}_service_"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity.unique_id.startswith(prefix)
            and entity.unique_id.removeprefix(prefix) in excluded
        ):
            registry.async_remove(entity.entity_id)
