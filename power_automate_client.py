"""Power Automate HTTP client -- Azure AD client-credentials auth against a
user-supplied Dataverse environment, thin wrappers around the Dataverse Web
API `workflow` table (flows) and the Power Platform REST API (flow runs).

WHY CLIENT CREDENTIALS (Azure AD App Registration + Application User),
NOT DELEGATED USER OAUTH -- see app.py module docstring for the full
architectural reasoning (Dataverse's per-environment scope + Global
Discovery Service retirement). Token is requested against
`https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token` with
`scope={environment_url}/.default` and `grant_type=client_credentials`.

WHY TWO DIFFERENT API SURFACES ARE USED TOGETHER.

Flow CRUD (list/get/create/update/delete/enable/disable) goes through the
Dataverse Web API (`{environment_url}/api/data/v9.2/workflows`) -- the
only OFFICIALLY DOCUMENTED way to manage flows as Dataverse records, per
Microsoft's own manage-flows-with-code doc. This ONLY covers "Solution"
flows (category=5, "Modern Flow", added to a Dataverse Solution) -- NOT
personal "My Flows", which Microsoft's own docs say "aren't supported
with code". This limitation is a Microsoft API limitation, not ours --
confirmed in CONNECTOR_DISCOVERY.md and must be surfaced honestly in the
app's Marketplace description and in any relevant error message.

Flow RUNS (list/get/cancel/resubmit) go through the newer, separately
documented Power Platform REST API
(`https://api.powerplatform.com/powerautomate/environments/{environmentId}
/flowRuns`, api-version query param) -- Dataverse's workflow table does not
expose run history itself; this is the officially documented endpoint for
it (learn.microsoft.com/rest/api/power-platform/powerautomate/flow-runs).

WHY 401 vs 403 ARE HANDLED DIFFERENTLY, SAME PRINCIPLE AS n8n/Make.com
CONNECTOR's clients.

A 401 means the Azure AD app registration's credentials are not accepted
at all (wrong tenant_id/client_id/client_secret, or the token request
itself failed). A 403 means Azure AD issued a token fine, but the
Application User tied to that App Registration lacks the Dataverse
security role/privilege for the specific operation -- a materially
different, more specific and more fixable cause (the fix is granting a
security role in the Power Platform admin center, not re-entering
credentials) that must not be reported as "wrong credentials".
"""
from __future__ import annotations

DATAVERSE_API_VERSION = "v9.2"
POWER_PLATFORM_API_VERSION = "2023-06-01"
POWER_PLATFORM_BASE = "https://api.powerplatform.com/powerautomate"

ACCOUNT_MISSING = "POWER_AUTOMATE_ACCOUNT_MISSING"
TOKEN_REJECTED = "POWER_AUTOMATE_TOKEN_REJECTED"
PERMISSION_DENIED = "POWER_AUTOMATE_PERMISSION_DENIED"
NOT_FOUND = "POWER_AUTOMATE_NOT_FOUND"
VALIDATION_FAILED = "POWER_AUTOMATE_VALIDATION_FAILED"
RESPONSE_UNEXPECTED = "POWER_AUTOMATE_RESPONSE_UNEXPECTED"
UNREACHABLE = "POWER_AUTOMATE_UNREACHABLE"
RATE_LIMITED = "POWER_AUTOMATE_RATE_LIMITED"
BACKEND_5XX = "POWER_AUTOMATE_BACKEND_5XX"
BACKEND_TIMEOUT = "POWER_AUTOMATE_BACKEND_TIMEOUT"
MY_FLOWS_UNSUPPORTED = "POWER_AUTOMATE_MY_FLOWS_UNSUPPORTED"
NO_DATAVERSE = "POWER_AUTOMATE_NO_DATAVERSE"

_MESSAGES = {
    ACCOUNT_MISSING: "No Power Automate environment is connected yet.",
    TOKEN_REJECTED: "Azure AD rejected these credentials. Check the tenant ID, client ID and client secret, then reconnect.",
    PERMISSION_DENIED: "Azure AD accepted the credentials, but this app registration's Application User lacks the Dataverse security role for this operation. Grant it a role (e.g. System Administrator or a custom role with workflow privileges) in the Power Platform admin center.",
    NOT_FOUND: "Power Automate has no such flow, or this environment cannot access it.",
    VALIDATION_FAILED: "Power Automate rejected the request as invalid.",
    RESPONSE_UNEXPECTED: "Power Automate returned a response the connector could not safely interpret.",
    UNREACHABLE: "Could not reach this Power Automate environment.",
    RATE_LIMITED: "Power Automate is rate-limiting requests; try again shortly.",
    BACKEND_5XX: "Power Automate returned a server error; try again shortly.",
    BACKEND_TIMEOUT: "Power Automate took too long to respond; try again shortly.",
    MY_FLOWS_UNSUPPORTED: "This flow is a personal \"My Flow\", not a Solution flow. Microsoft's Dataverse Web API only manages Solution-aware flows -- add this flow to a Solution in Power Automate first, then it will be manageable here.",
    NO_DATAVERSE: "This environment has no Dataverse database, so it has no manageable flows via this API.",
}
_RETRYABLE = {RATE_LIMITED, BACKEND_5XX, BACKEND_TIMEOUT}

# Dataverse `workflow` table: category=5 is "Modern Flow" (cloud flows);
# other values are classic Dataverse workflows/dialogs/business rules/
# actions/BPFs/desktop flows -- never touched by this connector.
CATEGORY_MODERN_FLOW = 5

# statecode / statuscode pairs for the workflow table, confirmed against
# Microsoft's own SetStateWorkflow.cs sample + the workflow EntityType
# reference. statecode is the coarse state; statuscode is the detailed
# reason code paired with it.
STATE_DRAFT = 0        # statuscode 1  (Draft)
STATE_ACTIVATED = 1    # statuscode 2  (Activated) -- flow is turned ON
STATE_SUSPENDED = 2    # statuscode 3  (Suspended) -- flow is turned OFF but not draft
_STATUSCODE_FOR_STATE = {STATE_DRAFT: 1, STATE_ACTIVATED: 2, STATE_SUSPENDED: 3}


def fail(code: str, detail: str = "") -> dict:
    message = _MESSAGES.get(code, code)
    if detail:
        message = f"{message} ({detail})"
    return {"ok": False, "error_code": code, "error": message, "retryable": code in _RETRYABLE}


def _env(environment_url: str) -> str:
    return environment_url.rstrip("/")


def _dataverse_api(environment_url: str, path: str) -> str:
    return f"{_env(environment_url)}/api/data/{DATAVERSE_API_VERSION}/{path.lstrip('/')}"


def _token_url(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


def _flow_runs_url(environment_id: str, path: str = "") -> str:
    base = f"{POWER_PLATFORM_BASE}/environments/{environment_id}/flowRuns"
    return f"{base}/{path.lstrip('/')}" if path else base


async def get_access_token(ctx, tenant_id: str, client_id: str, client_secret: str, environment_url: str) -> dict:
    """Client-credentials token request scoped to this specific Dataverse
    environment. Returns {"ok": True, "access_token": ...} or a fail() dict.
    """
    scope = f"{_env(environment_url)}/.default"
    resp = await ctx.http.post(
        _token_url(tenant_id),
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
            "grant_type": "client_credentials",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code == 401 or resp.status_code == 400:
        return fail(TOKEN_REJECTED)
    if resp.status_code >= 500:
        return fail(BACKEND_5XX)
    if resp.status_code != 200:
        return fail(RESPONSE_UNEXPECTED, f"token endpoint returned {resp.status_code}")
    body = resp.body if isinstance(resp.body, dict) else {}
    token = body.get("access_token")
    if not token:
        return fail(RESPONSE_UNEXPECTED, "token response had no access_token")
    return {"ok": True, "access_token": token, "expires_in": body.get("expires_in", 3600)}


def _headers(access_token: str, *, prefer: str | None = None) -> dict:
    h = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def _check_status(resp, action: str) -> dict:
    if resp.status_code in (200, 201, 204):
        return resp.body if isinstance(resp.body, dict) else {}
    if resp.status_code == 401:
        raise ClientFail(fail(TOKEN_REJECTED, action))
    if resp.status_code == 403:
        raise ClientFail(fail(PERMISSION_DENIED, action))
    if resp.status_code == 404:
        raise ClientFail(fail(NOT_FOUND, action))
    if resp.status_code == 429:
        raise ClientFail(fail(RATE_LIMITED, action))
    if resp.status_code >= 500:
        raise ClientFail(fail(BACKEND_5XX, action))
    if resp.status_code == 400:
        raise ClientFail(fail(VALIDATION_FAILED, action))
    raise ClientFail(fail(RESPONSE_UNEXPECTED, f"{action}: HTTP {resp.status_code}"))


class ClientFail(Exception):
    def __init__(self, payload: dict):
        super().__init__(payload.get("error", "Power Automate request failed"))
        self.payload = payload


async def check_connection(ctx, tenant_id: str, client_id: str, client_secret: str, environment_url: str) -> dict:
    """Get a token, then a cheap GET /workflows?$top=1 to prove the
    Application User actually has a working security role -- a valid
    token alone does not guarantee Dataverse access (see PERMISSION_DENIED
    docstring above)."""
    tok = await get_access_token(ctx, tenant_id, client_id, client_secret, environment_url)
    if not tok.get("ok"):
        return tok
    resp = await ctx.http.get(
        _dataverse_api(environment_url, "workflows"),
        headers=_headers(tok["access_token"]),
        params={"$top": 1, "$select": "workflowid,name"},
    )
    try:
        _check_status(resp, "verify connection")
    except ClientFail as e:
        return e.payload
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────
# Flows (Dataverse Web API -- Solution-aware "Modern Flow" workflow rows)
# ──────────────────────────────────────────────────────────────────────────

_FLOW_SELECT = (
    "workflowid,name,uniquename,description,statecode,statuscode,category,"
    "createdon,modifiedon,ismanaged,solutionid"
)


def _flow_filter(active: bool | None) -> str | None:
    if active is None:
        return None
    return "statecode eq 1" if active else "statecode ne 1"


async def list_flows(
    ctx, access_token: str, environment_url: str, *,
    active: bool | None = None, top: int = 50, skiptoken: str | None = None,
) -> tuple[list[dict], str | None]:
    filters = [f"category eq {CATEGORY_MODERN_FLOW}"]
    active_filter = _flow_filter(active)
    if active_filter:
        filters.append(active_filter)
    params = {
        "$select": _FLOW_SELECT,
        "$filter": " and ".join(filters),
        "$top": top,
    }
    headers = _headers(access_token)
    url = _dataverse_api(environment_url, "workflows")
    if skiptoken:
        headers["Cookie"] = ""  # not used; skiptoken passed via $skiptoken query param below
        params["$skiptoken"] = skiptoken
    resp = await ctx.http.get(url, headers=headers, params=params)
    body = _check_status(resp, "list flows")
    rows = body.get("value") or []
    next_link = body.get("@odata.nextLink")
    next_token = None
    if next_link and "$skiptoken=" in next_link:
        next_token = next_link.split("$skiptoken=", 1)[1]
    return rows, next_token


async def get_flow(ctx, access_token: str, environment_url: str, workflow_id: str) -> dict:
    resp = await ctx.http.get(
        _dataverse_api(environment_url, f"workflows({workflow_id})"),
        headers=_headers(access_token),
        params={"$select": _FLOW_SELECT},
    )
    return _check_status(resp, "get flow")


async def create_flow(
    ctx, access_token: str, environment_url: str, *,
    name: str, clientdata: str, description: str = "",
) -> dict:
    """Create a Modern Flow row. `clientdata` is the flow's own JSON
    definition string (triggers/actions/connectionReferences) -- Microsoft
    requires this exact shape; this connector does not attempt to author
    flow logic itself, only pass through a definition the caller supplies.
    New flows are created in Draft (statecode=0) by default -- Microsoft's
    Web API does not support creating them already-activated in one call
    (confirmed in Discovery, stackoverflow.com/q/76980177); activate as a
    separate explicit step via enable_flow()."""
    payload = {
        "name": name,
        "category": CATEGORY_MODERN_FLOW,
        "clientdata": clientdata,
    }
    if description:
        payload["description"] = description
    resp = await ctx.http.post(
        _dataverse_api(environment_url, "workflows"),
        headers=_headers(access_token, prefer="return=representation"),
        json=payload,
    )
    return _check_status(resp, "create flow")


async def update_flow(
    ctx, access_token: str, environment_url: str, workflow_id: str, *,
    name: str | None = None, description: str | None = None,
    clientdata: str | None = None,
) -> dict:
    payload = {}
    if name is not None:
        payload["name"] = name
    if description is not None:
        payload["description"] = description
    if clientdata is not None:
        payload["clientdata"] = clientdata
    resp = await ctx.http.patch(
        _dataverse_api(environment_url, f"workflows({workflow_id})"),
        headers=_headers(access_token),
        json=payload,
    )
    return _check_status(resp, "update flow")


async def delete_flow(ctx, access_token: str, environment_url: str, workflow_id: str) -> dict:
    resp = await ctx.http.delete(
        _dataverse_api(environment_url, f"workflows({workflow_id})"),
        headers=_headers(access_token),
    )
    return _check_status(resp, "delete flow")


async def set_flow_state(
    ctx, access_token: str, environment_url: str, workflow_id: str, state: int,
) -> dict:
    """Turn a flow on/off (or back to draft) by writing statecode +
    matching statuscode together in one PATCH -- Dataverse requires the
    pair to be internally consistent (special-update-operation-behavior
    doc); writing statecode alone with a mismatched statuscode is rejected."""
    payload = {"statecode": state, "statuscode": _STATUSCODE_FOR_STATE[state]}
    resp = await ctx.http.patch(
        _dataverse_api(environment_url, f"workflows({workflow_id})"),
        headers=_headers(access_token),
        json=payload,
    )
    return _check_status(resp, "set flow state")


async def enable_flow(ctx, access_token: str, environment_url: str, workflow_id: str) -> dict:
    return await set_flow_state(ctx, access_token, environment_url, workflow_id, STATE_ACTIVATED)


async def disable_flow(ctx, access_token: str, environment_url: str, workflow_id: str) -> dict:
    return await set_flow_state(ctx, access_token, environment_url, workflow_id, STATE_SUSPENDED)


# ──────────────────────────────────────────────────────────────────────────
# Flow runs (Power Platform REST API -- separate documented surface,
# requires environment_id, not environment_url)
# ──────────────────────────────────────────────────────────────────────────


async def list_flow_runs(
    ctx, access_token: str, environment_id: str, workflow_id: str, *,
    top: int = 50,
) -> list[dict]:
    resp = await ctx.http.get(
        _flow_runs_url(environment_id),
        headers=_headers(access_token),
        params={"api-version": POWER_PLATFORM_API_VERSION, "workflow": workflow_id, "$top": top},
    )
    body = _check_status(resp, "list flow runs")
    return body.get("value") or []


async def get_flow_run(ctx, access_token: str, environment_id: str, run_id: str) -> dict:
    resp = await ctx.http.get(
        _flow_runs_url(environment_id, run_id),
        headers=_headers(access_token),
        params={"api-version": POWER_PLATFORM_API_VERSION},
    )
    return _check_status(resp, "get flow run")


async def cancel_flow_run(ctx, access_token: str, environment_id: str, run_id: str) -> dict:
    resp = await ctx.http.post(
        _flow_runs_url(environment_id, f"{run_id}/cancel"),
        headers=_headers(access_token),
        params={"api-version": POWER_PLATFORM_API_VERSION},
        json={},
    )
    return _check_status(resp, "cancel flow run")


async def resubmit_flow_run(ctx, access_token: str, environment_id: str, run_id: str) -> dict:
    resp = await ctx.http.post(
        _flow_runs_url(environment_id, f"{run_id}/resubmit"),
        headers=_headers(access_token),
        params={"api-version": POWER_PLATFORM_API_VERSION},
        json={},
    )
    return _check_status(resp, "resubmit flow run")


# ──────────────────────────────────────────────────────────────────────────
# Bulk operations (Ярус 3 value-add -- NOT native to either Microsoft API;
# this connector's own convenience layer, looping the single-item calls
# above with per-item error isolation so one bad id doesn't abort the rest,
# same principle as WordPress Hub's apply_bulk_* helpers).
# ──────────────────────────────────────────────────────────────────────────


async def bulk_set_flow_state(
    ctx, access_token: str, environment_url: str, workflow_ids: list[str], state: int,
) -> list[dict]:
    results = []
    for wf_id in workflow_ids:
        try:
            await set_flow_state(ctx, access_token, environment_url, wf_id, state)
            results.append({"workflow_id": wf_id, "ok": True})
        except ClientFail as e:
            results.append({"workflow_id": wf_id, "ok": False, "error": e.payload.get("error")})
    return results


async def bulk_delete_flows(ctx, access_token: str, environment_url: str, workflow_ids: list[str]) -> list[dict]:
    results = []
    for wf_id in workflow_ids:
        try:
            await delete_flow(ctx, access_token, environment_url, wf_id)
            results.append({"workflow_id": wf_id, "ok": True})
        except ClientFail as e:
            results.append({"workflow_id": wf_id, "ok": False, "error": e.payload.get("error")})
    return results
