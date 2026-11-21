# ADR-0020: Optional SPA serving by the API process

* Status: accepted
* Date: 2026-11-21

## Decision
docker-compose serves the frontend via nginx (proxying `/api`, `/mcp`, `/oauth`, `/.well-known`).
For single-process deployments and Playwright E2E, the API can serve the built SPA itself when
`DP_AC_WEB_DIST` points to `frontend/dist` (static assets + index.html fallback; API prefixes are
never shadowed). Both keep the browser on one origin, which the auth-session cookie and CSRF
double-submit rely on.
