"""Authentication: auth sessions, CSRF, login rate limiting, and the Collector's own
OAuth 2.1 authorization server (authorization code + PKCE) for API/MCP clients (ADR-0010)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

from sqlalchemy import and_, delete, func, insert, select, update

from ..adapters.sql.schema import (
    auth_sessions,
    login_attempts,
    oauth_clients,
    oauth_codes,
    oauth_tokens,
)
from ..domain.entities import Tenant, User
from ..domain.errors import PermissionDenied, RateLimited, Unauthenticated, ValidationFailed
from ..domain.ids import new_id, utcnow
from ..domain.permissions import ActorContext, can_manage_tenant
from .base import ServiceBase

SESSION_TTL = timedelta(hours=12)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
CODE_TTL = timedelta(minutes=5)
SCOPES = ("dpac.read", "dpac.write")


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class LoginResult:
    session_id: str
    csrf_token: str
    user: User


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str
    token_type: str = "Bearer"


class AuthService(ServiceBase):
    # --- auth sessions ------------------------------------------------------------
    def login(self, tenant_name: str, email: str, password: str) -> LoginResult:
        tenant = self.services.tenancy.tenant_by_name(tenant_name)
        key = f"{tenant.tenant_id}:{email.strip().lower()}"
        window = utcnow() - timedelta(minutes=5)
        with self.db.engine.begin() as c:
            failures = c.execute(select(func.count()).select_from(login_attempts).where(and_(
                login_attempts.c.key == key, login_attempts.c.at >= window,
                login_attempts.c.success.is_(False)))).scalar_one()
        if failures >= self.app.settings.login_rate_limit:
            raise RateLimited("too many login attempts, try again later")
        try:
            ident = self.app.identity.authenticate(tenant.tenant_id, {"email": email, "password": password})
        except Unauthenticated:
            with self.db.engine.begin() as c:
                c.execute(insert(login_attempts).values(key=key, at=utcnow(), success=False))
            with self.db.uow(tenant.tenant_id) as uow:
                self.audit(uow, None, "auth.login", f"email:{_h(email)[:12]}", outcome="denied")
            raise
        session_id, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        now = utcnow()
        with self.db.engine.begin() as c:
            c.execute(insert(login_attempts).values(key=key, at=now, success=True))
            c.execute(insert(auth_sessions).values(session_hash=_h(session_id), tenant_id=tenant.tenant_id,
                                                   user_id=ident.user_id, csrf_token=csrf, created_at=now,
                                                   expires_at=now + SESSION_TTL))
        with self.db.uow(tenant.tenant_id) as uow:
            user = uow.repo(User).get(ident.user_id)
            self.audit(uow, self._actor(user), "auth.login", f"user:{user.user_id}")
        return LoginResult(session_id, csrf, user)

    def logout(self, session_id: str) -> None:
        with self.db.engine.begin() as c:
            c.execute(delete(auth_sessions).where(auth_sessions.c.session_hash == _h(session_id)))

    @staticmethod
    def _actor(user: User, trace_id: str | None = None, scopes: frozenset[str] | None = None) -> ActorContext:
        return ActorContext(tenant_id=user.tenant_id, user_id=user.user_id, roles=frozenset(user.roles),
                            trace_id=trace_id, scopes=scopes)

    def _active_user(self, tenant_id: str, user_id: str) -> User:
        with self.db.uow(tenant_id) as uow:
            user = uow.repo(User).find(user_id)
        if user is None or not user.active or user.erased:
            raise Unauthenticated("user inactive")
        return user

    def resolve_session(self, session_id: str, trace_id: str | None = None) -> tuple[ActorContext, str]:
        with self.db.engine.connect() as c:
            row = c.execute(select(auth_sessions).where(auth_sessions.c.session_hash == _h(session_id))).first()
        if row is None or row.expires_at < utcnow():
            raise Unauthenticated("auth session expired")
        user = self._active_user(row.tenant_id, row.user_id)
        return self._actor(user, trace_id), row.csrf_token

    @staticmethod
    def check_csrf(expected: str, provided: str | None) -> None:
        if not provided or not hmac.compare_digest(expected, provided):
            raise PermissionDenied("CSRF token missing or invalid")

    def actor_for_user(self, tenant_id: str, user_id: str, trace_id: str | None = None) -> ActorContext:
        """Context for background work on behalf of a user (never elevated, never for inactive users)."""
        return self._actor(self._active_user(tenant_id, user_id), trace_id)

    # --- OAuth 2.1 authorization server ---------------------------------------------
    def register_client(self, actor: ActorContext, name: str, redirect_uris: list[str]) -> str:
        can_manage_tenant(actor).enforce()
        client_id = new_id("cli")
        with self.db.engine.begin() as c:
            c.execute(insert(oauth_clients).values(client_id=client_id, tenant_id=actor.tenant_id, name=name,
                                                   redirect_uris=redirect_uris, created_at=utcnow()))
        self.audit_now(actor, "oauth.client_register", f"client:{client_id}")
        return client_id

    def register_client_dynamic(self, tenant_name: str, name: str, redirect_uris: list[str]) -> str:
        """RFC 7591 dynamic registration for public clients (MCP clients)."""
        tenant = self.services.tenancy.tenant_by_name(tenant_name)
        for uri in redirect_uris:
            if not (uri.startswith("https://") or uri.startswith("http://localhost") or uri.startswith("http://127.0.0.1")):
                raise ValidationFailed("redirect_uri must be https or loopback")
        client_id = new_id("cli")
        with self.db.engine.begin() as c:
            c.execute(insert(oauth_clients).values(client_id=client_id, tenant_id=tenant.tenant_id, name=name,
                                                   redirect_uris=redirect_uris, created_at=utcnow()))
        return client_id

    def client(self, client_id: str) -> dict[str, object]:
        with self.db.engine.connect() as c:
            row = c.execute(select(oauth_clients).where(oauth_clients.c.client_id == client_id)).first()
        if row is None:
            raise ValidationFailed("unknown client_id")
        return dict(row._mapping)

    def authorize(self, actor: ActorContext, client_id: str, redirect_uri: str, scope: str,
                  code_challenge: str, code_challenge_method: str, state: str | None) -> str:
        """Called after the user is logged in and consented; returns the redirect URL with the code."""
        client = self.client(client_id)
        if client["tenant_id"] != actor.tenant_id:
            raise PermissionDenied("client belongs to another tenant")
        if redirect_uri not in client["redirect_uris"]:  # type: ignore[operator]
            raise ValidationFailed("redirect_uri not registered")
        if code_challenge_method != "S256" or not code_challenge:
            raise ValidationFailed("PKCE with S256 is required")
        scopes = [s for s in scope.split() if s in SCOPES] or ["dpac.read"]
        code = secrets.token_urlsafe(32)
        with self.db.engine.begin() as c:
            c.execute(insert(oauth_codes).values(code_hash=_h(code), tenant_id=actor.tenant_id, client_id=client_id,
                                                 user_id=actor.user_id, redirect_uri=redirect_uri, scopes=scopes,
                                                 code_challenge=code_challenge, expires_at=utcnow() + CODE_TTL,
                                                 used=False))
        self.audit_now(actor, "oauth.authorize", f"client:{client_id}", scopes=scopes)
        params = {"code": code}
        if state:
            params["state"] = state
        return f"{redirect_uri}{'&' if '?' in redirect_uri else '?'}{urlencode(params)}"

    def _issue(self, conn: object, tenant_id: str, client_id: str, user_id: str, scopes: list[str]) -> TokenSet:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = utcnow()
        conn.execute(insert(oauth_tokens), [  # type: ignore[attr-defined]
            dict(token_hash=_h(access), kind="access", tenant_id=tenant_id, client_id=client_id, user_id=user_id,
                 scopes=scopes, expires_at=now + ACCESS_TTL, revoked=False),
            dict(token_hash=_h(refresh), kind="refresh", tenant_id=tenant_id, client_id=client_id, user_id=user_id,
                 scopes=scopes, expires_at=now + REFRESH_TTL, revoked=False),
        ])
        return TokenSet(access, refresh, int(ACCESS_TTL.total_seconds()), " ".join(scopes))

    def exchange_code(self, client_id: str, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        with self.db.engine.begin() as c:
            row = c.execute(select(oauth_codes).where(oauth_codes.c.code_hash == _h(code))).first()
            if row is None or row.used or row.expires_at < utcnow() or row.client_id != client_id \
                    or row.redirect_uri != redirect_uri:
                raise ValidationFailed("invalid_grant")
            challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
            if not hmac.compare_digest(challenge, row.code_challenge):
                raise ValidationFailed("invalid_grant: PKCE verification failed")
            c.execute(update(oauth_codes).where(oauth_codes.c.code_hash == row.code_hash).values(used=True))
            return self._issue(c, row.tenant_id, client_id, row.user_id, list(row.scopes))

    def refresh(self, client_id: str, refresh_token: str) -> TokenSet:
        with self.db.engine.begin() as c:
            row = c.execute(select(oauth_tokens).where(and_(oauth_tokens.c.token_hash == _h(refresh_token),
                                                             oauth_tokens.c.kind == "refresh"))).first()
            if row is None or row.revoked or row.expires_at < utcnow() or row.client_id != client_id:
                raise ValidationFailed("invalid_grant")
            c.execute(update(oauth_tokens).where(oauth_tokens.c.token_hash == row.token_hash).values(revoked=True))
            return self._issue(c, row.tenant_id, client_id, row.user_id, list(row.scopes))

    def revoke_token(self, token: str) -> None:
        with self.db.engine.begin() as c:
            c.execute(update(oauth_tokens).where(oauth_tokens.c.token_hash == _h(token)).values(revoked=True))

    def resolve_bearer(self, token: str, trace_id: str | None = None) -> ActorContext:
        with self.db.engine.connect() as c:
            row = c.execute(select(oauth_tokens).where(and_(oauth_tokens.c.token_hash == _h(token),
                                                             oauth_tokens.c.kind == "access"))).first()
        if row is None or row.revoked or row.expires_at < utcnow():
            raise Unauthenticated("invalid or expired access token")
        user = self._active_user(row.tenant_id, row.user_id)
        return self._actor(user, trace_id, scopes=frozenset(row.scopes))

    def personal_token(self, actor: ActorContext, scopes: list[str]) -> TokenSet:
        """Convenience for CLI/tests: issue a token for the acting user without browser flow."""
        scopes = [s for s in scopes if s in SCOPES]
        with self.db.engine.begin() as c:
            return self._issue(c, actor.tenant_id, "personal", actor.user_id, scopes)

    def tenant_of(self, tenant_name: str) -> Tenant:
        return self.services.tenancy.tenant_by_name(tenant_name)
