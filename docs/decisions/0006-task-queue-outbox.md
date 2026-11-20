# ADR-0006: Transactional outbox with an in-process TaskQueue

* Status: accepted
* Date: 2026-11-20

## Decision
Services write `OutboxEvent` rows in the same DB transaction as their state changes. The
`InProcessTaskQueue` polls pending events (`SELECT ... ORDER BY id`), dispatches them to
registered handlers in a worker thread, marks them done or schedules retries with backoff
(max attempts → `dead`). Events carry `tenant_id`, `actor` (user_id) and `trace_id`. In tests
the queue is drained synchronously (`drain()`).

## Alternatives
Celery/RQ (needs broker), Temporal (powerful but heavy for the MVP).

## Exit path
`TaskQueue` interface (`publish`, `register`, `run_pending`); an external queue adapter only
needs to forward outbox rows.
