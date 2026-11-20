from datetime import timedelta

from dp_agentic_collector.domain.enums import JobState
from dp_agentic_collector.domain.ids import utcnow


def test_reminder_overdue_escalation(app, world):
    owner = world.actor(world.owner)
    s = app.services
    cycle = s.cycles.create_cycle(owner, "c1", "application", due_date=utcnow() + timedelta(days=10),
                                  reminder_after_days=2, escalate_after_days=3)
    _, job = s.cycles.add_subject(owner, cycle.cycle_id, "ERP", world.alice.user_id)
    s.cycles.start_cycle(owner, cycle.cycle_id)
    app.drain()
    web = app.registries.channels.get("web")
    n0 = len(web.sent)
    tid = world.tenant.tenant_id

    stats = app.orchestrator.tick(tid, now=utcnow() + timedelta(days=3))
    assert stats["reminded"] == 1 and len(web.sent) == n0 + 1

    stats = app.orchestrator.tick(tid, now=utcnow() + timedelta(days=11))
    assert stats["overdue"] == 1
    assert s.cycles.get_job(owner, job.job_id).state == JobState.OVERDUE

    stats = app.orchestrator.tick(tid, now=utcnow() + timedelta(days=15))
    assert stats["escalated"] == 1
    assert s.cycles.get_job(owner, job.job_id).state == JobState.ESCALATED
    assert web.sent[-1].user_id == world.owner.user_id  # escalation goes to the cycle owner

    # a reply brings the job back in progress
    s.conversations.post_user_message(world.actor(world.alice), job.job_id, "sorry, here I am")
    assert s.cycles.get_job(owner, job.job_id).state == JobState.IN_PROGRESS


def test_background_actions_skip_inactive_users(app, world):
    owner = world.actor(world.owner)
    s = app.services
    cycle = s.cycles.create_cycle(owner, "c2", "application", due_date=utcnow() - timedelta(days=1))
    s.cycles.add_subject(owner, cycle.cycle_id, "ERP", world.alice.user_id)
    s.cycles.start_cycle(owner, cycle.cycle_id)
    s.tenancy.update_user(world.actor(world.admin), world.owner.user_id, active=False)
    stats = app.orchestrator.tick(world.tenant.tenant_id)
    assert stats == {"overdue": 0, "escalated": 0, "reminded": 0}
