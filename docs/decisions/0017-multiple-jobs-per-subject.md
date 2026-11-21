# ADR-0017: Several CollectionJobs per Subject; status-only versions advance the job base

* Status: accepted
* Date: 2026-11-21

## Context
Information about one subject is often held by several people (business and technical owner).
Concurrent contributions must be detected (optimistic locking), not serialized away.

## Decision
`CycleService.add_job` creates additional jobs for an existing Subject. Each job keeps its own
`base_version`; a submission whose base is outdated yields a conflict for that user (ADR-0015).
Versions that only change the status (`validated`, `approved`) carry identical content, so jobs
based on the previous version are moved to the new one (`_advance_base`) and their next
submission does not conflict with the system's own status versions. Recurring Cycle
initialization recreates one job per previous assignee.
