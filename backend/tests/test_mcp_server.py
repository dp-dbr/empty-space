"""The Collector's own MCP server: external clients see only their user's data."""

import base64
import hashlib
import threading
import time

import anyio
import httpx
import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from dp_agentic_collector.api.app import create_app
from dp_agentic_collector.testing.mcp_fixtures import free_port
from tests.conftest import World, confirm_all, make_settings


@pytest.fixture
def served(tmp_path):
    from dp_agentic_collector.container import Collector

    port = free_port()
    app = Collector.create(make_settings(tmp_path, public_base_url=f"http://127.0.0.1:{port}"))
    world = World(app)
    server = uvicorn.Server(uvicorn.Config(create_app(app, start_worker=False), host="127.0.0.1", port=port,
                                           log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    yield app, world, f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(timeout=5)


def call(base, token, tool, args=None):
    async def go():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as hc:
            async with Client(streamable_http_client(f"{base}/mcp/", http_client=hc)) as c:
                if tool == "__list__":
                    return [t.name for t in (await c.list_tools()).tools]
                r = await c.call_tool(tool, args or {})
                return r
    return anyio.run(go)


def seed(app, world):
    owner = world.actor(world.owner)
    s = app.services
    cycle = s.cycles.create_cycle(owner, "mcp-srv", "application")
    sa, ja = s.cycles.add_subject(owner, cycle.cycle_id, "CRM", world.alice.user_id)
    sb, jb = s.cycles.add_subject(owner, cycle.cycle_id, "HR", world.bob.user_id)
    s.cycles.start_cycle(owner, cycle.cycle_id)
    app.drain()
    alice = world.actor(world.alice)
    s.conversations.post_user_message(alice, ja.job_id, "name: CRM\nbusiness_owner: Sales\ncriticality: low")
    app.drain()
    confirm_all(app, alice, ja.job_id)
    s.artifacts.submit(alice, ja.job_id)
    app.drain()
    return cycle, sa, sb, ja, jb


def test_external_mcp_client_sees_only_its_users_data(served):
    app, world, base = served
    cycle, sa, sb, ja, jb = seed(app, world)
    alice_tok = app.services.auth.personal_token(world.actor(world.alice), ["dpac.read", "dpac.write"]).access_token
    bob_tok = app.services.auth.personal_token(world.actor(world.bob), ["dpac.read"]).access_token

    tools = call(base, alice_tok, "__list__")
    assert {"list_my_jobs", "get_data_file", "submit_data", "compare_with_snapshot"} <= set(tools)
    r = call(base, alice_tok, "list_my_jobs")
    assert ja.job_id in str(r.structured_content) and jb.job_id not in str(r.structured_content)
    r = call(base, alice_tok, "get_data_file", {"subject_id": sa.subject_id})
    assert not r.is_error and r.structured_content["data"]["name"] == "CRM"
    r = call(base, bob_tok, "get_data_file", {"subject_id": sa.subject_id})
    assert r.is_error  # bob may not read alice's subject
    r = call(base, bob_tok, "list_subjects", {"cycle_id": cycle.cycle_id})
    assert sa.subject_id not in str(r.structured_content)
    # read-only token cannot submit
    r = call(base, bob_tok, "submit_data", {"job_id": jb.job_id, "data": {"name": "HR"}})
    assert r.is_error
    # write-scoped token submits with optimistic locking
    r = call(base, alice_tok, "submit_data", {"job_id": ja.job_id, "data": {"number_of_users": 99}})
    assert not r.is_error and r.structured_content["status"] == "submitted"


def test_mcp_requires_valid_token(served):
    app, world, base = served
    with pytest.raises(BaseException):  # noqa: B017 - SDK raises an exception group on 401
        call(base, "invalid-token", "__list__")
    r = httpx.post(f"{base}/mcp/", json={})
    assert r.status_code == 401


def test_oauth_authorization_code_pkce_flow(served):
    app, world, base = served
    seed(app, world)
    http = httpx.Client(base_url=base)
    meta = http.get("/.well-known/oauth-authorization-server").json()
    assert meta["code_challenge_methods_supported"] == ["S256"]
    reg = http.post("/oauth/register", json={"client_name": "ext", "redirect_uris": ["http://localhost:7777/cb"]})
    client_id = reg.json()["client_id"]
    csrf = http.post("/api/v1/auth/login", json={"email": "alice@default.test", "password": "pw-alice-123"}).json()["csrf_token"]
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    r = http.post("/api/v1/oauth/authorize", headers={"X-CSRF-Token": csrf}, json={
        "client_id": client_id, "redirect_uri": "http://localhost:7777/cb", "scope": "dpac.read",
        "code_challenge": challenge, "state": "xyz"})
    redirect = r.json()["redirect_url"]
    assert "state=xyz" in redirect
    code = redirect.split("code=")[1].split("&")[0]
    bad = http.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": client_id, "code": code,
                                          "redirect_uri": "http://localhost:7777/cb", "code_verifier": "wrong" * 10})
    assert bad.status_code == 400
    # code is single-use only after success; obtain a fresh one
    r = http.post("/api/v1/oauth/authorize", headers={"X-CSRF-Token": csrf}, json={
        "client_id": client_id, "redirect_uri": "http://localhost:7777/cb", "scope": "dpac.read",
        "code_challenge": challenge})
    code = r.json()["redirect_url"].split("code=")[1]
    tok = http.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": client_id, "code": code,
                                          "redirect_uri": "http://localhost:7777/cb", "code_verifier": verifier}).json()
    assert tok["scope"] == "dpac.read"
    r = call(base, tok["access_token"], "list_my_jobs")
    assert not r.is_error
    again = http.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": client_id, "code": code,
                                            "redirect_uri": "http://localhost:7777/cb", "code_verifier": verifier})
    assert again.status_code == 400
