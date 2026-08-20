"""Panel UI -- connections list/connect form + flows list.

SKETCH, following the exact conventions proven in n8n Connector's / Make.com
Connector's panels.py:
  ui.Stack (v, gap=4)
    ui.Header
    ui.Card (connect form) -- ONE genuine widget, shown when nothing is
      connected yet, or appended below the connections list so the user can
      always add another environment (Dev/Test/Prod)
      ui.Form(action=connect_power_automate, submit_label="Verify and connect")
        ui.Input(tenant_id), ui.Input(client_id), ui.Password(client_secret),
        ui.Input(environment_url), ui.Input(environment_id)
      ui.Button("How do I set this up?") -> center_overlay help dialog
    [connected] plain ui.Stack+ui.Divider list of connected environments
      (NOT wrapped in individual Cards -- Vlad's standing sidebar rule,
      confirmed applied here from n8n/Make.com Connector's panels.py)
    ui.Divider() + flows list for the first/only connected environment

WHY A FULL 5-FIELD FORM, NOT A TOKEN LIKE n8n/Make.com/Slack.

Power Automate has no single bearer token to paste -- see app.py's module
docstring for the full reasoning (Dataverse per-environment OAuth scope,
Global Discovery Service retirement, Azure AD App Registration + Application
User being Microsoft's own recommended pattern here). The form therefore
asks for all five fields the Azure AD App Registration + Dataverse
environment require, with clear placeholders and a help dialog explaining
where to find each one -- the same shape as n8n's base_url+api_key form,
just with more fields because the underlying auth model has more parts.

PRE-PANEL CHECKLIST pass (same checklist as n8n/Make.com Connector):
  - ui.Input / ui.Password: no label=, no type=                     OK
  - ui.Card: content=, not children=                                OK
  - ui.Dialog on a center_overlay panel, opened via ui.Call("__panel__...")
    (same proven pattern as n8n_connect_help / make_connect_help)    OK
  - ui.Form does not submit pre-set value= fields -- every field is
    user-typed, no hidden-context workaround needed                  OK
  - Connections/flows lists are plain ui.Stack+ui.Divider, NOT wrapped
    in per-item ui.Card (Vlad's standing sidebar rule)                OK
"""
from __future__ import annotations

from imperal_sdk import ui

import power_automate_client as pac
from app import ext
import handlers as h


def _connection_row(c: dict) -> ui.UINode:
    label = c.get("label") or c.get("environment_url", "")
    return ui.Stack(direction="v", gap=1, children=[
        ui.Text(label, variant="body"),
        ui.Text(c.get("environment_url", ""), variant="caption"),
    ])


def _connections_section(connections: list[dict]) -> ui.UINode:
    if not connections:
        return ui.Text("No environments connected yet.", variant="caption")
    children: list[ui.UINode] = []
    for i, c in enumerate(connections):
        if i > 0:
            children.append(ui.Divider())
        children.append(_connection_row(c))
    return ui.Stack(direction="v", gap=2, children=children)


def _flow_row(f) -> ui.UINode:
    """One flow row -- plain content, no Card wrapper, no padding/border,
    per Vlad's standing sidebar rule (same as n8n Connector's _workflow_row)."""
    subtitle = f.state.capitalize() + (f" · {f.category}" if f.category else "")
    return ui.Stack(direction="v", gap=1, children=[
        ui.Text(f.name, variant="body"),
        ui.Text(subtitle, variant="caption"),
    ])


def _flows_section(flows: list) -> ui.UINode:
    if not flows:
        return ui.Text("No flows yet.", variant="caption")
    children: list[ui.UINode] = []
    for i, f in enumerate(flows):
        if i > 0:
            children.append(ui.Divider())
        children.append(_flow_row(f))
    return ui.Stack(direction="v", gap=2, children=children)


def _connect_card() -> ui.UINode:
    return ui.Card(
        title="Connect Power Automate",
        subtitle="Bring your own Power Platform environment",
        content=ui.Stack(direction="v", gap=3, children=[
            ui.Text(
                "This connects to your Dataverse environment via your own "
                "Azure AD App Registration -- Imperal never sees or stores "
                "your Microsoft account password. Only manages Solution-aware "
                "cloud flows; personal unattached \"My Flows\" aren't reachable "
                "through Microsoft's own API.",
                variant="caption",
            ),
            ui.Stack(direction="h", gap=2, align="center", children=[
                ui.Button("How do I set this up?", variant="ghost", size="sm",
                          icon="HelpCircle",
                          on_click=ui.Call("__panel__power_automate_connect_help")),
            ]),
            ui.Form(
                action="connect_power_automate",
                submit_label="Verify and connect",
                children=[
                    ui.Input(param_name="tenant_id",
                              placeholder="Azure AD tenant ID"),
                    ui.Input(param_name="client_id",
                              placeholder="App Registration client ID"),
                    ui.Password(param_name="client_secret",
                                 placeholder="App Registration client secret"),
                    ui.Input(param_name="environment_url",
                              placeholder="https://org12345.crm.dynamics.com"),
                    ui.Input(param_name="environment_id",
                              placeholder="Environment ID (GUID)"),
                    ui.Input(param_name="label",
                              placeholder="Label (optional, e.g. Production)"),
                ],
            ),
        ]),
    )


@ext.panel("power_automate_connect", slot="left", title="Power Automate", icon="🔗",
           default_width=320, min_width=260, max_width=420)
async def power_automate_connect_panel(ctx, **kwargs) -> object:
    connections = await h._load_connections(ctx)
    connected = bool(connections)

    header = ui.Header(text="Power Automate", level=2,
                        subtitle="Manage your Power Platform cloud flows from Imperal")

    if not connected:
        return ui.Stack(direction="v", gap=4, children=[
            header,
            _connect_card(),
            ui.Alert(
                title="Not connected yet",
                message="Connect a Power Platform environment to see and manage its cloud flows.",
                type="info",
            ),
        ])

    flows: list = []
    first = connections[0]
    try:
        tok = await pac.get_access_token(
            ctx, first["tenant_id"], first["client_id"], first["client_secret"],
            first["environment_url"],
        )
        if tok.get("ok"):
            rows, _ = await pac.list_flows(ctx, tok["access_token"], first["environment_url"], top=50)
            flows = [h._flow_entity(w) for w in rows]
    except pac.ClientFail:
        flows = []

    return ui.Stack(direction="v", gap=4, children=[
        header,
        ui.Text("Connected environments", variant="subtitle"),
        _connections_section(connections),
        ui.Divider(),
        _connect_card(),
        ui.Divider(),
        ui.Text(f"Flows -- {first.get('label') or first.get('environment_url', '')}", variant="subtitle"),
        _flows_section(flows),
    ])


@ext.panel("power_automate_connect_help", slot="center",
           title="How to connect Power Automate", center_overlay=True)
async def power_automate_connect_help(ctx, **kwargs) -> object:
    content = ui.Stack(direction="v", gap=3, children=[
        ui.Text("1. In the Azure Portal, open Microsoft Entra ID > App registrations > New registration."),
        ui.Text("2. Copy the Application (client) ID and Directory (tenant) ID from its Overview page."),
        ui.Text("3. Under Certificates & secrets, create a new client secret and copy its VALUE (shown only once)."),
        ui.Text("4. In the Power Platform admin center, open your environment > Settings > Users + permissions > Application users, and add your App Registration as an Application User."),
        ui.Text("5. Give that Application User a security role with workflow read/write privileges."),
        ui.Text("6. Copy your environment's URL (e.g. https://org12345.crm.dynamics.com) and its Environment ID (a GUID) from the environment's details page."),
        ui.Divider(),
        ui.Alert(
            title="Solution-aware flows only",
            message=(
                "This only manages cloud flows that have been added to a "
                "Dataverse Solution (the \"Modern Flow\" category). Personal, "
                "unattached \"My Flows\" cannot be reached via Microsoft's own "
                "Web API -- that limitation is Microsoft's, not ours."
            ),
            type="warning",
        ),
        ui.Divider(),
        ui.Link(
            label="Open Microsoft's official App Registration guide",
            href="https://learn.microsoft.com/en-us/power-apps/developer/data-platform/walkthrough-register-app-azure-active-directory",
        ),
    ])
    return ui.Dialog(
        title="How to connect Power Automate",
        content=content,
        confirm_label="",
        cancel_label="Close",
    )
