"""DataSourceConnectors: file-based prefill and previous versions from the ArtifactStore.
(The generic MCP connector lives in ``adapters/mcp_connector.py``.)"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ..domain.entities import Subject
from ..domain.enums import ArtifactStatus
from ..domain.ids import utcnow
from ..extensions.interfaces import ConnectorContext, DataSourceConnector, ToolOutput, ToolSpec

if TYPE_CHECKING:
    from ..container import Collector


class FilePrefillConnector(DataSourceConnector):
    """Reads ``<data_dir>/prefill/<tenant_id>/<lineage_key>.(json|yaml)`` prepared by cycle owners."""

    name = "file"

    def __init__(self, registries: Any) -> None:
        self._registries = registries

    @property
    def app(self) -> Collector:
        return self._registries.app  # type: ignore[no-any-return]

    def _path(self, ctx: ConnectorContext) -> Path | None:
        app = self.app
        root = Path(app.settings.data_dir) / "prefill" / ctx.actor.tenant_id
        for ext in ("json", "yaml", "yml"):
            p = root / f"{ctx.lineage_key}.{ext}"
            if p.exists():
                return p
        return None

    def tools(self, ctx: ConnectorContext) -> Sequence[ToolSpec]:
        if not ctx.lineage_key or self._path(ctx) is None:
            return []
        return [ToolSpec("lookup_prefill", "[read] Prefill data prepared by the cycle owner for this subject.",
                         {"type": "object", "properties": {}})]

    def prefill(self, ctx: ConnectorContext) -> dict[str, Any]:
        p = self._path(ctx)
        if p is None:
            return {}
        text = p.read_text()
        return json.loads(text) if p.suffix == ".json" else (yaml.safe_load(text) or {})

    def call(self, ctx: ConnectorContext, tool: str, arguments: Mapping[str, Any]) -> ToolOutput:
        p = self._path(ctx)
        data = self.prefill(ctx)
        return ToolOutput({"facts": data}, sources=({"kind": "file", "on_behalf_of": ctx.actor.user_id,
                                                     "retrieved_at": utcnow().isoformat(),
                                                     "location": f"prefill/{p.name if p else ''}"},))


class PreviousVersionsConnector(DataSourceConnector):
    """Latest approved data of the same subject (lineage) from previous Cycles."""

    name = "previous_versions"

    def __init__(self, registries: Any) -> None:
        self._registries = registries

    @property
    def app(self) -> Collector:
        return self._registries.app  # type: ignore[no-any-return]

    def _previous(self, ctx: ConnectorContext) -> tuple[Subject, Any] | None:
        app = self.app
        with app.db.uow(ctx.actor.tenant_id) as uow:
            candidates = [s for s in uow.repo(Subject).list(lineage_key=ctx.lineage_key)
                          if s.subject_id != ctx.subject_id]
        for s in sorted(candidates, key=lambda s: s.created_at, reverse=True):
            if not app.services.cycles.subject_read_decision(ctx.actor, s.subject_id).allowed:
                continue
            for v in reversed(app.artifact_store.history(ctx.actor.tenant_id, s.subject_id)):
                if v.header.status == ArtifactStatus.APPROVED:
                    return s, v
        return None

    def tools(self, ctx: ConnectorContext) -> Sequence[ToolSpec]:
        if not ctx.lineage_key:
            return []
        return [ToolSpec("get_previous_version", "[read] Approved data of this subject from a previous cycle.",
                         {"type": "object", "properties": {}})]

    def call(self, ctx: ConnectorContext, tool: str, arguments: Mapping[str, Any]) -> ToolOutput:
        found = self._previous(ctx)
        if found is None:
            return ToolOutput("No approved previous version available.")
        s, v = found
        return ToolOutput({"facts": v.data, "cycle": s.cycle_id, "version": v.header.version},
                          sources=({"kind": "artifact", "on_behalf_of": ctx.actor.user_id,
                                    "retrieved_at": utcnow().isoformat(),
                                    "reference": f"{s.subject_id}@v{v.header.version}",
                                    "content_hash": v.header.content_hash},))


def register(r: Any) -> None:
    r.connectors.register("file", FilePrefillConnector(r))
    r.connectors.register("previous_versions", PreviousVersionsConnector(r))
