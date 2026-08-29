"""Panel UI -- connections list/connect form + flows list.

SIDEBAR CONTENT -- NO CARDS ANYWHERE, updated 2026-08-20 per
~/UI_INTERFACE_STANDARD.md's "left sidebar, no decorated cards" rule.

Every section (connections, connect form, flows) is a plain ui.Stack,
content stacked vertically and left-aligned, sections separated by
ui.Divider() -- no Card border/background/shadow anywhere in this slot.
Disconnect (previously not exposed in the UI at all) now lives in the
"App settings" screen (panels_settings.py). The one secondary "App
settings" button is always the LAST element at the bottom of the sidebar.

WHY A FULL 5-FIELD FORM, NOT A TOKEN LIKE n8n/Make.com/Slack.

Power Automate has no single bearer token to paste -- see app.py's module
docstring for the full reasoning (Dataverse per-environment OAuth scope,
Global Discovery Service retirement, Azure AD App Registration + Application
User being Microsoft's own recommended pattern here). The form therefore
asks for all five fields the Azure AD App Registration + Dataverse
environment require, with clear placeholders and a help dialog explaining
where to find each one -- the same shape as n8n's base_url+api_key form,
just with more fields because the underlying auth model has more parts.
"""
from __future__ import annotations

from imperal_sdk import ui

import power_automate_client as pac
from app import ext
import handlers as h


def _settings_button() -> ui.UINode:
    """The one required secondary entry point into the settings screen --
    always the last element at the bottom of the sidebar."""
    return ui.Button(
        "App settings", variant="secondary", size="sm", full_width=True,
        icon="settings", on_click=ui.Call("__panel__power_automate_settings"),
    )


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


def _connect_section() -> ui.UINode:
    """Plain content, no Card wrapper. Stretched full-width per
    UI_INTERFACE_STANDARD.md (2026-08-20). No intro heading/description
    text here -- the \"Solution-aware flows only\" limitation and the
    Azure AD explanation live ONLY in power_automate_connect_help's modal
    (button below opens it); repeating them here would duplicate that
    instruction."""
    return ui.Stack(direction="v", gap=3, align="stretch", children=[
        ui.Button("How do I set this up?", variant="ghost", size="sm",
                  icon="HelpCircle",
                  on_click=ui.Call("__panel__power_automate_connect_help")),
        ui.Form(
            action="connect_power_automate",
            submit_label="Verify and connect",
            children=[
                ui.Stack(direction="v", gap=1, children=[
                    ui.Text("Azure AD tenant ID", variant="caption"),
                    ui.Input(param_name="tenant_id", placeholder="Azure AD tenant ID"),
                ]),
                ui.Stack(direction="v", gap=1, children=[
                    ui.Text("App Registration client ID", variant="caption"),
                    ui.Input(param_name="client_id", placeholder="App Registration client ID"),
                ]),
                ui.Stack(direction="v", gap=1, children=[
                    ui.Text("App Registration client secret", variant="caption"),
                    ui.Password(param_name="client_secret",
                                 placeholder="App Registration client secret"),
                ]),
                ui.Stack(direction="v", gap=1, children=[
                    ui.Text("Environment URL", variant="caption"),
                    ui.Input(param_name="environment_url",
                              placeholder="https://org12345.crm.dynamics.com"),
                ]),
                ui.Stack(direction="v", gap=1, children=[
                    ui.Text("Environment ID", variant="caption"),
                    ui.Input(param_name="environment_id", placeholder="Environment ID (GUID)"),
                ]),
                ui.Stack(direction="v", gap=1, children=[
                    ui.Text("Label (optional)", variant="caption"),
                    ui.Input(param_name="label", placeholder="e.g. Production"),
                ]),
            ],
        ),
    ])


@ext.panel("power_automate_connect", slot="left", title="Power Automate", icon="🔗",
           default_width=320, min_width=260, max_width=420)
async def power_automate_connect_panel(ctx, **kwargs) -> object:
    connections = await h._load_connections(ctx)
    connected = bool(connections)

    header = ui.Header(text="Power Automate", level=2,
                        subtitle="Manage your Power Platform cloud flows from Imperal")

    if not connected:
        return ui.Stack(direction="v", gap=4, align="stretch", children=[
            header,
            _connect_section(),
            ui.Divider(),
            _settings_button(),
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

    return ui.Stack(direction="v", gap=4, align="stretch", children=[
        header,
        ui.Text("Connected environments", variant="subtitle"),
        _connections_section(connections),
        ui.Divider(),
        _connect_section(),
        ui.Divider(),
        ui.Text(f"Flows -- {first.get('label') or first.get('environment_url', '')}", variant="subtitle"),
        _flows_section(flows),
        ui.Divider(),
        ui.Button("View flow overview", variant="primary", size="sm", full_width=True,
                  icon="LayoutDashboard", on_click=ui.Call("__panel__power_automate_center")),
        ui.Divider(),
        _settings_button(),
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


@ext.panel("power_automate_center", slot="center", title="Power Automate", icon="🔗", center_overlay=True)
async def power_automate_center_panel(ctx, **kwargs) -> object:
    """Base center panel -- per UI_INTERFACE_STANDARD.md (2026-08-20).
    This app has no list/detail content of its own to show in the center
    by default (everything lives in the sidebar). MUST carry
    center_overlay=True: per docs.imperal.io/en/concepts/panels, a plain
    slot="center" panel is registered but the Panel app never fetches it
    at session-init without that flag -- the center slot stays genuinely
    empty (not a caching issue) until center_overlay=True is set. Text is
    the shared canonical wording -- must stay identical across every app
    in this situation, not app-specific."""
    connections = await h._load_connections(ctx)
    if not connections:
        return ui.Empty(message="Connect a Power Platform environment from the sidebar to see it here.", icon="🔗")

    from schemas import ListFlowsParams
    conn_id = connections[0].get("id", "")
    result = await h.list_flows(ctx, ListFlowsParams(connection_id=conn_id))
    body: list[ui.UINode] = [ui.Text("Flow overview", variant="subtitle")]
    if result.success and result.data and result.data.items:
        items = result.data.items
        active = sum(1 for f in items if f.state == "activated")
        suspended = sum(1 for f in items if f.state == "suspended")
        body.append(ui.Stats(children=[
            ui.Stat(label="Total", value=str(len(items))),
            ui.Stat(label="Activated", value=str(active)),
            ui.Stat(label="Suspended", value=str(suspended)),
        ]))
        for f in items[:15]:
            color = {"activated": "green", "suspended": "red"}.get(f.state, "gray")
            body.append(ui.Stack(direction="h", gap=2, align="center", children=[
                ui.Badge(label=(f.state or "draft").upper(), color=color),
                ui.Text(f.title, variant="body"),
            ]))
    else:
        body.append(ui.Text("No flows found, or not yet connected.", variant="caption"))

    return ui.Stack(direction="v", gap=3, align="stretch", children=body)
