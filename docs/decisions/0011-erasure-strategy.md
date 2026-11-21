# ADR-0011: Personal-data erasure despite immutable versioning

* Status: accepted
* Date: 2026-11-20

## Decision
1. Artifacts and projections reference users only by `user_id` (contributor, sources).
2. `dpac admin erase-user` pseudonymizes the user row (name → "erased user", email/external
   identities removed), deletes credentials, auth sessions and connections, erases the content
   of the user's private attachments (metadata stays in the audit log), and records an audit
   event. Data files stay intact because they only hold IDs.
3. Personal data in free text (factsheets, data values such as owner names) must be captured as
   user references (`user:<id>`) where the schema allows. If personal data still lands in an
   artifact, a *redaction version* removes it from the latest state; if erasure from history is
   legally required, an operator rewrites the tenant repository history (tooling `dpac admin erase-text`
   is planned for phase 2)
   (audited, re-hashes subsequent versions, projection rebuilt). Snapshots are re-pointed.
4. Conversation messages of an erased user are replaced by a tombstone.

## Consequences
Immutability is the default; history rewrite is an exceptional operator action.
