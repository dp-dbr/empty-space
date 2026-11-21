"""E2E: data of tenant A is never visible in tenant B (services, API, stores, MCP tokens)."""

import pytest
from fastapi.testclient import TestClient

from dp_agentic_collector.api.app import create_app
from dp_agentic_collector.domain.entities import Cycle
from dp_agentic_collector.domain.errors import NotFound, PermissionDenied, TenantViolation
from tests.conftest import World, confirm_all


def populate(app, w):
    s = app.services
    owner, alice = w.actor(w.owner), w.actor(w.alice)
    cycle = s.cycles.create_cycle(owner, "inventory", "application")
    subject, job = s.cycles.add_subject(owner, cycle.cycle_id, "CRM", w.alice.user_id)
    s.cycles.start_cycle(owner, cycle.cycle_id)
    app.drain()
    s.conversations.post_user_message(alice, job.job_id, "name: CRM\nbusiness_owner: Sales\ncriticality: low")
    app.drain()
    confirm_all(app, alice, job.job_id)
    s.artifacts.submit(alice, job.job_id)
    app.drain()
    att = s.attachments.upload(alice, job.job_id, "shared.txt", b"shared content", "shared")
    s.cycles.snapshot(owner, cycle.cycle_id, "s1")
    return cycle, subject, job, att


def test_tenant_a_data_never_visible_in_tenant_b(app):
    a, b = World(app, "tenant-a"), World(app, "tenant-b")
    cycle, subject, job, att = populate(app, a)
    populate(app, b)  # same names in tenant B
    s = app.services
    for actor in (b.actor(b.admin), b.actor(b.owner), b.actor(b.viewer), b.actor(b.alice)):
        assert all(c.cycle_id != cycle.cycle_id for c in s.cycles.list_cycles(actor))
        with pytest.raises((NotFound, PermissionDenied)):
            s.cycles.get_cycle(actor, cycle.cycle_id)
        with pytest.raises((NotFound, PermissionDenied)):
            s.artifacts.get(actor, subject.subject_id)
        with pytest.raises((NotFound, PermissionDenied)):
            s.conversations.messages(actor, job.job_id)
        with pytest.raises((NotFound, PermissionDenied)):
            s.attachments.get(actor, att.attachment_id)
        with pytest.raises((NotFound, PermissionDenied)):
            s.artifacts.compare_snapshot(actor, cycle.cycle_id, f"{cycle.cycle_id}/s1")
        assert all(e.tenant_id == actor.tenant_id for e in s.tenancy.audit_log(actor, mine=True))
    # stores are partitioned by tenant
    assert app.projection.query(b.tenant.tenant_id, cycle_id=cycle.cycle_id) == []
    with pytest.raises(NotFound):
        app.artifact_store.get(b.tenant.tenant_id, subject.subject_id)
    digest = att.content_hash.split(":")[1]
    assert (app.settings.data_dir / "attachments" / a.tenant.tenant_id / digest[:2] / digest).exists()
    assert (app.settings.data_dir / "artifacts" / f"{a.tenant.tenant_id}.git").exists()
    # repositories refuse writes into another tenant
    with app.db.uow(b.tenant.tenant_id) as uow, pytest.raises(TenantViolation):
        uow.repo(Cycle).add(Cycle(tenant_id=a.tenant.tenant_id, name="x", subject_type="application",
                                  owner_id=a.owner.user_id))

    # HTTP API with a tenant-B auth session
    client = TestClient(create_app(app, start_worker=False))
    client.post("/api/v1/auth/login", json={"tenant": "tenant-b", "email": "admin@tenant-b.test",
                                            "password": "pw-admin-123"})
    for path in (f"/api/v1/cycles/{cycle.cycle_id}", f"/api/v1/subjects/{subject.subject_id}/artifact",
                 f"/api/v1/jobs/{job.job_id}/messages", f"/api/v1/attachments/{att.attachment_id}/content",
                 f"/api/v1/subjects/{subject.subject_id}/history"):
        assert client.get(path).status_code in (403, 404), path
    assert subject.subject_id not in client.get("/api/v1/projection").text
    # tenant-B bearer token cannot reach tenant-A data either
    tok = s.auth.personal_token(b.actor(b.admin), ["dpac.read"]).access_token
    r = client.get(f"/api/v1/subjects/{subject.subject_id}/artifact", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code in (403, 404)
    # same e-mail in another tenant does not log in there
    r = TestClient(create_app(app, start_worker=False)).post(
        "/api/v1/auth/login", json={"tenant": "tenant-b", "email": "admin@tenant-a.test", "password": "pw-admin-123"})
    assert r.status_code == 401
