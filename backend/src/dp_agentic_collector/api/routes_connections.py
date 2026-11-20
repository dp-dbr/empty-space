from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..domain.entities import McpServerDefinition
from ..domain.enums import AccessMode, McpAuthType, McpTransport, ToolClassification
from .deps import Ctx, get_ctx
from .schemas import ConnectionOut

router = APIRouter(tags=["connections"])


class McpServerIn(BaseModel):
    name: str
    transport: str = "streamable_http"
    endpoint: str
    auth_type: str = "none"
    max_access_mode: str = "read"
    tool_classification: dict[str, str] = Field(default_factory=dict)
    oauth: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class McpServerPatch(BaseModel):
    name: str | None = None
    endpoint: str | None = None
    enabled: bool | None = None
    max_access_mode: str | None = None
    tool_classification: dict[str, str] | None = None
    oauth: dict[str, Any] | None = None
    description: str | None = None


class McpServerOut(BaseModel):
    server_id: str
    name: str
    transport: str
    endpoint: str
    auth_type: str
    max_access_mode: str
    tool_classification: dict[str, str]
    enabled: bool
    description: str

    @staticmethod
    def of(s: McpServerDefinition) -> McpServerOut:
        return McpServerOut(server_id=s.server_id, name=s.name, transport=str(s.transport), endpoint=s.endpoint,
                            auth_type=str(s.auth_type), max_access_mode=str(s.max_access_mode),
                            tool_classification={k: str(v) for k, v in s.tool_classification.items()},
                            enabled=s.enabled, description=s.description)


class ConnectIn(BaseModel):
    server_id: str
    access_mode: str = "read"
    api_key: str | None = None


class AccessModeIn(BaseModel):
    access_mode: str


@router.get("/mcp-servers", response_model=list[McpServerOut])
def servers(ctx: Ctx = Depends(get_ctx)) -> list[McpServerOut]:
    return [McpServerOut.of(s) for s in ctx.s.connections.servers(ctx.actor)]


@router.post("/mcp-servers", response_model=McpServerOut, status_code=201)
def create_server(body: McpServerIn, ctx: Ctx = Depends(get_ctx)) -> McpServerOut:
    s = ctx.s.connections.create_server(
        ctx.actor, body.name, McpTransport(body.transport), body.endpoint, auth_type=McpAuthType(body.auth_type),
        max_access_mode=AccessMode(body.max_access_mode),
        tool_classification={k: ToolClassification(v) for k, v in body.tool_classification.items()},
        oauth=body.oauth, description=body.description)
    return McpServerOut.of(s)


@router.patch("/mcp-servers/{server_id}", response_model=McpServerOut)
def update_server(server_id: str, body: McpServerPatch, ctx: Ctx = Depends(get_ctx)) -> McpServerOut:
    changes = body.model_dump(exclude_none=True)
    if "max_access_mode" in changes:
        changes["max_access_mode"] = AccessMode(changes["max_access_mode"])
    if "tool_classification" in changes:
        changes["tool_classification"] = {k: ToolClassification(v) for k, v in changes["tool_classification"].items()}
    return McpServerOut.of(ctx.s.connections.update_server(ctx.actor, server_id, **changes))


@router.get("/connections", response_model=list[ConnectionOut])
def my_connections(ctx: Ctx = Depends(get_ctx)) -> list[ConnectionOut]:
    return [ConnectionOut.of(c) for c in ctx.s.connections.my_connections(ctx.actor)]


@router.post("/connections", response_model=ConnectionOut, status_code=201)
def connect(body: ConnectIn, ctx: Ctx = Depends(get_ctx)) -> ConnectionOut:
    con, url = ctx.s.connections.connect(ctx.actor, body.server_id, AccessMode(body.access_mode),
                                         api_key=body.api_key)
    return ConnectionOut.of(con, url)


@router.patch("/connections/{connection_id}", response_model=ConnectionOut)
def set_mode(connection_id: str, body: AccessModeIn, ctx: Ctx = Depends(get_ctx)) -> ConnectionOut:
    return ConnectionOut.of(ctx.s.connections.set_access_mode(ctx.actor, connection_id, AccessMode(body.access_mode)))


@router.delete("/connections/{connection_id}", response_model=ConnectionOut)
def revoke(connection_id: str, ctx: Ctx = Depends(get_ctx)) -> ConnectionOut:
    return ConnectionOut.of(ctx.s.connections.revoke(ctx.actor, connection_id))


@router.get("/connections/oauth/callback", include_in_schema=False)
def oauth_callback(state: str, code: str, ctx: Ctx = Depends(get_ctx)) -> RedirectResponse:
    ctx.s.connections.oauth_callback(ctx.actor, state, code)
    return RedirectResponse("/connections?connected=1", status_code=303)
