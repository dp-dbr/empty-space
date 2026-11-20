"""Request context: authentication (auth session cookie or bearer token), CSRF, tracing."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request

from ..container import Collector
from ..domain.errors import Unauthenticated
from ..domain.permissions import ActorContext
from ..observability import current_trace_id

SESSION_COOKIE = "dpac_session"
CSRF_COOKIE = "dpac_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_app(request: Request) -> Collector:
    return request.app.state.collector  # type: ignore[no-any-return]


@dataclass
class Ctx:
    app: Collector
    actor: ActorContext

    @property
    def s(self):  # type: ignore[no-untyped-def]
        return self.app.services


def get_actor(request: Request, app: Collector = Depends(get_app)) -> ActorContext:
    trace_id = current_trace_id()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        actor = app.services.auth.resolve_bearer(auth[7:].strip(), trace_id)
        if request.method not in SAFE_METHODS:
            actor.require_scope("dpac.write")
        else:
            actor.require_scope("dpac.read")
        return actor
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        raise Unauthenticated("login required")
    actor, csrf = app.services.auth.resolve_session(session_id, trace_id)
    if request.method not in SAFE_METHODS:
        app.services.auth.check_csrf(csrf, request.headers.get(CSRF_HEADER))
    return actor


def get_ctx(app: Collector = Depends(get_app), actor: ActorContext = Depends(get_actor)) -> Ctx:
    return Ctx(app, actor)
