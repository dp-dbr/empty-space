"""Database schema: generic entity tables + infrastructure tables."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Index, Integer, MetaData, String, Table, Text

from ...domain import entities as e
from .mapping import EntityMapping, UTCDateTime, build_mapping

metadata = MetaData()

MAPPINGS: dict[type, EntityMapping] = {}


def _m(cls: type, name: str, pk: str, indexes: list[tuple[str, ...]] | None = None) -> EntityMapping:
    mapping = build_mapping(metadata, cls, name, pk, indexes)
    MAPPINGS[cls] = mapping
    return mapping


_m(e.Tenant, "tenants", "tenant_id")
_m(e.User, "users", "user_id", [("tenant_id", "email")])
_m(e.SubjectGrant, "subject_grants", "grant_id", [("tenant_id", "subject_id")])
_m(e.Cycle, "cycles", "cycle_id")
_m(e.Subject, "subjects", "subject_id", [("tenant_id", "cycle_id"), ("tenant_id", "lineage_key")])
_m(e.CollectionJob, "collection_jobs", "job_id", [("tenant_id", "cycle_id"), ("tenant_id", "assignee_id")])
_m(e.Conversation, "conversations", "conversation_id", [("tenant_id", "job_id")])
_m(e.Message, "messages", "message_id", [("tenant_id", "conversation_id")])
_m(e.Attachment, "attachments", "attachment_id", [("tenant_id", "subject_id")])
_m(e.FactProposal, "fact_proposals", "proposal_id", [("tenant_id", "job_id")])
_m(e.McpServerDefinition, "mcp_server_definitions", "server_id")
_m(e.UserConnection, "user_connections", "connection_id", [("tenant_id", "user_id")])
_m(e.AgentRun, "agent_runs", "agent_run_id", [("tenant_id", "job_id")])
_m(e.PendingAction, "pending_actions", "action_id", [("tenant_id", "job_id")])
_m(e.AuditEvent, "audit_events", "audit_id", [("tenant_id", "actor_id")])

outbox = Table(
    "outbox", metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String(255), nullable=False),
    Column("type", String(128), nullable=False),
    Column("actor_id", String(255), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("trace_id", String(64)),
    Column("status", String(16), nullable=False, default="pending"),
    Column("attempts", Integer, nullable=False, default=0),
    Column("available_at", UTCDateTime(), nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("last_error", Text),
)
Index("ix_outbox_status_available", outbox.c.status, outbox.c.available_at)

credential_secrets = Table(
    "credential_secrets", metadata,
    Column("ref", String(64), primary_key=True),
    Column("tenant_id", String(255), nullable=False, index=True),
    Column("owner_id", String(255), nullable=False),
    Column("ciphertext", Text, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("updated_at", UTCDateTime(), nullable=False),
)

auth_sessions = Table(
    "auth_sessions", metadata,
    Column("session_hash", String(128), primary_key=True),
    Column("tenant_id", String(255), nullable=False, index=True),
    Column("user_id", String(255), nullable=False),
    Column("csrf_token", String(128), nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("expires_at", UTCDateTime(), nullable=False),
)

login_attempts = Table(
    "login_attempts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key", String(255), nullable=False, index=True),
    Column("at", UTCDateTime(), nullable=False),
    Column("success", Boolean, nullable=False),
)

oauth_clients = Table(
    "oauth_clients", metadata,
    Column("client_id", String(128), primary_key=True),
    Column("tenant_id", String(255), nullable=False, index=True),
    Column("name", String(255), nullable=False),
    Column("redirect_uris", JSON, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
)

oauth_codes = Table(
    "oauth_codes", metadata,
    Column("code_hash", String(128), primary_key=True),
    Column("tenant_id", String(255), nullable=False),
    Column("client_id", String(128), nullable=False),
    Column("user_id", String(255), nullable=False),
    Column("redirect_uri", Text, nullable=False),
    Column("scopes", JSON, nullable=False),
    Column("code_challenge", String(128), nullable=False),
    Column("expires_at", UTCDateTime(), nullable=False),
    Column("used", Boolean, nullable=False, default=False),
)

oauth_tokens = Table(
    "oauth_tokens", metadata,
    Column("token_hash", String(128), primary_key=True),
    Column("kind", String(16), nullable=False),  # access | refresh
    Column("tenant_id", String(255), nullable=False, index=True),
    Column("client_id", String(128), nullable=False),
    Column("user_id", String(255), nullable=False),
    Column("scopes", JSON, nullable=False),
    Column("expires_at", UTCDateTime(), nullable=False),
    Column("revoked", Boolean, nullable=False, default=False),
)

artifact_projection = Table(
    "artifact_projection", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String(255), nullable=False),
    Column("subject_id", String(255), nullable=False),
    Column("subject_type", String(255), nullable=False),
    Column("lineage_key", String(255), nullable=False),
    Column("cycle_id", String(255), nullable=False),
    Column("version", Integer, nullable=False),
    Column("based_on", Integer),
    Column("status", String(32), nullable=False),
    Column("schema_version", String(32), nullable=False),
    Column("contributor_id", String(255), nullable=False),
    Column("agent_run_id", String(255)),
    Column("created", UTCDateTime(), nullable=False),
    Column("content_hash", String(128), nullable=False),
    Column("change_reason", Text, nullable=False),
    Column("data", JSON, nullable=False),
    Column("is_latest", Boolean, nullable=False),
)
Index("ix_projection_subject_version", artifact_projection.c.tenant_id, artifact_projection.c.subject_id,
      artifact_projection.c.version, unique=True)
Index("ix_projection_cycle_latest", artifact_projection.c.tenant_id, artifact_projection.c.cycle_id,
      artifact_projection.c.is_latest)
