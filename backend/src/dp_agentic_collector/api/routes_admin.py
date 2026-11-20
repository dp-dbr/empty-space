from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..domain.entities import AuditEvent, SubjectGrant
from ..domain.enums import Role
from ..domain.permissions import can_manage_tenant
from .deps import Ctx, get_ctx
from .schemas import GrantIn, TenantSettingsIn, UserIn, UserOut, UserPatch

router = APIRouter(tags=["admin"])


@router.get("/tenant")
def tenant(ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    t = ctx.s.tenancy.get_tenant(ctx.actor)
    return {"tenant_id": t.tenant_id, "name": t.name,
            "settings": t.settings if ctx.actor.is_admin else {}}


@router.patch("/tenant/settings")
def tenant_settings(body: TenantSettingsIn, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    t = ctx.s.tenancy.update_tenant_settings(ctx.actor, body.settings)
    return {"tenant_id": t.tenant_id, "name": t.name, "settings": t.settings}


@router.get("/users", response_model=list[UserOut])
def users(ctx: Ctx = Depends(get_ctx)) -> list[UserOut]:
    return [UserOut.of(u) for u in ctx.s.tenancy.list_users(ctx.actor)]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserIn, ctx: Ctx = Depends(get_ctx)) -> UserOut:
    u = ctx.s.tenancy.create_user(ctx.actor, body.name, body.email, [Role(r) for r in body.roles],
                                  body.locale, body.password)
    return UserOut.of(u)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UserPatch, ctx: Ctx = Depends(get_ctx)) -> UserOut:
    u = ctx.s.tenancy.update_user(ctx.actor, user_id, roles=[Role(r) for r in body.roles] if body.roles is not None
                                  else None, active=body.active, locale=body.locale, name=body.name,
                                  password=body.password)
    return UserOut.of(u)


@router.post("/users/{user_id}/erase", status_code=204)
def erase_user(user_id: str, ctx: Ctx = Depends(get_ctx)) -> None:
    ctx.s.tenancy.erase_user(ctx.actor, user_id)


@router.post("/subjects/{subject_id}/grants", response_model=SubjectGrant, status_code=201)
def grant(subject_id: str, body: GrantIn, ctx: Ctx = Depends(get_ctx)) -> SubjectGrant:
    return ctx.s.tenancy.grant_subject(ctx.actor, subject_id, body.user_id, body.permission)  # type: ignore[no-any-return]


@router.get("/audit", response_model=list[AuditEvent])
def audit(mine: bool = False, limit: int = 200, ctx: Ctx = Depends(get_ctx)) -> list[AuditEvent]:
    return ctx.s.tenancy.audit_log(ctx.actor, mine=mine, limit=limit)  # type: ignore[no-any-return]


@router.get("/plugins")
def plugins(ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    can_manage_tenant(ctx.actor).enforce()
    reg = ctx.app.registries
    return {"extensions": reg.describe(), "loaded_plugins": reg.loaded_plugins}


@router.post("/oauth-clients", status_code=201)
def oauth_client(body: dict[str, Any], ctx: Ctx = Depends(get_ctx)) -> dict[str, str]:
    cid = ctx.s.auth.register_client(ctx.actor, body.get("name", "client"), list(body.get("redirect_uris", [])))
    return {"client_id": cid}
