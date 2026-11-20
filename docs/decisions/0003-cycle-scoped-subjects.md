# ADR-0003: Subjects are Cycle-scoped and linked by a lineage key

* Status: accepted
* Date: 2026-11-20

## Context
The mission defines `Subject(subject_id, subject_type, owner(s), cycle)` and requires that a
recurring Cycle can be initialized from the approved state of the previous one, and that
changes can be reported against snapshots of the previous Cycle.

## Decision
A Subject belongs to exactly one Cycle. A `lineage_key` (stable business key, e.g. `crm`)
links Subjects of the same real-world thing across Cycles. Initializing Cycle N+1 creates new
Subjects with the same `lineage_key` and stores the approved data of the predecessor as
version 1 (status `draft`, `change_reason="initialized from <cycle>"`, `sources` pointing to
the previous artifact version). Diffs across Cycles compare two `(subject, version)` refs.

## Alternatives
Long-lived subjects with per-cycle artifact branches (couples Git layout to domain).

## Consequences
History per Subject is per Cycle; cross-cycle history is reached via `lineage_key`.
