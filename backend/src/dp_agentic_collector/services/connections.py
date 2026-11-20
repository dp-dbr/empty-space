"""MCP server definitions (admin) and per-user connections (delegated access, ADR-0009)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..adapters import oauth_client
from ..adapters.mcp_client import McpClientAdapter, McpUnauthorized, ToolDescriptor
from ..domain.entities import McpServerDefinition, UserConnection
from ..domain.enums import (
    AccessMode,
    ConnectionStatus,
    McpAuthType,
    McpTransport,
    ToolClassification,
)
from ..domain.errors import ConnectionUnavailable, NotFound, PermissionDenied, ValidationFailed
from ..domain.ids import utcnow
from ..domain.permissions import (
    ActorContext,
    Decision,
    can_manage_tenant,
    classify_tool,
    effective_access_mode,
    mcp_tool_decision,
)
from .base import ServiceBase


@dataclass(frozen=True)
class AvailableTool:
    qualified_name: str
    server: McpServerDefinition
    connection: UserConnection
    tool: ToolDescriptor
    classification: ToolClassification


def qualified_tool_name(server: McpServerDefinition, tool: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", server.name).strip("_").lower()[:20] or "srv"
    t = re.sub(r"[^a-zA-Z0-9_-]+", "_", tool)
    return f"mcp__{slug}__{t}"[:64]


class ConnectionService(ServiceBase):
    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.client = McpClientAdapter()

    # --- admin: server definitions ----------------------------------------------------------
    def create_server(self, actor: ActorContext, name: str, transport: McpTransport, endpoint: str, *,
                      auth_type: McpAuthType = McpAuthType.NONE, max_access_mode: AccessMode = AccessMode.READ,
                      tool_classification: dict[str, ToolClassification] | None = None,
                      oauth: dict[str, Any] | None = None, description: str = "") -> McpServerDefinition:
        can_manage_tenant(actor).enforce()
        if transport == McpTransport.STREAMABLE_HTTP and not (endpoint.startswith("https://") or
                                                               endpoint.startswith("http://") or
                                                               endpoint.startswith("inproc://")):
            raise ValidationFailed("streamable HTTP endpoint must be a URL")
        server = McpServerDefinition(tenant_id=actor.tenant_id, name=name, transport=transport, endpoint=endpoint,
                                     auth_type=auth_type, max_access_mode=max_access_mode,
                                     tool_classification=tool_classification or {}, oauth=oauth or {},
                                     description=description)
        with self.db.uow(actor.tenant_id) as uow:
            uow.repo(McpServerDefinition).add(server)
            self.audit(uow, actor, "mcp_server.create", f"mcp_server:{server.server_id}", name=name)
        return server

    def update_server(self, actor: ActorContext, server_id: str, **changes: Any) -> McpServerDefinition:
        can_manage_tenant(actor).enforce()
        allowed = {"name", "endpoint", "enabled", "max_access_mode", "tool_classification", "oauth", "description",
                   "auth_type"}
        with self.db.uow(actor.tenant_id) as uow:
            server = uow.repo(McpServerDefinition).get(server_id)
            for k, v in changes.items():
                if k in allowed and v is not None:
                    setattr(server, k, v)
            uow.repo(McpServerDefinition).save(server)
            self.audit(uow, actor, "mcp_server.update", f"mcp_server:{server_id}", keys=sorted(changes))
        return server

    def servers(self, actor: ActorContext) -> list[McpServerDefinition]:
        with self.db.uow(actor.tenant_id) as uow:
            items = uow.repo(McpServerDefinition).list(order_by="name")
        return items if actor.is_admin else [s for s in items if s.enabled]

    def server(self, actor: ActorContext, server_id: str) -> McpServerDefinition:
        with self.db.uow(actor.tenant_id) as uow:
            return uow.repo(McpServerDefinition).get(server_id)

    # --- user connections ------------------------------------------------------------------
    def my_connections(self, actor: ActorContext) -> list[UserConnection]:
        with self.db.uow(actor.tenant_id) as uow:
            return uow.repo(UserConnection).list(user_id=actor.user_id, order_by="connected_at")

    def _own(self, actor: ActorContext, connection_id: str) -> UserConnection:
        with self.db.uow(actor.tenant_id) as uow:
            con = uow.repo(UserConnection).find(connection_id)
        if con is None or con.user_id != actor.user_id:
            raise NotFound("connection not found")
        return con

    def connect(self, actor: ActorContext, server_id: str, access_mode: AccessMode = AccessMode.READ, *,
                api_key: str | None = None, redirect_uri: str | None = None) -> tuple[UserConnection, str | None]:
        """Create a connection. Returns (connection, authorization_url for OAuth)."""
        actor.require_scope("dpac.write")
        server = self.server(actor, server_id)
        if not server.enabled:
            raise PermissionDenied("server disabled by admin policy")
        access_mode = AccessMode(access_mode)
        if access_mode == AccessMode.READ_WRITE and server.max_access_mode != AccessMode.READ_WRITE:
            raise PermissionDenied("admin policy allows read-only access for this server")
        con = UserConnection(tenant_id=actor.tenant_id, user_id=actor.user_id, server_id=server_id,
                             access_mode=access_mode)
        auth_url = None
        if server.auth_type == McpAuthType.API_KEY:
            if not api_key:
                raise ValidationFailed("api_key required")
            con.credential_ref = self.app.vault.put(actor.tenant_id, actor.user_id, {"api_key": api_key})
        elif server.auth_type == McpAuthType.OAUTH2:
            redirect = redirect_uri or f"{self.app.settings.public_base_url}/api/v1/connections/oauth/callback"
            req = oauth_client.authorization_request(server.oauth, redirect)
            con.status = ConnectionStatus.EXPIRED  # becomes active after the callback
            con.pending_oauth = {"state": req["state"], "code_verifier": req["code_verifier"], "redirect_uri": redirect}
            auth_url = req["url"]
        with self.db.uow(actor.tenant_id) as uow:
            for old in uow.repo(UserConnection).list(user_id=actor.user_id, server_id=server_id):
                if old.status != ConnectionStatus.REVOKED:
                    raise ValidationFailed("you already have a connection to this server; revoke it first")
            uow.repo(UserConnection).add(con)
            self.audit(uow, actor, "connection.create", f"connection:{con.connection_id}", server_id=server_id,
                       access_mode=str(access_mode))
        return con, auth_url

    def oauth_callback(self, actor: ActorContext, state: str, code: str) -> UserConnection:
        with self.db.uow(actor.tenant_id) as uow:
            pending = [c for c in uow.repo(UserConnection).list(user_id=actor.user_id)
                       if c.pending_oauth and c.pending_oauth.get("state") == state]
        if not pending:
            raise ValidationFailed("unknown or expired OAuth state")
        con = pending[0]
        server = self.server(actor, con.server_id)
        assert con.pending_oauth is not None
        token = oauth_client.exchange_code(server.oauth, con.pending_oauth["redirect_uri"], code,
                                           con.pending_oauth["code_verifier"])
        ref = self.app.vault.put(actor.tenant_id, actor.user_id, token)
        with self.db.uow(actor.tenant_id) as uow:
            con = uow.repo(UserConnection).get(con.connection_id)
            con.credential_ref, con.pending_oauth, con.status = ref, None, ConnectionStatus.ACTIVE
            con.connected_at = utcnow()
            uow.repo(UserConnection).save(con)
            self.audit(uow, actor, "connection.authorized", f"connection:{con.connection_id}")
        return con

    def set_access_mode(self, actor: ActorContext, connection_id: str, access_mode: AccessMode) -> UserConnection:
        con = self._own(actor, connection_id)
        server = self.server(actor, con.server_id)
        access_mode = AccessMode(access_mode)
        if access_mode == AccessMode.READ_WRITE and server.max_access_mode != AccessMode.READ_WRITE:
            raise PermissionDenied("admin policy allows read-only access for this server")
        with self.db.uow(actor.tenant_id) as uow:
            con.access_mode = access_mode
            uow.repo(UserConnection).save(con)
            self.audit(uow, actor, "connection.access_mode", f"connection:{connection_id}", access_mode=str(access_mode))
        return con

    def revoke(self, actor: ActorContext, connection_id: str) -> UserConnection:
        con = self._own(actor, connection_id)
        if con.credential_ref:
            self.app.vault.delete(actor.tenant_id, con.credential_ref)
        with self.db.uow(actor.tenant_id) as uow:
            con.status, con.credential_ref, con.pending_oauth = ConnectionStatus.REVOKED, None, None
            uow.repo(UserConnection).save(con)
            self.audit(uow, actor, "connection.revoke", f"connection:{connection_id}")
        return con

    # --- delegated tool access ---------------------------------------------------------------
    def _mark(self, actor: ActorContext, con: UserConnection, status: ConnectionStatus) -> None:
        with self.db.uow(actor.tenant_id) as uow:
            c = uow.repo(UserConnection).get(con.connection_id)
            c.status = status
            uow.repo(UserConnection).save(c)
            self.audit(uow, actor, f"connection.{status}", f"connection:{con.connection_id}")

    def credential(self, actor: ActorContext, server: McpServerDefinition, con: UserConnection) -> dict[str, Any] | None:
        """Fetch the acting user's credential; refresh OAuth tokens; never use anyone else's."""
        if con.user_id != actor.user_id:
            raise PermissionDenied("connections are never shared between users")
        if server.auth_type == McpAuthType.NONE:
            return None
        if not con.credential_ref:
            raise ConnectionUnavailable("connection has no credential")
        cred = self.app.vault.get(actor.tenant_id, con.credential_ref)
        if server.auth_type == McpAuthType.OAUTH2 and oauth_client.is_expired(cred):
            try:
                cred = oauth_client.refresh(server.oauth, cred)
            except ConnectionUnavailable:
                self._mark(actor, con, ConnectionStatus.EXPIRED)
                raise
            self.app.vault.replace(actor.tenant_id, con.credential_ref, cred)
        return cred

    def active_pairs(self, actor: ActorContext) -> list[tuple[McpServerDefinition, UserConnection]]:
        with self.db.uow(actor.tenant_id) as uow:
            cons = uow.repo(UserConnection).list(user_id=actor.user_id, status=str(ConnectionStatus.ACTIVE))
            servers = {s.server_id: s for s in uow.repo(McpServerDefinition).list()}
        return [(servers[c.server_id], c) for c in cons if c.server_id in servers and servers[c.server_id].enabled]

    def available_tools(self, actor: ActorContext, allowed_tools: tuple[str, ...] | None = None) -> list[AvailableTool]:
        """Tools of the acting user's active connections that pass the effective-permission check.
        Write tools are listed only on read_write connections. Unavailable servers degrade gracefully."""
        out: list[AvailableTool] = []
        for server, con in self.active_pairs(actor):
            try:
                tools = self.client.list_tools(server, self.credential(actor, server, con))
            except McpUnauthorized:
                self._mark(actor, con, ConnectionStatus.EXPIRED)
                continue
            except ConnectionUnavailable:
                continue
            for t in tools:
                cls = classify_tool(server, t.name, t.read_only_hint)
                q = qualified_tool_name(server, t.name)
                if mcp_tool_decision(actor, server, con, cls, q, allowed_tools).allowed:
                    out.append(AvailableTool(q, server, con, t, cls))
        return out

    def decision(self, actor: ActorContext, server: McpServerDefinition, con: UserConnection | None,
                 classification: ToolClassification, qualified: str,
                 allowed_tools: tuple[str, ...] | None) -> Decision:
        return mcp_tool_decision(actor, server, con, classification, qualified, allowed_tools)

    def call(self, actor: ActorContext, server_id: str, tool: str, arguments: dict[str, Any],
             classification: ToolClassification, allowed_tools: tuple[str, ...] | None = None,
             confirmed: bool = False) -> tuple[Any, bool, dict[str, Any]]:
        """Call a tool on behalf of ``actor``. Returns (content, is_error, source_ref)."""
        server = self.server(actor, server_id)
        with self.db.uow(actor.tenant_id) as uow:
            con = uow.repo(UserConnection).first(user_id=actor.user_id, server_id=server_id,
                                                 status=str(ConnectionStatus.ACTIVE))
        q = qualified_tool_name(server, tool)
        d = self.decision(actor, server, con, classification, q, allowed_tools)
        if not d.allowed:
            self.audit_now(actor, "mcp.call", f"mcp:{server.name}/{tool}", outcome="denied", reason=d.reason)
            raise PermissionDenied(d.reason)
        if d.requires_confirmation and not confirmed:
            raise PermissionDenied("write requires explicit user confirmation")
        assert con is not None
        try:
            result = self.client.call_tool(server, self.credential(actor, server, con), tool, arguments)
        except McpUnauthorized:
            self._mark(actor, con, ConnectionStatus.EXPIRED)
            self.audit_now(actor, "mcp.call", f"mcp:{server.name}/{tool}", outcome="error", reason="unauthorized")
            raise
        with self.db.uow(actor.tenant_id) as uow:
            c = uow.repo(UserConnection).get(con.connection_id)
            c.last_used_at = utcnow()
            uow.repo(UserConnection).save(c)
            self.audit(uow, actor, "mcp.call", f"mcp:{server.name}/{tool}",
                       outcome="error" if result.is_error else "ok", classification=str(classification),
                       confirmed=confirmed or None, access_mode=str(effective_access_mode(server, con)))
        source = {"kind": "mcp", "server": server.name, "tool": tool, "reference": server.server_id,
                  "on_behalf_of": actor.user_id, "retrieved_at": utcnow().isoformat()}
        return result.content, result.is_error, source
