"""Outbox event types (versioned contract: ``<name>.v1`` payloads)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CYCLE_STARTED = "cycle.started"
JOB_CREATED = "job.created"
MESSAGE_POSTED = "conversation.message_posted"
ATTACHMENT_UPLOADED = "attachment.uploaded"
ACTION_CONFIRMED = "pending_action.confirmed"
ARTIFACT_SUBMITTED = "artifact.submitted"
ARTIFACT_APPROVED = "artifact.approved"
TICK = "scheduler.tick"
CONNECTION_CHANGED = "connection.changed"


@dataclass(frozen=True)
class Event:
    type: str
    tenant_id: str
    actor_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    event_id: int | None = None
    attempts: int = 0
