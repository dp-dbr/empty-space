# Mission Statement — DP Agentic Collector

> Project kickoff: agentic information collection.
> This document is the binding mission statement for this repository. Deviations from it
> are recorded as ADRs in `docs/decisions/`.

## Goal
Build the core of a generic, extensible agent framework in this repo:
A central orchestrator agent dispatches collection jobs to people in an organization.
For each job, a collector agent holds a conversation with the person, gathers information
about a subject, and writes two versioned artifacts:
a factsheet (Markdown) and a data file (JSON/YAML, schema-validated).

Knowledge is decentralized: it stays in the sources where it lives. Agents access these
sources via MCP, strictly with the authorization of the authenticated user they act for,
in read or read/write mode. Users can also upload files into the conversation.

The core consists of: orchestration, collection jobs, conversations, attachments,
artifact generation, validation, versioning, user management and authentication,
delegated access to decentralized knowledge via MCP, a web frontend and an HTTP API.
No integrations with specific external platforms; those come later as plugins or
MCP server configurations.

Repository: empty-space (working name; independent of package names).
Name: DP Agentic Collector (referred to as "the Collector" below).
Technical names: Python package dp_agentic_collector (distribution dp-agentic-collector),
CLI command "dpac", env vars DP_AC_*, frontend package @dp-agentic-collector/web.

Work autonomously. When something is unclear, make a reasoned assumption and record it as an
ADR in docs/decisions/ instead of stopping.

Terminology: maintain a glossary in docs/ARCHITECTURE.md (Cycle, CollectionJob, Subject,
conversation, participant, agent run, user, role, tenant, attachment, MCP server definition,
user connection, access mode) and keep names consistent across code, docs and tests.
Use "Cycle" only for the domain concept below; use "state machine" (not "lifecycle")
for job and artifact states; use "auth session" for login sessions.

## Architectural guardrails (protect future change)
- **Domain over technology**: domain concepts (version, draft, approve, snapshot) never leak
  storage, queue, LLM or SDK specifics. Every infrastructure choice sits behind an interface
  and can be replaced without touching domain code.
- **Multi-tenant ready**: every entity carries tenant_id from day one, enforced in queries,
  permissions, stores and connections, even though the MVP runs with a single tenant.
- **Asynchronous by design**: agent work runs through an event outbox and a worker/queue
  interface (in-process in the MVP, swappable for an external queue). No LLM or tool calls
  inside HTTP request handling.
- **Isolation of extensions**: MCP is the preferred mechanism for connectors and tools
  (out-of-process, any language). In-process Python plugins are reserved for trusted
  extensions (SubjectTypes, validators, file processors).
- **Open-ended models**: conversations have a list of participants (users and agents);
  users have external identities (for later OIDC/SSO); locale is supported on users,
  templates and factsheets (DE/FR/IT/EN).
- **Versioned contracts**: API, JSON Schemas, AgentDefinitions and plugin interfaces carry
  versions; breaking changes require a new version.
- **Privacy by design**: artifacts reference users by ID, not by personal data; a documented
  erasure strategy exists for personal data despite immutable versioning.
- **Observable**: a correlation/trace ID (OpenTelemetry) flows through API → orchestrator →
  agent → tool/MCP calls; structured logs.
- **Provider-neutral AI**: an internal message and tool-call format; LLM providers and the MCP
  SDK are adapters.

## Build vs. use, vendor neutrality
Principle: build what differentiates the Collector; use widely adopted open-source libraries
for everything else. Stay vendor-neutral: every infrastructure dependency is reached through an
open standard or protocol and sits behind our own interface. Third-party types never appear
in domain code; libraries are used only inside adapters.

Build ourselves:
- Domain model and state machines (Cycle, CollectionJob, Subject, Conversation)
- Versioning semantics (artifact pairs, based_on, optimistic locking, snapshots, provenance)
- Orchestrator, job control, reminders/escalation, consolidation
- Permission evaluation (thin layer computing the effective-permission intersection)
- Agent behaviour (conversation guides, proposals, confirmations, schema extraction)
- Plugin system, registries, event outbox
- Chat UI on top of standard components

Use libraries / open standards:
- LLM: provider-neutral adapter layer with an internal message and tool-call format.
  Ship at least two adapters in the MVP: Anthropic and an OpenAI-compatible adapter
  (covers OpenAI, Azure OpenAI, Mistral, local models via Ollama/vLLM), plus FakeLLM.
  No heavy agent frameworks that impose their own state model (no LangChain/LangGraph).
- MCP: official MCP Python SDK
- Auth: OAuth 2.1 / OIDC via Authlib (any compliant IdP), argon2-cffi, cryptography.
  Never implement crypto ourselves.
- Persistence: SQLAlchemy + Alembic (PostgreSQL as target, SQLite for dev), pygit2,
  object storage via the S3-compatible API.
- Validation, templating, diffs: jsonschema, Jinja2, deepdiff, difflib
- File processing: pypdf, python-docx, openpyxl (Docling optional)
- API/CLI: FastAPI, Typer
- Frontend: React, TanStack Query, shadcn/ui, react-i18next, Monaco diff view
- Observability: OpenTelemetry with OTLP export (any backend)

Build now, swappable later (interface first):
- TaskQueue: in-process outbox → later Temporal, Celery or similar
- CredentialVault: encrypted in DB → later HashiCorp Vault, Azure Key Vault, AWS Secrets Manager
- IdentityProvider: local users → later any OIDC provider (Entra ID, Keycloak, Okta, ...)
- AttachmentStore: local filesystem → later any S3-compatible object storage

Deployment: OCI containers and docker-compose; no cloud-provider-specific services
in the core. Record every dependency choice with a short ADR (why, alternatives, exit path).

## Guiding principle: extensibility
The core knows no concrete subject types, channels or data sources. All domain specifics
are added via plugins and declarative configuration. Every extension must be possible without
modifying core code.

Extension points (each with a clear, versioned interface + registry + example implementation):
1. **SubjectType**: JSON Schema for the data file, factsheet template (Jinja2, localizable),
   business rules and a conversation guide. Deployable as a folder package.
2. **ChannelAdapter**: send/receive messages to/from people. MVP: web channel (primary) and CLI.
3. **DataSourceConnector**: prefill data for agents, exposed as tools. MVP: file-based,
   previous versions from the ArtifactStore, and the generic MCP connector.
4. **ArtifactStore**: versioned storage of artifact pairs. MVP: Git-based implementation.
5. **Projection**: project data files into a query store. MVP: relational DB.
6. **ValidationRule**: plausibility checks beyond the schema, per SubjectType.
7. **LLMProvider**: model adapters (Anthropic, OpenAI-compatible, FakeLLM for tests);
   provider and model selected per AgentDefinition via configuration.
8. **AgentDefinition**: agents declared in YAML (role, system prompt, model/provider,
   allowed tools incl. MCP tools, referenced SubjectTypes, escalation rules).
9. **IdentityProvider**: MVP local users; OIDC-ready.
10. **CredentialVault**: per-user credentials/tokens. MVP: encrypted in the DB.
11. **AttachmentStore**: content-addressed binary storage. MVP: local filesystem;
    S3-compatible ready. Binaries never go into the ArtifactStore.
12. **FileProcessor**: content extraction per MIME type (PDF, DOCX, XLSX, CSV, Markdown;
    images passed to the LLM where supported).
13. **TaskQueue**: event outbox + workers. MVP: in-process.

Plugins are loaded via Python entry points OR a local plugins/ directory.

## Domain model (mandatory; every entity has tenant_id)
- **Tenant**: tenant_id, name, settings.
- **User**: user_id, name, email, locale, roles, active flag, external identities.
- **Role / permissions**: RBAC with at least admin, cycle_owner, respondent, viewer;
  plus subject-level access.
- **Cycle**: a named collection round (e.g. "app-inventory-2026-Q4") with scope, owner,
  participants and deadlines; one-off or recurring; a recurring cycle can be initialized from
  the approved state of the previous one.
- **Subject**: subject_id, subject_type, owner(s), cycle.
- **CollectionJob**: job_id, subject_id, cycle, assignee, due_date, channel, prefill;
  state machine: created → notified → in_progress → submitted → closed, plus overdue →
  escalated. State is persisted; agents are woken by events (reply, deadline, follow-up).
- **Conversation**: within a job; participants (users and agents); persisted and resumable.
- **Attachment**: attachment_id, content_hash, filename, mime_type, size, uploaded_by,
  uploaded_at, conversation/job/subject, visibility (private | shared),
  status (active | withdrawn | deleted). Immutable.
- **ArtifactVersion**: always a pair of factsheet + data file, stored atomically.
- **McpServerDefinition** (admin-managed): transport (stdio | streamable HTTP), endpoint,
  auth type (none | OAuth 2.1 | API key), max access mode (read | read_write),
  tool classification (read vs write).
- **UserConnection** (per user): server, granted access mode, credential reference,
  status (active | expired | revoked), connected_at, last_used_at.

Shared header, identical in both artifact files:
  tenant_id, subject_id, cycle, version, based_on, schema_version, locale,
  status (draft|submitted|validated|approved), contributor (user_id + agent_run_id),
  created, content_hash, change_reason, sources

`sources` records the provenance of every externally obtained fact: MCP server/tool/reference
or attachment (id + hash + location), retrieved_at, on behalf of user_id.

## Versioning (core requirement)
- Every change creates a new version; factsheet and data file are versioned together as one
  atomic pair. No unversioned writes.
- Submitted versions are immutable; corrections create a new version with based_on.
- Every version records who, when, why, from which previous version and from which sources.
- Optimistic locking: if based_on is outdated, never overwrite silently; the agent shows the
  differences and has the user resolve them.
- Structured facts live only in the data file; factsheet tables are rendered from it. The
  factsheet adds context, rationale, sources, open points and an auto-generated summary of
  changes since the previous version.
- Snapshots per cycle; changes can be reported against any snapshot, incl. the previous cycle.
- Two-stage validation (schema, then ValidationRules) → validated; approval by a cycle owner
  in the UI → approved.
- schema_version + migrations keep old versions readable.
- ArtifactStore capabilities (with tests): get(subject, version | latest | snapshot),
  history, field-level diff of data files + text diff of factsheets, as_of(cycle, timestamp),
  integrity check via content_hash.
- Git implementation detail (not domain): path per subject, commit per version, refs/tags
  for drafts and snapshots. Must be replaceable by a DB-based store.
- The projection keeps all versions for history queries.

## Authentication and authorization
- Every action (UI, API, CLI, MCP, agent, worker) runs in the context of an authenticated
  user within a tenant. Agents never have their own privileges.
- Effective permission = RBAC/subject permission ∩ admin policy ∩ user grant ∩
  AgentDefinition allowlist ∩ attachment visibility. Enforced in the core.
- Background actions (reminders, scheduled prefill) use refreshed user tokens and never
  run with expired or elevated rights.

## Decentralized knowledge via MCP (both directions)
MCP client:
- Admins register MCP server definitions; users connect their own accounts in the UI
  (OAuth 2.1 per the MCP authorization spec, or API key). Tokens per user in the
  CredentialVault, auto-refreshed, revocable.
- An agent uses only the connections of the user it acts for; no cross-user access.
- Access mode per connection: read (default) or read_write within the admin maximum.
  Tools classified read/write (from MCP annotations, admin-overridable; unknown = write).
- Every write call requires explicit user confirmation showing what is written where.
- Knowledge stays at the source: no bulk copying or central index; only facts that land in
  a data file are stored, with provenance.
- All tool calls audited; expired/revoked connections degrade gracefully.

MCP server:
- The Collector exposes cycles, jobs, subjects, factsheets, data files, shared attachments
  (plus the caller's own private ones), history, diffs and snapshots; with write scope it can
  start cycles or submit data. OAuth 2.1 with read/write scopes; same permission filtering
  as the API.

Use the official MCP Python SDK behind an adapter. Test fixtures: read-only server,
server with write tools, server with OAuth.

## Attachments in conversations
- Upload in the chat window (drag & drop, multiple files); each file becomes an Attachment
  linked to conversation, job and subject and is available to the agent as context.
- Visibility chosen at upload, default private:
  private = only uploader and agents acting for them; shared = everyone with read access to
  the subject.
- Uploader can switch private → shared, withdraw shared attachments, and delete own private
  ones (content erased, metadata kept in the audit log). Shared retention follows the Cycle.
- Facts extracted from a private attachment into the data file require user confirmation
  that they become visible; provenance records the hash, not the file.
- Limits: configurable max size and MIME allowlist, filename sanitization, safe content
  serving, malware-scan hook (no-op in MVP).

## Web frontend and API
- HTTP API: FastAPI, versioned (/api/v1), OpenAPI as the contract; thin layer over the core.
  The core stays usable as a library and via the CLI.
- Frontend in frontend/ (React + TypeScript + Vite, typed client generated from OpenAPI,
  i18n-ready).
- Respondent: "My jobs" inbox; conversation view with prefill proposals, structured fact
  confirmation, write confirmations, sources per fact, file upload with private/shared
  toggle; own submissions and versions; "My connections" with access modes and own audit log.
- Cycle owner: manage cycles and jobs, status monitoring, consolidation report, subject view
  with factsheet, shared attachments, history, diffs, snapshot comparison, approval.
- Admin: tenants (basic), users and roles, MCP server definitions and policies, LLM provider
  configuration, plugin overview, audit log.
- Responsive and accessible (WCAG AA basics); clean, consistent component library.

## Tech stack (defaults; deviate only with an ADR)
Backend: Python 3.12, uv, Pydantic v2, FastAPI, SQLAlchemy 2 + Alembic (SQLite for dev,
PostgreSQL as target; no DB-specific features in domain code), Jinja2, jsonschema, deepdiff,
pygit2, MCP Python SDK, Typer, argon2-cffi, Authlib, cryptography, OpenTelemetry,
pypdf, python-docx, openpyxl, python-magic.
LLM: provider-neutral adapters (Anthropic + OpenAI-compatible in the MVP, FakeLLM for tests);
model and provider selected per AgentDefinition via configuration.
Frontend: React, TypeScript, Vite, TanStack Query, shadcn/ui, react-i18next, Monaco diff,
Playwright.
Tooling: pytest, ruff, mypy, eslint, docker-compose. Config via .env (.env.example only,
no secrets in the repo).

## Milestones (in order; each ends green with tests and a commit)
1. Repo scaffold (backend/, frontend/, docs/), CI, docker-compose.
2. Core interfaces + registries for all 13 extension points; domain model with tenant_id;
   DB migrations; TaskQueue with outbox; tracing.
3. ArtifactStore (Git implementation) with full versioning + projection.
4. Users, tenants, auth, RBAC + subject permissions, CredentialVault; HTTP API.
5. LLM adapters (Anthropic, OpenAI-compatible, FakeLLM); orchestrator and collector agent
   (async via TaskQueue, always on behalf of a user).
6. Example SubjectType "application" (name, business owner, technical owner, number of
   users, criticality, technologies, interfaces; rules: criticality "high" requires a
   technical owner; interfaces must reference existing applications).
7. Attachments: AttachmentStore, FileProcessors, visibility model.
8. MCP client (per-user connections, access modes, write confirmation, provenance) and
   MCP server (user-scoped OAuth).
9. Web frontend (respondent incl. chat attachments and connections, cycle owner, admin).
10. Consolidation: gaps and contradictions across subjects of a Cycle.
11. End-to-end tests with FakeLLM:
    - 2 users, version conflict and resolution, approval, snapshot comparison, second Cycle
      initialized from the first;
    - users with different MCP connections see only their own data; read-only blocks writes;
      writes need confirmation; revoked connection degrades gracefully; correct provenance;
    - private attachment invisible to others via UI, API and MCP; extraction from private
      attachment requires confirmation; visibility change and withdrawal work;
    - external MCP client sees only its user's data;
    - data of tenant A is never visible in tenant B;
    - the same agent runs unchanged with both LLM adapters (contract tests);
    - Playwright flow through the UI incl. connecting a source and uploading a file.

Out of scope: integrations with specific external platforms (interfaces and MCP
configuration only).

## Cross-cutting concerns
- Audit trail for every user, agent, tool and attachment action.
- Security: CSRF protection, input validation, login rate limiting, encryption at rest for
  tokens, no secrets or tokens in logs, least privilege by default.
- Data protection (Swiss FADP/DSG): minimal personal data, documented erasure strategy,
  no real data in tests/fixtures.

## Approach
1. First write docs/ARCHITECTURE.md (glossary, guardrails, build vs. use, components,
   extension points, versioning, permission and delegated MCP access model, attachment
   visibility, sequence diagrams in Mermaid) and CLAUDE.md (conventions, commands,
   how to add a plugin).
2. Implement milestone by milestone; run tests and commit after each step.
3. docs/extending.md: how to add a SubjectType, channel, connector, MCP server,
   identity provider, LLM provider, file processor and storage backend.
4. Open a PR with a summary, open points and a proposal for phase 2.
