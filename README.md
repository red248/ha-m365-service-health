# M365 Service Health for Home Assistant

[![Validate](https://github.com/red248/ha-m365-service-health/actions/workflows/validate.yml/badge.svg)](https://github.com/red248/ha-m365-service-health/actions/workflows/validate.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)

*[Version française](README.fr.md)*

Monitor the **Microsoft 365 service health of your tenant** (Exchange Online, Teams,
SharePoint, Intune…) in Home Assistant, using the Microsoft Graph
[service communications API](https://learn.microsoft.com/graph/api/resources/service-communications-api-overview).

- One sensor per service: `operational`, `advisory` or `incident`
- Counters of open incidents and advisories, and a global "incident" problem sensor
- An event for every new, updated or resolved issue, ready for notifications
  (a [blueprint](#notifications) is included)
- UI configuration, re-authentication when the client secret expires, diagnostics
- No extra Python dependency, read-only access

> This is a community project. It is not affiliated with or endorsed by Microsoft.

## Why not just Microsoft's status?

Microsoft's overview status is often misleading on its own: a service is reported as
`serviceDegradation` as soon as a minor *advisory* is open, and sometimes even when
all its issues are resolved. This integration computes the state from the issues that
are **still open**:

| State | Meaning |
|---|---|
| `operational` | No open issue for this service |
| `advisory` | Only advisories (limited impact, often a few users or a feature) |
| `incident` | At least one incident (noticeable user impact) |

Microsoft's raw status stays available in the `microsoft_status` attribute.

## Prerequisites: an Entra ID app registration

You need an administrator able to grant admin consent (for example *Privileged Role
Administrator* or *Global Administrator*) for steps 2 and 3.

1. In the [Microsoft Entra admin center](https://entra.microsoft.com), go to
   **Applications → App registrations → New registration**. Give it a name
   (for example `HomeAssistant-M365-ServiceHealth`), keep *Single tenant*, no redirect URI.
2. **API permissions → Add a permission → Microsoft Graph → Application permissions**,
   select **`ServiceHealth.Read.All`**, then **Add permissions**.
   It must be an *Application* permission, not a *Delegated* one.
3. Click **Grant admin consent for &lt;your tenant&gt;**: the status must turn green.
4. **Certificates & secrets → New client secret**. Copy the secret **Value** right away
   (it is shown only once; the *Secret ID* is not what you need). Note the expiry date.
5. On the **Overview** page, copy the **Application (client) ID** and the
   **Directory (tenant) ID**.

If several people use the integration, create **one secret per person** in the same app
registration: each one can be revoked without affecting the others.

## Installation

### HACS (recommended)

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/red248/ha-m365-service-health` with the type **Integration**.
3. Search for **M365 Service Health**, download it, then restart Home Assistant.

### Manual

Copy `custom_components/m365_service_health` into the `custom_components` folder of
your Home Assistant configuration, then restart Home Assistant.

## Configuration

**Settings → Devices & services → Add integration → M365 Service Health**, then enter:

| Field | Value |
|---|---|
| Name | Name of the device, `Microsoft 365` by default (useful with several tenants) |
| Tenant ID | Directory (tenant) ID, or a domain such as `contoso.onmicrosoft.com` |
| Application (client) ID | From the Overview page of the app registration |
| Client secret | The secret **Value** |

Options (**Configure** button): polling interval (5 minutes by default, 2 to 60) and
services to ignore. Services that Microsoft adds later are monitored automatically.

When the secret expires, Home Assistant shows a **Reconfigure** notification: create a
new secret in Entra ID and enter it there. To use another app registration, use
**Reconfigure** in the integration menu.

## Entities

With the default name `Microsoft 365`:

| Entity | Description |
|---|---|
| `sensor.microsoft_365_<service>` | One per service, for example `sensor.microsoft_365_exchange_online`: `operational` / `advisory` / `incident`. Attributes: `microsoft_status`, `incidents`, `advisories` (id, title, status, start, link to the admin center) |
| `sensor.microsoft_365_active_incidents` | Number of open incidents. Attribute `issues` |
| `sensor.microsoft_365_active_advisories` | Number of open advisories. Attribute `issues` |
| `binary_sensor.microsoft_365_incident` | `on` while at least one incident is open (device class *problem*) |

Entity IDs follow the language of Home Assistant when the integration is added (in
French: `..._incidents_en_cours` and `..._avis_en_cours`). Only the services you monitor
are counted. The list attributes are not stored in the recorder, to keep your database
small.

The `url` links open the issue in the Microsoft 365 admin center: they need an account with an
admin role that can read service health (for example *Global Reader* or *Service Support
Administrator*). A regular user account is redirected or refused.

## Notifications

The integration fires the `m365_service_health_issue` event when an issue is opened
(`new`), changes (`updated`, for example an advisory escalated to an incident) or
disappears from the open issues (`resolved`). No event is fired for the issues already
open when Home Assistant starts.

Event data: `action`, `id`, `service`, `title`, `classification`, `status`, `feature`,
`impact_description`, `start`, `last_modified`, `url`, `tenant`, `config_entry_id`.

### Blueprint

[![Import the blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fred248%2Fha-m365-service-health%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fm365_service_health_notify.yaml)

Choose the issue types and changes you care about, and the actions to run.

### Automation example

```yaml
triggers:
  - trigger: event
    event_type: m365_service_health_issue
    event_data:
      action: new
      classification: incident
actions:
  - action: notify.mobile_app_my_phone
    data:
      title: "Microsoft 365 incident: {{ trigger.event.data.service }}"
      message: "{{ trigger.event.data.title }}"
      data:
        url: "{{ trigger.event.data.url }}"
```

### Dashboard example

```yaml
type: markdown
title: Microsoft 365
content: >-
  {% set issues = state_attr('sensor.microsoft_365_active_incidents', 'issues') or [] %}
  {% for i in issues %}
  - **{{ i.service }}**: [{{ i.title }}]({{ i.url }})
  {% else %}
  No open incident.
  {% endfor %}
```

## Troubleshooting

| Error | What to check |
|---|---|
| Invalid client secret | Copy the secret **Value**, not the *Secret ID* |
| Client secret expired | Create a new secret, then use the re-authentication notification |
| Missing permission | `ServiceHealth.Read.All` must be an *Application* permission with admin consent granted |
| No application with this client ID | Wrong client ID, or app registered in another tenant |
| Blocked by Conditional Access | A Conditional Access policy for workload identities blocks the app |

For more details, enable debug logs from the integration page, or download the
diagnostics (credentials and tenant ID are removed).

## Security

- The app only gets `ServiceHealth.Read.All`: it can read the service health of the
  tenant, nothing else (no mailbox, no user data).
- Home Assistant stores the secret in its configuration (`.storage`), which is also
  included in its backups: protect them accordingly.
- Prefer short-lived secrets and one secret per person.

## Limitations

- The message center (planned changes, `ServiceMessage.Read.All`) is not included yet.
- Microsoft updates its issues every few minutes: polling more often than every
  2 minutes is not useful.

## Development

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest --cov
```

## License

[MIT](LICENSE)
