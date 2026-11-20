"""API request/response schemas (Pydantic, API layer only)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..domain.entities import User, UserConnection


class LoginIn(BaseModel):
    tenant: str = "default"
    email: str
    password: str


class UserOut(BaseModel):
    user_id: str
    tenant_id: str
    name: str
    email: str
    locale: str
    roles: list[str]
    active: bool

    @staticmethod
    def of(u: User) -> UserOut:
        return UserOut(user_id=u.user_id, tenant_id=u.tenant_id, name=u.name, email=u.email, locale=u.locale,
                       roles=[str(r) for r in u.roles], active=u.active)


class MeOut(BaseModel):
    user: UserOut
    csrf_token: str | None = None


class UserIn(BaseModel):
    name: str
    email: str
    roles: list[str] = Field(default_factory=lambda: ["respondent"])
    locale: str = "en"
    password: str | None = None


class UserPatch(BaseModel):
    roles: list[str] | None = None
    active: bool | None = None
    locale: str | None = None
    name: str | None = None
    password: str | None = None


class GrantIn(BaseModel):
    user_id: str
    permission: str = "read"


class ConnectionOut(BaseModel):
    connection_id: str
    server_id: str
    access_mode: str
    status: str
    connected_at: datetime
    last_used_at: datetime | None
    authorization_url: str | None = None

    @staticmethod
    def of(c: UserConnection, authorization_url: str | None = None) -> ConnectionOut:
        return ConnectionOut(connection_id=c.connection_id, server_id=c.server_id, access_mode=str(c.access_mode),
                             status=str(c.status), connected_at=c.connected_at, last_used_at=c.last_used_at,
                             authorization_url=authorization_url)


class TenantSettingsIn(BaseModel):
    settings: dict[str, Any]
