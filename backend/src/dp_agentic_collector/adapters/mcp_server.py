"""The Collector's own MCP server (streamable HTTP, mounted at /mcp).

Every tool resolves the caller from the OAuth bearer token (issued by the Collector,
ADR-0010) and goes through the same services and permission checks as the HTTP API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from ..domain.errors import DomainError, Unauthenticated
from ..domain.permissions import ActorContext
from ..domain.serialization import to_jsonable

if TYPE_CHECKING:
    from ..container import Collector

READ = ToolAnnotations(read_only_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)


class CollectorTokenVerifier:
    def __init__(self, app: Collector) -> None:
        self.app = app

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            actor = self.app.services.auth.resolve_bearer(token)
        except DomainError:
            return None
        return AccessToken(token=token, client_id="dpac", scopes=sorted(actor.scopes or []),
                           subject=actor.user_id, resource=f"{self.app.settings.public_base_url}/mcp")


def build_mcp_server(app: Collector) -> MCPServer:
    base = app.settings.public_base_url.rstrip("/")
    srv = MCPServer(
        "dp-agentic-collector",
        instructions="Access to collection cycles, jobs, subjects, factsheets and data files of the "
                     "authenticated user. Write tools need the dpac.write scope.",
        token_verifier=CollectorTokenVerifier(app),
        auth=AuthSettings(issuer_url=base, resource_server_url=f"{base}/mcp", required_scopes=["dpac.read"],
                          validate_token_resource=True),
    )
    s = app.services

    def actor() -> ActorContext:
        tok = get_access_token()
        if tok is None:
            raise Unauthenticated("missing token")
        return s.auth.resolve_bearer(tok.token)

    def out(value: Any) -> Any:
        return to_jsonable(value)

    @srv.tool(annotations=READ)
    def list_cycles() -> list[dict[str, Any]]:
        """Cycles visible to the caller."""
        return out(s.cycles.list_cycles(actor()))  # type: ignore[no-any-return]

    @srv.tool(annotations=READ)
    def list_my_jobs() -> list[dict[str, Any]]:
        """Collection jobs assigned to the caller."""
        return out(s.cycles.my_jobs(actor()))  # type: ignore[no-any-return]

    @srv.tool(annotations=READ)
    def list_cycle_jobs(cycle_id: str) -> list[dict[str, Any]]:
        """Jobs of a Cycle (all for cycle owners, own ones otherwise)."""
        return out(s.cycles.jobs(actor(), cycle_id))  # type: ignore[no-any-return]

    @srv.tool(annotations=READ)
    def list_subjects(cycle_id: str) -> list[dict[str, Any]]:
        """Subjects of a Cycle the caller may read."""
        return out(s.cycles.subjects(actor(), cycle_id))  # type: ignore[no-any-return]

    @srv.tool(annotations=READ)
    def get_factsheet(subject_id: str, version: str = "latest") -> dict[str, Any]:
        """Factsheet (Markdown) of a subject version: number, 'latest' or 'snapshot:<cycle>/<name>'."""
        v = s.artifacts.get(actor(), subject_id, version)
        return {"header": v.header.to_dict(), "factsheet": v.factsheet_body}

    @srv.tool(annotations=READ)
    def get_data_file(subject_id: str, version: str = "latest") -> dict[str, Any]:
        """Structured data file of a subject version."""
        v = s.artifacts.get(actor(), subject_id, version)
        return {"header": v.header.to_dict(), "data": v.data}

    @srv.tool(annotations=READ)
    def get_history(subject_id: str) -> list[dict[str, Any]]:
        """Version history (headers) of a subject."""
        return [v.header.to_dict() for v in s.artifacts.history(actor(), subject_id)]

    @srv.tool(annotations=READ)
    def diff_versions(subject_id: str, from_version: str, to_version: str = "latest") -> dict[str, Any]:
        """Field-level diff of data files and text diff of factsheets."""
        d = s.artifacts.diff(actor(), subject_id, from_version, to_version)
        return {"from": d.from_ref, "to": d.to_ref, "field_changes": out(list(d.field_changes)),
                "factsheet_diff": d.factsheet_diff}

    @srv.tool(annotations=READ)
    def list_snapshots(cycle_id: str) -> list[dict[str, Any]]:
        """Snapshots of a Cycle."""
        return out(s.cycles.snapshots(actor(), cycle_id))  # type: ignore[no-any-return]

    @srv.tool(annotations=READ)
    def compare_with_snapshot(cycle_id: str, snapshot: str) -> list[dict[str, Any]]:
        """Changes of a Cycle against a snapshot '<cycle_id>/<name>' (also of a previous Cycle)."""
        return s.artifacts.compare_snapshot(actor(), cycle_id, snapshot)  # type: ignore[no-any-return]

    @srv.tool(annotations=READ)
    def list_attachments(subject_id: str) -> list[dict[str, Any]]:
        """Shared attachments of a subject plus the caller's own private ones."""
        return [{"attachment_id": a.attachment_id, "filename": a.filename, "mime_type": a.mime_type,
                 "visibility": str(a.visibility), "status": str(a.status), "content_hash": a.content_hash}
                for a in s.attachments.list_for_subject(actor(), subject_id)]

    @srv.tool(annotations=READ)
    def read_attachment_text(attachment_id: str) -> dict[str, Any]:
        """Extracted text of a visible attachment."""
        a = s.attachments.get(actor(), attachment_id)
        return {"filename": a.filename, "text": (a.extracted_text or "")[:50_000]}

    @srv.tool(annotations=WRITE)
    def start_cycle(cycle_id: str) -> dict[str, Any]:
        """Start a Cycle (cycle owners; needs dpac.write)."""
        a = actor()
        a.require_scope("dpac.write")
        return out(s.cycles.start_cycle(a, cycle_id))  # type: ignore[no-any-return]

    @srv.tool(annotations=WRITE)
    def submit_data(job_id: str, data: dict[str, Any], change_reason: str = "submitted via MCP") -> dict[str, Any]:
        """Submit field values for an assigned job (needs dpac.write). Values are recorded as stated by
        the caller and submitted with optimistic locking; a conflict is returned, never overwritten."""
        a = actor()
        a.require_scope("dpac.write")
        facts = [{"path": k, "value": v, "rationale": "submitted via MCP"} for k, v in data.items()]
        for p in s.conversations.add_proposals(a, job_id, facts):
            s.conversations.decide_proposal(a, p.proposal_id, "confirm")
        res = s.artifacts.submit(a, job_id, change_reason)
        if res.conflict:
            return {"status": "conflict", "conflict": res.conflict}
        return {"status": "submitted", "version": res.version.header.version if res.version else None}

    return srv


def build_mcp_http_app(app: Collector) -> Any:
    srv = build_mcp_server(app)
    app.mcp_server = srv  # type: ignore[attr-defined]
    host = urlparse(app.settings.public_base_url).netloc
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host, "localhost:*", "127.0.0.1:*", "testserver", *app.settings.mcp_allowed_hosts],
        allowed_origins=[app.settings.public_base_url, "http://localhost:*", "http://127.0.0.1:*"])
    return srv.streamable_http_app(streamable_http_path="/", transport_security=security, stateless_http=True,
                                   json_response=True)
