"""Tool runtime for agent runs.

Every tool is evaluated against the effective permission (acting user's rights ∩ admin policy ∩
user grant ∩ AgentDefinition allowlist). Write tools never execute directly: they become
PendingActions the user must confirm.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..domain.entities import CollectionJob, Cycle, Subject
from ..domain.errors import DomainError
from ..domain.ids import utcnow
from ..domain.permissions import ActorContext, agent_allows_tool
from ..extensions.interfaces import AgentDefinition, ConnectorContext, LlmToolSpec, ToolOutput
from ..observability import span

if TYPE_CHECKING:
    from ..container import Collector

log = logging.getLogger(__name__)


@dataclass
class RunContext:
    app: Collector
    actor: ActorContext  # agent context: user rights + agent_run_id
    definition: AgentDefinition
    job: CollectionJob
    subject: Subject
    cycle: Cycle
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)  # source_id -> SourceRef dict

    def connector_ctx(self) -> ConnectorContext:
        return ConnectorContext(actor=self.actor, job_id=self.job.job_id, subject_id=self.subject.subject_id,
                                cycle_id=self.cycle.cycle_id, lineage_key=self.subject.lineage_key)

    def register_sources(self, sources: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> list[str]:
        ids = []
        for s in sources:
            sid = f"src_{len(self.sources) + 1}"
            self.sources[sid] = dict(s)
            ids.append(sid)
        return ids


@dataclass
class AgentTool:
    spec: LlmToolSpec
    classification: str  # read | write
    handler: Callable[[RunContext, dict[str, Any]], ToolOutput]
    target: str = "collector"
    # For write tools: creates the pending action summary
    describe: Callable[[dict[str, Any]], str] | None = None


@dataclass
class ToolOutcome:
    content: str
    is_error: bool = False
    pending_action_id: str | None = None


ToolProvider = Callable[[RunContext], list[AgentTool]]


def internal_tools(rc: RunContext) -> list[AgentTool]:
    s = rc.app.services

    def get_draft(rc: RunContext, args: dict[str, Any]) -> ToolOutput:
        return ToolOutput(s.conversations.draft_view(rc.actor, rc.job.job_id))

    def propose_facts(rc: RunContext, args: dict[str, Any]) -> ToolOutput:
        facts = []
        for f in args.get("facts") or []:
            if not isinstance(f, dict):
                continue
            srcs = []
            for sid in ([f.get("source_id")] if f.get("source_id") else []) + list(f.get("source_ids") or []):
                if sid in rc.sources:
                    srcs.append({**rc.sources[sid], "fields": [f.get("path")]})
            facts.append({"path": f.get("path"), "value": f.get("value"), "rationale": f.get("rationale", ""),
                          "sources": srcs})
        created = s.conversations.add_proposals(rc.actor, rc.job.job_id, facts)
        return ToolOutput({"proposed": [{"proposal_id": p.proposal_id, "path": p.path} for p in created],
                           "note": "The person will confirm or reject each proposal in the UI."})

    return [
        AgentTool(LlmToolSpec("get_draft", "Get the current working data of the data file, including facts "
                              "awaiting confirmation and any version conflict.",
                              {"type": "object", "properties": {}}), "read", get_draft),
        AgentTool(LlmToolSpec(
            "propose_facts",
            "Propose values for data-file fields. The person confirms each one. Use schema field paths "
            "(e.g. 'technical_owner' or 'interfaces[0]'). Pass source_id from tool results when the value "
            "comes from a source.",
            {"type": "object", "properties": {"facts": {"type": "array", "items": {
                "type": "object", "properties": {
                    "path": {"type": "string"}, "value": {},
                    "rationale": {"type": "string"}, "source_id": {"type": "string"}},
                "required": ["path", "value"]}}}, "required": ["facts"]}),
            "read", propose_facts),
    ]


def connector_tools(rc: RunContext) -> list[AgentTool]:
    """Expose DataSourceConnectors (file, previous versions, MCP, plugins) as tools."""
    out: list[AgentTool] = []
    ctx = rc.connector_ctx()
    for name, connector in rc.app.registries.connectors.items():
        try:
            specs = connector.tools(ctx)
        except DomainError as exc:
            log.info("connector %s unavailable: %s", name, exc.message)
            continue
        for spec in specs:
            def handler(rc: RunContext, args: dict[str, Any], _c: Any = connector, _t: str = spec.name) -> ToolOutput:
                return _c.call(rc.connector_ctx(), _t, args)  # type: ignore[no-any-return]

            desc = spec.description
            out.append(AgentTool(LlmToolSpec(spec.name, desc, spec.input_schema), spec.classification, handler,
                                 target=getattr(connector, "target_for", lambda t, _n=name: _n)(spec.name),
                                 describe=lambda a: json.dumps(a, ensure_ascii=False)[:500]))
    return out


def attachment_tools(rc: RunContext) -> list[AgentTool]:
    """Attachments visible to the acting user (own private + shared)."""
    svc = rc.app.services.attachments

    def list_attachments(rc: RunContext, args: dict[str, Any]) -> ToolOutput:
        items = svc.list_for_job(rc.actor, rc.job.job_id)
        return ToolOutput([{"attachment_id": a.attachment_id, "filename": a.filename, "mime_type": a.mime_type,
                            "visibility": str(a.visibility), "status": str(a.status),
                            "extracted": a.extracted_text is not None} for a in items])

    def read_attachment(rc: RunContext, args: dict[str, Any]) -> ToolOutput:
        att = svc.get(rc.actor, str(args.get("attachment_id", "")))
        if att.extracted_text is None:
            att = svc.process(rc.actor, att.attachment_id)
        text = att.extracted_text or ""
        facts = _facts_in_text(text)
        content: dict[str, Any] = {"filename": att.filename, "visibility": str(att.visibility), "text": text[:15000]}
        if facts:
            content["facts"] = facts
        private = att.visibility == "private"
        if private:
            content["note"] = ("This attachment is private. Facts taken from it need the person's explicit "
                               "confirmation that they become visible to others.")
        return ToolOutput(content, sources=({
            "kind": "attachment", "attachment_id": att.attachment_id, "content_hash": att.content_hash,
            "location": f"conversation:{att.conversation_id}", "on_behalf_of": rc.actor.user_id,
            "retrieved_at": utcnow().isoformat(), "private": private},))

    return [
        AgentTool(LlmToolSpec("list_attachments", "[read] List the files available in this conversation.",
                              {"type": "object", "properties": {}}), "read", list_attachments),
        AgentTool(LlmToolSpec("read_attachment", "[read] Read the extracted content of an attachment.",
                              {"type": "object", "properties": {"attachment_id": {"type": "string"}},
                               "required": ["attachment_id"]}), "read", read_attachment),
    ]


def _facts_in_text(text: str) -> dict[str, Any]:
    """Pick up simple 'field: value' lines (helps deterministic test models; real models read the text)."""
    import re

    out: dict[str, Any] = {}
    for line in text.splitlines()[:500]:
        m = re.match(r"^\s*([a-z_][a-z0-9_]*)\s*:\s*(.+?)\s*$", line)
        if m:
            out[m.group(1)] = int(m.group(2)) if m.group(2).isdigit() else m.group(2)
    return out


DEFAULT_PROVIDERS: list[ToolProvider] = [internal_tools, attachment_tools, connector_tools]


class ToolRuntime:
    def __init__(self, rc: RunContext, providers: list[ToolProvider] | None = None) -> None:
        self.rc = rc
        self.tools: dict[str, AgentTool] = {}
        for provider in providers or DEFAULT_PROVIDERS:
            for tool in provider(rc):
                if agent_allows_tool(rc.definition.allowed_tools, tool.spec.name):
                    self.tools[tool.spec.name] = tool

    def specs(self) -> list[LlmToolSpec]:
        return [t.spec for t in self.tools.values()]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        rc = self.rc
        s = rc.app.services
        tool = self.tools.get(name)
        if tool is None:
            s.tenancy.audit_now(rc.actor, "tool.call", f"tool:{name}", outcome="denied", reason="not available")
            return ToolOutcome(f"Tool {name} is not available to you.", is_error=True)
        with span("tool.call", tool=name, classification=tool.classification, agent_run_id=rc.actor.agent_run_id):
            if tool.classification == "write":
                summary = tool.describe(arguments) if tool.describe else json.dumps(arguments)[:500]
                action = s.conversations.create_pending_action(rc.actor, rc.job.job_id, name, arguments,
                                                               summary, tool.target)
                return ToolOutcome("This write needs the person's confirmation. A confirmation request was "
                                   "shown to them; stop here and wait.", pending_action_id=action.action_id)
            try:
                out = tool.handler(rc, arguments)
            except DomainError as exc:
                s.tenancy.audit_now(rc.actor, "tool.call", f"tool:{name}", outcome="error", error=exc.code)
                return ToolOutcome(f"{exc.code}: {exc.message}", is_error=True)
            except Exception as exc:  # noqa: BLE001 - tool failures go back to the model
                log.exception("tool %s failed", name)
                s.tenancy.audit_now(rc.actor, "tool.call", f"tool:{name}", outcome="error", error=type(exc).__name__)
                return ToolOutcome(f"Tool failed: {type(exc).__name__}", is_error=True)
        if name not in ("get_draft", "propose_facts"):
            s.tenancy.audit_now(rc.actor, "tool.call", f"tool:{name}", outcome="error" if out.is_error else "ok")
        source_ids = rc.register_sources(out.sources)
        payload: dict[str, Any] = {"result": out.content}
        if source_ids:
            payload["source_id"] = source_ids[0] if len(source_ids) == 1 else source_ids
        return ToolOutcome(json.dumps(payload, ensure_ascii=False, default=str)[:20_000], is_error=out.is_error)

    def execute_confirmed(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        """Execute a write tool after explicit user confirmation (re-checks permissions)."""
        tool = self.tools.get(name)
        if tool is None:
            return ToolOutcome(f"Tool {name} is no longer available (connection changed?).", is_error=True)
        with span("tool.call", tool=name, classification="write", confirmed=True):
            try:
                out = tool.handler(self.rc, arguments)
            except DomainError as exc:
                return ToolOutcome(f"{exc.code}: {exc.message}", is_error=True)
        self.rc.app.services.tenancy.audit_now(self.rc.actor, "tool.call", f"tool:{name}",
                                               outcome="error" if out.is_error else "ok", confirmed=True)
        return ToolOutcome(json.dumps({"result": out.content}, default=str)[:20_000], is_error=out.is_error)
