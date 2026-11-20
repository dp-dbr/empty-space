"""Tenants, users, roles, subject grants, audit log, erasure."""

from __future__ import annotations

from typing import Any

from ..adapters.identity import hash_password
from ..adapters.sql.schema import auth_sessions
from ..domain.entities import (
    Attachment,
    AuditEvent,
    ExternalIdentity,
    Message,
    SubjectGrant,
    Tenant,
    User,
    UserConnection,
)
from ..domain.enums import AttachmentStatus, AttachmentVisibility, Role
from ..domain.errors import NotFound, ValidationFailed
from ..domain.permissions import ActorContext, can_manage_cycle, can_manage_tenant
from .base import ServiceBase


class TenancyService(ServiceBase):
    # --- system level (bootstrap / CLI) ------------------------------------------------
    def create_tenant(self, name: str, settings: dict[str, Any] | None = None) -> Tenant:
        with self.db.uow(None) as uow:
            if uow.repo(Tenant).first(name=name):
                raise ValidationFailed(f"tenant {name} exists")
            t = Tenant(name=name, settings=settings or {})
            uow.repo(Tenant).add(t)
        return t

    def tenant_by_name(self, name: str) -> Tenant:
        with self.db.uow(None) as uow:
            t = uow.repo(Tenant).first(name=name)
        if t is None:
            raise NotFound(f"tenant {name} not found")
        return t

    def get_tenant(self, actor: ActorContext) -> Tenant:
        with self.db.uow(None) as uow:
            return uow.repo(Tenant).get(actor.tenant_id)

    def update_tenant_settings(self, actor: ActorContext, settings: dict[str, Any]) -> Tenant:
        can_manage_tenant(actor).enforce()
        with self.db.uow(None) as uow:
            t = uow.repo(Tenant).get(actor.tenant_id)
            t.settings = {**t.settings, **settings}
            uow.repo(Tenant).save(t)
        self.audit_now(actor, "tenant.update_settings", f"tenant:{actor.tenant_id}", keys=sorted(settings))
        return t

    def bootstrap_user(self, tenant_id: str, name: str, email: str, password: str | None,
                       roles: list[Role], locale: str = "en") -> User:
        """System-level user creation (CLI/seed). API uses ``create_user``."""
        user = User(tenant_id=tenant_id, name=name, email=email.strip().lower(), roles=list(roles),
                    locale=locale, password_hash=hash_password(password) if password else None)
        with self.db.uow(tenant_id) as uow:
            if uow.repo(User).first(email=user.email):
                raise ValidationFailed(f"user {email} exists")
            uow.repo(User).add(user)
        return user

    # --- admin -----------------------------------------------------------------------
    def create_user(self, actor: ActorContext, name: str, email: str, roles: list[Role],
                    locale: str = "en", password: str | None = None,
                    external_identities: list[ExternalIdentity] | None = None) -> User:
        can_manage_tenant(actor).enforce()
        user = self.bootstrap_user(actor.tenant_id, name, email, password, roles, locale)
        if external_identities:
            user.external_identities = external_identities
            with self.db.uow(actor.tenant_id) as uow:
                uow.repo(User).save(user)
        self.audit_now(actor, "user.create", f"user:{user.user_id}", roles=[str(r) for r in roles])
        return user

    def update_user(self, actor: ActorContext, user_id: str, *, roles: list[Role] | None = None,
                    active: bool | None = None, locale: str | None = None, name: str | None = None,
                    password: str | None = None) -> User:
        if actor.user_id != user_id or roles is not None or active is not None:
            can_manage_tenant(actor).enforce()
        with self.db.uow(actor.tenant_id) as uow:
            user = uow.repo(User).get(user_id)
            if roles is not None:
                user.roles = list(roles)
            if active is not None:
                user.active = active
            if locale is not None:
                user.locale = locale
            if name is not None:
                user.name = name
            if password is not None:
                user.password_hash = hash_password(password)
            uow.repo(User).save(user)
            self.audit(uow, actor, "user.update", f"user:{user_id}",
                       roles=[str(r) for r in roles] if roles is not None else None, active=active)
        return user

    def get_user(self, actor: ActorContext, user_id: str) -> User:
        with self.db.uow(actor.tenant_id) as uow:
            return uow.repo(User).get(user_id)

    def list_users(self, actor: ActorContext) -> list[User]:
        if not (actor.is_admin or can_manage_cycle(actor).allowed):
            can_manage_tenant(actor).enforce()
        with self.db.uow(actor.tenant_id) as uow:
            return uow.repo(User).list(order_by="name")

    def grant_subject(self, actor: ActorContext, subject_id: str, user_id: str, permission: str = "read") -> SubjectGrant:
        if permission not in ("read", "contribute"):
            raise ValidationFailed("permission must be read or contribute")
        cycle = self.services.cycles.cycle_of_subject(actor, subject_id)
        can_manage_cycle(actor, cycle).enforce()
        g = SubjectGrant(tenant_id=actor.tenant_id, subject_id=subject_id, user_id=user_id, permission=permission)
        with self.db.uow(actor.tenant_id) as uow:
            uow.repo(SubjectGrant).add(g)
            self.audit(uow, actor, "subject.grant", f"subject:{subject_id}", user_id=user_id, permission=permission)
        return g

    def audit_log(self, actor: ActorContext, *, mine: bool = False, limit: int = 200) -> list[AuditEvent]:
        with self.db.uow(actor.tenant_id) as uow:
            if mine:
                return uow.repo(AuditEvent).list(order_by="-at", limit=limit, actor_id=actor.user_id)
            can_manage_tenant(actor).enforce()
            return uow.repo(AuditEvent).list(order_by="-at", limit=limit)

    def erase_user(self, actor: ActorContext, user_id: str) -> None:
        """Erasure strategy (ADR-0011): pseudonymize, drop credentials/sessions, erase private files."""
        can_manage_tenant(actor).enforce()
        from sqlalchemy import delete

        with self.db.uow(actor.tenant_id) as uow:
            user = uow.repo(User).get(user_id)
            user.name, user.email = "erased user", f"erased-{user.user_id}@invalid"
            user.external_identities, user.password_hash = [], None
            user.active, user.erased = False, True
            uow.repo(User).save(user)
            for con in uow.repo(UserConnection).list(user_id=user_id):
                if con.credential_ref:
                    self.app.vault.delete(actor.tenant_id, con.credential_ref)
                uow.repo(UserConnection).remove(con.connection_id)
            uow.conn.execute(delete(auth_sessions).where(auth_sessions.c.user_id == user_id))
            for att in uow.repo(Attachment).list(uploaded_by=user_id, visibility=str(AttachmentVisibility.PRIVATE)):
                att.status = AttachmentStatus.DELETED
                att.extracted_text = None
                uow.repo(Attachment).save(att)
                self.services.attachments.erase_content_if_unused(uow, att)
            for msg in uow.repo(Message).list():
                if msg.sender.ref == user_id:
                    msg.text, msg.payload = "[erased]", {}
                    uow.repo(Message).save(msg)
            self.audit(uow, actor, "user.erase", f"user:{user_id}")
