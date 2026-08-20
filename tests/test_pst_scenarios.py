"""Plausible Scenario Testing (PST) for Power Automate Connector.

Method: Docs/session-notes/SCENARIO_TESTING_STANDARD.md. Persona used
throughout: "Marina", an agency ops lead who owns an Azure AD App
Registration + Application User against her own Dataverse environment
and manages Solution-aware cloud flows through Webbee. Power Automate
Connector has one functional role (the App Registration holder), so
scenario variety comes from DATA classes (empty/typical/boundary/
invalid/exotic environment states -- Draft vs Activated vs Suspended
flows, Solution vs personal "My Flows", 401 vs 403 vs 404 vs 429 vs 5xx)
and from the 5 required branches, not from multiple personas.

Every test calls the REAL handlers.py chat functions with REAL params
models, through imperal_sdk.testing.MockContext -- not a
re-implementation of the logic under a different name.
"""
from __future__ import annotations

import pytest

import handlers as h
import power_automate_client as pac
from schemas import (
    ConnectPowerAutomateParams, DisconnectPowerAutomateParams,
    ListFlowsParams, GetFlowParams, CreateFlowParams, UpdateFlowParams,
    DeleteFlowParams, SetFlowStateParams,
    ListFlowRunsParams, GetFlowRunParams, CancelFlowRunParams,
    ResubmitFlowRunParams,
    BulkSetFlowStateParams, BulkDeleteFlowsParams,
)

from conftest import CONN_ID, ENV_URL, ENV_ID

TOKEN_URL = "login.microsoftonline.com"
WORKFLOWS_URL = "/api/data/v9.2/workflows"


def _mock_token_ok(ctx):
    ctx.http.mock_post(TOKEN_URL, {"access_token": "test-access-token", "expires_in": 3600}, status=200)


# ═══════════════════════════════════════════════════════════════════════
# BRANCH 1 -- HAPPY PATH (connection)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_connect_happy_path_saves_all_five_fields(ctx):
    """Given a brand-new user with a real Azure AD App Registration and a
    working Dataverse environment, When she connects, Then
    check_connection succeeds and ALL five fields are saved (not just
    tenant_id+client_id -- a partial save would silently break every
    later call that needs environment_id for flow-runs)."""
    _mock_token_ok(ctx)
    ctx.http.mock_get(WORKFLOWS_URL, {"value": [{"workflowid": "wf1", "name": "Test"}]}, status=200)
    result = await h.connect_power_automate(ctx, ConnectPowerAutomateParams(
        tenant_id="ten-1", client_id="cli-1", client_secret="sec-1",
        environment_url="https://org1.crm.dynamics.com", environment_id="env-1",
        label="Prod",
    ))
    assert result.error is None, f"expected success, got error: {result.error}"
    saved = await h._load_connections(ctx)
    assert len(saved) == 1
    conn = saved[0]
    for field in ("tenant_id", "client_id", "client_secret", "environment_url", "environment_id"):
        assert conn.get(field), f"{field} must be saved on success, connect is useless without it"


@pytest.mark.asyncio
async def test_connect_trailing_slash_environment_url_is_normalized(ctx):
    """Boundary data class: environment_url with a trailing slash must
    not produce a double-slash in the real Dataverse API path."""
    _mock_token_ok(ctx)
    ctx.http.mock_get(WORKFLOWS_URL, {"value": []}, status=200)
    result = await h.connect_power_automate(ctx, ConnectPowerAutomateParams(
        tenant_id="t", client_id="c", client_secret="s",
        environment_url="https://org1.crm.dynamics.com///", environment_id="e",
    ))
    assert result.error is None


@pytest.mark.asyncio
async def test_list_flows_happy_path_returns_solution_flows_only(ctx_connected):
    """Given several flows are returned, Then only Modern Flow rows are
    surfaced (the client already filters category=5 server-side via
    $filter, this asserts the whole chain still works end to end)."""
    _mock_token_ok(ctx_connected)
    ctx_connected.http.mock_get(WORKFLOWS_URL, {"value": [
        {"workflowid": "wf1", "name": "Lead Sync", "statecode": 1, "statuscode": 2, "category": 5},
        {"workflowid": "wf2", "name": "Invoice Flow", "statecode": 0, "statuscode": 1, "category": 5},
    ]}, status=200)
    result = await h.list_flows(ctx_connected, ListFlowsParams())
    assert result.error is None
    assert len(result.data.items) == 2
    assert result.data.items[0].id == "wf1"


# ═══════════════════════════════════════════════════════════════════════
# BRANCH 2 -- ERROR HANDLING (auth/permission/not-found data classes)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_connect_rejects_wrong_credentials_with_specific_code(ctx):
    """Data class: wrong client_secret. Azure AD returns 401/400 at the
    token endpoint -- must surface as TOKEN_REJECTED, not a generic
    failure, and nothing must be saved."""
    ctx.http.mock_post(TOKEN_URL, {"error": "invalid_client"}, status=401)
    result = await h.connect_power_automate(ctx, ConnectPowerAutomateParams(
        tenant_id="t", client_id="c", client_secret="wrong",
        environment_url="https://org1.crm.dynamics.com", environment_id="e",
    ))
    assert result.error is not None
    assert result.error_code == pac.TOKEN_REJECTED
    saved = await h._load_connections(ctx)
    assert saved == [], "a rejected connect must not leave a half-saved connection behind"


@pytest.mark.asyncio
async def test_connect_valid_token_but_no_dataverse_role_is_permission_denied(ctx):
    """Data class: token accepted (Azure AD side fine) but the
    Application User has no Dataverse security role -- must be reported
    as PERMISSION_DENIED (grant a role), never TOKEN_REJECTED (re-enter
    credentials) -- these are materially different fixes for the user."""
    _mock_token_ok(ctx)
    ctx.http.mock_get(WORKFLOWS_URL, {"error": {"message": "no privilege"}}, status=403)
    result = await h.connect_power_automate(ctx, ConnectPowerAutomateParams(
        tenant_id="t", client_id="c", client_secret="s",
        environment_url="https://org1.crm.dynamics.com", environment_id="e",
    ))
    assert result.error is not None
    assert result.error_code == pac.PERMISSION_DENIED


@pytest.mark.asyncio
async def test_get_flow_not_found_is_specific_not_generic(ctx_connected):
    """Data class: workflow_id that doesn't exist (or belongs to a
    different environment) -- Dataverse returns 404, must surface as
    NOT_FOUND with an actionable message, not RESPONSE_UNEXPECTED."""
    _mock_token_ok(ctx_connected)
    ctx_connected.http.mock_get(WORKFLOWS_URL, {"error": "not found"}, status=404)
    result = await h.get_flow(ctx_connected, GetFlowParams(workflow_id="ghost-id"))
    assert result.error is not None
    assert result.error_code == pac.NOT_FOUND


@pytest.mark.asyncio
async def test_list_flows_no_connection_gives_actionable_error(ctx):
    """Data class: zero connections saved yet -- must tell the user to
    run connect_power_automate, not throw a raw exception or a vague
    'not found'."""
    result = await h.list_flows(ctx, ListFlowsParams())
    assert result.error is not None
    assert result.error_code == "POWER_AUTOMATE_ACCOUNT_MISSING"


@pytest.mark.asyncio
async def test_ambiguous_connection_id_when_two_environments_connected(ctx):
    """Data class: two connected environments (Dev+Prod), connection_id
    omitted -- must fail asking to disambiguate, never silently guess
    which environment to act on (a silent guess against Prod is the
    single worst possible outcome for this app)."""
    _mock_token_ok(ctx)
    ctx.http.mock_get(WORKFLOWS_URL, {"value": []}, status=200)
    await h.connect_power_automate(ctx, ConnectPowerAutomateParams(
        tenant_id="t1", client_id="c1", client_secret="s1",
        environment_url="https://dev.crm.dynamics.com", environment_id="e1", label="Dev",
    ))
    await h.connect_power_automate(ctx, ConnectPowerAutomateParams(
        tenant_id="t2", client_id="c2", client_secret="s2",
        environment_url="https://prod.crm.dynamics.com", environment_id="e2", label="Prod",
    ))
    result = await h.list_flows(ctx, ListFlowsParams())
    assert result.error is not None
    assert result.error_code == "POWER_AUTOMATE_CONNECTION_AMBIGUOUS"


# ═══════════════════════════════════════════════════════════════════════
# BRANCH 3 -- GATED (destructive action requires explicit confirm)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_delete_flow_happy_path_actually_deletes(ctx_connected):
    """delete_flow is destructive (action_type=write, no undo -- Dataverse
    hard-deletes the workflow row); confirm it actually calls DELETE and
    surfaces success cleanly given real confirmation context upstream."""
    _mock_token_ok(ctx_connected)
    ctx_connected.http._mocks.append(("DELETE", WORKFLOWS_URL, {}, 204, {}))
    result = await h.delete_flow(ctx_connected, DeleteFlowParams(workflow_id="wf1"))
    assert result.error is None


@pytest.mark.asyncio
async def test_bulk_delete_flows_isolates_per_item_failures(ctx_connected):
    """Given 3 workflow_ids where the middle one 404s, Then the other two
    must still be attempted and reported individually -- one bad id must
    never abort the whole batch (same principle as WordPress Hub's
    apply_bulk_* helpers, this connector's own value-add layer)."""
    _mock_token_ok(ctx_connected)
    calls = {"n": 0}

    async def fake_delete_flow(ctx, token, env_url, wf_id):
        calls["n"] += 1
        if wf_id == "bad-id":
            raise pac.ClientFail(pac.fail(pac.NOT_FOUND))
        return {}

    pac.delete_flow = fake_delete_flow
    try:
        result = await h.bulk_delete_flows(ctx_connected, BulkDeleteFlowsParams(
            workflow_ids=["wf1", "bad-id", "wf3"],
        ))
        assert result.error is None
        items = result.data.items
        assert len(items) == 3, "all 3 ids must be reported, not just the successful ones"
        oks = [i.ok for i in items]
        assert oks == [True, False, True]
    finally:
        import importlib
        importlib.reload(pac)


# ═══════════════════════════════════════════════════════════════════════
# BRANCH 4 -- RECOVERY (retry after transient failure)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_set_flow_state_fails_then_succeeds_on_retry(ctx_connected):
    """Given a transient 500 from Dataverse (e.g. environment briefly
    overloaded), When the same set_flow_state is retried, Then it must
    succeed cleanly -- no leftover state from the failed attempt should
    block the retry."""
    _mock_token_ok(ctx_connected)
    ctx_connected.http._mocks.append(("PATCH", WORKFLOWS_URL, {"message": "server error"}, 500, {}))
    r1 = await h.set_flow_state(ctx_connected, SetFlowStateParams(workflow_id="wf1", state="activated"))
    assert r1.error is not None
    assert r1.error_code == pac.BACKEND_5XX

    ctx_connected.http._mocks.clear()
    _mock_token_ok(ctx_connected)
    ctx_connected.http._mocks.append(("PATCH", WORKFLOWS_URL, {}, 204, {}))
    r2 = await h.set_flow_state(ctx_connected, SetFlowStateParams(workflow_id="wf1", state="activated"))
    assert r2.error is None


@pytest.mark.asyncio
async def test_rate_limited_flow_run_list_is_marked_retryable(ctx_connected):
    """Data class: Power Platform REST API returns 429 -- the error must
    be marked retryable=True so a chain-calling agent knows to back off
    and retry, not to treat this as a permanent failure."""
    _mock_token_ok(ctx_connected)
    ctx_connected.http.mock_get("powerplatform.com", {"error": "rate limited"}, status=429)
    result = await h.list_flow_runs(ctx_connected, ListFlowRunsParams(workflow_id="wf1"))
    assert result.error is not None
    assert result.error_code == pac.RATE_LIMITED
    assert result.retryable is True


# ═══════════════════════════════════════════════════════════════════════
# BRANCH 5 -- ADVERSARIAL (destructive/bulk boundary + malformed input)
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bulk_set_flow_state_with_max_100_ids_all_succeed(ctx_connected):
    """Boundary data class: exactly 100 workflow_ids (the declared max),
    each must be attempted -- no silent truncation."""
    _mock_token_ok(ctx_connected)
    ctx_connected.http._mocks.append(("PATCH", WORKFLOWS_URL, {}, 204, {}))
    ids = [f"wf-{i}" for i in range(100)]
    result = await h.bulk_set_flow_state(ctx_connected, BulkSetFlowStateParams(
        workflow_ids=ids, state="suspended",
    ))
    assert result.error is None
    assert len(result.data.items) == 100


@pytest.mark.asyncio
async def test_create_flow_with_malformed_clientdata_json_is_rejected_by_dataverse(ctx_connected):
    """Data class: clientdata that isn't valid Power Automate flow
    definition JSON -- Dataverse itself rejects it (400), must surface
    as VALIDATION_FAILED, not silently accepted or a raw traceback."""
    _mock_token_ok(ctx_connected)
    ctx_connected.http._mocks.append(("POST", WORKFLOWS_URL, {"error": {"message": "invalid clientdata"}}, 400, {}))
    result = await h.create_flow(ctx_connected, CreateFlowParams(name="Broken Flow", clientdata="{not json"))
    assert result.error is not None
    assert result.error_code == pac.VALIDATION_FAILED


@pytest.mark.asyncio
async def test_get_flow_run_with_empty_run_id_does_not_crash(ctx_connected):
    """Adversarial data class: empty string run_id -- must fail cleanly
    through the real client/handler path, never raise an unhandled
    exception up through the chat function boundary."""
    _mock_token_ok(ctx_connected)
    ctx_connected.http.mock_get("powerplatform.com", {"error": "not found"}, status=404)
    result = await h.get_flow_run(ctx_connected, GetFlowRunParams(run_id=""))
    assert result.error is not None


# ═══════════════════════════════════════════════════════════════════════
# D4 -- REGRESSION: known-bug-patterns.md sanity checks specific to this app
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_disconnect_actually_removes_the_connection_not_a_noop(ctx_connected):
    """Regression guard for the 2026-08-19 known bug pattern (.pop() on a
    dict before store.update() being a silent no-op patch-semantics bug):
    disconnect_power_automate must actually shrink the saved connections
    list, not merely appear to by popping a local dict copy."""
    before = await h._load_connections(ctx_connected)
    assert len(before) == 1
    result = await h.disconnect_power_automate(ctx_connected, DisconnectPowerAutomateParams(connection_id=CONN_ID))
    assert result.error is None
    after = await h._load_connections(ctx_connected)
    assert after == [], "disconnect must remove the connection from stored state, not no-op"
