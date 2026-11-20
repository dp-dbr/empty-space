from dp_agentic_collector.domain.enums import ArtifactStatus, JobState, MessageKind
from tests.conftest import confirm_all


def test_full_collection_flow(app, world, cycle_setup):
    cycle, subject, job = cycle_setup
    alice = world.actor(world.alice)
    s = app.services
    assert s.cycles.get_job(alice, job.job_id).state == JobState.NOTIFIED
    msgs = s.conversations.messages(alice, job.job_id)
    assert msgs and "CRM" in msgs[0].text  # greeting
    assert app.registries.channels.get("web").sent[-1].user_id == world.alice.user_id

    s.conversations.post_user_message(alice, job.job_id, "name: CRM\nbusiness_owner: Sales\ncriticality: high\n"
                                                         "number_of_users: 250\ntechnologies: java, postgres")
    app.drain()
    assert s.cycles.get_job(alice, job.job_id).state == JobState.IN_PROGRESS
    props = s.conversations.proposals(alice, job.job_id, status="proposed")
    assert {p.path for p in props} == {"name", "business_owner", "criticality", "number_of_users", "technologies"}
    confirm_all(app, alice, job.job_id)
    res = s.artifacts.submit(alice, job.job_id)
    assert res.version.header.status == ArtifactStatus.SUBMITTED and res.version.header.version == 1
    assert res.version.header.contributor.user_id == world.alice.user_id
    app.drain()  # validation: high criticality without technical owner fails
    assert s.cycles.get_job(alice, job.job_id).state == JobState.IN_PROGRESS
    val = [m for m in s.conversations.messages(alice, job.job_id) if m.kind == MessageKind.VALIDATION]
    assert val[-1].payload["violations"][0]["rule"] == "high_criticality_requires_technical_owner"

    s.conversations.post_user_message(alice, job.job_id, "technical_owner: Team Platform")
    app.drain()
    confirm_all(app, alice, job.job_id)
    res = s.artifacts.submit(alice, job.job_id, "added technical owner")
    assert res.version.header.version == 2 and res.version.header.based_on == 1
    app.drain()
    latest = s.artifacts.get(alice, subject.subject_id)
    assert latest.header.status == ArtifactStatus.VALIDATED
    assert "Team Platform" in latest.factsheet_body
    assert "technical_owner" in latest.factsheet_body  # changes section

    owner = world.actor(world.owner)
    approved = s.artifacts.approve(owner, subject.subject_id)
    assert approved.header.status == ArtifactStatus.APPROVED
    assert s.cycles.get_job(owner, job.job_id).state == JobState.CLOSED
    assert all(app.artifact_store.verify(world.tenant.tenant_id, subject.subject_id, v)
               for v in range(1, approved.header.version + 1))
    rows = app.projection.history(world.tenant.tenant_id, subject.subject_id)
    assert [r["status"] for r in rows] == ["submitted", "submitted", "validated", "approved"]
    assert app.projection.query(world.tenant.tenant_id, cycle.cycle_id)[0]["data"]["technical_owner"] == "Team Platform"


def test_other_respondent_cannot_access_job(app, world, cycle_setup):
    import pytest

    from dp_agentic_collector.domain.errors import NotFound, PermissionDenied

    _, subject, job = cycle_setup
    bob = world.actor(world.bob)
    with pytest.raises(NotFound):
        app.services.conversations.messages(bob, job.job_id)
    with pytest.raises((NotFound, PermissionDenied)):
        app.services.artifacts.get(bob, subject.subject_id)
    viewer = world.actor(world.viewer)
    assert app.services.conversations.messages(viewer, job.job_id)  # viewer may read


def test_agent_run_records_and_trace(app, world, cycle_setup):
    from dp_agentic_collector.domain.entities import AgentRun

    _, _, job = cycle_setup
    alice = world.actor(world.alice)
    app.services.conversations.post_user_message(alice, job.job_id, "hello")
    app.drain()
    with app.db.uow(world.tenant.tenant_id) as uow:
        runs = uow.repo(AgentRun).list(job_id=job.job_id)
    assert runs and runs[0].on_behalf_of == world.alice.user_id and runs[0].status == "completed"
    assert runs[0].provider == "fake"
