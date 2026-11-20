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


class CycleIn(BaseModel):
    name: str
    subject_type: str = "application"
    scope: str = ""
    due_date: datetime | None = None
    recurrence: str = "one_off"
    previous_cycle_id: str | None = None
    locale: str = "en"
    agent_definition: str = "collector"
    reminder_after_days: int = 3
    escalate_after_days: int = 7


class SubjectIn(BaseModel):
    name: str
    assignee_id: str
    lineage_key: str | None = None
    owners: list[str] | None = None
    due_date: datetime | None = None
    channel: str = "web"
    prefill: dict[str, Any] | None = None


class SnapshotIn(BaseModel):
    name: str


class MessageIn(BaseModel):
    text: str = ""
    attachment_ids: list[str] = Field(default_factory=list)


class ProposalDecisionIn(BaseModel):
    decision: str  # confirm | reject | edit
    value: Any = None
    visibility_ack: bool = False


class ActionDecisionIn(BaseModel):
    confirm: bool


class SubmitIn(BaseModel):
    change_reason: str = "submitted"


class ResolveIn(BaseModel):
    resolutions: dict[str, Any]
    change_reason: str = "submitted after conflict resolution"


class ApproveIn(BaseModel):
    comment: str = ""


class ArtifactOut(BaseModel):
    header: dict[str, Any]
    data: dict[str, Any]
    factsheet: str
    integrity_ok: bool

    @staticmethod
    def of(v: Any) -> ArtifactOut:
        return ArtifactOut(header=v.header.to_dict(), data=v.data, factsheet=v.factsheet_body, integrity_ok=v.verify())


class DiffOut(BaseModel):
    from_ref: str
    to_ref: str
    field_changes: list[dict[str, Any]]
    factsheet_diff: str
    from_factsheet: str = ""
    to_factsheet: str = ""


class SubmitOut(BaseModel):
    version: ArtifactOut | None
    conflict: dict[str, Any] | None


class SnapshotOut(BaseModel):
    cycle_id: str
    name: str
    created: datetime
    created_by: str
    versions: dict[str, int]


class AttachmentOut(BaseModel):
    attachment_id: str
    filename: str
    mime_type: str
    size: int
    uploaded_by: str
    uploaded_at: datetime
    visibility: str
    status: str
    content_hash: str
    job_id: str
    subject_id: str
    extracted: bool

    @staticmethod
    def of(a: Any) -> AttachmentOut:
        return AttachmentOut(attachment_id=a.attachment_id, filename=a.filename, mime_type=a.mime_type, size=a.size,
                             uploaded_by=a.uploaded_by, uploaded_at=a.uploaded_at, visibility=str(a.visibility),
                             status=str(a.status), content_hash=a.content_hash, job_id=a.job_id,
                             subject_id=a.subject_id, extracted=a.extracted_text is not None)
