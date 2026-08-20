"""Extension declaration, secrets, lifecycle hooks.

WHY BYOK (bring-your-own-key), same reasoning as Make.com Connector / n8n
Connector / DataForSEO Connector. Power Automate lives inside the USER'S
own Microsoft 365 / Power Platform tenant -- Imperal cannot and should not
broker access to someone else's Azure AD tenant centrally.

WHY AZURE AD APP REGISTRATION (client_id + client_secret + tenant_id) +
DATAVERSE ENVIRONMENT URL, NOT THE PLATFORM'S GENERIC `ext.oauth("microsoft")`.

The SDK already has a static "microsoft" entry in its OAuth authorize-URL
table (login.microsoftonline.com/common/oauth2/v2.0/authorize), but that
mechanism builds a FIXED scope list declared once at `ext.oauth(provider,
scopes=[...])` time. Dataverse requires a scope shaped like
`https://org12345.crm.dynamics.com/.default` -- tied to the URL of the
user's SPECIFIC environment, which is not known in advance. This is the
same chicken-and-egg n8n Connector already solved by asking for `base_url`
directly instead of guessing it (see n8n PREPARATION.md section 4).

Global Discovery Service used to solve exactly this (enumerate every org a
user can reach after one generic Microsoft login) -- but Microsoft has it
in retirement since 2026-06-19 (MC1253577), replaced by the newer Power
Platform "List Environments" API. Building fresh architecture on a
retiring service would be a mistake -- confirmed during Discovery
(CONNECTOR_DISCOVERY.md).

Microsoft's own recommended pattern for exactly this kind of server-to-
server / unattended integration is an Azure AD App Registration configured
as a Dataverse "Application User" with an explicit security role -- the
official non-interactive alternative to delegated user OAuth. That is what
this connector asks the user for: their own `tenant_id` (Azure AD tenant),
`client_id` + `client_secret` (App Registration credentials), and
`environment_url` (their Dataverse org URL, e.g.
https://org12345.crm.dynamics.com) -- four fields, all user-supplied,
nothing hosted by Imperal. Confirmed 2026-08-20, PREPARATION.md section 3.

WHY `write_mode="both"`, SAME REASONING AS n8n/Make.com CONNECTOR.

Declaring `write_mode="user"` would mean only the platform's generic
Secrets screen could write these -- leaving a first-time user with no
in-app screen explaining what an Azure AD App Registration even is or how
to create one. `"both"` keeps the generic Secrets screen as a fallback
while letting `connect_power_automate` be the friendly guided path.

WHY SCOPE IS PER-ACCOUNT, NOT APP-LEVEL, SAME AS n8n/Make.com CONNECTOR.

Each user connects their OWN tenant/environment -- these are not
developer-owned app credentials (unlike Google Drive Connector's OAuth
client id/secret, which ARE app-scope because that's a single Google Cloud
project this app's developer owns). Every field here is end-user data, so
the connections secret is declared per-account (default scope), not
`scope="app"`.

WHY ONE SECRET HOLDING A JSON ARRAY, NOT FOUR FLAT SECRETS FOR "the"
ENVIRONMENT (multi-environment support).

Unlike n8n (one self-hosted instance per user) or Slack (workspaces differ
only by a single token), Power Platform environments are explicitly
multi-instance by design -- Dev/Test/UAT/Prod is the normal, expected setup
for any real Power Platform user, not an edge case. `ctx.secrets` only
supports a fixed, manifest-declared set of NAMES -- there is no
"one secret per connection_id" primitive (confirmed reading
imperal_sdk/secrets/client.py during Fase 2). Slack Connector already
solved the structurally similar problem (multiple workspaces) by storing
ALL of them serialised inside ONE declared secret, one line per workspace.
This connector follows the same precedent, adapted for records with five
fields instead of one token: `power_automate_connections` holds a JSON
array of `{id, label, tenant_id, client_id, client_secret, environment_url,
environment_id}` objects. `schemas.py`'s `connection_id` parameter on every
tool call addresses one specific entry in that array -- see handlers.py's
`_load_connections`/`_save_connections` helpers.
"""
from __future__ import annotations

from imperal_sdk import ChatExtension, Extension

ext = Extension(
    "power-automate-connector",
    version="0.1.0",
    display_name="Power Automate",
    description=(
        "Connect your own Power Platform environment (Dataverse) to see and "
        "manage your Power Automate cloud flows from Imperal -- list flows "
        "with their status, create/update/delete them, turn them on or off, "
        "inspect and cancel/resubmit their runs, and run bulk operations "
        "across many flows at once. Uses your own Azure AD App Registration "
        "(Application User) -- nothing is hosted or proxied by Imperal beyond "
        "the request itself. Note: this connects to flows added to a "
        "Dataverse Solution; personal \"My Flows\" not added to a Solution are "
        "not reachable by Microsoft's own management API and are out of "
        "scope here."
    ),
    icon="icon.svg",
    capabilities=[
        "power-automate:read",
        "power-automate:write",
    ],
    actions_explicit=True,
    system=False,
)

chat = ChatExtension(
    ext,
    tool_name="power_automate",
    description=(
        "Power Automate Connector -- connect your Dataverse environment via "
        "your own Azure AD App Registration, then list/get/create/update/"
        "delete cloud flows, turn them on/off, inspect and cancel/resubmit "
        "flow runs, and run bulk operations across many flows at once."
    ),
)

ext.secret(
    "power_automate_connections",
    (
        "Your connected Power Platform environments -- stored as a JSON "
        "array, one entry per environment (Dev/Test/Prod etc), each with its "
        "own Azure AD App Registration (tenant_id, client_id, client_secret) "
        "and Dataverse environment_url/environment_id. Managed through "
        "connect_power_automate / disconnect_power_automate -- you should "
        "not need to edit this directly."
    ),
    required=True,
    write_mode="both",
    max_bytes=65536,
    rotation_hint_days=180,
)(lambda: None)


@ext.health_check
async def health_check(ctx) -> dict:
    """Fast configuration health; no third-party call -- just confirms at
    least one environment connection is stored, same shape as n8n
    Connector's health_check."""
    import json as _json
    raw = await ctx.secrets.get("power_automate_connections")
    try:
        count = len(_json.loads(raw)) if raw else 0
    except Exception:
        count = 0
    return {
        "healthy": True,
        "detail": (
            f"{count} Power Platform environment(s) connected." if count
            else "Not connected yet -- run connect_power_automate."
        ),
    }
