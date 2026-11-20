"""Collector agent: one run = one LLM tool loop on behalf of the job's assignee."""

from __future__ import annotations

import base64
import json
import logging
from typing import TYPE_CHECKING

from ..domain.entities import AgentRun, CollectionJob, Cycle, Message, Subject, Tenant
from ..domain.enums import AgentRunStatus, MessageKind, ParticipantKind, PendingActionStatus
from ..domain.ids import utcnow
from ..domain.permissions import ActorContext
from ..extensions.interfaces import AgentDefinition, ContentPart, LlmMessage
from ..observability import log_context, span
from .tools import RunContext, ToolRuntime

if TYPE_CHECKING:
    from ..container import Collector

log = logging.getLogger(__name__)


class CollectorAgent:
    def __init__(self, app: Collector) -> None:
        self.app = app

    # --- configuration ------------------------------------------------------------------
    def resolve_llm(self, definition: AgentDefinition, tenant_id: str) -> tuple[str, str]:
        provider, model = definition.provider, definition.model
        with self.app.db.uow(None) as uow:
            tenant = uow.repo(Tenant).get(tenant_id)
        override = (tenant.settings or {}).get("llm", {}).get(definition.name) or (tenant.settings or {}).get(
            "llm", {}).get("default")
        if isinstance(override, dict):
            provider = override.get("provider", provider)
            model = override.get("model", model)
        if self.app.settings.default_llm_provider:
            provider = self.app.settings.default_llm_provider
            model = self.app.settings.default_llm_model or model
        return provider, model

    def _context(self, actor: ActorContext, job_id: str) -> tuple[CollectionJob, Subject, Cycle, AgentDefinition]:
        with self.app.db.uow(actor.tenant_id) as uow:
            job = uow.repo(CollectionJob).get(job_id)
            subject = uow.repo(Subject).get(job.subject_id)
            cycle = uow.repo(Cycle).get(job.cycle_id)
        definition = self.app.registries.agent_definitions.get(cycle.agent_definition)
        return job, subject, cycle, definition

    def system_prompt(self, rc: RunContext, user_locale: str) -> str:
        st = self.app.registries.subject_types.get(rc.subject.subject_type)
        schema = st.json_schema()
        fields = ", ".join(schema.get("properties", {}).keys())
        draft = self.app.services.conversations.draft_view(rc.actor, rc.job.job_id)
        return (
            f"{rc.definition.system_prompt}\n"
            f"<context>\nsubject: {rc.subject.name} (type {st.name})\ncycle: {rc.cycle.name}\n"
            f"locale: {user_locale}\n</context>\n"
            f"<schema>{json.dumps(schema, ensure_ascii=False)}</schema>\n"
            f"<schema_fields>{fields}</schema_fields>\n"
            f"<conversation_guide>\n{st.conversation_guide(user_locale)}\n</conversation_guide>\n"
            f"<current_draft>{json.dumps(draft['data'], ensure_ascii=False)}</current_draft>\n"
            f"<pending_proposals>{json.dumps(draft['pending'], ensure_ascii=False, default=str)}</pending_proposals>"
        )

    def transcript(self, rc: RunContext) -> list[LlmMessage]:
        """Rebuild the LLM view of the conversation (only what the acting user may see)."""
        msgs = self.app.services.conversations.messages(rc.actor, rc.job.job_id)
        att_names: dict[str, str] = {}
        images: dict[str, ContentPart] = {}
        for a in self.app.services.attachments.list_for_job(rc.actor, rc.job.job_id):
            att_names[a.attachment_id] = f"{a.filename} ({a.mime_type}, {a.visibility})"
            if a.mime_type in ("image/png", "image/jpeg") and a.size <= 5 * 1024 * 1024 and a.status == "active":
                data = self.app.attachment_store.get(rc.actor.tenant_id, a.content_hash)
                images[a.attachment_id] = image_part(a.mime_type, base64.b64encode(data).decode())
        out: list[LlmMessage] = []
        for m in msgs:
            if m.sender.kind == ParticipantKind.USER:
                text = m.text
                if m.attachment_ids:
                    refs = "\n".join(f"[attachment {aid}: {att_names.get(aid, 'unavailable')}]"
                                     for aid in m.attachment_ids)
                    text = f"{text}\n{refs}".strip()
                parts = tuple(images[aid] for aid in m.attachment_ids if aid in images)
                out.append(LlmMessage(role="user", content=text, parts=parts))
            elif m.kind in (MessageKind.TEXT, MessageKind.CONFIRMATION_REQUEST):
                out.append(LlmMessage(role="assistant", content=m.text))
            elif m.kind in (MessageKind.VALIDATION, MessageKind.CONFLICT, MessageKind.NOTICE):
                out.append(LlmMessage(role="user", content=f"[system notice] {m.text} "
                                      f"{json.dumps(m.payload, default=str)[:2000]}"))
        # merge consecutive same-role messages, ensure first is user
        merged: list[LlmMessage] = []
        for m in out:
            if merged and merged[-1].role == m.role and m.role in ("user", "assistant"):
                merged[-1] = LlmMessage(role=m.role, content=f"{merged[-1].content}\n\n{m.content}",
                                        parts=merged[-1].parts + m.parts)
            else:
                merged.append(m)
        if not merged or merged[0].role != "user":
            merged.insert(0, LlmMessage(role="user", content="[system notice] The job was assigned to me. Please start."))
        return merged

    # --- run ----------------------------------------------------------------------------------
    def run(self, user_actor: ActorContext, job_id: str, trigger: str,
            extra: list[LlmMessage] | None = None) -> AgentRun:
        job, subject, cycle, definition = self._context(user_actor, job_id)
        provider, model = self.resolve_llm(definition, user_actor.tenant_id)
        run = AgentRun(tenant_id=user_actor.tenant_id, agent=definition.name, on_behalf_of=user_actor.user_id,
                       trigger=trigger, job_id=job_id, trace_id=user_actor.trace_id, provider=provider, model=model)
        with self.app.db.uow(user_actor.tenant_id) as uow:
            uow.repo(AgentRun).add(run)
        actor = user_actor.for_agent(run.agent_run_id, definition.name)
        rc = RunContext(self.app, actor, definition, job, subject, cycle)
        with span("agent.run", agent=definition.name, agent_run_id=run.agent_run_id, job_id=job_id,
                  tenant_id=actor.tenant_id), log_context(agent_run_id=run.agent_run_id, job_id=job_id):
            try:
                self._loop(rc, run, provider, model, extra or [])
            except Exception as exc:
                log.exception("agent run failed")
                run.status, run.error = AgentRunStatus.FAILED, f"{type(exc).__name__}: {exc}"[:500]
                self.app.services.conversations.post_agent_message(
                    actor, job_id, "Sorry, I ran into a technical problem. Please try again in a moment.",
                    kind=MessageKind.NOTICE, agent_name=definition.name)
            run.finished_at = utcnow()
            with self.app.db.uow(actor.tenant_id) as uow:
                uow.repo(AgentRun).save(run)
        return run

    def _loop(self, rc: RunContext, run: AgentRun, provider: str, model: str, extra: list[LlmMessage]) -> None:
        user = self.app.services.tenancy.get_user(rc.actor, rc.actor.user_id)
        runtime = ToolRuntime(rc)
        llm = self.app.llm(provider)
        messages = self.transcript(rc) + extra
        conv = self.app.services.conversations
        for _step in range(rc.definition.max_steps):
            system = self.system_prompt(rc, user.locale)
            resp = llm.complete(model=model, system=system, messages=messages, tools=runtime.specs())
            messages.append(resp.as_message())
            if resp.text.strip():
                conv.post_agent_message(rc.actor, rc.job.job_id, resp.text.strip(), agent_name=rc.definition.name)
            if not resp.tool_calls:
                run.status = AgentRunStatus.COMPLETED
                return
            waiting = False
            for call in resp.tool_calls:
                outcome = runtime.execute(call.name, call.arguments)
                messages.append(LlmMessage(role="tool", tool_call_id=call.id, content=outcome.content,
                                           is_error=outcome.is_error))
                waiting = waiting or outcome.pending_action_id is not None
            if waiting:
                run.status = AgentRunStatus.WAITING
                return
        run.status = AgentRunStatus.COMPLETED
        conv.post_agent_message(rc.actor, rc.job.job_id, "Let me pause here. What else can you tell me?",
                                agent_name=rc.definition.name)

    def continue_after_action(self, user_actor: ActorContext, action_id: str) -> AgentRun | None:
        """Execute (or drop) a confirmed/rejected write and let the agent continue."""
        from ..domain.entities import PendingAction

        with self.app.db.uow(user_actor.tenant_id) as uow:
            action = uow.repo(PendingAction).get(action_id)
        if action.status == PendingActionStatus.REJECTED:
            note = (f"[system notice] The person rejected the write via {action.tool}. Do not retry it.")
            return self.run(user_actor, action.job_id, "pending_action.rejected", [LlmMessage(role="user", content=note)])
        if action.status != PendingActionStatus.CONFIRMED:
            return None
        job, subject, cycle, definition = self._context(user_actor, action.job_id)
        actor = user_actor.for_agent(action.agent_run_id or "confirmed", definition.name)
        rc = RunContext(self.app, actor, definition, job, subject, cycle)
        runtime = ToolRuntime(rc)
        outcome = runtime.execute_confirmed(action.tool, action.arguments)
        status = PendingActionStatus.FAILED if outcome.is_error else PendingActionStatus.EXECUTED
        self.app.services.conversations.finish_action(actor, action_id, status, {"content": outcome.content[:4000]})
        note = (f"[system notice] The person confirmed the write via {action.tool}. Result: {outcome.content[:2000]}")
        return self.run(user_actor, action.job_id, "pending_action.confirmed", [LlmMessage(role="user", content=note)])

    def greet(self, user_actor: ActorContext, job_id: str) -> None:
        """Opening message when a job is dispatched (no LLM call needed)."""
        job, subject, cycle, definition = self._context(user_actor, job_id)
        st = self.app.registries.subject_types.get(subject.subject_type)
        with self.app.db.uow(user_actor.tenant_id) as uow:
            proposals_open = uow.repo(Message).first(conversation_id=self.app.services.conversations
                                                     ._conversation(uow, job_id).conversation_id)
        if proposals_open:
            return
        title = st.title.get(cycle.locale) or st.title.get("en") or st.name
        due = f" by {job.due_date.date().isoformat()}" if job.due_date else ""
        self.app.services.conversations.post_agent_message(
            user_actor, job_id,
            f"Hello! I'm collecting information about the {title} \"{subject.name}\" for {cycle.name}{due}. "
            "Tell me what you know, upload documents, or ask me to look things up in your connected sources.",
            agent_name=definition.name)


def image_part(media_type: str, data_b64: str) -> ContentPart:
    return ContentPart(type="image", media_type=media_type, data_b64=data_b64)
