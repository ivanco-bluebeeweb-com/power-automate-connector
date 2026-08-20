"""Pydantic params models + SDL entity contracts for Power Automate Connector.

All params models are module-scope (V17 federal invariant, same rule as
Make.com Connector / n8n Connector's schemas.py).
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from imperal_sdk import sdl


class NoParams(BaseModel):
    """Explicit empty params model -- V17 disallows untyped handlers."""
    pass


# ──────────────────────────────────────────────────────────────────────────
# Connection
# ──────────────────────────────────────────────────────────────────────────


class ConnectPowerAutomateParams(BaseModel):
    tenant_id: str = Field(
        "",
        description="Azure AD (Microsoft Entra ID) tenant ID that owns your Power Platform environment.",
    )
    client_id: str = Field(
        "",
        description="Application (client) ID of the Azure AD App Registration created for this connection.",
    )
    client_secret: str = Field(
        "",
        description="Client secret value of the Azure AD App Registration. Create it under Certificates & secrets.",
    )
    environment_url: str = Field(
        "",
        description=(
            "Your Dataverse environment URL, e.g. https://org12345.crm.dynamics.com. "
            "Found in the Power Platform admin center under the environment's details."
        ),
    )
    environment_id: str = Field(
        "",
        description=(
            "Your Power Platform environment ID (a GUID), used for the flow-runs REST API. "
            "Found in the Power Platform admin center under the environment's details."
        ),
    )
    label: str = Field("", description="Optional friendly name for this environment connection.")


class ProviderConnection(sdl.Entity):
    id: str = ""
    title: str = ""
    connected: bool = False
    detail: str = ""
    environment_url: str = ""


class ProviderConnectionList(sdl.Entity):
    id: str = "provider_connection_list"
    title: str = ""
    items: list[ProviderConnection] = Field(default_factory=list)


class DisconnectPowerAutomateParams(BaseModel):
    connection_id: str = Field(..., description="Connection id to disconnect, from list_connections.")


# ──────────────────────────────────────────────────────────────────────────
# Flows (Dataverse `workflow` table -- Solution-aware cloud flows only)
# ──────────────────────────────────────────────────────────────────────────


class ListFlowsParams(BaseModel):
    connection_id: str = Field("", description="Which connected environment to use. Omit if only one is connected.")
    state: str | None = Field(
        None, description="Filter by state: 'draft', 'activated', or 'suspended'. Omit for all."
    )
    search: str | None = Field(None, description="Optional name substring filter.")
    limit: int = Field(50, ge=1, le=200, description="Max flows to return.")


class PowerAutomateFlow(sdl.Entity):
    id: str = ""
    title: str = ""
    name: str = ""
    unique_name: str = ""
    state: str = ""
    status: str = ""
    category: str = ""
    created_on: str = ""
    modified_on: str = ""
    solution_aware: bool = True


class PowerAutomateFlowList(sdl.Entity):
    id: str = "power_automate_flow_list"
    title: str = ""
    items: list[PowerAutomateFlow] = Field(default_factory=list)


class GetFlowParams(BaseModel):
    connection_id: str = Field("", description="Which connected environment to use. Omit if only one is connected.")
    workflow_id: str = Field(..., description="Dataverse workflowid (GUID) of the flow.")


class CreateFlowParams(BaseModel):
    connection_id: str = Field("", description="Which connected environment to use. Omit if only one is connected.")
    name: str = Field(..., description="Display name for the new flow.")
    clientdata: str = Field(
        ...,
        description=(
            "String-encoded JSON of the flow definition and its connectionReferences "
            "(the same shape the Power Automate designer produces). Must be a valid, complete flow definition."
        ),
    )
    activate: bool = Field(False, description="Create the flow already turned on (Activated) instead of Draft.")


class UpdateFlowParams(BaseModel):
    connection_id: str = Field("", description="Which connected environment to use. Omit if only one is connected.")
    workflow_id: str = Field(..., description="Dataverse workflowid (GUID) of the flow.")
    name: str | None = Field(None, description="New display name.")
    clientdata: str | None = Field(None, description="New string-encoded JSON flow definition, replacing the current one.")


class DeleteFlowParams(BaseModel):
    connection_id: str = Field("", description="Which connected environment to use. Omit if only one is connected.")
    workflow_id: str = Field(..., description="Dataverse workflowid (GUID) of the flow to permanently delete.")


class SetFlowStateParams(BaseModel):
    connection_id: str = Field("", description="Which connected environment to use. Omit if only one is connected.")
    workflow_id: str = Field(..., description="Dataverse workflowid (GUID) of the flow.")
    state: str = Field(..., description="Target state: 'activated' (turn on) or 'suspended' (turn off).")


class FlowActionResult(sdl.Entity):
    id: str = ""
    title: str = ""
    ok: bool = True
    detail: str = ""


class DeleteResult(sdl.Entity):
    id: str = ""
    title: str = ""
    ok: bool = True


# ──────────────────────────────────────────────────────────────────────────
# Flow runs (Power Platform REST API)
# ──────────────────────────────────────────────────────────────────────────


class ListFlowRunsParams(BaseModel):
    connection_id: str = Field("", description="Which connected environment to use. Omit if only one is connected.")
    workflow_id: str = Field(..., description="Dataverse workflowid (GUID) of the flow whose runs to list.")
    limit: int = Field(50, ge=1, le=200, description="Max runs to return.")


class PowerAutomateFlowRun(sdl.Entity):
    id: str = ""
    title: str = ""
    workflow_id: str = ""
    status: str = ""
    start_time: str = ""
    end_time: str = ""
    error: str = ""


class PowerAutomateFlowRunList(sdl.Entity):
    id: str = "power_automate_flow_run_list"
    title: str = ""
    items: list[PowerAutomateFlowRun] = Field(default_factory=list)


class GetFlowRunParams(BaseModel):
    connection_id: str = Field("", description="Which connected environment to use. Omit if only one is connected.")
    run_id: str = Field(..., description="Flow run id, from list_flow_runs.")


class CancelFlowRunParams(BaseModel):
    connection_id: str = Field("", description="Which connected environment to use. Omit if only one is connected.")
    run_id: str = Field(..., description="Flow run id to cancel, from list_flow_runs.")


class ResubmitFlowRunParams(BaseModel):
    connection_id: str = Field("", description="Which connected environment to use. Omit if only one is connected.")
    run_id: str = Field(..., description="Flow run id to resubmit (re-run with the same trigger inputs), from list_flow_runs.")


class FlowRunActionResult(sdl.Entity):
    id: str = ""
    title: str = ""
    ok: bool = True
    detail: str = ""


# ──────────────────────────────────────────────────────────────────────────
# Bulk operations (Ярус 3 value-add -- not native to either Microsoft API)
# ──────────────────────────────────────────────────────────────────────────


class BulkFlowResultItem(sdl.Entity):
    id: str = ""
    title: str = ""
    workflow_id: str = ""
    ok: bool = True
    error: str = ""


class BulkFlowResult(sdl.Entity):
    id: str = "bulk_flow_result"
    title: str = ""
    items: list[BulkFlowResultItem] = Field(default_factory=list)
    succeeded: int = 0
    failed: int = 0


class BulkSetFlowStateParams(BaseModel):
    connection_id: str = Field("", description="Which connected environment to use. Omit if only one is connected.")
    workflow_ids: list[str] = Field(
        ..., min_length=1, max_length=100,
        description="Explicit Dataverse workflowids (GUIDs); 1-100, never inferred.",
    )
    state: str = Field(..., description="Target state for every listed flow: 'activated' (turn on) or 'suspended' (turn off).")


class BulkDeleteFlowsParams(BaseModel):
    connection_id: str = Field("", description="Which connected environment to use. Omit if only one is connected.")
    workflow_ids: list[str] = Field(
        ..., min_length=1, max_length=100,
        description="Explicit Dataverse workflowids (GUIDs) to permanently delete; 1-100, never inferred.",
    )
