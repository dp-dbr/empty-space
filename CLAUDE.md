# CLAUDE.md — working conventions for this repository

Read `docs/MISSION.md` (goals) and `docs/ARCHITECTURE.md` (glossary, guardrails) first.
Record every assumption or deviation as an ADR in `docs/decisions/` (copy `0000-template.md`).

## Layout
- `backend/src/dp_agentic_collector/`
  - `domain/` — pure Python (stdlib only, enforced by `tests/test_architecture.py`)
  - `extensions/` — the 13 extension-point interfaces + `Registries` + plugin loader
  - `adapters/` — the only place third-party libraries are imported (SQL, Git, LLM, MCP, …)
  - `services/` — application services; every public method takes an `ActorContext`
  - `agents/` — orchestrator, collector loop, tool runtime, AgentDefinition YAMLs
  - `api/` — FastAPI (`/api/v1`), `cli.py` — Typer (`dpac`)
- `backend/plugins/` — local plugin folders (e.g. `subject_types/application`)
- `frontend/` — React + Vite (`@dp-agentic-collector/web`)

## Commands
```bash
cd backend
uv sync                         # install
uv run pytest                   # all backend tests (incl. e2e with FakeLLM)
uv run ruff check . && uv run mypy
uv run dpac db upgrade          # alembic migrations
uv run alembic revision --autogenerate -m "..."   # new migration after schema change
uv run dpac serve               # API + worker
uv run dpac openapi > ../docs/openapi.json        # refresh the API contract
cd ../frontend
npm ci && npm run gen:api       # typed client from docs/openapi.json
npm run lint && npm run build
npm run e2e                     # Playwright (starts backend with FakeLLM)
```

## Conventions
- Names from the glossary everywhere: Cycle, CollectionJob, Subject, Conversation, Participant,
  AgentRun, User, Role, Tenant, Attachment, McpServerDefinition, UserConnection, AccessMode.
  "state machine" (not lifecycle); "auth session" for logins.
- Every entity has `tenant_id`; use `Database.uow(tenant_id)` repositories, never raw queries
  without a tenant filter.
- No LLM/MCP/tool call in request handlers — publish an outbox event (`uow.publish`).
- Artifacts: never write files directly; use `ArtifactService` → `ArtifactStore.put` with
  `based_on` (optimistic locking).
- Third-party types never cross into `domain/` or `extensions/`.
- Tests use `FakeLlm` and fixture MCP servers; no real personal data in fixtures.
- API changes: bump `/api/v2` for breaking changes; regenerate `docs/openapi.json`.

## Adding a plugin (short version, details in docs/extending.md)
1. Create `backend/plugins/<kind>/<name>/`.
2. Either a declarative `subject_type.yaml` (SubjectType folder package) or a `plugin.py`
   with `def register(registries): registries.<point>.register("name", impl)`.
3. Or ship a package exposing the entry point group `dp_agentic_collector.plugins`.
4. Add tests next to the core tests (`backend/tests/plugins/`).
