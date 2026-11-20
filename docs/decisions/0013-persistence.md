# ADR-0013: SQLAlchemy 2 + Alembic; SQLite for dev, PostgreSQL as target

* Status: accepted
* Date: 2026-11-20

## Decision
SQLAlchemy Core/ORM with portable types only (`String`, `Text`, `JSON`, `DateTime(timezone)`,
`Integer`). IDs are UUIDv4 strings. Alembic migrations in `backend/migrations/`. Repositories
are created per tenant and add `tenant_id` filters to every statement; inserting a row with a
foreign `tenant_id` raises `TenantViolation`.

## Alternatives
SQLModel (couples API and DB models), raw SQL.

## Exit path
Repository interfaces in `services/` ports; another persistence needs only new adapters.
