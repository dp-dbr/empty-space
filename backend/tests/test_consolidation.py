from tests.conftest import confirm_all


def submit(app, actor, job, text):
    app.services.conversations.post_user_message(actor, job.job_id, text)
    app.drain()
    confirm_all(app, actor, job.job_id)
    app.services.artifacts.submit(actor, job.job_id)
    app.drain()


def test_gaps_and_contradictions(app, world):
    owner = world.actor(world.owner)
    s = app.services
    cycle = s.cycles.create_cycle(owner, "cons", "application")
    _, crm = s.cycles.add_subject(owner, cycle.cycle_id, "CRM", world.alice.user_id)
    _, erp = s.cycles.add_subject(owner, cycle.cycle_id, "ERP", world.bob.user_id)
    s.cycles.add_subject(owner, cycle.cycle_id, "HR", world.bob.user_id)
    s.cycles.start_cycle(owner, cycle.cycle_id)
    app.drain()
    submit(app, world.actor(world.bob), erp, "name: ERP\nbusiness_owner: Finance\ncriticality: low")
    submit(app, world.actor(world.alice), crm, "name: CRM\nbusiness_owner: Sales\ncriticality: low\ninterfaces: ERP, Billing")
    rep = s.consolidation.report(owner, cycle.cycle_id)
    checks = {(c["check"], tuple(c["subjects"])) for c in rep["contradictions"]}
    assert ("interface_symmetry", ("crm", "erp")) in checks
    assert ("interfaces_reference_existing_applications", ("crm",)) in checks
    assert any(g["check"] == "no_submission" and g["subjects"] == ["hr"] for g in rep["gaps"])
    assert rep["coverage"]["name"] == 1.0 and rep["coverage"]["technical_owner"] == 0.0
    assert rep["with_data"] == 2
