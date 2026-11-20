# ADR-0005: Git-based ArtifactStore with pygit2

* Status: accepted
* Date: 2026-11-20

## Decision
One bare Git repository per tenant under `DP_AC_DATA_DIR/artifacts/<tenant_id>.git`.
Path per subject `subjects/<subject_id>/{data.json,factsheet.md}`; one commit per version on
`refs/heads/main`; lookup ref `refs/dpac/versions/<subject_id>/<n>`; snapshot tags
`refs/tags/snapshots/<cycle_id>/<name>` pointing to a commit containing a `snapshot.json`
manifest. Optimistic locking is checked under a per-tenant lock (process-local; a file lock is
used for multi-process deployments).

## Alternatives
GitPython / git CLI (subprocess, slower, harder to test); DB-only store (no human-readable
history). pygit2 binds libgit2 (GPL with linking exception) and needs no git binary.

## Exit path
`ArtifactStore` interface + contract tests (`tests/contract/test_artifact_store.py`) run
against any implementation; a DB-backed store can be added without touching domain code.
