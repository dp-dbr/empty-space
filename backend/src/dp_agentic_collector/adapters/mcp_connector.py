"""Generic MCP DataSourceConnector: exposes the acting user's MCP connections as agent tools."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..domain.enums import ToolClassification
from ..domain.errors import ConnectionUnavailable, PermissionDenied
from ..extensions.interfaces import ConnectorContext, DataSourceConnector, ToolOutput, ToolSpec


class McpConnector(DataSourceConnector):
    name = "mcp"

    def __init__(self, registries: Any) -> None:
        self._registries = registries
        self._index: dict[tuple[str, str], tuple[str, str, ToolClassification, str]] = {}

    @property
    def app(self) -> Any:
        return self._registries.app

    def _allowlist(self, ctx: ConnectorContext) -> tuple[str, ...] | None:
        if not ctx.actor.agent_name:
            return None
        return tuple(self.app.registries.agent_definitions.get(ctx.actor.agent_name).allowed_tools)

    def tools(self, ctx: ConnectorContext) -> Sequence[ToolSpec]:
        svc = self.app.services.connections
        specs = []
        for t in svc.available_tools(ctx.actor, self._allowlist(ctx)):
            self._index[(ctx.actor.user_id, t.qualified_name)] = (t.server.server_id, t.tool.name, t.classification,
                                                                  t.server.name)
            specs.append(ToolSpec(
                name=t.qualified_name,
                description=f"[{t.classification}] {t.server.name}: {t.tool.description}"[:1000],
                input_schema=t.tool.input_schema or {"type": "object"},
                classification="write" if t.classification == ToolClassification.WRITE else "read"))
        return specs

    def target_for(self, qualified: str) -> str:
        for (_, q), (_, tool, _, server_name) in self._index.items():
            if q == qualified:
                return f"{server_name} ({tool})"
        return qualified

    def call(self, ctx: ConnectorContext, tool: str, arguments: Mapping[str, Any]) -> ToolOutput:
        entry = self._index.get((ctx.actor.user_id, tool))
        if entry is None:
            # re-resolve (e.g. confirmation executed in a later worker run)
            self.tools(ctx)
            entry = self._index.get((ctx.actor.user_id, tool))
        if entry is None:
            return ToolOutput("This source is no longer available (connection revoked or expired).", is_error=True)
        server_id, remote_tool, classification, _ = entry
        try:
            content, is_error, source = self.app.services.connections.call(
                ctx.actor, server_id, remote_tool, dict(arguments), classification, self._allowlist(ctx),
                confirmed=classification == ToolClassification.WRITE)
        except (ConnectionUnavailable, PermissionDenied) as exc:
            return ToolOutput(f"Source unavailable: {exc.message}. Ask the person to reconnect it in "
                              "'My connections' or continue without it.", is_error=True)
        return ToolOutput(content, sources=(source,) if not is_error else (), is_error=is_error)


def register(r: Any) -> None:
    r.connectors.register("mcp", McpConnector(r))
