from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    CYCLE_OWNER = "cycle_owner"
    RESPONDENT = "respondent"
    VIEWER = "viewer"


class Locale(StrEnum):
    DE = "de"
    FR = "fr"
    IT = "it"
    EN = "en"


class CycleStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    CLOSED = "closed"


class CycleRecurrence(StrEnum):
    ONE_OFF = "one_off"
    RECURRING = "recurring"


class JobState(StrEnum):
    CREATED = "created"
    NOTIFIED = "notified"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    CLOSED = "closed"
    OVERDUE = "overdue"
    ESCALATED = "escalated"


class ArtifactStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    VALIDATED = "validated"
    APPROVED = "approved"


class ParticipantKind(StrEnum):
    USER = "user"
    AGENT = "agent"


class MessageKind(StrEnum):
    TEXT = "text"
    FACT_PROPOSAL = "fact_proposal"
    CONFIRMATION_REQUEST = "confirmation_request"
    CONFLICT = "conflict"
    NOTICE = "notice"
    VALIDATION = "validation"


class ProposalStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    APPLIED = "applied"


class AttachmentVisibility(StrEnum):
    PRIVATE = "private"
    SHARED = "shared"


class AttachmentStatus(StrEnum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"
    DELETED = "deleted"


class AccessMode(StrEnum):
    READ = "read"
    READ_WRITE = "read_write"

    def allows_write(self) -> bool:
        return self is AccessMode.READ_WRITE

    @staticmethod
    def minimum(a: AccessMode, b: AccessMode) -> AccessMode:
        return AccessMode.READ if AccessMode.READ in (a, b) else AccessMode.READ_WRITE


class ToolClassification(StrEnum):
    READ = "read"
    WRITE = "write"


class McpTransport(StrEnum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class McpAuthType(StrEnum):
    NONE = "none"
    OAUTH2 = "oauth2"
    API_KEY = "api_key"


class ConnectionStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class PendingActionStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class AgentRunStatus(StrEnum):
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    DEAD = "dead"
