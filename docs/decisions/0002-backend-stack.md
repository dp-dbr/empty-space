# ADR-0002: Backend stack — Python 3.12, uv, FastAPI, Typer, Pydantic v2 (edges only)

* Status: accepted
* Date: 2026-11-20

## Decision
Python 3.12 managed with `uv` (lockfile `uv.lock`). FastAPI for HTTP, Typer for the CLI.
Pydantic v2 is used for API schemas and settings only. **Domain entities are stdlib
`dataclasses`** so that the domain imports no third-party types (guardrail "domain over
technology"); an architecture test enforces the import rule.

## Alternatives
Pydantic models in the domain (less mapping code, but couples domain to a library);
Litestar/Flask (smaller ecosystem for OpenAPI-first).

## Exit path
API and CLI are thin; replacing FastAPI/Typer touches only `api/` and `cli.py`.
