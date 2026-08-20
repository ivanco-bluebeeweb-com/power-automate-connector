"""The single 'App settings' screen (center slot) -- connection management
(disconnect per environment) for Power Automate Connector. Split out of
panels.py per the same convention as Aidentika's / n8n's / Make.com
Connector's panels_settings.py.

Per ~/UI_INTERFACE_STANDARD.md (updated 2026-08-20): the left sidebar no
longer wraps the connect form in a Card, and disconnect (previously not
exposed anywhere in the UI at all) now lives here, one row per connected
environment. The one secondary "App settings" button sits LAST at the
bottom of the sidebar.
"""
from __future__ import annotations

from imperal_sdk import ui

from app import ext
import handlers as h


def _connection_row(c: dict) -> ui.UINode:
    label = c.get("label") or c.get("environment_url", "")
    return ui.Stack(direction="v", gap=1, align="start", children=[
        ui.Text(label, variant="body"),
        ui.Text(c.get("environment_url", ""), variant="caption"),
        ui.Button(
            "Disconnect", variant="danger", size="sm",
            on_click=ui.Call("disconnect_power_automate", {"connection_id": c.get("id")}),
        ),
    ])


def _connections_section(connections: list[dict]) -> ui.UINode:
    if not connections:
        return ui.Stack(direction="v", gap=1, children=[
            ui.Text("Connections", variant="heading"),
            ui.Text("No environments connected yet.", variant="caption"),
        ])
    children: list[ui.UINode] = [ui.Text("Connections", variant="heading")]
    for i, c in enumerate(connections):
        if i > 0:
            children.append(ui.Divider())
        children.append(_connection_row(c))
    return ui.Stack(direction="v", gap=2, align="start", children=children)


@ext.panel("power_automate_settings", slot="center", title="App settings", icon="⚙️",
           center_overlay=True)
async def power_automate_settings_panel(ctx, **kwargs) -> object:
    connections = await h._load_connections(ctx)
    return ui.Stack(direction="v", gap=4, children=[
        ui.Header(text="App settings", level=2,
                  subtitle="Manage your connected Power Platform environments"),
        _connections_section(connections),
    ])
