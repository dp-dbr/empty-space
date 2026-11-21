# ADR-0021: Consolidation as generic checks plus SubjectType-provided checks

* Status: accepted
* Date: 2026-11-21

## Decision
The consolidation report of a Cycle combines generic gap detection (no submission, empty required
fields, schema errors, overdue jobs, field coverage), the SubjectType's ValidationRules evaluated
across peers, and optional `SubjectType.consolidation_checks()` (added as a non-abstract method,
so interface v1 stays compatible). The `application` type contributes interface symmetry and
owner conflicts. The report is computed on demand from the projection (no stored state).
