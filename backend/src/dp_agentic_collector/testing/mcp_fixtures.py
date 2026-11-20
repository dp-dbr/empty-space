"""MCP fixture servers for tests and demos (no real data).

* ``/registry/mcp`` — read-only "asset registry", API-key auth; data depends on the caller's key.
* ``/tickets/mcp`` — server with read and write tools, OAuth 2.1 (authorization code + PKCE)
  via the fixture authorization server at ``/as``.
* ``/open/mcp`` — no auth, read-only.

Run standalone: ``python -m dp_agentic_collector.testing.mcp_fixtures --port 9100``.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import secrets
import socket
import threading
import time
from typing import Any
from urllib.parse import urlencode

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Mount, Route

REGISTRY_DATA = {
    "key-alice": {"crm": {"number_of_users": 1200, "technologies": ["java", "postgres"]},
                  "erp": {"number_of_users": 800, "technologies": ["abap"]}},
    "key-bob": {"crm": {"number_of_users": 5, "technologies": ["excel"]}},
}


class FixtureState:
    def __init__(self) -> None:
        self.codes: dict[str, dict[str, Any]] = {}
        self.tokens: dict[str, dict[str, Any]] = {}
        self.tickets: list[dict[str, Any]] = []
        self.revoked: set[str] = set()
        self.counter = 0


def build_fixture_app(base_url: str) -> tuple[Starlette, FixtureState]:
    state = FixtureState()
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

    class KeyVerifier:
        async def verify_token(self, token: str) -> AccessToken | None:
            if token in REGISTRY_DATA:
                return AccessToken(token=token, client_id="registry", scopes=["read"], subject=token,
                                   resource=f"{base_url}/registry/mcp")
            return None

    class OAuthVerifier:
        async def verify_token(self, token: str) -> AccessToken | None:
            t = state.tokens.get(token)
            if not t or token in state.revoked or t["expires_at"] < time.time():
                return None
            return AccessToken(token=token, client_id=t["client_id"], scopes=["tickets"], subject=t["subject"],
                               resource=f"{base_url}/tickets/mcp")

    registry = MCPServer("asset-registry", token_verifier=KeyVerifier(), auth=AuthSettings(
        issuer_url=base_url, resource_server_url=f"{base_url}/registry/mcp", validate_token_resource=True))

    @registry.tool(annotations=ToolAnnotations(read_only_hint=True))
    def lookup_application(query: str) -> dict[str, Any]:
        """Look up an application in the asset registry (data visible to your account only)."""
        tok = get_access_token()
        data = REGISTRY_DATA.get(tok.subject if tok else "", {})
        q = query.lower()
        for key, facts in data.items():
            if key in q:
                return {"application": key, "facts": facts}
        if data:
            key = next(iter(data))
            return {"application": key, "facts": data[key]}
        return {"application": None, "facts": {}}

    tickets = MCPServer("tickets", token_verifier=OAuthVerifier(), auth=AuthSettings(
        issuer_url=f"{base_url}/as", resource_server_url=f"{base_url}/tickets/mcp", validate_token_resource=True))

    @tickets.tool(annotations=ToolAnnotations(read_only_hint=True))
    def search_tickets(query: str) -> dict[str, Any]:
        """Search tickets of your account."""
        tok = get_access_token()
        mine = [t for t in state.tickets if tok and t["subject"] == tok.subject]
        return {"tickets": mine, "facts": {}}

    @tickets.tool()
    def create_ticket(record: str) -> dict[str, Any]:
        """Create a ticket (write)."""
        tok = get_access_token()
        ticket = {"id": len(state.tickets) + 1, "record": record, "subject": tok.subject if tok else None}
        state.tickets.append(ticket)
        return {"created": ticket["id"]}

    open_srv = MCPServer("open-catalog")

    @open_srv.tool(annotations=ToolAnnotations(read_only_hint=True))
    def catalog(query: str) -> dict[str, Any]:
        """Public technology catalog."""
        return {"facts": {"technologies": ["kubernetes"]}}

    # --- fixture OAuth authorization server ----------------------------------------------
    async def authorize(request: Request) -> RedirectResponse:
        p = request.query_params
        state.counter += 1
        code = secrets.token_urlsafe(16)
        state.codes[code] = {"client_id": p["client_id"], "redirect_uri": p["redirect_uri"],
                             "challenge": p["code_challenge"], "subject": p.get("login_hint") or f"acct_{state.counter}"}
        return RedirectResponse(f"{p['redirect_uri']}?{urlencode({'code': code, 'state': p.get('state', '')})}")

    async def token(request: Request) -> JSONResponse:
        form = await request.form()
        if form.get("grant_type") == "refresh_token":
            old = next((t for t in state.tokens.values() if t.get("refresh") == form.get("refresh_token")), None)
            if old is None:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return JSONResponse(_issue(state, old["client_id"], old["subject"]))
        c = state.codes.pop(str(form.get("code")), None)
        verifier = str(form.get("code_verifier", ""))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if c is None or challenge != c["challenge"]:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse(_issue(state, c["client_id"], c["subject"]))

    reg_app = registry.streamable_http_app(streamable_http_path="/mcp", transport_security=security,
                                           stateless_http=True, json_response=True)
    tik_app = tickets.streamable_http_app(streamable_http_path="/mcp", transport_security=security,
                                          stateless_http=True, json_response=True)
    open_app = open_srv.streamable_http_app(streamable_http_path="/mcp", transport_security=security,
                                            stateless_http=True, json_response=True)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):  # type: ignore[no-untyped-def]
        async with contextlib.AsyncExitStack() as stack:
            for sub in (reg_app, tik_app, open_app):
                await stack.enter_async_context(sub.router.lifespan_context(sub))
            yield

    app = Starlette(routes=[
        Route("/as/authorize", authorize), Route("/as/token", token, methods=["POST"]),
        Mount("/registry", app=reg_app), Mount("/tickets", app=tik_app), Mount("/open", app=open_app),
    ], lifespan=lifespan)
    return app, state


def _issue(state: FixtureState, client_id: str, subject: str, ttl: int = 3600) -> dict[str, Any]:
    access, refresh = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    state.tokens[access] = {"client_id": client_id, "subject": subject, "expires_at": time.time() + ttl,
                            "refresh": refresh}
    return {"access_token": access, "token_type": "Bearer", "expires_in": ttl, "refresh_token": refresh}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class RunningFixture:
    """Runs the fixture app with uvicorn in a background thread."""

    def __init__(self, port: int | None = None) -> None:
        import uvicorn

        self.port = port or free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.app, self.state = build_fixture_app(self.base_url)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> RunningFixture:
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


if __name__ == "__main__":  # pragma: no cover
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9100)
    args = ap.parse_args()
    fixture_app, _ = build_fixture_app(f"http://127.0.0.1:{args.port}")
    uvicorn.run(fixture_app, host="127.0.0.1", port=args.port)
