"""OAuth 2.1 client flows for user connections to external MCP servers (Authlib)."""

from __future__ import annotations

import secrets
import time
from collections.abc import Mapping
from typing import Any

from authlib.common.security import generate_token
from authlib.integrations.httpx_client import OAuth2Client
from authlib.oauth2.rfc7636 import create_s256_code_challenge

from ..domain.errors import ConnectionUnavailable, ValidationFailed


def authorization_request(oauth: Mapping[str, Any], redirect_uri: str) -> dict[str, str]:
    """Build an authorization URL with PKCE (S256). Returns url, state and code_verifier."""
    for key in ("authorize_url", "token_url", "client_id"):
        if not oauth.get(key):
            raise ValidationFailed(f"MCP server OAuth config lacks {key}")
    verifier = generate_token(64)
    state = secrets.token_urlsafe(24)
    client = OAuth2Client(oauth["client_id"], redirect_uri=redirect_uri, scope=oauth.get("scope", ""))
    url, _ = client.create_authorization_url(oauth["authorize_url"], state=state,
                                             code_challenge=create_s256_code_challenge(verifier),
                                             code_challenge_method="S256",
                                             **({"resource": oauth["resource"]} if oauth.get("resource") else {}))
    return {"url": url, "state": state, "code_verifier": verifier}


def exchange_code(oauth: Mapping[str, Any], redirect_uri: str, code: str, code_verifier: str) -> dict[str, Any]:
    client = OAuth2Client(oauth["client_id"], oauth.get("client_secret"), redirect_uri=redirect_uri,
                          token_endpoint_auth_method="none" if not oauth.get("client_secret") else "client_secret_post")
    try:
        token = client.fetch_token(oauth["token_url"], code=code, code_verifier=code_verifier,
                                   grant_type="authorization_code")
    except Exception as exc:  # noqa: BLE001
        raise ConnectionUnavailable(f"token exchange failed: {type(exc).__name__}") from None
    return _normalize(token)


def refresh(oauth: Mapping[str, Any], credential: Mapping[str, Any]) -> dict[str, Any]:
    if not credential.get("refresh_token"):
        raise ConnectionUnavailable("token expired and no refresh token available")
    client = OAuth2Client(oauth["client_id"], oauth.get("client_secret"),
                          token_endpoint_auth_method="none" if not oauth.get("client_secret") else "client_secret_post")
    try:
        token = client.refresh_token(oauth["token_url"], refresh_token=credential["refresh_token"])
    except Exception as exc:  # noqa: BLE001
        raise ConnectionUnavailable(f"token refresh failed: {type(exc).__name__}") from None
    out = _normalize(token)
    out.setdefault("refresh_token", credential["refresh_token"])
    return out


def is_expired(credential: Mapping[str, Any], skew: int = 60) -> bool:
    exp = credential.get("expires_at")
    return bool(exp) and float(exp) - skew < time.time()


def _normalize(token: Mapping[str, Any]) -> dict[str, Any]:
    out = {"access_token": token["access_token"], "token_type": token.get("token_type", "Bearer")}
    if token.get("refresh_token"):
        out["refresh_token"] = token["refresh_token"]
    if token.get("expires_at"):
        out["expires_at"] = float(token["expires_at"])
    elif token.get("expires_in"):
        out["expires_at"] = time.time() + float(token["expires_in"])
    if token.get("scope"):
        out["scope"] = token["scope"]
    return out
