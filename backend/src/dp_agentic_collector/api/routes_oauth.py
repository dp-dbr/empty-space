"""OAuth 2.1 authorization server endpoints for API/MCP clients (ADR-0010) and discovery metadata."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from ..container import Collector
from ..domain.errors import DomainError
from .deps import SESSION_COOKIE, Ctx, get_app, get_ctx

router = APIRouter(tags=["oauth"])


@router.get("/.well-known/oauth-authorization-server")
def as_metadata(app: Collector = Depends(get_app)) -> dict[str, Any]:
    base = app.settings.public_base_url.rstrip("/")
    return {"issuer": base, "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token", "registration_endpoint": f"{base}/oauth/register",
            "revocation_endpoint": f"{base}/oauth/revoke", "scopes_supported": ["dpac.read", "dpac.write"],
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"]}


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def resource_metadata(app: Collector = Depends(get_app)) -> dict[str, Any]:
    base = app.settings.public_base_url.rstrip("/")
    return {"resource": f"{base}/mcp", "authorization_servers": [base], "scopes_supported": ["dpac.read", "dpac.write"],
            "bearer_methods_supported": ["header"]}


@router.post("/oauth/register", status_code=201)
async def register(request: Request, app: Collector = Depends(get_app)) -> dict[str, Any]:
    body = await request.json()
    tenant = request.query_params.get("tenant", app.settings.default_tenant)
    cid = app.services.auth.register_client_dynamic(tenant, body.get("client_name", "mcp-client"),
                                                    list(body.get("redirect_uris", [])))
    return {"client_id": cid, "redirect_uris": body.get("redirect_uris", []), "token_endpoint_auth_method": "none"}


@router.get("/oauth/authorize", include_in_schema=False)
def authorize_redirect(request: Request) -> RedirectResponse:
    """Browser entry: send the user to the SPA (login if needed, then consent)."""
    params = urlencode(dict(request.query_params))
    if not request.cookies.get(SESSION_COOKIE):
        return RedirectResponse(f"/login?{urlencode({'next': f'/oauth/consent?{params}'})}", status_code=303)
    return RedirectResponse(f"/oauth/consent?{params}", status_code=303)


class AuthorizeIn(BaseModel):
    client_id: str
    redirect_uri: str
    scope: str = "dpac.read"
    code_challenge: str
    code_challenge_method: str = "S256"
    state: str | None = None


@router.post("/api/v1/oauth/authorize")
def authorize(body: AuthorizeIn, ctx: Ctx = Depends(get_ctx)) -> dict[str, str]:
    """Consent given by the logged-in user; returns the redirect URL carrying the code."""
    url = ctx.s.auth.authorize(ctx.actor, body.client_id, body.redirect_uri, body.scope, body.code_challenge,
                               body.code_challenge_method, body.state)
    return {"redirect_url": url}


@router.get("/api/v1/oauth/clients/{client_id}")
def client_info(client_id: str, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    c = ctx.s.auth.client(client_id)
    return {"client_id": c["client_id"], "name": c["name"]}


@router.post("/oauth/token")
def token(grant_type: str = Form(...), client_id: str = Form(...), code: str | None = Form(None),
          redirect_uri: str | None = Form(None), code_verifier: str | None = Form(None),
          refresh_token: str | None = Form(None), app: Collector = Depends(get_app)) -> JSONResponse:
    try:
        if grant_type == "authorization_code" and code and redirect_uri and code_verifier:
            t = app.services.auth.exchange_code(client_id, code, redirect_uri, code_verifier)
        elif grant_type == "refresh_token" and refresh_token:
            t = app.services.auth.refresh(client_id, refresh_token)
        else:
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    except DomainError:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    return JSONResponse({"access_token": t.access_token, "token_type": t.token_type, "expires_in": t.expires_in,
                         "refresh_token": t.refresh_token, "scope": t.scope}, headers={"Cache-Control": "no-store"})


@router.post("/oauth/revoke")
def revoke(token: str = Form(...), app: Collector = Depends(get_app)) -> dict[str, str]:
    app.services.auth.revoke_token(token)
    return {"status": "ok"}
