"""Permission evaluation (pure).

Effective permission = RBAC/subject permission ∩ admin policy ∩ user grant
∩ AgentDefinition allowlist ∩ attachment visibility.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from .entities import (
    Attachment,
    CollectionJob,
    Cycle,
    McpServerDefinition,
    SubjectGrant,
    UserConnection,
)
from .enums import (
    AccessMode,
    AttachmentStatus,
    AttachmentVisibility,
    ConnectionStatus,
    Role,
    ToolClassification,
)
from .errors import PermissionDenied


@dataclass(frozen=True)
class ActorContext:
    """Who acts. Agents always carry the context of the user they act for."""

    tenant_id: str
    user_id: str
    roles: frozenset[Role] = field(default_factory=frozenset)
    agent_run_id: str | None = None
    agent_name: str | None = None
    trace_id: str | None = None
    scopes: frozenset[str] | None = None  # OAuth scopes for API/MCP tokens; None = auth session

    def has(self, role: Role) -> bool:
        return role in self.roles

    @property
    def is_admin(self) -> bool:
        return Role.ADMIN in self.roles

    def for_agent(self, agent_run_id: str, agent_name: str) -> ActorContext:
        return ActorContext(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            roles=self.roles,
            agent_run_id=agent_run_id,
            agent_name=agent_name,
            trace_id=self.trace_id,
            scopes=self.scopes,
        )

    def require_scope(self, scope: str) -> None:
        if self.scopes is not None and scope not in self.scopes:
            raise PermissionDenied(f"token lacks scope {scope}")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    requires_confirmation: bool = False

    def enforce(self) -> None:
        if not self.allowed:
            raise PermissionDenied(self.reason)


ALLOW = Decision(True)


def deny(reason: str) -> Decision:
    return Decision(False, reason)


# --- RBAC / subject level -----------------------------------------------------------

def can_manage_tenant(actor: ActorContext) -> Decision:
    return ALLOW if actor.is_admin else deny("admin role required")


def can_manage_cycle(actor: ActorContext, cycle: Cycle | None = None) -> Decision:
    if actor.is_admin:
        return ALLOW
    if actor.has(Role.CYCLE_OWNER) and (cycle is None or cycle.owner_id == actor.user_id):
        return ALLOW
    return deny("cycle owner of this cycle required")


def can_read_subject(
    actor: ActorContext,
    cycle: Cycle,
    subject_id: str,
    jobs: Iterable[CollectionJob],
    grants: Iterable[SubjectGrant],
) -> Decision:
    if actor.is_admin or actor.has(Role.VIEWER):
        return ALLOW
    if can_manage_cycle(actor, cycle).allowed:
        return ALLOW
    if any(j.subject_id == subject_id and j.assignee_id == actor.user_id for j in jobs):
        return ALLOW
    if any(g.subject_id == subject_id and g.user_id == actor.user_id for g in grants):
        return ALLOW
    return deny("no read access to subject")


def can_contribute(actor: ActorContext, job: CollectionJob, grants: Iterable[SubjectGrant] = ()) -> Decision:
    if job.assignee_id == actor.user_id:
        return ALLOW
    if any(
        g.subject_id == job.subject_id and g.user_id == actor.user_id and g.permission == "contribute"
        for g in grants
    ):
        return ALLOW
    return deny("only the assignee may contribute to this job")


# --- attachments --------------------------------------------------------------------

def attachment_visible(actor: ActorContext, attachment: Attachment, subject_readable: bool) -> Decision:
    if attachment.tenant_id != actor.tenant_id:
        return deny("foreign tenant")
    if attachment.status == AttachmentStatus.DELETED:
        return deny("attachment deleted")
    if attachment.uploaded_by == actor.user_id:
        return ALLOW
    if attachment.visibility == AttachmentVisibility.PRIVATE:
        return deny("private attachment")
    if attachment.status == AttachmentStatus.WITHDRAWN:
        return deny("attachment withdrawn")
    return ALLOW if subject_readable else deny("no read access to subject")


# --- tools / MCP ----------------------------------------------------------------------

def agent_allows_tool(allowed_tools: Iterable[str], tool_name: str) -> bool:
    return any(fnmatchcase(tool_name, pattern) for pattern in allowed_tools)


def classify_tool(
    server: McpServerDefinition, tool: str, read_only_hint: bool | None
) -> ToolClassification:
    override = server.tool_classification.get(tool)
    if override is not None:
        return ToolClassification(override)
    if read_only_hint is True:
        return ToolClassification.READ
    return ToolClassification.WRITE  # unknown = write


def effective_access_mode(server: McpServerDefinition, connection: UserConnection) -> AccessMode:
    return AccessMode.minimum(server.max_access_mode, connection.access_mode)


def mcp_tool_decision(
    actor: ActorContext,
    server: McpServerDefinition,
    connection: UserConnection | None,
    classification: ToolClassification,
    qualified_tool: str,
    agent_allowed_tools: Iterable[str] | None,
) -> Decision:
    """Intersection of admin policy, user grant and agent allowlist for one MCP tool."""
    if server.tenant_id != actor.tenant_id:
        return deny("foreign tenant")
    if not server.enabled:
        return deny("server disabled by admin policy")
    if connection is None or connection.user_id != actor.user_id:
        return deny("no connection of the acting user")
    if connection.status != ConnectionStatus.ACTIVE:
        return deny(f"connection {connection.status}")
    if agent_allowed_tools is not None and not agent_allows_tool(agent_allowed_tools, qualified_tool):
        return deny("tool not in agent allowlist")
    if classification == ToolClassification.WRITE:
        if not effective_access_mode(server, connection).allows_write():
            return deny("connection is read-only")
        return Decision(True, "write requires confirmation", requires_confirmation=True)
    return ALLOW
