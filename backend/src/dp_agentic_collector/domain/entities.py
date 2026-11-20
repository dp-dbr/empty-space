"""Domain entities. Every entity carries ``tenant_id``.

Entities are plain dataclasses; persistence adapters map them to tables generically
(see ``adapters/sql/mapping.py``). Field names are part of the persistence contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enums import (
    AccessMode,
    AgentRunStatus,
    ArtifactStatus,
    AttachmentStatus,
    AttachmentVisibility,
    ConnectionStatus,
    CycleRecurrence,
    CycleStatus,
    JobState,
    McpAuthType,
    McpTransport,
    MessageKind,
    ParticipantKind,
    PendingActionStatus,
    ProposalStatus,
    Role,
    ToolClassification,
)
from .ids import new_id, utcnow


@dataclass
class Tenant:
    name: str
    settings: dict[str, Any] = field(default_factory=dict)
    tenant_id: str = field(default_factory=lambda: new_id("ten"))
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class ExternalIdentity:
    provider: str
    subject: str


@dataclass
class User:
    tenant_id: str
    name: str
    email: str
    locale: str = "en"
    roles: list[Role] = field(default_factory=list)
    active: bool = True
    external_identities: list[ExternalIdentity] = field(default_factory=list)
    password_hash: str | None = None
    erased: bool = False
    user_id: str = field(default_factory=lambda: new_id("usr"))
    created_at: datetime = field(default_factory=utcnow)

    def has_role(self, role: Role) -> bool:
        return role in self.roles


@dataclass
class SubjectGrant:
    """Subject-level access in addition to RBAC (``read`` or ``contribute``)."""

    tenant_id: str
    subject_id: str
    user_id: str
    permission: str = "read"
    grant_id: str = field(default_factory=lambda: new_id("grt"))


@dataclass
class Cycle:
    tenant_id: str
    name: str
    subject_type: str
    owner_id: str
    scope: str = ""
    participants: list[str] = field(default_factory=list)
    due_date: datetime | None = None
    reminder_after_days: int = 3
    escalate_after_days: int = 7
    recurrence: CycleRecurrence = CycleRecurrence.ONE_OFF
    previous_cycle_id: str | None = None
    status: CycleStatus = CycleStatus.PLANNED
    locale: str = "en"
    agent_definition: str = "collector"
    cycle_id: str = field(default_factory=lambda: new_id("cyc"))
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Subject:
    tenant_id: str
    cycle_id: str
    subject_type: str
    name: str
    lineage_key: str
    owners: list[str] = field(default_factory=list)
    subject_id: str = field(default_factory=lambda: new_id("sub"))
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class CollectionJob:
    tenant_id: str
    subject_id: str
    cycle_id: str
    assignee_id: str
    due_date: datetime | None = None
    channel: str = "web"
    prefill: dict[str, Any] = field(default_factory=dict)
    state: JobState = JobState.CREATED
    base_version: int | None = None
    conflict: dict[str, Any] | None = None
    last_activity_at: datetime = field(default_factory=utcnow)
    reminders_sent: int = 0
    job_id: str = field(default_factory=lambda: new_id("job"))
    created_at: datetime = field(default_factory=utcnow)
    state_changed_at: datetime = field(default_factory=utcnow)


@dataclass
class Participant:
    kind: ParticipantKind
    ref: str  # user_id or agent definition name
    display: str = ""


@dataclass
class Conversation:
    tenant_id: str
    job_id: str
    subject_id: str
    participants: list[Participant] = field(default_factory=list)
    conversation_id: str = field(default_factory=lambda: new_id("cnv"))
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Message:
    tenant_id: str
    conversation_id: str
    sender: Participant
    kind: MessageKind
    text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    attachment_ids: list[str] = field(default_factory=list)
    # Messages produced for/with a private context are only visible to these user ids.
    visible_to: list[str] | None = None
    agent_run_id: str | None = None
    message_id: str = field(default_factory=lambda: new_id("msg"))
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Attachment:
    tenant_id: str
    content_hash: str
    filename: str
    mime_type: str
    size: int
    uploaded_by: str
    conversation_id: str
    job_id: str
    subject_id: str
    visibility: AttachmentVisibility = AttachmentVisibility.PRIVATE
    status: AttachmentStatus = AttachmentStatus.ACTIVE
    extracted_text: str | None = None
    attachment_id: str = field(default_factory=lambda: new_id("att"))
    uploaded_at: datetime = field(default_factory=utcnow)


@dataclass
class FactProposal:
    tenant_id: str
    job_id: str
    conversation_id: str
    path: str
    value: Any
    proposed_by: str  # agent_run_id or user_id
    rationale: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    requires_visibility_confirmation: bool = False
    status: ProposalStatus = ProposalStatus.PROPOSED
    decided_by: str | None = None
    proposal_id: str = field(default_factory=lambda: new_id("prp"))
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class McpServerDefinition:
    tenant_id: str
    name: str
    transport: McpTransport
    endpoint: str  # URL for streamable HTTP; command line for stdio
    auth_type: McpAuthType = McpAuthType.NONE
    max_access_mode: AccessMode = AccessMode.READ
    tool_classification: dict[str, ToolClassification] = field(default_factory=dict)
    oauth: dict[str, Any] = field(default_factory=dict)  # authorize_url, token_url, client_id, scopes
    enabled: bool = True
    description: str = ""
    server_id: str = field(default_factory=lambda: new_id("mcp"))
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class UserConnection:
    tenant_id: str
    user_id: str
    server_id: str
    access_mode: AccessMode = AccessMode.READ
    credential_ref: str | None = None
    status: ConnectionStatus = ConnectionStatus.ACTIVE
    pending_oauth: dict[str, Any] | None = None
    connected_at: datetime = field(default_factory=utcnow)
    last_used_at: datetime | None = None
    connection_id: str = field(default_factory=lambda: new_id("con"))


@dataclass
class AgentRun:
    tenant_id: str
    agent: str
    on_behalf_of: str
    trigger: str
    job_id: str | None = None
    status: AgentRunStatus = AgentRunStatus.RUNNING
    trace_id: str | None = None
    provider: str = ""
    model: str = ""
    error: str | None = None
    agent_run_id: str = field(default_factory=lambda: new_id("run"))
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None


@dataclass
class PendingAction:
    """A write tool call awaiting explicit user confirmation."""

    tenant_id: str
    user_id: str
    job_id: str
    conversation_id: str
    agent_run_id: str
    tool: str  # qualified name "mcp:<server_id>:<tool>" or internal tool name
    arguments: dict[str, Any]
    summary: str
    target: str
    status: PendingActionStatus = PendingActionStatus.PENDING
    result: dict[str, Any] | None = None
    action_id: str = field(default_factory=lambda: new_id("act"))
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class AuditEvent:
    tenant_id: str
    actor_id: str | None
    action: str
    resource: str
    outcome: str = "ok"
    agent_run_id: str | None = None
    trace_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    audit_id: str = field(default_factory=lambda: new_id("aud"))
    at: datetime = field(default_factory=utcnow)


@dataclass
class ArtifactStatusInfo:
    """Light read model used by services; the full version lives in the ArtifactStore."""

    subject_id: str
    version: int
    status: ArtifactStatus
