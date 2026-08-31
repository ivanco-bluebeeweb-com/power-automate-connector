"""Chat functions for Power Automate Connector: connection management,
flows (Dataverse Web API), flow runs (Power Platform REST API), and
bulk operations (Ярус 3 value-add). Built on power_automate_client.py /
schemas.py, following the same shape as n8n Connector's handlers.py.
"""
from __future__ import annotations

import json
import uuid

from imperal_sdk import ActionResult

import power_automate_client as pac
from app import ext, chat
from schemas import (
    NoParams,
    ConnectPowerAutomateParams, ProviderConnection, ProviderConnectionList,
    DisconnectPowerAutomateParams,
    ListFlowsParams, PowerAutomateFlow, PowerAutomateFlowList,
    GetFlowParams, CreateFlowParams, UpdateFlowParams, DeleteFlowParams,
    SetFlowStateParams, FlowActionResult, DeleteResult,
    ListFlowRunsParams, PowerAutomateFlowRun, PowerAutomateFlowRunList,
    GetFlowRunParams, CancelFlowRunParams, ResubmitFlowRunParams,
    FlowRunActionResult,
    BulkFlowResultItem, BulkFlowResult, BulkSetFlowStateParams,
    BulkDeleteFlowsParams,
)

_SECRET_NAME = "power_automate_connections"


# ──────────────────────────────────────────────────────────────────────────
# Connection storage helpers -- one secret holding a JSON array of
# connection records (see app.py module docstring for why: ctx.secrets has
# no "one secret per id" primitive, so this follows Slack Connector's
# precedent of packing multiple accounts into one declared secret).
# ──────────────────────────────────────────────────────────────────────────


async def _load_connections(ctx) -> list[dict]:
    raw = await ctx.secrets.get(_SECRET_NAME)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    return data if isinstance(data, list) else []


async def _save_connections(ctx, connections: list[dict]) -> None:
    await ctx.secrets.set(_SECRET_NAME, json.dumps(connections))


def _connection_entity(c: dict) -> ProviderConnection:
    return ProviderConnection(
        id=c.get("id", ""),
        title=c.get("label") or c.get("environment_url", ""),
        connected=True,
        detail=f"{c.get('environment_url', '')}",
        environment_url=c.get("environment_url", ""),
    )


def _resolve_connection(connections: list[dict], connection_id: str) -> dict | None:
    if not connections:
        return None
    if connection_id:
        for c in connections:
            if c.get("id") == connection_id:
                return c
        return None
    if len(connections) == 1:
        return connections[0]
    return None  # ambiguous -- caller must ask the user to specify


_TOKEN_CACHE = "power_automate_token_cache"


async def _cached_token(ctx, conn_id: str) -> str:
    import time as _time
    page = await ctx.store.query(_TOKEN_CACHE, where={"connection_id": conn_id}, limit=1)
    if not page.data:
        return ""
    doc = page.data[0].data
    if int(doc.get("expires_at", 0)) <= int(_time.time()):
        return ""
    return doc.get("access_token", "")


async def _store_token(ctx, conn_id: str, access_token: str, expires_in: int) -> None:
    import time as _time
    page = await ctx.store.query(_TOKEN_CACHE, where={"connection_id": conn_id}, limit=1)
    doc = {
        "connection_id": conn_id,
        "access_token": access_token,
        "expires_at": int(_time.time()) + max(int(expires_in or 3600) - 60, 60),
    }
    if page.data:
        await ctx.store.update(_TOKEN_CACHE, page.data[0].id, doc)
    else:
        await ctx.store.create(_TOKEN_CACHE, doc)


async def _resolve_and_authenticate(ctx, connections: list[dict], connection_id: str):
    """Shared preamble for every flow/run handler: load connections, pick
    the right one (or fail with a clear, actionable error), reuse a cached
    access token when still fresh (Azure AD's client-credentials token
    endpoint returns a real expires_in -- see AUTH_AND_CREDENTIALS_STANDARD.md
    Part B3, re-minting on every single tool call risks the token endpoint's
    own rate limit during bulk operations). Returns (connection, access_token)
    or raises via return of an ActionResult.error the caller must propagate."""
    if not connections:
        return None, None, ActionResult.error(
            "No Power Platform environment is connected yet -- run connect_power_automate first.",
            code="POWER_AUTOMATE_ACCOUNT_MISSING",
        )
    conn = _resolve_connection(connections, connection_id)
    if conn is None:
        if connection_id:
            return None, None, ActionResult.error(
                f"No connected environment with id '{connection_id}'. Run list_connections to see valid ids.",
                code="POWER_AUTOMATE_CONNECTION_NOT_FOUND",
            )
        names = ", ".join(c.get("label") or c.get("id", "") for c in connections)
        return None, None, ActionResult.error(
            f"Several environments are connected ({names}) -- specify connection_id.",
            code="POWER_AUTOMATE_CONNECTION_AMBIGUOUS",
        )
    conn_id = conn.get("id", "")
    access_token = await _cached_token(ctx, conn_id) if conn_id else ""
    if access_token:
        return conn, access_token, None
    tok = await pac.get_access_token(
        ctx, conn["tenant_id"], conn["client_id"], conn["client_secret"], conn["environment_url"],
    )
    if not tok.get("ok"):
        return None, None, ActionResult.error(tok["error"], code=tok["error_code"], retryable=tok.get("retryable", False))
    if conn_id:
        await _store_token(ctx, conn_id, tok["access_token"], tok.get("expires_in", 3600))
    return conn, tok["access_token"], None


# ──────────────────────────────────────────────────────────────────────────
# Connection management
# ──────────────────────────────────────────────────────────────────────────


@chat.function(
    "connect_power_automate",
    "Connect a Microsoft Power Platform environment by saving your Azure AD "
    "App Registration credentials (tenant_id, client_id, client_secret) and "
    "your Dataverse environment_url/environment_id, after checking they "
    "actually work together. You'll need: an Azure AD App Registration "
    "(Azure Portal > Microsoft Entra ID > App registrations), a client "
    "secret created under Certificates & secrets, and that app registered "
    "as an Application User in your Power Platform environment with a "
    "security role granting workflow privileges. Note: this only manages "
    "Solution-aware cloud flows -- personal unattached \"My Flows\" are not "
    "supported by Microsoft's own Web API and are out of scope here.",
    action_type="write",
    chain_callable=True,
    data_model=ProviderConnection,
    event="power-automate-connector.connect_power_automate",
    effects=["power_automate.provider.connected"],
)
async def connect_power_automate(ctx, params: ConnectPowerAutomateParams) -> ActionResult:
    """Connect a Power Platform environment via Azure AD client credentials."""
    tenant_id = params.tenant_id.strip()
    client_id = params.client_id.strip()
    client_secret = params.client_secret.strip()
    environment_url = params.environment_url.strip().rstrip("/")
    environment_id = params.environment_id.strip()
    missing = [
        n for n, v in [
            ("tenant_id", tenant_id), ("client_id", client_id),
            ("client_secret", client_secret), ("environment_url", environment_url),
        ] if not v
    ]
    if missing:
        return ActionResult.error(
            f"Please provide: {', '.join(missing)}.",
            code="POWER_AUTOMATE_MISSING_FIELD",
        )
    check = await pac.check_connection(ctx, tenant_id, client_id, client_secret, environment_url)
    if not check.get("ok"):
        return ActionResult.error(check["error"], code=check["error_code"], retryable=check.get("retryable", False))

    connections = await _load_connections(ctx)
    # Reconnecting the SAME environment (e.g. after rotating the client
    # secret) must replace the existing record, not add a second one --
    # two divergent copies of the same environment_url would make
    # _resolve_connection's no-connection_id single-match shortcut
    # ambiguous and silently break every later call. Matched by
    # environment_url (the real-world identity of "this environment"),
    # keeping the existing record's id stable for any external reference.
    existing = next((c for c in connections if c.get("environment_url") == environment_url), None)
    conn_id = existing["id"] if existing else str(uuid.uuid4())
    record = {
        "id": conn_id,
        "label": params.label.strip() or environment_url,
        "tenant_id": tenant_id,
        "client_id": client_id,
        "client_secret": client_secret,
        "environment_url": environment_url,
        "environment_id": environment_id,
    }
    connections = [c for c in connections if c.get("environment_url") != environment_url]
    connections.append(record)
    await _save_connections(ctx, connections)
    return ActionResult.success(
        _connection_entity(record),
        summary=f"Connected Power Platform environment '{record['label']}'.",
    )


@chat.function(
    "list_connections",
    "List the Power Platform environments currently connected.",
    action_type="read",
    chain_callable=True,
    data_model=ProviderConnectionList,
)
async def list_connections(ctx, params: NoParams) -> ActionResult:
    """List connected Power Platform environments."""
    connections = await _load_connections(ctx)
    items = [_connection_entity(c) for c in connections]
    return ActionResult.success(
        ProviderConnectionList(title=f"{len(items)} connection(s)", items=items),
        summary=f"{len(items)} environment(s) connected.",
    )


@chat.function(
    "disconnect_power_automate",
    "Disconnect one Power Platform environment: deletes its saved Azure AD "
    "credentials. Existing flows and their runs in that environment are not "
    "affected -- only Webbee's access to them is removed.",
    action_type="write",
    chain_callable=True,
    data_model=DeleteResult,
    event="power-automate-connector.disconnect_power_automate",
    effects=["power_automate.provider.disconnected"],
)
async def disconnect_power_automate(ctx, params: DisconnectPowerAutomateParams) -> ActionResult:
    """Disconnect one Power Platform environment."""
    connections = await _load_connections(ctx)
    remaining = [c for c in connections if c.get("id") != params.connection_id]
    if len(remaining) == len(connections):
        return ActionResult.error(
            f"No connected environment with id '{params.connection_id}'.",
            code="POWER_AUTOMATE_CONNECTION_NOT_FOUND",
        )
    await _save_connections(ctx, remaining)
    return ActionResult.success(
        DeleteResult(id=params.connection_id, title=f"Connection {params.connection_id}", ok=True),
        summary="Environment disconnected.",
    )


# ──────────────────────────────────────────────────────────────────────────
# Flows (Dataverse Web API -- Solution-aware cloud flows only)
# ──────────────────────────────────────────────────────────────────────────


def _flow_entity(w: dict) -> PowerAutomateFlow:
    state_map = {0: "draft", 1: "activated", 2: "suspended"}
    return PowerAutomateFlow(
        id=w.get("workflowid", ""),
        title=w.get("name") or w.get("workflowid", ""),
        name=w.get("name", ""),
        unique_name=w.get("uniquename", ""),
        state=state_map.get(w.get("statecode"), str(w.get("statecode", ""))),
        status=str(w.get("statuscode", "")),
        category=str(w.get("category", "")),
        created_on=w.get("createdon", "") or "",
        modified_on=w.get("modifiedon", "") or "",
        solution_aware=not bool(w.get("ismanaged", False)) or True,
    )


@chat.function(
    "list_flows",
    "List cloud flows (Solution-aware) in a connected Power Platform "
    "environment, via the Dataverse Web API. Personal unattached \"My "
    "Flows\" are not returned -- Microsoft's Web API does not expose them.",
    action_type="read",
    chain_callable=True,
    data_model=PowerAutomateFlowList,
)
async def list_flows(ctx, params: ListFlowsParams) -> ActionResult:
    """List cloud flows in a connected environment."""
    connections = await _load_connections(ctx)
    conn, token, err = await _resolve_and_authenticate(ctx, connections, params.connection_id)
    if err:
        return err
    active = None
    if params.state == "activated":
        active = True
    elif params.state in ("draft", "suspended"):
        active = False
    try:
        flows, _ = await pac.list_flows(
            ctx, token, conn["environment_url"], active=active, top=params.limit,
        )
    except pac.ClientFail as e:
        return ActionResult.error(e.payload["error"], code=e.payload["error_code"], retryable=e.payload.get("retryable", False))
    if params.search:
        needle = params.search.lower()
        flows = [f for f in flows if needle in (f.get("name") or "").lower()]
    items = [_flow_entity(f) for f in flows]
    return ActionResult.success(PowerAutomateFlowList(title=f"{len(items)} flow(s)", items=items), summary=f"{len(items)} flow(s).")


@chat.function(
    "get_flow",
    "Read one cloud flow in full from the Dataverse Web API.",
    action_type="read",
    chain_callable=True,
    data_model=PowerAutomateFlow,
)
async def get_flow(ctx, params: GetFlowParams) -> ActionResult:
    """Read one cloud flow in full."""
    connections = await _load_connections(ctx)
    conn, token, err = await _resolve_and_authenticate(ctx, connections, params.connection_id)
    if err:
        return err
    try:
        w = await pac.get_flow(ctx, token, conn["environment_url"], params.workflow_id)
    except pac.ClientFail as e:
        return ActionResult.error(e.payload["error"], code=e.payload["error_code"], retryable=e.payload.get("retryable", False))
    return ActionResult.success(_flow_entity(w), summary=f"Flow '{w.get('name')}'.")


@chat.function(
    "create_flow",
    "Create a new Solution-aware cloud flow in the Dataverse `workflow` "
    "table. clientdata must be valid Power Automate flow definition JSON "
    "(the format Power Automate itself exports/imports) -- this connector "
    "does not validate its internal shape, only that it is well-formed "
    "JSON. New flows are created in Draft state; call set_flow_state to "
    "activate.",
    action_type="write",
    chain_callable=True,
    data_model=PowerAutomateFlow,
    event="power-automate-connector.create_flow",
    effects=["power_automate.flow.created"],
)
async def create_flow(ctx, params: CreateFlowParams) -> ActionResult:
    """Create a new cloud flow."""
    connections = await _load_connections(ctx)
    conn, token, err = await _resolve_and_authenticate(ctx, connections, params.connection_id)
    if err:
        return err
    try:
        w = await pac.create_flow(
            ctx, token, conn["environment_url"],
            name=params.name, clientdata=params.clientdata,
        )
        if params.activate:
            w = await pac.enable_flow(ctx, token, conn["environment_url"], w["workflowid"])
    except pac.ClientFail as e:
        return ActionResult.error(e.payload["error"], code=e.payload["error_code"], retryable=e.payload.get("retryable", False))
    state_note = "activated" if params.activate else "draft"
    return ActionResult.success(_flow_entity(w), summary=f"Flow '{params.name}' created ({state_note}).")


@chat.function(
    "update_flow",
    "Update selected fields of an existing cloud flow (name, description, "
    "and/or its clientdata definition) without touching omitted fields.",
    action_type="write",
    chain_callable=True,
    data_model=PowerAutomateFlow,
    event="power-automate-connector.update_flow",
    effects=["power_automate.flow.updated"],
)
async def update_flow(ctx, params: UpdateFlowParams) -> ActionResult:
    """Update selected fields of an existing cloud flow."""
    connections = await _load_connections(ctx)
    conn, token, err = await _resolve_and_authenticate(ctx, connections, params.connection_id)
    if err:
        return err
    try:
        w = await pac.update_flow(
            ctx, token, conn["environment_url"], params.workflow_id,
            name=params.name, clientdata=params.clientdata,
        )
    except pac.ClientFail as e:
        return ActionResult.error(e.payload["error"], code=e.payload["error_code"], retryable=e.payload.get("retryable", False))
    return ActionResult.success(_flow_entity(w), summary=f"Flow '{w.get('name')}' updated.")


@chat.function(
    "delete_flow",
    "Permanently delete a cloud flow from Dataverse. Cannot be undone.",
    action_type="destructive",
    chain_callable=True,
    data_model=DeleteResult,
    event="power-automate-connector.delete_flow",
    effects=["power_automate.flow.deleted"],
)
async def delete_flow(ctx, params: DeleteFlowParams) -> ActionResult:
    """Permanently delete a cloud flow."""
    connections = await _load_connections(ctx)
    conn, token, err = await _resolve_and_authenticate(ctx, connections, params.connection_id)
    if err:
        return err
    try:
        await pac.delete_flow(ctx, token, conn["environment_url"], params.workflow_id)
    except pac.ClientFail as e:
        return ActionResult.error(e.payload["error"], code=e.payload["error_code"], retryable=e.payload.get("retryable", False))
    return ActionResult.success(
        DeleteResult(id=params.workflow_id, title=f"Flow {params.workflow_id}", ok=True),
        summary="Flow deleted.",
    )


_STATE_NAME_TO_CODE = {"draft": pac.STATE_DRAFT, "activated": pac.STATE_ACTIVATED, "suspended": pac.STATE_SUSPENDED}


@chat.function(
    "set_flow_state",
    "Turn a cloud flow on (activated) or off (suspended/draft) by setting "
    "its Dataverse statecode/statuscode.",
    action_type="write",
    chain_callable=True,
    data_model=FlowActionResult,
    event="power-automate-connector.set_flow_state",
    effects=["power_automate.flow.state_changed"],
)
async def set_flow_state(ctx, params: SetFlowStateParams) -> ActionResult:
    """Turn a cloud flow on/off by setting its state."""
    state_code = _STATE_NAME_TO_CODE.get(params.state)
    if state_code is None:
        return ActionResult.error(
            f"Unknown state '{params.state}' -- use 'draft', 'activated', or 'suspended'.",
            code="POWER_AUTOMATE_INVALID_STATE",
        )
    connections = await _load_connections(ctx)
    conn, token, err = await _resolve_and_authenticate(ctx, connections, params.connection_id)
    if err:
        return err
    try:
        await pac.set_flow_state(ctx, token, conn["environment_url"], params.workflow_id, state_code)
    except pac.ClientFail as e:
        return ActionResult.error(e.payload["error"], code=e.payload["error_code"], retryable=e.payload.get("retryable", False))
    return ActionResult.success(
        FlowActionResult(id=params.workflow_id, title=f"Flow {params.workflow_id}", detail=f"state={params.state}"),
        summary=f"Flow set to '{params.state}'.",
    )


# ──────────────────────────────────────────────────────────────────────────
# Flow runs (Power Platform REST API -- separate surface from Dataverse)
# ──────────────────────────────────────────────────────────────────────────


def _run_entity(r: dict, workflow_id: str) -> PowerAutomateFlowRun:
    props = r.get("properties", r)
    run_id = r.get("name") or r.get("id", "")
    return PowerAutomateFlowRun(
        id=run_id,
        title=f"Run {run_id}",
        workflow_id=workflow_id,
        status=props.get("status", ""),
        start_time=props.get("startTime", "") or "",
        end_time=props.get("endTime", "") or "",
        error=str(props.get("error", "")) if props.get("error") else "",
    )


@chat.function(
    "list_flow_runs",
    "List recent runs of a cloud flow, via the Power Platform REST API "
    "(a separate, newer surface from the Dataverse Web API used for the "
    "flow definition itself).",
    action_type="read",
    chain_callable=True,
    data_model=PowerAutomateFlowRunList,
)
async def list_flow_runs(ctx, params: ListFlowRunsParams) -> ActionResult:
    """List recent runs of a cloud flow."""
    connections = await _load_connections(ctx)
    conn, token, err = await _resolve_and_authenticate(ctx, connections, params.connection_id)
    if err:
        return err
    try:
        runs = await pac.list_flow_runs(
            ctx, token, conn["environment_id"], params.workflow_id, top=params.limit,
        )
    except pac.ClientFail as e:
        return ActionResult.error(e.payload["error"], code=e.payload["error_code"], retryable=e.payload.get("retryable", False))
    items = [_run_entity(r, params.workflow_id) for r in runs]
    return ActionResult.success(PowerAutomateFlowRunList(title=f"{len(items)} run(s)", items=items), summary=f"{len(items)} run(s).")


@chat.function(
    "get_flow_run",
    "Get details of one flow run by id, via the Power Platform REST API.",
    action_type="read",
    chain_callable=True,
    data_model=PowerAutomateFlowRun,
)
async def get_flow_run(ctx, params: GetFlowRunParams) -> ActionResult:
    """Read one flow run in full."""
    connections = await _load_connections(ctx)
    conn, token, err = await _resolve_and_authenticate(ctx, connections, params.connection_id)
    if err:
        return err
    try:
        r = await pac.get_flow_run(ctx, token, conn["environment_id"], params.run_id)
    except pac.ClientFail as e:
        return ActionResult.error(e.payload["error"], code=e.payload["error_code"], retryable=e.payload.get("retryable", False))
    return ActionResult.success(_run_entity(r, r.get("properties", r).get("workflow", {}).get("name", "")), summary="Flow run retrieved.")


@chat.function(
    "cancel_flow_run",
    "Cancel a currently running flow run.",
    action_type="write",
    chain_callable=True,
    data_model=FlowRunActionResult,
    event="power-automate-connector.cancel_flow_run",
    effects=["power_automate.flow_run.cancelled"],
)
async def cancel_flow_run(ctx, params: CancelFlowRunParams) -> ActionResult:
    """Cancel a running flow run."""
    connections = await _load_connections(ctx)
    conn, token, err = await _resolve_and_authenticate(ctx, connections, params.connection_id)
    if err:
        return err
    try:
        await pac.cancel_flow_run(ctx, token, conn["environment_id"], params.run_id)
    except pac.ClientFail as e:
        return ActionResult.error(e.payload["error"], code=e.payload["error_code"], retryable=e.payload.get("retryable", False))
    return ActionResult.success(
        FlowRunActionResult(id=params.run_id, title=f"Run {params.run_id}", detail="cancelled"),
        summary="Run cancelled.",
    )


@chat.function(
    "resubmit_flow_run",
    "Resubmit (re-run) a flow run with the same trigger inputs it originally received.",
    action_type="write",
    chain_callable=True,
    data_model=FlowRunActionResult,
    event="power-automate-connector.resubmit_flow_run",
    effects=["power_automate.flow_run.resubmitted"],
)
async def resubmit_flow_run(ctx, params: ResubmitFlowRunParams) -> ActionResult:
    """Resubmit (re-run) a flow run with the same trigger inputs."""
    connections = await _load_connections(ctx)
    conn, token, err = await _resolve_and_authenticate(ctx, connections, params.connection_id)
    if err:
        return err
    try:
        await pac.resubmit_flow_run(ctx, token, conn["environment_id"], params.run_id)
    except pac.ClientFail as e:
        return ActionResult.error(e.payload["error"], code=e.payload["error_code"], retryable=e.payload.get("retryable", False))
    return ActionResult.success(
        FlowRunActionResult(id=params.run_id, title=f"Run {params.run_id}", detail="resubmitted"),
        summary="Run resubmitted.",
    )


# ──────────────────────────────────────────────────────────────────────────
# Bulk operations -- Tier 3 value-add, NOT part of Microsoft's native API.
# Sequential per-item calls (Dataverse Web API has no atomic bulk-state
# endpoint for workflow rows), same shape as n8n Connector's
# bulk_stop_executions: partial success is reported item-by-item, never
# silently swallowed.
# ──────────────────────────────────────────────────────────────────────────


@chat.function(
    "bulk_set_flow_state",
    "Turn multiple cloud flows on/off in one call -- a value-add "
    "convenience this connector adds on top of Microsoft's API, which has "
    "no native bulk-state endpoint. Applies set_flow_state to each "
    "workflow_id in turn and reports success/failure per item.",
    action_type="write",
    chain_callable=True,
    data_model=BulkFlowResult,
    event="power-automate-connector.bulk_set_flow_state",
    effects=["power_automate.flow.bulk_state_changed"],
)
async def bulk_set_flow_state(ctx, params: BulkSetFlowStateParams) -> ActionResult:
    """Set the same state on 1-100 explicit flows."""
    state_code = _STATE_NAME_TO_CODE.get(params.state)
    if state_code is None:
        return ActionResult.error(
            f"Unknown state '{params.state}' -- use 'draft', 'activated', or 'suspended'.",
            code="POWER_AUTOMATE_INVALID_STATE",
        )
    connections = await _load_connections(ctx)
    conn, token, err = await _resolve_and_authenticate(ctx, connections, params.connection_id)
    if err:
        return err
    results = await pac.bulk_set_flow_state(ctx, token, conn["environment_url"], params.workflow_ids, state_code)
    items = [BulkFlowResultItem(id=r["workflow_id"], workflow_id=r["workflow_id"], title=f"Flow {r['workflow_id']}", ok=r["ok"], error=r.get("error", "")) for r in results]
    ok_count = sum(1 for r in results if r["ok"])
    return ActionResult.success(
        BulkFlowResult(title=f"{ok_count}/{len(items)} succeeded", items=items, succeeded=ok_count, failed=len(items) - ok_count),
        summary=f"{ok_count}/{len(items)} flow(s) set to '{params.state}'.",
    )


@chat.function(
    "bulk_delete_flows",
    "Permanently delete multiple cloud flows in one call -- a value-add "
    "convenience this connector adds on top of Microsoft's API, which has "
    "no native bulk-delete endpoint. Deletes each workflow_id in turn and "
    "reports success/failure per item. This cannot be undone.",
    action_type="destructive",
    chain_callable=True,
    data_model=BulkFlowResult,
    event="power-automate-connector.bulk_delete_flows",
    effects=["power_automate.flow.bulk_deleted"],
)
async def bulk_delete_flows(ctx, params: BulkDeleteFlowsParams) -> ActionResult:
    """Permanently delete 1-100 explicit flows."""
    connections = await _load_connections(ctx)
    conn, token, err = await _resolve_and_authenticate(ctx, connections, params.connection_id)
    if err:
        return err
    results = await pac.bulk_delete_flows(ctx, token, conn["environment_url"], params.workflow_ids)
    items = [BulkFlowResultItem(id=r["workflow_id"], workflow_id=r["workflow_id"], title=f"Flow {r['workflow_id']}", ok=r["ok"], error=r.get("error", "")) for r in results]
    ok_count = sum(1 for r in results if r["ok"])
    return ActionResult.success(
        BulkFlowResult(title=f"{ok_count}/{len(items)} succeeded", items=items, succeeded=ok_count, failed=len(items) - ok_count),
        summary=f"{ok_count}/{len(items)} flow(s) deleted.",
    )
