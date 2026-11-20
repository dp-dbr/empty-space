"""Conversations: messages, fact proposals (structured confirmation) and pending write actions."""

from __future__ import annotations

from typing import Any

from ..adapters.sql.db import UnitOfWork
from ..domain import events
from ..domain.datapath import get_path
from ..domain.entities import (
    CollectionJob,
    Conversation,
    FactProposal,
    Message,
    Participant,
    PendingAction,
)
from ..domain.enums import (
    JobState,
    MessageKind,
    ParticipantKind,
    PendingActionStatus,
    ProposalStatus,
)
from ..domain.errors import NotFound, PermissionDenied, ValidationFailed
from ..domain.ids import utcnow
from ..domain.permissions import ActorContext, can_contribute
from ..domain.state_machines import transition_job
from .base import ServiceBase

REOPEN_ON_REPLY = {JobState.CREATED, JobState.NOTIFIED, JobState.OVERDUE, JobState.ESCALATED, JobState.SUBMITTED}


class ConversationService(ServiceBase):
    @staticmethod
    def _conversation(uow: UnitOfWork, job_id: str) -> Conversation:
        conv = uow.repo(Conversation).first(job_id=job_id)
        if conv is None:
            raise NotFound("conversation not found")
        return conv

    def conversation(self, actor: ActorContext, job_id: str) -> Conversation:
        self.app.services.cycles.get_job(actor, job_id)
        with self.db.uow(actor.tenant_id) as uow:
            return self._conversation(uow, job_id)

    def messages(self, actor: ActorContext, job_id: str) -> list[Message]:
        conv = self.conversation(actor, job_id)
        with self.db.uow(actor.tenant_id) as uow:
            msgs = uow.repo(Message).list(conversation_id=conv.conversation_id, order_by="created_at")
        return [m for m in msgs if m.visible_to is None or actor.user_id in m.visible_to]

    def post_user_message(self, actor: ActorContext, job_id: str, text: str,
                          attachment_ids: list[str] | None = None) -> Message:
        actor.require_scope("dpac.write")
        if not text.strip() and not attachment_ids:
            raise ValidationFailed("message is empty")
        if len(text) > 20_000:
            raise ValidationFailed("message too long")
        job = self.app.services.cycles.get_job(actor, job_id)
        can_contribute(actor, job).enforce()
        with self.db.uow(actor.tenant_id) as uow:
            conv = self._conversation(uow, job_id)
            msg = Message(tenant_id=actor.tenant_id, conversation_id=conv.conversation_id,
                          sender=Participant(ParticipantKind.USER, actor.user_id), kind=MessageKind.TEXT,
                          text=text, attachment_ids=list(attachment_ids or []))
            uow.repo(Message).add(msg)
            j = uow.repo(CollectionJob).get(job_id)
            if j.state in REOPEN_ON_REPLY:
                if j.state == JobState.CREATED:
                    transition_job(j, JobState.NOTIFIED)
                transition_job(j, JobState.IN_PROGRESS)
            j.last_activity_at = utcnow()
            uow.repo(CollectionJob).save(j)
            uow.publish(events.MESSAGE_POSTED, actor.user_id, {"job_id": job_id, "message_id": msg.message_id},
                        actor.trace_id)
            self.audit(uow, actor, "conversation.message", f"job:{job_id}")
        return msg

    def post_agent_message(self, actor: ActorContext, job_id: str, text: str, kind: MessageKind = MessageKind.TEXT,
                           payload: dict[str, Any] | None = None, visible_to: list[str] | None = None,
                           agent_name: str = "collector") -> Message:
        with self.db.uow(actor.tenant_id) as uow:
            conv = self._conversation(uow, job_id)
            msg = Message(tenant_id=actor.tenant_id, conversation_id=conv.conversation_id,
                          sender=Participant(ParticipantKind.AGENT, agent_name, "Collector"), kind=kind, text=text,
                          payload=payload or {}, visible_to=visible_to, agent_run_id=actor.agent_run_id)
            uow.repo(Message).add(msg)
        return msg

    # --- fact proposals -------------------------------------------------------------------
    def add_proposals(self, actor: ActorContext, job_id: str, facts: list[dict[str, Any]],
                      sources: list[dict[str, Any]] | None = None) -> list[FactProposal]:
        """Called by the collector agent (acting for the assignee)."""
        job = self.app.services.cycles.get_job(actor, job_id)
        can_contribute(actor, job).enforce()
        sources = sources or []
        out = []
        with self.db.uow(actor.tenant_id) as uow:
            conv = self._conversation(uow, job_id)
            for f in facts:
                path = str(f.get("path", "")).strip()
                if not path:
                    continue
                src = list(f.get("sources") or [])
                idx = f.get("source_index")
                if isinstance(idx, int) and 0 <= idx < len(sources):
                    src.append(sources[idx])
                from_private = any(s.get("kind") == "attachment" and s.get("private") for s in src)
                # supersede open proposals for the same path
                for old in uow.repo(FactProposal).list(job_id=job_id, path=path, status=str(ProposalStatus.PROPOSED)):
                    old.status = ProposalStatus.REJECTED
                    old.decided_by = "superseded"
                    uow.repo(FactProposal).save(old)
                p = FactProposal(tenant_id=actor.tenant_id, job_id=job_id, conversation_id=conv.conversation_id,
                                 path=path, value=f.get("value"), proposed_by=actor.agent_run_id or actor.user_id,
                                 rationale=str(f.get("rationale", ""))[:1000],
                                 sources=[{k: v for k, v in s.items() if k != "private"} for s in src],
                                 requires_visibility_confirmation=from_private)
                uow.repo(FactProposal).add(p)
                out.append(p)
            if out:
                uow.repo(Message).add(Message(
                    tenant_id=actor.tenant_id, conversation_id=conv.conversation_id,
                    sender=Participant(ParticipantKind.AGENT, actor.agent_name or "collector", "Collector"),
                    kind=MessageKind.FACT_PROPOSAL, text="Please confirm these facts.",
                    payload={"proposal_ids": [p.proposal_id for p in out]},
                    visible_to=[job.assignee_id] if any(p.requires_visibility_confirmation for p in out) else None,
                    agent_run_id=actor.agent_run_id))
            self.audit(uow, actor, "proposal.create", f"job:{job_id}", count=len(out))
        return out

    def proposals(self, actor: ActorContext, job_id: str, status: str | None = None) -> list[FactProposal]:
        job = self.app.services.cycles.get_job(actor, job_id)
        with self.db.uow(actor.tenant_id) as uow:
            filters = {"status": status} if status else {}
            items = uow.repo(FactProposal).list(job_id=job_id, order_by="created_at", **filters)
        if actor.user_id != job.assignee_id:
            # proposals derived from private attachments are only visible to the uploader
            items = [p for p in items if not p.requires_visibility_confirmation]
        return items

    def decide_proposal(self, actor: ActorContext, proposal_id: str, decision: str, *,
                        value: Any = None, visibility_ack: bool = False) -> FactProposal:
        """decision: confirm | reject | edit (with ``value``). Facts from private attachments
        require ``visibility_ack`` because they become visible to everyone who can read the subject."""
        actor.require_scope("dpac.write")
        with self.db.uow(actor.tenant_id) as uow:
            p = uow.repo(FactProposal).get(proposal_id)
            job = uow.repo(CollectionJob).get(p.job_id)
            can_contribute(actor, job).enforce()
            if p.status not in (ProposalStatus.PROPOSED, ProposalStatus.CONFIRMED):
                raise ValidationFailed(f"proposal is {p.status}")
            if decision == "reject":
                p.status = ProposalStatus.REJECTED
            elif decision in ("confirm", "edit"):
                if p.requires_visibility_confirmation and not visibility_ack:
                    raise ValidationFailed("this fact comes from a private attachment; confirm that it becomes "
                                           "visible to everyone with access to the subject", code="visibility_ack")
                if decision == "edit":
                    p.value = value
                    p.sources = [*p.sources, {"kind": "user", "on_behalf_of": actor.user_id,
                                              "retrieved_at": utcnow().isoformat()}]
                p.requires_visibility_confirmation = False
                p.status = ProposalStatus.CONFIRMED
            else:
                raise ValidationFailed("decision must be confirm, reject or edit")
            p.decided_by = actor.user_id
            uow.repo(FactProposal).save(p)
            self.audit(uow, actor, f"proposal.{decision}", f"proposal:{proposal_id}", path=p.path,
                       visibility_ack=visibility_ack or None)
        return p

    def draft_view(self, actor: ActorContext, job_id: str) -> dict[str, Any]:
        data, confirmed = self.app.services.artifacts.working_data(actor, job_id)
        job = self.app.services.cycles.get_job(actor, job_id)
        pending = self.proposals(actor, job_id, status=str(ProposalStatus.PROPOSED))
        return {"job_id": job_id, "base_version": job.base_version, "data": data,
                "confirmed": [p.path for p in confirmed],
                "pending": [{"proposal_id": p.proposal_id, "path": p.path, "value": p.value,
                             "current": get_path(data, p.path)} for p in pending],
                "conflict": job.conflict}

    # --- pending actions (write confirmations) -------------------------------------------------
    def create_pending_action(self, actor: ActorContext, job_id: str, tool: str, arguments: dict[str, Any],
                              summary: str, target: str) -> PendingAction:
        with self.db.uow(actor.tenant_id) as uow:
            conv = self._conversation(uow, job_id)
            action = PendingAction(tenant_id=actor.tenant_id, user_id=actor.user_id, job_id=job_id,
                                   conversation_id=conv.conversation_id, agent_run_id=actor.agent_run_id or "",
                                   tool=tool, arguments=arguments, summary=summary, target=target)
            uow.repo(PendingAction).add(action)
            uow.repo(Message).add(Message(
                tenant_id=actor.tenant_id, conversation_id=conv.conversation_id,
                sender=Participant(ParticipantKind.AGENT, actor.agent_name or "collector", "Collector"),
                kind=MessageKind.CONFIRMATION_REQUEST,
                text=f"I would like to write to {target}: {summary}. Do you confirm?",
                payload={"action_id": action.action_id, "tool": tool, "target": target, "arguments": arguments},
                visible_to=[actor.user_id], agent_run_id=actor.agent_run_id))
            self.audit(uow, actor, "pending_action.create", f"action:{action.action_id}", tool=tool, target=target)
        return action

    def pending_actions(self, actor: ActorContext, job_id: str) -> list[PendingAction]:
        self.app.services.cycles.get_job(actor, job_id)
        with self.db.uow(actor.tenant_id) as uow:
            return uow.repo(PendingAction).list(job_id=job_id, user_id=actor.user_id, order_by="created_at")

    def decide_action(self, actor: ActorContext, action_id: str, confirm: bool) -> PendingAction:
        actor.require_scope("dpac.write")
        with self.db.uow(actor.tenant_id) as uow:
            action = uow.repo(PendingAction).get(action_id)
            if action.user_id != actor.user_id:
                raise PermissionDenied("only the user the agent acts for can confirm this action")
            if action.status != PendingActionStatus.PENDING:
                raise ValidationFailed(f"action is {action.status}")
            action.status = PendingActionStatus.CONFIRMED if confirm else PendingActionStatus.REJECTED
            uow.repo(PendingAction).save(action)
            uow.publish(events.ACTION_CONFIRMED, actor.user_id, {"action_id": action_id, "confirmed": confirm},
                        actor.trace_id)
            self.audit(uow, actor, "pending_action.confirm" if confirm else "pending_action.reject",
                       f"action:{action_id}", tool=action.tool)
        return action

    def finish_action(self, actor: ActorContext, action_id: str, status: PendingActionStatus,
                      result: dict[str, Any]) -> PendingAction:
        with self.db.uow(actor.tenant_id) as uow:
            action = uow.repo(PendingAction).get(action_id)
            action.status = status
            action.result = result
            uow.repo(PendingAction).save(action)
        return action
