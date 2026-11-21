"""MCP client (delegated, per-user access) and the Collector's own MCP server."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from dp_agentic_collector.domain.entities import PendingAction
from dp_agentic_collector.domain.enums import (
    AccessMode,
    ConnectionStatus,
    McpAuthType,
    McpTransport,
    ToolClassification,
)
from dp_agentic_collector.domain.errors import PermissionDenied
from dp_agentic_collector.testing.mcp_fixtures import RunningFixture
from tests.conftest import confirm_all


@pytest.fixture(scope="module")
def fixture_server():
    with RunningFixture() as f:
        yield f


@pytest.fixture
def servers(app, world, fixture_server):
    admin = world.actor(world.admin)
    c = app.services.connections
    base = fixture_server.base_url
    registry = c.create_server(admin, "Asset Registry", McpTransport.STREAMABLE_HTTP, f"{base}/registry/mcp",
                               auth_type=McpAuthType.API_KEY, max_access_mode=AccessMode.READ)
    tickets = c.create_server(admin, "Tickets", McpTransport.STREAMABLE_HTTP, f"{base}/tickets/mcp",
                              auth_type=McpAuthType.OAUTH2, max_access_mode=AccessMode.READ_WRITE,
                              oauth={"authorize_url": f"{base}/as/authorize", "token_url": f"{base}/as/token",
                                     "client_id": "dpac-test"})
    return registry, tickets


def oauth_connect(app, actor, server, mode=AccessMode.READ_WRITE, login_hint=None):
    con, url = app.services.connections.connect(actor, server.server_id, mode,
                                                redirect_uri="http://testserver/api/v1/connections/oauth/callback")
    assert con.status == ConnectionStatus.EXPIRED and url
    if login_hint:
        url += f"&login_hint={login_hint}"
    r = httpx.get(url, follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    return app.services.connections.oauth_callback(actor, q["state"][0], q["code"][0])


@pytest.fixture
def two_jobs(app, world):
    owner = world.actor(world.owner)
    s = app.services
    cycle = s.cycles.create_cycle(owner, "mcp-cycle", "application")
    _, job_a = s.cycles.add_subject(owner, cycle.cycle_id, "CRM", world.alice.user_id)
    _, job_b = s.cycles.add_subject(owner, cycle.cycle_id, "CRM Mobile", world.bob.user_id, lineage_key="crm-mobile")
    s.cycles.start_cycle(owner, cycle.cycle_id)
    app.drain()
    return job_a, job_b


def test_users_see_only_their_own_source_data_with_provenance(app, world, servers, two_jobs):
    registry, _ = servers
    job_a, job_b = two_jobs
    s = app.services
    alice, bob = world.actor(world.alice), world.actor(world.bob)
    s.connections.connect(alice, registry.server_id, api_key="key-alice")
    s.connections.connect(bob, registry.server_id, api_key="key-bob")

    s.conversations.post_user_message(alice, job_a.job_id, "please look up crm in the registry")
    s.conversations.post_user_message(bob, job_b.job_id, "please look up crm in the registry")
    app.drain()
    pa = {p.path: p for p in s.conversations.proposals(alice, job_a.job_id, status="proposed")}
    pb = {p.path: p for p in s.conversations.proposals(bob, job_b.job_id, status="proposed")}
    assert pa["number_of_users"].value == 1200 and pb["number_of_users"].value == 5
    src = pa["number_of_users"].sources[0]
    assert src["kind"] == "mcp" and src["server"] == "Asset Registry" and src["tool"] == "lookup_application"
    assert src["on_behalf_of"] == world.alice.user_id

    s.conversations.post_user_message(alice, job_a.job_id, "name: CRM\nbusiness_owner: Sales\ncriticality: low")
    app.drain()
    confirm_all(app, alice, job_a.job_id)
    v = s.artifacts.submit(alice, job_a.job_id).version
    mcp_src = [x for x in v.header.sources if x.kind == "mcp"]
    assert mcp_src and mcp_src[0].on_behalf_of == world.alice.user_id and "number_of_users" in mcp_src[0].fields
    assert "Asset Registry/lookup_application" in v.factsheet_body
    # every tool call is audited for the acting user
    mine = s.tenancy.audit_log(alice, mine=True)
    assert any(e.action == "mcp.call" and e.agent_run_id for e in mine)


def test_admin_policy_and_read_only_blocks_writes(app, world, servers, two_jobs, fixture_server):
    registry, tickets = servers
    job_a, _ = two_jobs
    s = app.services
    alice = world.actor(world.alice)
    with pytest.raises(PermissionDenied):
        s.connections.connect(alice, registry.server_id, AccessMode.READ_WRITE, api_key="key-alice")
    oauth_connect(app, alice, tickets, AccessMode.READ)
    names = {t.qualified_name: t for t in s.connections.available_tools(alice)}
    assert "mcp__tickets__search_tickets" in names and "mcp__tickets__create_ticket" not in names
    with pytest.raises(PermissionDenied):
        s.connections.call(alice, tickets.server_id, "create_ticket", {"record": "x"}, ToolClassification.WRITE,
                           confirmed=True)
    n = len(fixture_server.state.tickets)
    s.conversations.post_user_message(alice, job_a.job_id, "write a ticket to update source")
    app.drain()
    assert len(fixture_server.state.tickets) == n
    assert s.conversations.pending_actions(alice, job_a.job_id) == []


def test_write_requires_confirmation_then_executes(app, world, servers, two_jobs, fixture_server):
    _, tickets = servers
    job_a, _ = two_jobs
    s = app.services
    alice = world.actor(world.alice)
    oauth_connect(app, alice, tickets, AccessMode.READ_WRITE, login_hint="alice-account")
    n = len(fixture_server.state.tickets)
    s.conversations.post_user_message(alice, job_a.job_id, "write: please update source with CRM owner Sales")
    app.drain()
    actions = s.conversations.pending_actions(alice, job_a.job_id)
    assert len(actions) == 1 and actions[0].status == "pending" and "Tickets" in actions[0].target
    assert len(fixture_server.state.tickets) == n  # nothing written yet
    msgs = s.conversations.messages(alice, job_a.job_id)
    assert msgs[-1].kind == "confirmation_request"
    with pytest.raises(PermissionDenied):
        s.conversations.decide_action(world.actor(world.bob), actions[0].action_id, True)
    s.conversations.decide_action(alice, actions[0].action_id, True)
    app.drain()
    assert len(fixture_server.state.tickets) == n + 1
    assert fixture_server.state.tickets[-1]["subject"] == "alice-account"
    with app.db.uow(world.tenant.tenant_id) as uow:
        assert uow.repo(PendingAction).get(actions[0].action_id).status == "executed"
    assert any(e.action == "mcp.call" and e.details.get("confirmed") for e in s.tenancy.audit_log(alice, mine=True))
    # the agent continues without asking for the same write again
    assert [a.status for a in s.conversations.pending_actions(alice, job_a.job_id)] == ["executed"]
    assert "carried out" in s.conversations.messages(alice, job_a.job_id)[-1].text


def test_revoked_and_expired_connections_degrade_gracefully(app, world, servers, two_jobs, fixture_server):
    registry, tickets = servers
    job_a, _ = two_jobs
    s = app.services
    alice = world.actor(world.alice)
    con, _ = s.connections.connect(alice, registry.server_id, api_key="key-alice")
    tcon = oauth_connect(app, alice, tickets, AccessMode.READ_WRITE)
    # a write is pending, then the connection is revoked before confirmation
    s.conversations.post_user_message(alice, job_a.job_id, "write: update source please")
    app.drain()
    action = s.conversations.pending_actions(alice, job_a.job_id)[0]
    s.connections.revoke(alice, tcon.connection_id)
    s.conversations.decide_action(alice, action.action_id, True)
    app.drain()
    with app.db.uow(world.tenant.tenant_id) as uow:
        assert uow.repo(PendingAction).get(action.action_id).status == "failed"
    # revoked registry connection: tools disappear, conversation continues
    s.connections.revoke(alice, con.connection_id)
    assert s.connections.available_tools(alice) == []
    s.conversations.post_user_message(alice, job_a.job_id, "please look up crm")
    app.drain()
    assert "no connected source" in s.conversations.messages(alice, job_a.job_id)[-1].text
    # expired token at the source -> connection marked expired, no crash
    tcon2 = oauth_connect(app, alice, tickets, AccessMode.READ)
    cred = app.vault.get(world.tenant.tenant_id, tcon2.credential_ref)
    fixture_server.state.revoked.add(cred["access_token"])
    assert s.connections.available_tools(alice) == []
    assert next(c for c in s.connections.my_connections(alice)
                if c.connection_id == tcon2.connection_id).status == ConnectionStatus.EXPIRED


def test_no_cross_user_connection_use(app, world, servers):
    registry, _ = servers
    s = app.services
    s.connections.connect(world.actor(world.alice), registry.server_id, api_key="key-alice")
    # bob has no connection: calling the server on his behalf is denied
    with pytest.raises(PermissionDenied):
        s.connections.call(world.actor(world.bob), registry.server_id, "lookup_application", {"query": "crm"},
                           ToolClassification.READ)
