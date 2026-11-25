"""Constants for the M365 Service Health integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "m365_service_health"

CONF_TENANT_ID: Final = "tenant_id"
CONF_EXCLUDED_SERVICES: Final = "excluded_services"

DEFAULT_NAME: Final = "Microsoft 365"
DEFAULT_SCAN_INTERVAL: Final = 5  # minutes
MIN_SCAN_INTERVAL: Final = 2
MAX_SCAN_INTERVAL: Final = 60

# Fired on the event bus when an open issue appears, changes or is resolved.
EVENT_ISSUE: Final = f"{DOMAIN}_issue"

# Simplified per-service state, computed from the *open* issues.
# Microsoft's own overview status is unreliable on its own: it can report
# "serviceDegradation" for advisories only, or for services whose issues are
# all resolved. It is still exposed as the `microsoft_status` attribute.
STATE_OPERATIONAL: Final = "operational"
STATE_ADVISORY: Final = "advisory"
STATE_INCIDENT: Final = "incident"
SERVICE_STATES: Final = [STATE_OPERATIONAL, STATE_ADVISORY, STATE_INCIDENT]

ADMIN_CENTER_URL: Final = "https://admin.microsoft.com/Adminportal/Home#/servicehealth"
ISSUE_URL: Final = ADMIN_CENTER_URL + "/:/alerts/{issue_id}"

# Keep state attributes small (they are also excluded from the recorder).
MAX_ATTRIBUTE_ISSUES: Final = 25
