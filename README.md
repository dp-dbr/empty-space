# DP Agentic Collector

Agentic information collection: an orchestrator dispatches collection jobs to people; a collector
agent talks to each person, gathers facts about a subject — also from the person's own
MCP-connected sources and uploaded files — and writes versioned artifact pairs
(Markdown factsheet + schema-validated JSON data file). Knowledge stays at its source; agents act
strictly with the rights of the user they work for.

* Mission: [docs/MISSION.md](docs/MISSION.md)
* Architecture, glossary, guardrails: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
* Extending (SubjectTypes, channels, connectors, MCP servers, IdPs, LLM providers, file processors, storage): [docs/extending.md](docs/extending.md)
* Decisions (ADRs): [docs/decisions/](docs/decisions/)
* HTTP API contract: [docs/openapi.json](docs/openapi.json) (`/api/v1/docs` when running)

## Try it (demo with fixture MCP servers and the deterministic FakeLlm)

```bash
cd frontend && npm ci && npm run build && cd ..
cd backend && uv sync
uv run python -m dp_agentic_collector.testing.e2e_server --port 8765 --web-dist ../frontend/dist
# open http://127.0.0.1:8765 — users: alice@ / bob@ / owner@ / admin@ / viewer@example.test, password "demo-password"
# demo API keys for the "Asset Registry" source: key-alice, key-bob
```

## Development

```bash
cp .env.example .env                       # FakeLlm by default; set DP_AC_ANTHROPIC_API_KEY etc. for real models
cd backend && uv sync && uv run dpac db upgrade
uv run dpac demo seed                      # demo tenant "default", users with password "demo-password"
uv run dpac serve                          # API + worker on :8000
cd ../frontend && npm ci && npm run dev    # UI on :5173 (proxies /api, /mcp, /oauth)
```

Tests: `cd backend && uv run pytest` (SQLite; set `DP_AC_TEST_DATABASE_URL` for PostgreSQL),
`cd frontend && npm run e2e` (Playwright, starts the demo server). Lint: `uv run ruff check . && uv run mypy`,
`npm run lint`.

## Docker

```bash
cp .env.example .env && docker compose up --build    # PostgreSQL, API, worker, web on :8080
```

## Repository layout

```
backend/   Python package dp_agentic_collector (CLI "dpac"), plugins/, migrations/, tests/
frontend/  @dp-agentic-collector/web (React, Vite, TanStack Query, shadcn/ui, i18next, Monaco), e2e/
docs/      mission, architecture, extending guide, ADRs, OpenAPI contract
```
