# ADR-0016: MVP runs API and worker in one process by default

* Status: accepted
* Date: 2026-11-20

## Decision
`dpac serve` starts the API and an in-process worker thread (`DP_AC_RUN_WORKER=true`).
`dpac worker` runs only the worker, so docker-compose can scale them separately once the queue
is external. The guarantee "no LLM/tool calls inside request handling" holds in both modes,
because requests only write outbox events.
