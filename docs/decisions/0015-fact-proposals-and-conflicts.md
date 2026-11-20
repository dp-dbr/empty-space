# ADR-0015: Fact proposals, confirmations and conflict resolution

* Status: accepted
* Date: 2026-11-20

## Decision
Agents never write data-file fields directly. They create `FactProposal`s (field path, value,
sources, `requires_visibility_confirmation` if derived from a private attachment). The user
confirms/rejects/edits each proposal. A job keeps `base_version`; the working data is
`base data + confirmed proposals`. Submitting calls `ArtifactStore.put(expected_based_on =
base_version)`. On `VersionConflict` the job stores a `ConflictState` with the three-way field
diff (base, theirs=latest, mine); the user resolves per field (`mine`/`theirs`/custom) and the
submission is retried with `based_on = latest`.
