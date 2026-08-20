"""Shared fixtures for Power Automate Connector PST (Plausible Scenario
Testing).

Mirrors the accepted pattern used by n8n Connector / Make.com Connector:
imperal_sdk.testing.MockContext + MockSecretStore give us the REAL
handlers.py / power_automate_client.py code path (real HTTP call
construction, real header names, real error mapping) against a
controlled fake HTTP backend -- not a hand-rolled imitation of the
logic itself.

UNLIKE n8n (two flat secrets: base_url + api_key), Power Automate
Connector packs MULTIPLE environment connections into ONE declared
secret as a JSON array (see handlers.py's _load_connections/
_save_connections) -- same precedent as Slack Connector. ctx_connected
therefore pre-seeds `power_automate_connections` with one realistic
connection record, not two flat keys.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

CONN_ID = "11111111-1111-1111-1111-111111111111"
ENV_URL = "https://acme-agency.crm4.dynamics.com"
ENV_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def ctx():
    from imperal_sdk.testing import MockContext, MockSecretStore

    mock = MockContext()
    mock.secrets = MockSecretStore({})
    return mock


@pytest.fixture
def ctx_connected(ctx):
    """Same as `ctx` but with one Power Platform environment already
    connected -- the state every persona in SCENARIO_TESTS.md starts
    from except the brand-new user in the connection scenarios."""
    from imperal_sdk.testing import MockSecretStore

    connections = [{
        "id": CONN_ID,
        "label": "Acme Agency Prod",
        "tenant_id": "33333333-3333-3333-3333-333333333333",
        "client_id": "44444444-4444-4444-4444-444444444444",
        "client_secret": "test-client-secret-value",
        "environment_url": ENV_URL,
        "environment_id": ENV_ID,
    }]
    ctx.secrets = MockSecretStore({
        "power_automate_connections": json.dumps(connections),
    })
    return ctx


def mock_token_ok(http):
    """Register the Azure AD client-credentials token endpoint as a
    healthy response -- every authenticated flow/run handler call needs
    this mocked first (get_access_token is always step 1)."""
    http.mock_post(
        "https://login.microsoftonline.com/33333333-3333-3333-3333-333333333333/oauth2/v2.0/token",
        {"access_token": "fake.jwt.token", "expires_in": 3600},
        status=200,
    )
