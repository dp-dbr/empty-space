"""Artifact versions: working data, drafts, submission with optimistic locking and conflict
resolution, two-stage validation, approval, history, diffs and snapshot comparison."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..adapters.diff import diff_versions, field_changes
from ..adapters.schema_validation import validate_schema
from ..domain import events
from ..domain.artifacts import (
    ArtifactDiff,
    ArtifactVersion,
    Contributor,
    NewArtifact,
    SourceRef,
    VersionSelector,
    source_from_dict,
)
from ..domain.datapath import set_path, three_way
from ..domain.entities import (
    CollectionJob,
    Cycle,
    FactProposal,
    Message,
    Participant,
    Subject,
    User,
)
from ..domain.enums import ArtifactStatus, JobState, MessageKind, ParticipantKind, ProposalStatus
from ..domain.errors import NotFound, ValidationFailed, VersionConflict
from ..domain.permissions import ActorContext, can_contribute, can_manage_cycle
from ..domain.state_machines import transition_job
from ..extensions.interfaces import RuleViolation, SubjectType, ValidationContext
from .base import ServiceBase


@dataclass(frozen=True)
class SubmitResult:
    version: ArtifactVersion | None
    conflict: dict[str, Any] | None = None


class ArtifactService(ServiceBase):
    # --- helpers --------------------------------------------------------------------
    def subject_type(self, name: str) -> SubjectType:
        return self.app.registries.subject_types.get(name)

    def _load_job(self, actor: ActorContext, job_id: str) -> tuple[CollectionJob, Subject, Cycle]:
        with self.db.uow(actor.tenant_id) as uow:
            job = uow.repo(CollectionJob).get(job_id)
            subject = uow.repo(Subject).get(job.subject_id)
            cycle = uow.repo(Cycle).get(job.cycle_id)
        return job, subject, cycle

    def migrate(self, st: SubjectType, data: dict[str, Any], schema_version: str) -> dict[str, Any]:
        """Apply registered SchemaMigrations so old versions stay readable."""
        current = schema_version
        for _ in range(20):
            if current == st.schema_version:
                break
            step = next((m for m in st.migrations() if m.from_version == current), None)
            if step is None:
                break
            data, current = step.migrate(copy.deepcopy(data)), step.to_version
        return data

    def base_data(self, actor: ActorContext, job: CollectionJob) -> dict[str, Any]:
        if job.base_version is None:
            return dict(job.prefill.get("data", {})) if isinstance(job.prefill.get("data"), dict) else {}
        v = self.app.artifact_store.get(actor.tenant_id, job.subject_id, VersionSelector(job.base_version))
        st = self.subject_type(self.app.services.cycles.cycle_of_subject(actor, job.subject_id).subject_type)
        return self.migrate(st, v.data, v.header.schema_version)

    def working_data(self, actor: ActorContext, job_id: str) -> tuple[dict[str, Any], list[FactProposal]]:
        job = self.app.services.cycles.get_job(actor, job_id)
        data = self.base_data(actor, job)
        with self.db.uow(actor.tenant_id) as uow:
            confirmed = uow.repo(FactProposal).list(job_id=job_id, status=str(ProposalStatus.CONFIRMED),
                                                    order_by="created_at")
        for p in confirmed:
            data = set_path(data, p.path, p.value)
        return data, confirmed

    def render(self, st: SubjectType, subject: Subject, data: Mapping[str, Any], locale: str,
               previous: ArtifactVersion | None, sources: list[SourceRef], open_points: list[str],
               rationale: list[dict[str, Any]], user_names: Mapping[str, str] | None = None) -> str:
        changes = field_changes(previous.data, dict(data)) if previous else []
        ctx = {
            "subject": {"name": subject.name, "lineage_key": subject.lineage_key, "id": subject.subject_id},
            "changes": [{"path": c.path, "change": c.change, "old": c.old, "new": c.new} for c in changes],
            "previous_version": previous.header.version if previous else None,
            "sources": [s.__dict__ for s in sources],
            "open_points": open_points,
            "rationale": rationale,
        }
        return st.render_factsheet(data, ctx, locale)

    def _put(self, actor: ActorContext, subject: Subject, cycle: Cycle, *, based_on: int | None,
             status: ArtifactStatus, data: dict[str, Any], reason: str, sources: list[SourceRef],
             rationale: list[dict[str, Any]] | None = None, factsheet_body: str | None = None) -> ArtifactVersion:
        st = self.subject_type(subject.subject_type)
        previous = None
        if based_on is not None:
            previous = self.app.artifact_store.get(actor.tenant_id, subject.subject_id, VersionSelector(based_on))
        if factsheet_body is None:
            open_points = [f"{v.path}: {v.message}" for v in self.check(actor, subject, cycle, data)
                           if v.severity == "error"] if status == ArtifactStatus.DRAFT else []
            factsheet_body = self.render(st, subject, data, cycle.locale, previous, sources, open_points,
                                         rationale or [])
        version = self.app.artifact_store.put(NewArtifact(
            tenant_id=actor.tenant_id, subject_id=subject.subject_id, cycle=cycle.cycle_id, based_on=based_on,
            schema_version=st.schema_version, locale=cycle.locale, status=status,
            contributor=Contributor(actor.user_id, actor.agent_run_id), change_reason=reason, data=data,
            factsheet_body=factsheet_body, sources=tuple(sources)))
        self.app.projection.project(version, subject.subject_type, subject.lineage_key)
        with self.db.uow(actor.tenant_id) as uow:
            self.audit(uow, actor, f"artifact.{status}", f"subject:{subject.subject_id}@v{version.header.version}",
                       based_on=based_on, content_hash=version.header.content_hash)
        return version

    # --- respondent actions ----------------------------------------------------------------
    def _sources_and_rationale(self, proposals: list[FactProposal]) -> tuple[list[SourceRef], list[dict[str, Any]]]:
        sources: list[SourceRef] = []
        rationale = []
        for p in proposals:
            for s in p.sources:
                src = source_from_dict({**s, "fields": tuple(s.get("fields") or ()) or (p.path,)})
                if src not in sources:
                    sources.append(src)
            if p.rationale:
                rationale.append({"path": p.path, "rationale": p.rationale})
        return sources, rationale

    def _require_visibility_acks(self, proposals: list[FactProposal]) -> None:
        missing = [p.path for p in proposals if p.requires_visibility_confirmation]
        if missing:
            raise ValidationFailed("facts from private attachments need visibility confirmation", paths=missing)

    def save_draft(self, actor: ActorContext, job_id: str, reason: str = "draft") -> ArtifactVersion:
        job, subject, cycle = self._load_job(actor, job_id)
        can_contribute(actor, job).enforce()
        data, confirmed = self.working_data(actor, job_id)
        self._require_visibility_acks(confirmed)
        sources, rationale = self._sources_and_rationale(confirmed)
        version = self._put(actor, subject, cycle, based_on=job.base_version, status=ArtifactStatus.DRAFT,
                            data=data, reason=reason, sources=sources, rationale=rationale)
        self._applied(actor, job_id, confirmed, version.header.version)
        return version

    def _applied(self, actor: ActorContext, job_id: str, proposals: list[FactProposal], version: int) -> None:
        with self.db.uow(actor.tenant_id) as uow:
            for p in proposals:
                p.status = ProposalStatus.APPLIED
                uow.repo(FactProposal).save(p)
            job = uow.repo(CollectionJob).get(job_id)
            job.base_version = version
            job.conflict = None
            uow.repo(CollectionJob).save(job)

    def submit(self, actor: ActorContext, job_id: str, change_reason: str = "submitted") -> SubmitResult:
        """Submit the working data. On an outdated base, never overwrite: record a conflict for the
        user to resolve (ADR-0015)."""
        actor.require_scope("dpac.write")
        job, subject, cycle = self._load_job(actor, job_id)
        can_contribute(actor, job).enforce()
        data, confirmed = self.working_data(actor, job_id)
        self._require_visibility_acks(confirmed)
        sources, rationale = self._sources_and_rationale(confirmed)
        try:
            version = self._put(actor, subject, cycle, based_on=job.base_version, status=ArtifactStatus.SUBMITTED,
                                data=data, reason=change_reason, sources=sources, rationale=rationale)
        except VersionConflict as exc:
            return SubmitResult(None, self._record_conflict(actor, job, data, exc.details.get("latest")))
        self._applied(actor, job_id, confirmed, version.header.version)
        self._after_submit(actor, job, version)
        return SubmitResult(version)

    def _after_submit(self, actor: ActorContext, job: CollectionJob, version: ArtifactVersion) -> None:
        with self.db.uow(actor.tenant_id) as uow:
            j = uow.repo(CollectionJob).get(job.job_id)
            if j.state != JobState.SUBMITTED:
                if j.state in (JobState.CREATED,):
                    transition_job(j, JobState.IN_PROGRESS)
                transition_job(j, JobState.SUBMITTED)
            uow.repo(CollectionJob).save(j)
            uow.publish(events.ARTIFACT_SUBMITTED, actor.user_id,
                        {"job_id": job.job_id, "subject_id": job.subject_id, "version": version.header.version},
                        actor.trace_id)

    def _record_conflict(self, actor: ActorContext, job: CollectionJob, mine: dict[str, Any],
                         latest: int | None) -> dict[str, Any]:
        base = self.base_data(actor, job)
        theirs_v = self.app.artifact_store.get(actor.tenant_id, job.subject_id, VersionSelector(latest))
        merged, conflicts = three_way(base, mine, theirs_v.data)
        conflict = {
            "base_version": job.base_version, "latest_version": latest,
            "latest_contributor": theirs_v.header.contributor.user_id,
            "merged": merged, "mine": mine,
            "conflicts": [{"path": c.path, "base": c.base, "mine": c.mine, "theirs": c.theirs} for c in conflicts],
            "their_changes": [c.__dict__ for c in field_changes(base, theirs_v.data)],
        }
        with self.db.uow(actor.tenant_id) as uow:
            j = uow.repo(CollectionJob).get(job.job_id)
            j.conflict = conflict
            uow.repo(CollectionJob).save(j)
            conv = self.app.services.conversations._conversation(uow, job.job_id)
            uow.repo(Message).add(Message(
                tenant_id=actor.tenant_id, conversation_id=conv.conversation_id,
                sender=Participant(ParticipantKind.AGENT, "collector", "Collector"), kind=MessageKind.CONFLICT,
                text=(f"Someone else saved version {latest} of this subject since you started. "
                      f"{len(conflicts)} field(s) conflict. Please choose for each field which value to keep."),
                payload=conflict, visible_to=[job.assignee_id]))
            self.audit(uow, actor, "artifact.conflict", f"subject:{job.subject_id}", latest=latest,
                       conflicts=len(conflicts))
        return conflict

    def resolve_conflict(self, actor: ActorContext, job_id: str, resolutions: Mapping[str, Any],
                         change_reason: str = "submitted after conflict resolution") -> SubmitResult:
        """``resolutions``: path → "mine" | "theirs" | {"value": ...}. Unlisted conflicts keep "theirs"."""
        job, subject, cycle = self._load_job(actor, job_id)
        can_contribute(actor, job).enforce()
        if not job.conflict:
            raise ValidationFailed("no conflict to resolve")
        data = copy.deepcopy(job.conflict["merged"])
        for c in job.conflict["conflicts"]:
            choice = resolutions.get(c["path"], "theirs")
            if choice == "mine":
                data = set_path(data, c["path"], c["mine"])
            elif isinstance(choice, dict) and "value" in choice:
                data = set_path(data, c["path"], choice["value"])
        _, confirmed = self.working_data(actor, job_id)
        sources, rationale = self._sources_and_rationale(confirmed)
        latest = job.conflict["latest_version"]
        try:
            version = self._put(actor, subject, cycle, based_on=latest, status=ArtifactStatus.SUBMITTED, data=data,
                                reason=change_reason, sources=sources, rationale=rationale)
        except VersionConflict as exc:
            return SubmitResult(None, self._record_conflict(actor, job, data, exc.details.get("latest")))
        self._applied(actor, job_id, confirmed, version.header.version)
        self._after_submit(actor, job, version)
        return SubmitResult(version)

    # --- validation (worker) -----------------------------------------------------------------
    def check(self, actor: ActorContext, subject: Subject, cycle: Cycle, data: Mapping[str, Any]) -> list[RuleViolation]:
        st = self.subject_type(subject.subject_type)
        violations = validate_schema(st.json_schema(), data)
        if violations:
            return violations  # stage 2 only runs on schema-valid data
        peers = {row["lineage_key"]: row["data"] for row in
                 self.app.projection.query(actor.tenant_id, cycle_id=cycle.cycle_id)
                 if row["subject_id"] != subject.subject_id}
        ctx = ValidationContext(tenant_id=actor.tenant_id, cycle_id=cycle.cycle_id, subject_id=subject.subject_id,
                                peer_data=peers)
        for rule in st.rules():
            violations.extend(rule.check(data, ctx))
        return violations

    def validate_version(self, actor: ActorContext, job_id: str, version_no: int) -> list[RuleViolation]:
        job, subject, cycle = self._load_job(actor, job_id)
        store = self.app.artifact_store
        version = store.get(actor.tenant_id, subject.subject_id, VersionSelector(version_no))
        violations = self.check(actor, subject, cycle, version.data)
        errors = [v for v in violations if v.severity == "error"]
        with self.db.uow(actor.tenant_id) as uow:
            conv = self.app.services.conversations._conversation(uow, job_id)
            agent = Participant(ParticipantKind.AGENT, cycle.agent_definition, "Collector")
            if errors:
                j = uow.repo(CollectionJob).get(job_id)
                if j.state == JobState.SUBMITTED:
                    transition_job(j, JobState.IN_PROGRESS)
                    uow.repo(CollectionJob).save(j)
                uow.repo(Message).add(Message(
                    tenant_id=actor.tenant_id, conversation_id=conv.conversation_id, sender=agent,
                    kind=MessageKind.VALIDATION, text="Validation found issues. Please correct them and submit again.",
                    payload={"version": version_no, "violations": [v.__dict__ for v in violations]}))
                self.audit(uow, actor, "artifact.validation_failed", f"subject:{subject.subject_id}@v{version_no}",
                           errors=len(errors))
                return violations
        if store.latest_version(actor.tenant_id, subject.subject_id) != version_no:
            return violations  # superseded meanwhile; nothing to mark
        self._put(actor, subject, cycle, based_on=version_no, status=ArtifactStatus.VALIDATED, data=version.data,
                  reason="validated (schema + rules)", sources=list(version.header.sources),
                  factsheet_body=version.factsheet_body)
        with self.db.uow(actor.tenant_id) as uow:
            conv = self.app.services.conversations._conversation(uow, job_id)
            uow.repo(Message).add(Message(
                tenant_id=actor.tenant_id, conversation_id=conv.conversation_id,
                sender=Participant(ParticipantKind.AGENT, cycle.agent_definition, "Collector"),
                kind=MessageKind.VALIDATION, text="Your submission passed validation and awaits approval.",
                payload={"version": version_no + 1, "violations": [v.__dict__ for v in violations]}))
        return violations

    # --- cycle owner actions ------------------------------------------------------------------
    def approve(self, actor: ActorContext, subject_id: str, comment: str = "") -> ArtifactVersion:
        actor.require_scope("dpac.write")
        with self.db.uow(actor.tenant_id) as uow:
            subject = uow.repo(Subject).get(subject_id)
            cycle = uow.repo(Cycle).get(subject.cycle_id)
            can_manage_cycle(actor, cycle).enforce()
        latest = self.app.artifact_store.get(actor.tenant_id, subject_id)
        if latest.header.status != ArtifactStatus.VALIDATED:
            raise ValidationFailed(f"only validated versions can be approved (latest is {latest.header.status})")
        version = self._put(actor, subject, cycle, based_on=latest.header.version, status=ArtifactStatus.APPROVED,
                            data=latest.data, reason=f"approved by {actor.user_id}" + (f": {comment}" if comment else ""),
                            sources=list(latest.header.sources), factsheet_body=latest.factsheet_body)
        with self.db.uow(actor.tenant_id) as uow:
            for job in uow.repo(CollectionJob).list(subject_id=subject_id):
                if job.state == JobState.SUBMITTED:
                    transition_job(job, JobState.CLOSED)
                    uow.repo(CollectionJob).save(job)
            uow.publish(events.ARTIFACT_APPROVED, actor.user_id,
                        {"subject_id": subject_id, "version": version.header.version}, actor.trace_id)
        return version

    def reopen(self, actor: ActorContext, job_id: str) -> CollectionJob:
        job, _, cycle = self._load_job(actor, job_id)
        can_manage_cycle(actor, cycle).enforce()
        return self.app.services.cycles.set_job_state(actor, job_id, JobState.IN_PROGRESS)

    # --- reading ---------------------------------------------------------------------------
    def get(self, actor: ActorContext, subject_id: str, selector: str | int | None = None) -> ArtifactVersion:
        self.app.services.cycles.require_subject_read(actor, subject_id)
        return self.app.artifact_store.get(actor.tenant_id, subject_id, VersionSelector.parse(selector))

    def history(self, actor: ActorContext, subject_id: str) -> list[ArtifactVersion]:
        self.app.services.cycles.require_subject_read(actor, subject_id)
        return self.app.artifact_store.history(actor.tenant_id, subject_id)

    def diff(self, actor: ActorContext, subject_id: str, from_sel: str | int, to_sel: str | int | None = None,
             other_subject_id: str | None = None) -> ArtifactDiff:
        """Diff two versions; ``other_subject_id`` compares across Cycles (same lineage)."""
        a = self.get(actor, other_subject_id or subject_id, from_sel)
        b = self.get(actor, subject_id, to_sel)
        return diff_versions(a, b)

    def compare_snapshot(self, actor: ActorContext, cycle_id: str, snapshot: str) -> list[dict[str, Any]]:
        """Compare latest versions of a Cycle with a snapshot ``<cycle_id>/<name>`` (may be a previous
        Cycle's snapshot; subjects are matched by lineage_key)."""
        snap_cycle, _, name = snapshot.partition("/")
        snap = self.app.artifact_store.get_snapshot(actor.tenant_id, snap_cycle, name)
        subjects = self.app.services.cycles.subjects(actor, cycle_id)
        with self.db.uow(actor.tenant_id) as uow:
            snap_subjects = {s.subject_id: s for s in uow.repo(Subject).list(cycle_id=snap_cycle)}
        by_lineage = {s.lineage_key: sid for sid, s in snap_subjects.items() if sid in snap.versions}
        out = []
        store = self.app.artifact_store
        for s in subjects:
            latest_no = store.latest_version(actor.tenant_id, s.subject_id)
            old_sid = s.subject_id if s.subject_id in snap.versions else by_lineage.get(s.lineage_key)
            entry: dict[str, Any] = {"subject_id": s.subject_id, "name": s.name, "lineage_key": s.lineage_key}
            if latest_no is None and old_sid is None:
                continue
            if old_sid is None:
                entry["change"] = "added"
            elif latest_no is None:
                entry["change"] = "missing"
            else:
                a = store.get(actor.tenant_id, old_sid, VersionSelector(snap.versions[old_sid]))
                b = store.get(actor.tenant_id, s.subject_id, VersionSelector(latest_no))
                d = diff_versions(a, b)
                entry.update(change="changed" if d.field_changes else "unchanged",
                             field_changes=[c.__dict__ for c in d.field_changes],
                             from_ref=d.from_ref, to_ref=d.to_ref)
            out.append(entry)
        return out

    def user_name(self, actor: ActorContext, user_id: str) -> str:
        with self.db.uow(actor.tenant_id) as uow:
            u = uow.repo(User).find(user_id)
        if u is None:
            raise NotFound("user not found")
        return u.name
