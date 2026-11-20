from dp_agentic_collector.adapters.sql.db import Database
from dp_agentic_collector.adapters.task_queue import InProcessTaskQueue
from dp_agentic_collector.observability import current_trace_id


def make():
    db = Database("sqlite://")
    db.create_all()
    return db, InProcessTaskQueue(db, max_attempts=2)


def test_outbox_dispatch_with_trace_and_retry():
    db, q = make()
    seen, traces = [], []
    attempts = {"n": 0}

    def ok(ev):
        seen.append(ev.payload["x"])
        traces.append(current_trace_id())

    def flaky(ev):
        attempts["n"] += 1
        raise RuntimeError("boom")

    q.register("a", ok)
    q.register("b", flaky)
    trace = "0123456789abcdef0123456789abcdef"
    with db.uow("t1") as uow:
        uow.publish("a", "u1", {"x": 1}, trace_id=trace)
        uow.publish("b", "u1", {})
    assert q.run_pending() == 2
    assert seen == [1] and traces == [trace]
    assert attempts["n"] == 1
    # event b is retried later with backoff, then goes dead after max attempts
    from sqlalchemy import select, update

    from dp_agentic_collector.adapters.sql.schema import outbox
    from dp_agentic_collector.domain.ids import utcnow

    with db.engine.begin() as c:
        c.execute(update(outbox).values(available_at=utcnow()))
    q.run_pending()
    with db.engine.connect() as c:
        statuses = {r.type: r.status for r in c.execute(select(outbox))}
    assert statuses == {"a": "done", "b": "dead"}
