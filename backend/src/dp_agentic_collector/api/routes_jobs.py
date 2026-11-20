from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..domain.entities import CollectionJob, FactProposal, Message, PendingAction
from .deps import Ctx, get_ctx
from .schemas import (
    ActionDecisionIn,
    ArtifactOut,
    MessageIn,
    ProposalDecisionIn,
    ResolveIn,
    SubmitIn,
    SubmitOut,
)

router = APIRouter(tags=["jobs"])


def _submit_out(res: Any) -> SubmitOut:
    return SubmitOut(version=ArtifactOut.of(res.version) if res.version else None, conflict=res.conflict)


@router.get("/jobs", response_model=list[dict[str, Any]])
def my_jobs(ctx: Ctx = Depends(get_ctx)) -> list[dict[str, Any]]:
    """"My jobs" inbox, enriched with subject and cycle names."""
    out = []
    for j in ctx.s.cycles.my_jobs(ctx.actor):
        subject = ctx.s.cycles.get_subject(ctx.actor, j.subject_id)
        cycle = ctx.s.cycles.get_cycle(ctx.actor, j.cycle_id)
        out.append({"job": j, "subject_name": subject.name, "cycle_name": cycle.name,
                    "subject_type": subject.subject_type})
    return out


@router.get("/jobs/{job_id}", response_model=CollectionJob)
def get_job(job_id: str, ctx: Ctx = Depends(get_ctx)) -> CollectionJob:
    return ctx.s.cycles.get_job(ctx.actor, job_id)  # type: ignore[no-any-return]


@router.get("/jobs/{job_id}/messages", response_model=list[Message])
def messages(job_id: str, ctx: Ctx = Depends(get_ctx)) -> list[Message]:
    return ctx.s.conversations.messages(ctx.actor, job_id)  # type: ignore[no-any-return]


@router.post("/jobs/{job_id}/messages", response_model=Message, status_code=202)
def post_message(job_id: str, body: MessageIn, ctx: Ctx = Depends(get_ctx)) -> Message:
    return ctx.s.conversations.post_user_message(ctx.actor, job_id, body.text, body.attachment_ids)  # type: ignore[no-any-return]


@router.get("/jobs/{job_id}/proposals", response_model=list[FactProposal])
def proposals(job_id: str, status: str | None = None, ctx: Ctx = Depends(get_ctx)) -> list[FactProposal]:
    return ctx.s.conversations.proposals(ctx.actor, job_id, status)  # type: ignore[no-any-return]


@router.post("/proposals/{proposal_id}/decision", response_model=FactProposal)
def decide_proposal(proposal_id: str, body: ProposalDecisionIn, ctx: Ctx = Depends(get_ctx)) -> FactProposal:
    return ctx.s.conversations.decide_proposal(ctx.actor, proposal_id, body.decision, value=body.value,  # type: ignore[no-any-return]
                                               visibility_ack=body.visibility_ack)


@router.get("/jobs/{job_id}/draft")
def draft(job_id: str, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    return ctx.s.conversations.draft_view(ctx.actor, job_id)  # type: ignore[no-any-return]


@router.post("/jobs/{job_id}/draft", response_model=ArtifactOut)
def save_draft(job_id: str, ctx: Ctx = Depends(get_ctx)) -> ArtifactOut:
    return ArtifactOut.of(ctx.s.artifacts.save_draft(ctx.actor, job_id))


@router.post("/jobs/{job_id}/submit", response_model=SubmitOut)
def submit(job_id: str, body: SubmitIn, ctx: Ctx = Depends(get_ctx)) -> SubmitOut:
    return _submit_out(ctx.s.artifacts.submit(ctx.actor, job_id, body.change_reason))


@router.post("/jobs/{job_id}/resolve-conflict", response_model=SubmitOut)
def resolve(job_id: str, body: ResolveIn, ctx: Ctx = Depends(get_ctx)) -> SubmitOut:
    return _submit_out(ctx.s.artifacts.resolve_conflict(ctx.actor, job_id, body.resolutions, body.change_reason))


@router.post("/jobs/{job_id}/reopen", response_model=CollectionJob)
def reopen(job_id: str, ctx: Ctx = Depends(get_ctx)) -> CollectionJob:
    return ctx.s.artifacts.reopen(ctx.actor, job_id)  # type: ignore[no-any-return]


@router.get("/jobs/{job_id}/pending-actions", response_model=list[PendingAction])
def pending_actions(job_id: str, ctx: Ctx = Depends(get_ctx)) -> list[PendingAction]:
    return ctx.s.conversations.pending_actions(ctx.actor, job_id)  # type: ignore[no-any-return]


@router.post("/pending-actions/{action_id}/decision", response_model=PendingAction)
def decide_action(action_id: str, body: ActionDecisionIn, ctx: Ctx = Depends(get_ctx)) -> PendingAction:
    return ctx.s.conversations.decide_action(ctx.actor, action_id, body.confirm)  # type: ignore[no-any-return]
