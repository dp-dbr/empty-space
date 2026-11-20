"""FastAPI application: versioned HTTP API (/api/v1), OAuth endpoints, MCP server mount."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..container import Collector
from ..domain.errors import DomainError
from ..observability import configure, current_trace_id, span

log = logging.getLogger(__name__)
API_VERSION = "1.0.0"


def create_app(collector: Collector | None = None, *, start_worker: bool | None = None) -> FastAPI:
    collector = collector or Collector.create()
    configure(collector.settings.otlp_endpoint, collector.settings.log_json)
    run_worker = collector.settings.run_worker if start_worker is None else start_worker
    mcp_app = None
    try:
        from ..adapters.mcp_server import build_mcp_http_app

        mcp_app = build_mcp_http_app(collector)
    except ModuleNotFoundError:
        mcp_app = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        if run_worker:
            collector.queue.start()
        if mcp_app is not None and hasattr(mcp_app, "router") and getattr(mcp_app.router, "lifespan_context", None):
            async with mcp_app.router.lifespan_context(mcp_app):
                yield
        else:
            yield
        if run_worker:
            collector.queue.stop()

    app = FastAPI(title="DP Agentic Collector API", version=API_VERSION, lifespan=lifespan,
                  openapi_url="/api/v1/openapi.json", docs_url="/api/v1/docs")
    app.state.collector = collector

    @app.middleware("http")
    async def tracing(request: Request, call_next: Any) -> Any:
        with span("api.request", method=request.method, path=request.url.path):
            response = await call_next(request)
            response.headers["X-Trace-Id"] = current_trace_id() or ""
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers.setdefault("Referrer-Policy", "same-origin")
            return response

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status,
                            content={"code": exc.code, "message": exc.message, "details": _jsonable(exc.details)})

    from . import routes_admin, routes_auth

    app.include_router(routes_auth.router, prefix="/api/v1")
    app.include_router(routes_admin.router, prefix="/api/v1")
    for name in ("routes_cycles", "routes_jobs", "routes_attachments", "routes_connections", "routes_oauth"):
        try:
            module = __import__(f"{__package__}.{name}", fromlist=["router"])
        except ModuleNotFoundError:
            continue
        prefix = "" if name == "routes_oauth" else "/api/v1"
        app.include_router(module.router, prefix=prefix)

    @app.get("/api/v1/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": API_VERSION}

    if mcp_app is not None:
        app.mount("/mcp", mcp_app)
    return app


def _jsonable(v: Any) -> Any:
    from ..domain.serialization import to_jsonable

    return to_jsonable(v)
