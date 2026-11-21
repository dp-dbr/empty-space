"""E2E (FakeLlm): two users, version conflict and resolution, approval, snapshot comparison,
second Cycle initialized from the first."""

from fastapi.testclient import TestClient

from dp_agentic_collector.api.app import create_app
from dp_agentic_collector.domain.enums import ArtifactStatus, CycleRecurrence, JobState
from tests.conftest import confirm_all


def say(app, actor, job_id, text):
    app.services.conversations.post_user_message(actor, job_id, text)
    app.drain()
    confirm_all(app, actor, job_id)


def test_two_users_conflict_approval_snapshot_and_next_cycle(app, world):
    s = app.services
    owner, alice, bob = world.actor(world.owner), world.actor(world.alice), world.actor(world.bob)

    # --- Cycle 1: CRM with two contributors -------------------------------------------------
    c1 = s.cycles.create_cycle(owner, "app-inventory-2026-Q4", "application", recurrence=CycleRecurrence.RECURRING)
    crm, job_a = s.cycles.add_subject(owner, c1.cycle_id, "CRM", world.alice.user_id)
    _, job_erp = s.cycles.add_subject(owner, c1.cycle_id, "ERP", world.bob.user_id)
    job_b = s.cycles.add_job(owner, crm.subject_id, world.bob.user_id)
    s.cycles.start_cycle(owner, c1.cycle_id)
    app.drain()

    # both start from the empty state; Alice submits first
    say(app, alice, job_a.job_id, "name: CRM\nbusiness_owner: Sales\ncriticality: medium\nnumber_of_users: 200")
    say(app, bob, job_b.job_id, "name: CRM\nbusiness_owner: Marketing\ncriticality: medium\ntechnologies: java")
    va = s.artifacts.submit(alice, job_a.job_id).version
    assert va.header.version == 1
    app.drain()  # -> v2 validated

    # Bob's submission is based on an outdated version: never overwritten silently
    res = s.artifacts.submit(bob, job_b.job_id)
    assert res.version is None and res.conflict is not None
    conflict = res.conflict
    assert conflict["latest_version"] == 2
    assert [c["path"] for c in conflict["conflicts"]] == ["business_owner"]
    assert conflict["merged"]["technologies"] == ["java"]  # non-conflicting change merged
    assert conflict["merged"]["number_of_users"] == 200     # Alice's change kept
    assert s.artifacts.get(alice, crm.subject_id).data["business_owner"] == "Sales"
    assert any(m.kind == "conflict" for m in s.conversations.messages(bob, job_b.job_id))
    assert s.conversations.draft_view(bob, job_b.job_id)["conflict"]["latest_version"] == 2

    # Bob resolves: keeps his business owner
    v3 = s.artifacts.resolve_conflict(bob, job_b.job_id, {"business_owner": "mine"}).version
    assert v3.header.version == 3 and v3.header.based_on == 2
    assert v3.data == {"name": "CRM", "business_owner": "Marketing", "criticality": "medium",
                       "number_of_users": 200, "technologies": ["java"]}
    app.drain()  # -> v4 validated
    assert s.artifacts.get(owner, crm.subject_id).header.status == ArtifactStatus.VALIDATED

    # ERP is submitted too; owner approves both
    say(app, bob, job_erp.job_id, "name: ERP\nbusiness_owner: Finance\ncriticality: high\ntechnical_owner: Team SAP")
    s.artifacts.submit(bob, job_erp.job_id)
    app.drain()
    for subj in s.cycles.subjects(owner, c1.cycle_id):
        s.artifacts.approve(owner, subj.subject_id, "looks good")
    assert {j.state for j in s.cycles.jobs(owner, c1.cycle_id)} == {JobState.CLOSED}
    hist = [v.header for v in s.artifacts.history(owner, crm.subject_id)]
    assert [h.status for h in hist] == ["submitted", "validated", "submitted", "validated", "approved"]
    assert [h.contributor.user_id for h in hist][:3] == [world.alice.user_id, world.alice.user_id, world.bob.user_id]
    assert all(h.based_on == (h.version - 1 if h.version > 1 else None) for h in hist)
    snap = s.cycles.snapshot(owner, c1.cycle_id, "final")
    assert len(snap.versions) == 2

    # --- Cycle 2 initialized from the approved state of Cycle 1 --------------------------------
    c2 = s.cycles.create_cycle(owner, "app-inventory-2027-Q1", "application", previous_cycle_id=c1.cycle_id,
                               recurrence=CycleRecurrence.RECURRING)
    jobs2 = s.cycles.init_from_previous(owner, c2.cycle_id)
    assert len(jobs2) == 3  # CRM keeps both contributors
    s.cycles.start_cycle(owner, c2.cycle_id)
    app.drain()
    crm2 = next(x for x in s.cycles.subjects(owner, c2.cycle_id) if x.lineage_key == "crm")
    v1 = s.artifacts.get(owner, crm2.subject_id)
    assert v1.header.status == ArtifactStatus.DRAFT and v1.data["business_owner"] == "Marketing"
    assert v1.header.sources[0].reference == f"{crm.subject_id}@v5"

    # Alice (assignee of CRM in cycle 1) changes only what changed; previous version tool works
    job_a2 = next(j for j in jobs2 if j.subject_id == crm2.subject_id and j.assignee_id == world.alice.user_id)
    say(app, alice, job_a2.job_id, "number_of_users: 350")
    res2 = s.artifacts.submit(alice, job_a2.job_id, "update users")
    assert res2.version.header.based_on == 1 and res2.version.data["number_of_users"] == 350
    assert "number_of_users" in res2.version.factsheet_body and "350" in res2.version.factsheet_body

    # compare the new cycle against the previous cycle's snapshot (matched by lineage)
    comp = {r["lineage_key"]: r for r in s.artifacts.compare_snapshot(owner, c2.cycle_id, f"{c1.cycle_id}/final")}
    assert comp["crm"]["change"] == "changed"
    assert comp["crm"]["field_changes"] == [{"path": "number_of_users", "change": "changed", "old": 200, "new": 350}]
    assert comp["erp"]["change"] == "unchanged"
    # cross-cycle diff of single subjects
    d = s.artifacts.diff(owner, crm2.subject_id, 5, "latest", other_subject_id=crm.subject_id)
    assert [c.path for c in d.field_changes] == ["number_of_users"]

    # same via the HTTP API (cycle owner session)
    client = TestClient(create_app(app, start_worker=False))
    client.post("/api/v1/auth/login", json={"email": "owner@default.test", "password": "pw-owner-123"})
    r = client.get(f"/api/v1/cycles/{c2.cycle_id}/compare", params={"snapshot": f"{c1.cycle_id}/final"})
    assert r.status_code == 200 and {x["lineage_key"] for x in r.json()} == {"crm", "erp"}
    r = client.get(f"/api/v1/subjects/{crm.subject_id}/artifact", params={"version": "snapshot:" + f"{c1.cycle_id}/final"})
    assert r.json()["header"]["version"] == 5 and r.json()["integrity_ok"]


def test_previous_version_connector_offers_last_approved_data(app, world):
    s = app.services
    owner, alice = world.actor(world.owner), world.actor(world.alice)
    c1 = s.cycles.create_cycle(owner, "c1", "application")
    crm, job = s.cycles.add_subject(owner, c1.cycle_id, "CRM", world.alice.user_id)
    s.cycles.start_cycle(owner, c1.cycle_id)
    app.drain()
    say(app, alice, job.job_id, "name: CRM\nbusiness_owner: Sales\ncriticality: low")
    s.artifacts.submit(alice, job.job_id)
    app.drain()
    s.artifacts.approve(owner, crm.subject_id)
    c2 = s.cycles.create_cycle(owner, "c2", "application")  # not initialized: agent can fetch previous data
    _, job2 = s.cycles.add_subject(owner, c2.cycle_id, "CRM", world.alice.user_id)
    s.cycles.start_cycle(owner, c2.cycle_id)
    app.drain()
    s.conversations.post_user_message(alice, job2.job_id, "please start from the previous cycle")
    app.drain()
    props = {p.path: p for p in s.conversations.proposals(alice, job2.job_id, status="proposed")}
    assert props["business_owner"].value == "Sales"
    assert props["business_owner"].sources[0]["kind"] == "artifact"
