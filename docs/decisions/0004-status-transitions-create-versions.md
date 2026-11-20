# ADR-0004: Artifact status transitions create new versions; content_hash excludes the header

* Status: accepted
* Date: 2026-11-20

## Context
`status` is part of the header, which is part of both immutable files. Validation and approval
change the status.

## Decision
`submitted → validated → approved` each create a new version with identical content and a new
header (`based_on` = previous, `change_reason` = "validated" / "approved by <user_id>").
`content_hash` = sha256(canonical JSON of data + "\n" + factsheet body), header excluded, so
status versions share the content hash and integrity checks stay meaningful.
Drafts are versions too (`status=draft`); there are no unversioned writes. Working state during
a conversation (fact proposals) is conversation state, not artifact content.

## Consequences
History shows status steps explicitly (auditable). Slightly more versions per Subject.
