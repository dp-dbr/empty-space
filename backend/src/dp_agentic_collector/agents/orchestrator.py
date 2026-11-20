"""Orchestrator: dispatches jobs, reacts to events, reminders and escalation.

Every handler acts in the context of a real user (event actor, assignee or cycle owner);
inactive users are skipped, never replaced by elevated rights.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ..domain import events
from ..domain.entities import CollectionJob, Cycle, Subject, Tenant, User
from ..domain.enums import CycleStatus, JobState, MessageKind
from ..domain.errors import DomainError, Unauthenticated
from ..domain.events import Event
from ..domain.ids import utcnow
from ..domain.permissions import ActorContext
from ..domain.state_machines import transition_job
from ..extensions.interfaces import OutboundMessage, TaskQueue
from .collector import CollectorAgent

if TYPE_CHECKING:
    from ..container import Collector

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, app: Collector) -> None:
        self.app = app
        self.collector = CollectorAgent(app)
        self._scheduler: threading.Thread | None = None
        self._stop = threading.Event()

    def register(self, queue: TaskQueue) -> None:
        queue.register(events.CYCLE_STARTED, self.on_cycle_started)
        queue.register(events.JOB_CREATED, self.on_job_created)
        queue.register(events.MESSAGE_POSTED, self.on_message)
        queue.register(events.ACTION_CONFIRMED, self.on_action)
        queue.register(events.ARTIFACT_SUBMITTED, self.on_submitted)
        queue.register(events.ARTIFACT_APPROVED, self.on_approved)
        queue.register(events.ATTACHMENT_UPLOADED, self.on_attachment)
        queue.register(events.TICK, self.on_tick)

    def _actor(self, ev: Event, user_id: str | None = None) -> ActorContext:
        return self.app.services.auth.actor_for_user(ev.tenant_id, user_id or ev.actor_id, ev.trace_id)

    # --- dispatch --------------------------------------------------------------------------
    def notify(self, actor: ActorContext, job: CollectionJob, text: str, to_user: str | None = None) -> None:
        channel = self.app.registries.channels.get(job.channel)
        link = f"{self.app.settings.public_base_url}/jobs/{job.job_id}"
        channel.send(OutboundMessage(tenant_id=job.tenant_id, user_id=to_user or job.assignee_id,
                                     subject="DP Agentic Collector", text=text, link=link, job_id=job.job_id))
        self.app.services.tenancy.audit_now(actor, "channel.notify", f"job:{job.job_id}", channel=job.channel,
                                            to=to_user or job.assignee_id)

    def dispatch_job(self, owner: ActorContext, job_id: str) -> None:
        with self.app.db.uow(owner.tenant_id) as uow:
            job = uow.repo(CollectionJob).get(job_id)
            if job.state != JobState.CREATED:
                return
            transition_job(job, JobState.NOTIFIED)
            uow.repo(CollectionJob).save(job)
        try:
            assignee = self.app.services.auth.actor_for_user(owner.tenant_id, job.assignee_id, owner.trace_id)
        except Unauthenticated:
            log.warning("assignee of %s inactive; not greeting", job_id)
            return
        self.collector.greet(assignee, job_id)
        self.notify(owner, job, "You have a new collection job. Please open it to start the conversation.")

    def on_cycle_started(self, ev: Event) -> None:
        owner = self._actor(ev)
        with self.app.db.uow(ev.tenant_id) as uow:
            jobs = uow.repo(CollectionJob).list(cycle_id=ev.payload["cycle_id"], state=str(JobState.CREATED))
        for job in jobs:
            self.dispatch_job(owner, job.job_id)

    def on_job_created(self, ev: Event) -> None:
        self.dispatch_job(self._actor(ev), ev.payload["job_id"])

    # --- conversation ------------------------------------------------------------------------
    def on_message(self, ev: Event) -> None:
        self.collector.run(self._actor(ev), ev.payload["job_id"], "conversation.message")

    def on_action(self, ev: Event) -> None:
        self.collector.continue_after_action(self._actor(ev), ev.payload["action_id"])

    def on_attachment(self, ev: Event) -> None:
        actor = self._actor(ev)
        self.app.services.attachments.process(actor, ev.payload["attachment_id"])

    # --- artifacts ---------------------------------------------------------------------------
    def on_submitted(self, ev: Event) -> None:
        actor = self._actor(ev)
        self.app.services.artifacts.validate_version(actor, ev.payload["job_id"], ev.payload["version"])

    def on_approved(self, ev: Event) -> None:
        actor = self._actor(ev)
        with self.app.db.uow(ev.tenant_id) as uow:
            jobs = uow.repo(CollectionJob).list(subject_id=ev.payload["subject_id"])
        for job in jobs:
            try:
                self.app.services.conversations.post_agent_message(
                    actor, job.job_id, f"Version {ev.payload['version']} was approved. Thank you!",
                    kind=MessageKind.NOTICE)
            except DomainError:
                continue

    # --- deadlines, reminders, escalation ---------------------------------------------------
    def on_tick(self, ev: Event) -> None:
        self.tick(ev.tenant_id, now=datetime.fromisoformat(ev.payload["now"]) if ev.payload.get("now") else None)

    def tick(self, tenant_id: str, now: datetime | None = None) -> dict[str, int]:
        now = now or utcnow()
        stats = {"overdue": 0, "escalated": 0, "reminded": 0}
        with self.app.db.uow(tenant_id) as uow:
            cycles = uow.repo(Cycle).list(status=str(CycleStatus.ACTIVE))
        for cycle in cycles:
            try:
                owner = self.app.services.auth.actor_for_user(tenant_id, cycle.owner_id)
            except Unauthenticated:
                log.warning("owner of cycle %s inactive; skipping tick", cycle.cycle_id)
                continue
            definition = self.app.registries.agent_definitions.get("orchestrator")
            reminder_limit = int(definition.escalation.get("reminder_limit", 3))
            with self.app.db.uow(tenant_id) as uow:
                jobs = uow.repo(CollectionJob).list(cycle_id=cycle.cycle_id)
            for job in jobs:
                due = job.due_date or cycle.due_date
                if job.state in (JobState.CREATED, JobState.NOTIFIED, JobState.IN_PROGRESS) and due and due < now:
                    self.app.services.cycles.set_job_state(owner, job.job_id, JobState.OVERDUE)
                    self.notify(owner, job, "Your collection job is overdue. Please complete it as soon as possible.")
                    self._notice(owner, job, "This job is now overdue.")
                    stats["overdue"] += 1
                elif job.state == JobState.OVERDUE and job.state_changed_at + timedelta(
                        days=cycle.escalate_after_days) < now:
                    self.app.services.cycles.set_job_state(owner, job.job_id, JobState.ESCALATED)
                    self.notify(owner, job, f"Escalation: job {job.job_id} is overdue.", to_user=cycle.owner_id)
                    self._notice(owner, job, "This job has been escalated to the cycle owner.")
                    stats["escalated"] += 1
                elif job.state in (JobState.NOTIFIED, JobState.IN_PROGRESS) and job.reminders_sent < reminder_limit \
                        and job.last_activity_at + timedelta(days=cycle.reminder_after_days) < now:
                    with self.app.db.uow(tenant_id) as uow:
                        j = uow.repo(CollectionJob).get(job.job_id)
                        j.reminders_sent += 1
                        j.last_activity_at = now
                        uow.repo(CollectionJob).save(j)
                    self.notify(owner, job, "Reminder: your collection job is waiting for you.")
                    stats["reminded"] += 1
        return stats

    def _notice(self, actor: ActorContext, job: CollectionJob, text: str) -> None:
        self.app.services.conversations.post_agent_message(actor, job.job_id, text, kind=MessageKind.NOTICE)

    # --- scheduler (publishes ticks; runs with the worker) -----------------------------------
    def start_scheduler(self, interval_seconds: float = 300) -> None:
        def loop() -> None:
            while not self._stop.wait(interval_seconds):
                with self.app.db.uow(None) as uow:
                    tenants = uow.repo(Tenant).list()
                for t in tenants:
                    with self.app.db.uow(t.tenant_id) as uow:
                        owner = uow.repo(User).first(active=True)
                        if owner:
                            uow.publish(events.TICK, owner.user_id, {})

        self._scheduler = threading.Thread(target=loop, name="dpac-scheduler", daemon=True)
        self._scheduler.start()

    def stop_scheduler(self) -> None:
        self._stop.set()


def subject_of(app: Collector, tenant_id: str, subject_id: str) -> Subject:
    with app.db.uow(tenant_id) as uow:
        return uow.repo(Subject).get(subject_id)
