# DP Agentic Collector

Agentic information collection: an orchestrator dispatches collection jobs; a collector agent
talks to each person, gathers facts about a subject (also from the person's own MCP-connected
sources and uploaded files) and writes versioned artifact pairs (factsheet + data file).

* Mission: [docs/MISSION.md](docs/MISSION.md)
* Architecture & glossary: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
* Extending (plugins, connectors, providers): [docs/extending.md](docs/extending.md)
* Decisions: [docs/decisions/](docs/decisions/)

## Quick start (dev)

```bash
cd backend && uv sync
uv run dpac db upgrade
uv run dpac demo seed          # demo tenant, users, MCP fixture servers (FakeLLM)
uv run dpac serve              # API + in-process worker on :8000
cd ../frontend && npm ci && npm run dev   # UI on :5173
```

Docker: `cp .env.example .env && docker compose up --build`.
