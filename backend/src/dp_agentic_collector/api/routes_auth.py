from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from ..container import Collector
from .deps import CSRF_COOKIE, SESSION_COOKIE, Ctx, get_app, get_ctx
from .schemas import LoginIn, MeOut, UserOut

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=MeOut)
def login(body: LoginIn, response: Response, app: Collector = Depends(get_app)) -> MeOut:
    res = app.services.auth.login(body.tenant, body.email, body.password)
    secure = app.settings.cookie_secure
    response.set_cookie(SESSION_COOKIE, res.session_id, httponly=True, samesite="lax", secure=secure, path="/")
    response.set_cookie(CSRF_COOKIE, res.csrf_token, httponly=False, samesite="lax", secure=secure, path="/")
    return MeOut(user=UserOut.of(res.user), csrf_token=res.csrf_token)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, ctx: Ctx = Depends(get_ctx)) -> None:
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        ctx.s.auth.logout(sid)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.get("/auth/me", response_model=MeOut)
def me(request: Request, ctx: Ctx = Depends(get_ctx)) -> MeOut:
    user = ctx.s.tenancy.get_user(ctx.actor, ctx.actor.user_id)
    return MeOut(user=UserOut.of(user), csrf_token=request.cookies.get(CSRF_COOKIE))
