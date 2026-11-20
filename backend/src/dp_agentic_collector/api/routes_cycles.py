from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..domain.entities import CollectionJob, Cycle, Subject
from ..domain.enums import CycleRecurrence
from .deps import Ctx, get_ctx
from .schemas import ApproveIn, ArtifactOut, CycleIn, DiffOut, SnapshotIn, SnapshotOut, SubjectIn

router = APIRouter(tags=["cycles"])


def _snap(s: Any) -> SnapshotOut:
    return SnapshotOut(cycle_id=s.cycle_id, name=s.name, created=s.created, created_by=s.created_by,
                       versions=s.versions)


@router.get("/cycles", response_model=list[Cycle])
def list_cycles(ctx: Ctx = Depends(get_ctx)) -> list[Cycle]:
    return ctx.s.cycles.list_cycles(ctx.actor)  # type: ignore[no-any-return]


@router.post("/cycles", response_model=Cycle, status_code=201)
def create_cycle(body: CycleIn, ctx: Ctx = Depends(get_ctx)) -> Cycle:
    return ctx.s.cycles.create_cycle(  # type: ignore[no-any-return]
        ctx.actor, body.name, body.subject_type, scope=body.scope, due_date=body.due_date,
        recurrence=CycleRecurrence(body.recurrence), previous_cycle_id=body.previous_cycle_id, locale=body.locale,
        agent_definition=body.agent_definition, reminder_after_days=body.reminder_after_days,
        escalate_after_days=body.escalate_after_days)


@router.get("/cycles/{cycle_id}", response_model=Cycle)
def get_cycle(cycle_id: str, ctx: Ctx = Depends(get_ctx)) -> Cycle:
    return ctx.s.cycles.get_cycle(ctx.actor, cycle_id)  # type: ignore[no-any-return]


@router.post("/cycles/{cycle_id}/start", response_model=Cycle)
def start_cycle(cycle_id: str, ctx: Ctx = Depends(get_ctx)) -> Cycle:
    return ctx.s.cycles.start_cycle(ctx.actor, cycle_id)  # type: ignore[no-any-return]


@router.post("/cycles/{cycle_id}/close", response_model=Cycle)
def close_cycle(cycle_id: str, ctx: Ctx = Depends(get_ctx)) -> Cycle:
    return ctx.s.cycles.close_cycle(ctx.actor, cycle_id)  # type: ignore[no-any-return]


@router.post("/cycles/{cycle_id}/init-from-previous", response_model=list[CollectionJob])
def init_from_previous(cycle_id: str, ctx: Ctx = Depends(get_ctx)) -> list[CollectionJob]:
    return ctx.s.cycles.init_from_previous(ctx.actor, cycle_id)  # type: ignore[no-any-return]


@router.get("/cycles/{cycle_id}/subjects", response_model=list[Subject])
def subjects(cycle_id: str, ctx: Ctx = Depends(get_ctx)) -> list[Subject]:
    return ctx.s.cycles.subjects(ctx.actor, cycle_id)  # type: ignore[no-any-return]


@router.post("/cycles/{cycle_id}/subjects", status_code=201)
def add_subject(cycle_id: str, body: SubjectIn, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    subject, job = ctx.s.cycles.add_subject(ctx.actor, cycle_id, body.name, body.assignee_id,
                                            lineage_key=body.lineage_key, owners=body.owners, due_date=body.due_date,
                                            channel=body.channel, prefill=body.prefill)
    return {"subject": subject, "job": job}


@router.get("/cycles/{cycle_id}/jobs", response_model=list[CollectionJob])
def cycle_jobs(cycle_id: str, ctx: Ctx = Depends(get_ctx)) -> list[CollectionJob]:
    return ctx.s.cycles.jobs(ctx.actor, cycle_id)  # type: ignore[no-any-return]


@router.get("/cycles/{cycle_id}/status")
def cycle_status(cycle_id: str, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    jobs = ctx.s.cycles.jobs(ctx.actor, cycle_id)
    counts: dict[str, int] = {}
    for j in jobs:
        counts[str(j.state)] = counts.get(str(j.state), 0) + 1
    rows = ctx.app.projection.query(ctx.actor.tenant_id, cycle_id=cycle_id)
    statuses: dict[str, int] = {}
    for r in rows:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    return {"jobs": counts, "artifacts": statuses, "total_jobs": len(jobs)}


@router.post("/cycles/{cycle_id}/snapshots", response_model=SnapshotOut, status_code=201)
def snapshot(cycle_id: str, body: SnapshotIn, ctx: Ctx = Depends(get_ctx)) -> SnapshotOut:
    return _snap(ctx.s.cycles.snapshot(ctx.actor, cycle_id, body.name))


@router.get("/cycles/{cycle_id}/snapshots", response_model=list[SnapshotOut])
def snapshots(cycle_id: str, ctx: Ctx = Depends(get_ctx)) -> list[SnapshotOut]:
    return [_snap(s) for s in ctx.s.cycles.snapshots(ctx.actor, cycle_id)]


@router.get("/cycles/{cycle_id}/compare")
def compare(cycle_id: str, snapshot: str, ctx: Ctx = Depends(get_ctx)) -> list[dict[str, Any]]:
    """Compare latest versions against a snapshot ``<cycle_id>/<name>`` (any Cycle)."""
    return ctx.s.artifacts.compare_snapshot(ctx.actor, cycle_id, snapshot)  # type: ignore[no-any-return]


@router.get("/cycles/{cycle_id}/consolidation")
def consolidation(cycle_id: str, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    return ctx.s.consolidation.report(ctx.actor, cycle_id)  # type: ignore[no-any-return]


# --- subjects ------------------------------------------------------------------------------

@router.get("/subjects/{subject_id}", response_model=Subject)
def get_subject(subject_id: str, ctx: Ctx = Depends(get_ctx)) -> Subject:
    return ctx.s.cycles.get_subject(ctx.actor, subject_id)  # type: ignore[no-any-return]


@router.get("/subjects/{subject_id}/artifact", response_model=ArtifactOut)
def artifact(subject_id: str, version: str = "latest", ctx: Ctx = Depends(get_ctx)) -> ArtifactOut:
    return ArtifactOut.of(ctx.s.artifacts.get(ctx.actor, subject_id, version))


@router.get("/subjects/{subject_id}/history", response_model=list[ArtifactOut])
def history(subject_id: str, ctx: Ctx = Depends(get_ctx)) -> list[ArtifactOut]:
    return [ArtifactOut.of(v) for v in ctx.s.artifacts.history(ctx.actor, subject_id)]


@router.get("/subjects/{subject_id}/diff", response_model=DiffOut)
def diff(subject_id: str, from_version: str, to_version: str = "latest", other_subject_id: str | None = None,
         ctx: Ctx = Depends(get_ctx)) -> DiffOut:
    d = ctx.s.artifacts.diff(ctx.actor, subject_id, from_version, to_version, other_subject_id)
    a = ctx.s.artifacts.get(ctx.actor, other_subject_id or subject_id, from_version)
    b = ctx.s.artifacts.get(ctx.actor, subject_id, to_version)
    return DiffOut(from_ref=d.from_ref, to_ref=d.to_ref, field_changes=[c.__dict__ for c in d.field_changes],
                   factsheet_diff=d.factsheet_diff, from_factsheet=a.factsheet_body, to_factsheet=b.factsheet_body)


@router.post("/subjects/{subject_id}/approve", response_model=ArtifactOut)
def approve(subject_id: str, body: ApproveIn, ctx: Ctx = Depends(get_ctx)) -> ArtifactOut:
    return ArtifactOut.of(ctx.s.artifacts.approve(ctx.actor, subject_id, body.comment))


@router.get("/projection")
def projection(cycle_id: str | None = None, subject_type: str | None = None, latest_only: bool = True,
               ctx: Ctx = Depends(get_ctx)) -> list[dict[str, Any]]:
    """Query projected data files (only subjects the caller may read)."""
    rows = ctx.app.projection.query(ctx.actor.tenant_id, cycle_id=cycle_id, subject_type=subject_type,
                                    latest_only=latest_only)
    allowed: dict[str, bool] = {}
    out = []
    for r in rows:
        sid = r["subject_id"]
        if sid not in allowed:
            allowed[sid] = ctx.s.cycles.subject_read_decision(ctx.actor, sid).allowed
        if allowed[sid]:
            out.append(r)
    return out
