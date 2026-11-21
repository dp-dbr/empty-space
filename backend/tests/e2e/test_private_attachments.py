"""E2E: private attachments are invisible to others via UI/API and MCP; extraction needs confirmation."""

from fastapi.testclient import TestClient

from dp_agentic_collector.api.app import create_app
from tests.e2e.test_mcp_server import call, served  # noqa: F401  (fixture)


def test_private_attachment_invisible_via_api_and_mcp(served):  # noqa: F811
    app, world, base = served
    s = app.services
    owner, alice = world.actor(world.owner), world.actor(world.alice)
    cycle = s.cycles.create_cycle(owner, "priv", "application")
    subject, job = s.cycles.add_subject(owner, cycle.cycle_id, "CRM", world.alice.user_id)
    s.cycles.start_cycle(owner, cycle.cycle_id)
    app.drain()
    private = s.attachments.upload(alice, job.job_id, "salaries.txt", b"number_of_users: 77\n")
    shared = s.attachments.upload(alice, job.job_id, "architecture.md", b"# Architecture\n", "shared")
    app.drain()

    # extraction from the private file requires an explicit visibility confirmation
    s.conversations.post_user_message(alice, job.job_id, "please use the attachment", [private.attachment_id])
    app.drain()
    prop = s.conversations.proposals(alice, job.job_id, status="proposed")[0]
    assert prop.requires_visibility_confirmation
    client = TestClient(create_app(app, start_worker=False))
    csrf = client.post("/api/v1/auth/login", json={"email": "alice@default.test", "password": "pw-alice-123"}).json()["csrf_token"]
    r = client.post(f"/api/v1/proposals/{prop.proposal_id}/decision", json={"decision": "confirm"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 422 and r.json()["details"]["code"] == "visibility_ack"
    r = client.post(f"/api/v1/proposals/{prop.proposal_id}/decision",
                    json={"decision": "confirm", "visibility_ack": True}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200

    # owner (cycle owner, can read the subject) sees the shared file only — via API ...
    owner_client = TestClient(create_app(app, start_worker=False))
    owner_client.post("/api/v1/auth/login", json={"email": "owner@default.test", "password": "pw-owner-123"})
    names = [a["filename"] for a in owner_client.get(f"/api/v1/subjects/{subject.subject_id}/attachments").json()]
    assert names == ["architecture.md"]
    assert owner_client.get(f"/api/v1/attachments/{private.attachment_id}/content").status_code == 404
    # ... and via MCP
    owner_tok = s.auth.personal_token(owner, ["dpac.read"]).access_token
    r = call(base, owner_tok, "list_attachments", {"subject_id": subject.subject_id})
    listed = str(r.structured_content)
    assert "architecture.md" in listed and "salaries.txt" not in listed
    assert call(base, owner_tok, "read_attachment_text", {"attachment_id": private.attachment_id}).is_error
    # the uploader sees her own private file via MCP
    alice_tok = s.auth.personal_token(alice, ["dpac.read"]).access_token
    assert "salaries.txt" in str(call(base, alice_tok, "list_attachments", {"subject_id": subject.subject_id}).structured_content)

    # visibility change and withdrawal
    s.attachments.share(alice, private.attachment_id)
    assert "salaries.txt" in str(call(base, owner_tok, "list_attachments", {"subject_id": subject.subject_id}).structured_content)
    s.attachments.withdraw(alice, shared.attachment_id)
    assert "architecture.md" not in str(call(base, owner_tok, "list_attachments", {"subject_id": subject.subject_id}).structured_content)
