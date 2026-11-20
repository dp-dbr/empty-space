"""Cycles, subjects, collection jobs, snapshots and Cycle initialization."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..adapters.sql.db import UnitOfWork
from ..domain import events
from ..domain.artifacts import Contributor, NewArtifact, Snapshot, SourceRef
from ..domain.entities import (
    CollectionJob,
    Conversation,
    Cycle,
    Participant,
    Subject,
    SubjectGrant,
    User,
)
from ..domain.enums import (
    ArtifactStatus,
    CycleRecurrence,
    CycleStatus,
    JobState,
    ParticipantKind,
    Role,
)
from ..domain.errors import NotFound, PermissionDenied, ValidationFailed
from ..domain.ids import utcnow
from ..domain.permissions import ActorContext, Decision, can_manage_cycle, can_read_subject
from ..domain.state_machines import transition_job
from .base import ServiceBase


class CycleService(ServiceBase):
    # --- cycles ---------------------------------------------------------------------
    def create_cycle(self, actor: ActorContext, name: str, subject_type: str, *, scope: str = "",
                     due_date: datetime | None = None, participants: list[str] | None = None,
                     recurrence: CycleRecurrence = CycleRecurrence.ONE_OFF, previous_cycle_id: str | None = None,
                     locale: str = "en", agent_definition: str = "collector",
                     reminder_after_days: int = 3, escalate_after_days: int = 7) -> Cycle:
        can_manage_cycle(actor).enforce()
        actor.require_scope("dpac.write")
        self.app.registries.subject_types.get(subject_type)
        self.app.registries.agent_definitions.get(agent_definition)
        cycle = Cycle(tenant_id=actor.tenant_id, name=name, subject_type=subject_type, owner_id=actor.user_id,
                      scope=scope, participants=participants or [], due_date=due_date, recurrence=recurrence,
                      previous_cycle_id=previous_cycle_id, locale=locale, agent_definition=agent_definition,
                      reminder_after_days=reminder_after_days, escalate_after_days=escalate_after_days)
        with self.db.uow(actor.tenant_id) as uow:
            if uow.repo(Cycle).first(name=name):
                raise ValidationFailed(f"cycle {name} exists")
            if previous_cycle_id:
                uow.repo(Cycle).get(previous_cycle_id)
            uow.repo(Cycle).add(cycle)
            self.audit(uow, actor, "cycle.create", f"cycle:{cycle.cycle_id}", name=name)
        return cycle

    def get_cycle(self, actor: ActorContext, cycle_id: str) -> Cycle:
        with self.db.uow(actor.tenant_id) as uow:
            cycle = uow.repo(Cycle).get(cycle_id)
            if not self._can_see_cycle(uow, actor, cycle):
                raise NotFound(f"cycles {cycle_id} not found")
        return cycle

    def _can_see_cycle(self, uow: UnitOfWork, actor: ActorContext, cycle: Cycle) -> bool:
        if actor.is_admin or actor.has(Role.VIEWER) or cycle.owner_id == actor.user_id:
            return True
        if uow.repo(CollectionJob).first(cycle_id=cycle.cycle_id, assignee_id=actor.user_id):
            return True
        subject_ids = [s.subject_id for s in uow.repo(Subject).list(cycle_id=cycle.cycle_id)]
        return bool(subject_ids and uow.repo(SubjectGrant).first(user_id=actor.user_id, subject_id=subject_ids))

    def list_cycles(self, actor: ActorContext) -> list[Cycle]:
        with self.db.uow(actor.tenant_id) as uow:
            return [c for c in uow.repo(Cycle).list(order_by="-created_at") if self._can_see_cycle(uow, actor, c)]

    def start_cycle(self, actor: ActorContext, cycle_id: str) -> Cycle:
        with self.db.uow(actor.tenant_id) as uow:
            cycle = uow.repo(Cycle).get(cycle_id)
            can_manage_cycle(actor, cycle).enforce()
            cycle.status = CycleStatus.ACTIVE
            uow.repo(Cycle).save(cycle)
            uow.publish(events.CYCLE_STARTED, actor.user_id, {"cycle_id": cycle_id}, actor.trace_id)
            self.audit(uow, actor, "cycle.start", f"cycle:{cycle_id}")
        return cycle

    def close_cycle(self, actor: ActorContext, cycle_id: str) -> Cycle:
        with self.db.uow(actor.tenant_id) as uow:
            cycle = uow.repo(Cycle).get(cycle_id)
            can_manage_cycle(actor, cycle).enforce()
            cycle.status = CycleStatus.CLOSED
            uow.repo(Cycle).save(cycle)
            self.audit(uow, actor, "cycle.close", f"cycle:{cycle_id}")
        return cycle

    # --- subjects & jobs ------------------------------------------------------------------
    def add_subject(self, actor: ActorContext, cycle_id: str, name: str, assignee_id: str, *,
                    lineage_key: str | None = None, owners: list[str] | None = None,
                    due_date: datetime | None = None, channel: str = "web",
                    prefill: dict[str, Any] | None = None) -> tuple[Subject, CollectionJob]:
        if channel not in self.app.registries.channels:
            raise ValidationFailed(f"unknown channel {channel}")
        with self.db.uow(actor.tenant_id) as uow:
            cycle = uow.repo(Cycle).get(cycle_id)
            can_manage_cycle(actor, cycle).enforce()
            subject, job = self._create_subject_job(uow, actor, cycle, name, assignee_id,
                                                    lineage_key or _slug(name), owners or [assignee_id],
                                                    due_date or cycle.due_date, channel, prefill or {})
        return subject, job

    def _create_subject_job(self, uow: UnitOfWork, actor: ActorContext, cycle: Cycle, name: str,
                            assignee_id: str, lineage_key: str, owners: list[str], due_date: datetime | None,
                            channel: str, prefill: dict[str, Any], base_version: int | None = None,
                            subject_id: str | None = None) -> tuple[Subject, CollectionJob]:
        assignee = uow.repo(User).get(assignee_id)
        if not assignee.active:
            raise ValidationFailed("assignee is inactive")
        if uow.repo(Subject).first(cycle_id=cycle.cycle_id, lineage_key=lineage_key):
            raise ValidationFailed(f"subject {lineage_key} already exists in this cycle")
        subject = Subject(tenant_id=actor.tenant_id, cycle_id=cycle.cycle_id, subject_type=cycle.subject_type,
                          name=name, lineage_key=lineage_key, owners=owners)
        if subject_id:
            subject.subject_id = subject_id
        job = CollectionJob(tenant_id=actor.tenant_id, subject_id=subject.subject_id, cycle_id=cycle.cycle_id,
                            assignee_id=assignee_id, due_date=due_date, channel=channel, prefill=prefill,
                            base_version=base_version)
        conv = Conversation(tenant_id=actor.tenant_id, job_id=job.job_id, subject_id=subject.subject_id,
                            participants=[Participant(ParticipantKind.USER, assignee_id, assignee.name),
                                          Participant(ParticipantKind.AGENT, cycle.agent_definition, "Collector")])
        uow.repo(Subject).add(subject)
        uow.repo(CollectionJob).add(job)
        uow.repo(Conversation).add(conv)
        self.audit(uow, actor, "job.create", f"job:{job.job_id}", subject_id=subject.subject_id,
                   assignee_id=assignee_id)
        if cycle.status == CycleStatus.ACTIVE:
            uow.publish(events.JOB_CREATED, actor.user_id, {"job_id": job.job_id}, actor.trace_id)
        return subject, job

    def init_from_previous(self, actor: ActorContext, cycle_id: str) -> list[CollectionJob]:
        """Initialize a recurring Cycle from the approved state of its predecessor (ADR-0003)."""
        with self.db.uow(actor.tenant_id) as uow:
            cycle = uow.repo(Cycle).get(cycle_id)
            can_manage_cycle(actor, cycle).enforce()
            if not cycle.previous_cycle_id:
                raise ValidationFailed("cycle has no previous_cycle_id")
            prev_subjects = uow.repo(Subject).list(cycle_id=cycle.previous_cycle_id)
            prev_jobs = {j.subject_id: j for j in uow.repo(CollectionJob).list(cycle_id=cycle.previous_cycle_id)}
        created = []
        store = self.app.artifact_store
        for ps in prev_subjects:
            approved = next((v for v in reversed(store.history(actor.tenant_id, ps.subject_id))
                             if v.header.status == ArtifactStatus.APPROVED), None)
            prev_job = prev_jobs.get(ps.subject_id)
            if prev_job is None:
                continue
            with self.db.uow(actor.tenant_id) as uow:
                subject, job = self._create_subject_job(
                    uow, actor, cycle, ps.name, prev_job.assignee_id, ps.lineage_key, ps.owners, cycle.due_date,
                    prev_job.channel, {}, base_version=1 if approved else None)
                uow.repo(SubjectGrant).add(SubjectGrant(tenant_id=actor.tenant_id, subject_id=ps.subject_id,
                                                        user_id=prev_job.assignee_id, permission="read"))
            if approved:
                src = SourceRef(kind="artifact", on_behalf_of=actor.user_id, retrieved_at=utcnow().isoformat(),
                                reference=f"{ps.subject_id}@v{approved.header.version}",
                                content_hash=approved.header.content_hash)
                version = store.put(NewArtifact(
                    tenant_id=actor.tenant_id, subject_id=subject.subject_id, cycle=cycle.cycle_id, based_on=None,
                    schema_version=approved.header.schema_version, locale=approved.header.locale,
                    status=ArtifactStatus.DRAFT, contributor=Contributor(actor.user_id),
                    change_reason=f"initialized from cycle {cycle.previous_cycle_id}", data=approved.data,
                    factsheet_body=approved.factsheet_body, sources=(src,)))
                self.app.projection.project(version, subject.subject_type, subject.lineage_key)
            created.append(job)
        self.audit_now(actor, "cycle.init_from_previous", f"cycle:{cycle_id}", jobs=len(created))
        return created

    def subjects(self, actor: ActorContext, cycle_id: str) -> list[Subject]:
        cycle = self.get_cycle(actor, cycle_id)
        with self.db.uow(actor.tenant_id) as uow:
            subs = uow.repo(Subject).list(cycle_id=cycle_id, order_by="name")
            jobs = uow.repo(CollectionJob).list(cycle_id=cycle_id)
            grants = uow.repo(SubjectGrant).list(user_id=actor.user_id)
        return [s for s in subs if can_read_subject(actor, cycle, s.subject_id, jobs, grants).allowed]

    def jobs(self, actor: ActorContext, cycle_id: str) -> list[CollectionJob]:
        with self.db.uow(actor.tenant_id) as uow:
            cycle = uow.repo(Cycle).get(cycle_id)
            jobs = uow.repo(CollectionJob).list(cycle_id=cycle_id, order_by="created_at")
        if can_manage_cycle(actor, cycle).allowed or actor.has(Role.VIEWER):
            return jobs
        return [j for j in jobs if j.assignee_id == actor.user_id]

    def my_jobs(self, actor: ActorContext) -> list[CollectionJob]:
        with self.db.uow(actor.tenant_id) as uow:
            return uow.repo(CollectionJob).list(assignee_id=actor.user_id, order_by="-created_at")

    def get_job(self, actor: ActorContext, job_id: str) -> CollectionJob:
        with self.db.uow(actor.tenant_id) as uow:
            job = uow.repo(CollectionJob).get(job_id)
            cycle = uow.repo(Cycle).get(job.cycle_id)
            grants = uow.repo(SubjectGrant).list(user_id=actor.user_id, subject_id=job.subject_id)
        if job.assignee_id != actor.user_id and not can_read_subject(actor, cycle, job.subject_id, [job],
                                                                     grants).allowed:
            raise NotFound(f"collection_jobs {job_id} not found")
        return job

    def get_subject(self, actor: ActorContext, subject_id: str) -> Subject:
        self.require_subject_read(actor, subject_id)
        with self.db.uow(actor.tenant_id) as uow:
            return uow.repo(Subject).get(subject_id)

    def cycle_of_subject(self, actor: ActorContext, subject_id: str) -> Cycle:
        with self.db.uow(actor.tenant_id) as uow:
            subject = uow.repo(Subject).get(subject_id)
            return uow.repo(Cycle).get(subject.cycle_id)

    def subject_read_decision(self, actor: ActorContext, subject_id: str) -> Decision:
        with self.db.uow(actor.tenant_id) as uow:
            subject = uow.repo(Subject).find(subject_id)
            if subject is None:
                return Decision(False, "subject not found")
            cycle = uow.repo(Cycle).get(subject.cycle_id)
            jobs = uow.repo(CollectionJob).list(subject_id=subject_id)
            grants = uow.repo(SubjectGrant).list(subject_id=subject_id)
        return can_read_subject(actor, cycle, subject_id, jobs, grants)

    def require_subject_read(self, actor: ActorContext, subject_id: str) -> None:
        d = self.subject_read_decision(actor, subject_id)
        if not d.allowed:
            # do not leak existence across permission boundaries
            raise NotFound(f"subjects {subject_id} not found") if "not found" in d.reason else PermissionDenied(d.reason)

    def set_job_state(self, actor: ActorContext, job_id: str, state: JobState, **fields: Any) -> CollectionJob:
        with self.db.uow(actor.tenant_id) as uow:
            job = uow.repo(CollectionJob).get(job_id)
            transition_job(job, state)
            for k, v in fields.items():
                setattr(job, k, v)
            job.last_activity_at = utcnow()
            uow.repo(CollectionJob).save(job)
            self.audit(uow, actor, f"job.{state}", f"job:{job_id}")
        return job

    # --- snapshots -----------------------------------------------------------------------
    def snapshot(self, actor: ActorContext, cycle_id: str, name: str) -> Snapshot:
        with self.db.uow(actor.tenant_id) as uow:
            cycle = uow.repo(Cycle).get(cycle_id)
            can_manage_cycle(actor, cycle).enforce()
            subject_ids = [s.subject_id for s in uow.repo(Subject).list(cycle_id=cycle_id)]
        snap = self.app.artifact_store.snapshot(actor.tenant_id, cycle_id, name, actor.user_id, subject_ids)
        self.audit_now(actor, "cycle.snapshot", f"cycle:{cycle_id}", name=name)
        return snap

    def snapshots(self, actor: ActorContext, cycle_id: str) -> list[Snapshot]:
        self.get_cycle(actor, cycle_id)
        return self.app.artifact_store.list_snapshots(actor.tenant_id, cycle_id)


def _slug(name: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "subject"
