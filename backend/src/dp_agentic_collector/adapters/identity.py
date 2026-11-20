"""Identity providers: local users (argon2) and an OIDC adapter (Authlib)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from ..domain.entities import User
from ..domain.errors import Unauthenticated
from ..extensions.interfaces import AuthenticatedIdentity, IdentityProvider
from .sql.db import Database

_hasher = PasswordHasher()
_DUMMY = _hasher.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hashed: str | None, password: str) -> bool:
    try:
        return _hasher.verify(hashed or _DUMMY, password) and hashed is not None
    except (VerificationError, InvalidHashError):
        return False


class LocalIdentityProvider(IdentityProvider):
    name = "local"

    def __init__(self, db: Database) -> None:
        self.db = db

    def authenticate(self, tenant_id: str, credentials: Mapping[str, str]) -> AuthenticatedIdentity:
        email = credentials.get("email", "").strip().lower()
        with self.db.uow(tenant_id) as uow:
            user = uow.repo(User).first(email=email)
        ok = verify_password(user.password_hash if user else None, credentials.get("password", ""))
        if not ok or user is None or not user.active or user.erased:
            raise Unauthenticated("invalid credentials")
        return AuthenticatedIdentity(tenant_id=tenant_id, user_id=user.user_id, provider=self.name)


class OidcIdentityProvider(IdentityProvider):
    """Authorization-code login against any compliant OIDC IdP (Entra ID, Keycloak, Okta, ...).

    Users are matched by ``ExternalIdentity(provider=<name>, subject=<sub>)``. The browser
    redirect part lives in the API; this class validates the code exchange result.
    """

    name = "oidc"

    def __init__(self, db: Database, issuer: str, client_id: str, client_secret: str, redirect_uri: str) -> None:
        from authlib.integrations.httpx_client import OAuth2Client

        self.db = db
        self.issuer = issuer
        self.client = OAuth2Client(client_id, client_secret, redirect_uri=redirect_uri, scope="openid email profile")

    def authenticate(self, tenant_id: str, credentials: Mapping[str, str]) -> AuthenticatedIdentity:
        import httpx

        meta: dict[str, Any] = httpx.get(self.issuer.rstrip("/") + "/.well-known/openid-configuration").json()
        token = self.client.fetch_token(meta["token_endpoint"], code=credentials["code"],
                                        code_verifier=credentials.get("code_verifier"))
        info = self.client.get(meta["userinfo_endpoint"], token=token).json()
        with self.db.uow(tenant_id) as uow:
            for user in uow.repo(User).list(active=True):
                if any(x.provider == self.name and x.subject == info["sub"] for x in user.external_identities):
                    return AuthenticatedIdentity(tenant_id, user.user_id, self.name, info["sub"])
        raise Unauthenticated("no user linked to this identity")
