"""Attachments in conversations: upload limits, visibility model, extraction, withdrawal, deletion."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

from ..adapters.file_processors import processor_for, sniff_mime
from ..adapters.sql.db import UnitOfWork
from ..domain import events
from ..domain.entities import Attachment, CollectionJob, Conversation
from ..domain.enums import AttachmentStatus, AttachmentVisibility
from ..domain.errors import LimitExceeded, NotFound, PermissionDenied, ValidationFailed
from ..domain.permissions import ActorContext, attachment_visible, can_contribute
from .base import ServiceBase

MalwareScanner = Callable[[bytes, str], bool]  # returns True if clean


def noop_scanner(content: bytes, filename: str) -> bool:
    return True


def sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r"[^\w.\- ()]+", "_", name).strip(" .")
    return (name or "file")[:200]


class AttachmentService(ServiceBase):
    scanner: MalwareScanner = staticmethod(noop_scanner)  # type: ignore[assignment]

    def upload(self, actor: ActorContext, job_id: str, filename: str, content: bytes,
               visibility: AttachmentVisibility = AttachmentVisibility.PRIVATE) -> Attachment:
        actor.require_scope("dpac.write")
        settings = self.app.settings
        job = self.app.services.cycles.get_job(actor, job_id)
        can_contribute(actor, job).enforce()
        if len(content) == 0:
            raise ValidationFailed("empty file")
        if len(content) > settings.attachment_max_bytes:
            raise LimitExceeded(f"file exceeds {settings.attachment_max_bytes} bytes")
        safe_name = sanitize_filename(filename)
        mime = sniff_mime(content, safe_name)
        if mime not in settings.attachment_mime_allowlist:
            raise ValidationFailed(f"file type {mime} is not allowed")
        if not self.scanner(content, safe_name):
            self.audit_now(actor, "attachment.upload", f"job:{job_id}", outcome="denied", reason="malware")
            raise ValidationFailed("file rejected by malware scan")
        content_hash = self.app.attachment_store.put(actor.tenant_id, content)
        with self.db.uow(actor.tenant_id) as uow:
            conv = uow.repo(Conversation).first(job_id=job_id)
            assert conv is not None
            att = Attachment(tenant_id=actor.tenant_id, content_hash=content_hash, filename=safe_name,
                             mime_type=mime, size=len(content), uploaded_by=actor.user_id,
                             conversation_id=conv.conversation_id, job_id=job_id, subject_id=job.subject_id,
                             visibility=AttachmentVisibility(visibility))
            uow.repo(Attachment).add(att)
            uow.publish(events.ATTACHMENT_UPLOADED, actor.user_id, {"attachment_id": att.attachment_id},
                        actor.trace_id)
            self.audit(uow, actor, "attachment.upload", f"attachment:{att.attachment_id}", mime=mime,
                       size=len(content), visibility=str(visibility), content_hash=content_hash)
        return att

    def process(self, actor: ActorContext, attachment_id: str) -> Attachment:
        """Worker: extract text with the matching FileProcessor."""
        with self.db.uow(actor.tenant_id) as uow:
            att = uow.repo(Attachment).get(attachment_id)
        if att.status != AttachmentStatus.ACTIVE or att.extracted_text is not None:
            return att
        proc = processor_for(self.app.registries.file_processors, att.mime_type)
        text = ""
        if proc is not None:
            try:
                text = proc.extract(self.app.attachment_store.get(actor.tenant_id, att.content_hash), att.filename,
                                    att.mime_type).text
            except Exception as exc:  # noqa: BLE001 - corrupt files must not break the worker
                text = f"[could not extract content: {type(exc).__name__}]"
        with self.db.uow(actor.tenant_id) as uow:
            att = uow.repo(Attachment).get(attachment_id)
            att.extracted_text = text
            uow.repo(Attachment).save(att)
        return att

    # --- reading -----------------------------------------------------------------------------
    def _visible(self, actor: ActorContext, att: Attachment) -> bool:
        readable = self.app.services.cycles.subject_read_decision(actor, att.subject_id).allowed
        return attachment_visible(actor, att, readable).allowed

    def get(self, actor: ActorContext, attachment_id: str) -> Attachment:
        with self.db.uow(actor.tenant_id) as uow:
            att = uow.repo(Attachment).find(attachment_id)
        if att is None or not self._visible(actor, att):
            raise NotFound("attachment not found")
        return att

    def content(self, actor: ActorContext, attachment_id: str) -> tuple[Attachment, bytes]:
        att = self.get(actor, attachment_id)
        if att.status == AttachmentStatus.DELETED:
            raise NotFound("attachment content erased")
        self.audit_now(actor, "attachment.download", f"attachment:{attachment_id}")
        return att, self.app.attachment_store.get(actor.tenant_id, att.content_hash)

    def list_for_job(self, actor: ActorContext, job_id: str) -> list[Attachment]:
        self.app.services.cycles.get_job(actor, job_id)
        with self.db.uow(actor.tenant_id) as uow:
            items = uow.repo(Attachment).list(job_id=job_id, order_by="uploaded_at")
        return [a for a in items if self._visible(actor, a)]

    def list_for_subject(self, actor: ActorContext, subject_id: str) -> list[Attachment]:
        self.app.services.cycles.require_subject_read(actor, subject_id)
        with self.db.uow(actor.tenant_id) as uow:
            items = uow.repo(Attachment).list(subject_id=subject_id, order_by="uploaded_at")
        return [a for a in items if self._visible(actor, a)]

    # --- visibility changes ---------------------------------------------------------------------
    def _own(self, actor: ActorContext, attachment_id: str, uow: UnitOfWork) -> Attachment:
        att = uow.repo(Attachment).find(attachment_id)
        if att is None or (att.uploaded_by != actor.user_id and not self._visible(actor, att)):
            raise NotFound("attachment not found")
        if att.uploaded_by != actor.user_id:
            raise PermissionDenied("only the uploader can change this attachment")
        return att

    def share(self, actor: ActorContext, attachment_id: str) -> Attachment:
        """private → shared (one-way)."""
        actor.require_scope("dpac.write")
        with self.db.uow(actor.tenant_id) as uow:
            att = self._own(actor, attachment_id, uow)
            if att.status != AttachmentStatus.ACTIVE:
                raise ValidationFailed(f"attachment is {att.status}")
            att.visibility = AttachmentVisibility.SHARED
            uow.repo(Attachment).save(att)
            self.audit(uow, actor, "attachment.share", f"attachment:{attachment_id}")
        return att

    def withdraw(self, actor: ActorContext, attachment_id: str) -> Attachment:
        """Withdraw a shared attachment: no longer visible to others (retention follows the Cycle)."""
        actor.require_scope("dpac.write")
        with self.db.uow(actor.tenant_id) as uow:
            att = self._own(actor, attachment_id, uow)
            if att.visibility != AttachmentVisibility.SHARED:
                raise ValidationFailed("only shared attachments can be withdrawn")
            att.status = AttachmentStatus.WITHDRAWN
            uow.repo(Attachment).save(att)
            self.audit(uow, actor, "attachment.withdraw", f"attachment:{attachment_id}")
        return att

    def delete(self, actor: ActorContext, attachment_id: str) -> Attachment:
        """Delete an own private attachment: content erased, metadata kept for the audit log."""
        actor.require_scope("dpac.write")
        with self.db.uow(actor.tenant_id) as uow:
            att = self._own(actor, attachment_id, uow)
            if att.visibility != AttachmentVisibility.PRIVATE:
                raise ValidationFailed("only private attachments can be deleted; withdraw shared ones")
            att.status = AttachmentStatus.DELETED
            att.extracted_text = None
            uow.repo(Attachment).save(att)
            self.erase_content_if_unused(uow, att)
            self.audit(uow, actor, "attachment.delete", f"attachment:{attachment_id}",
                       content_hash=att.content_hash)
        return att

    def erase_content_if_unused(self, uow: UnitOfWork, att: Attachment) -> None:
        """Content-addressed storage: erase bytes only when no other non-deleted attachment uses them."""
        others = [a for a in uow.repo(Attachment).list(content_hash=att.content_hash)
                  if a.attachment_id != att.attachment_id and a.status != AttachmentStatus.DELETED]
        if not others:
            self.app.attachment_store.erase(att.tenant_id, att.content_hash)

    def job_of(self, actor: ActorContext, attachment_id: str) -> CollectionJob:
        att = self.get(actor, attachment_id)
        return self.app.services.cycles.get_job(actor, att.job_id)
