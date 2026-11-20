# ADR-0012: Frontend — React + TypeScript + Vite, TanStack Query, shadcn/ui, i18next

* Status: accepted
* Date: 2026-11-20

## Decision
Vite SPA in `frontend/`. Typed client generated from the OpenAPI contract with
`openapi-typescript` and called through `openapi-fetch`. shadcn/ui components are vendored in
`src/components/ui` (Tailwind CSS + Radix primitives), as shadcn intends. `react-i18next` with
de/fr/it/en resources. Monaco diff editor (`@monaco-editor/react`) configured to load the
bundled `monaco-editor` package (no CDN). Playwright for E2E.

## Exit path
Only API contract couples the frontend to the backend.
