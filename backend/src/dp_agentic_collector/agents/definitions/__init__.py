"""AgentDefinition loading from YAML (extension point 8)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ...domain.errors import ValidationFailed
from ...extensions.interfaces import AgentDefinition

SUPPORTED_API_VERSIONS = {"dpac/v1"}


def parse(doc: dict[str, Any]) -> AgentDefinition:
    if doc.get("api_version") not in SUPPORTED_API_VERSIONS:
        raise ValidationFailed(f"unsupported AgentDefinition api_version {doc.get('api_version')}")
    llm = doc.get("llm") or {}
    return AgentDefinition(
        name=doc["name"], role=doc["role"], system_prompt=doc.get("system_prompt", ""),
        provider=llm.get("provider", "fake"), model=llm.get("model", ""),
        allowed_tools=tuple(doc.get("allowed_tools") or ()), subject_types=tuple(doc.get("subject_types") or ("*",)),
        escalation=doc.get("escalation") or {}, max_steps=int(doc.get("max_steps", 8)), api_version=doc["api_version"],
    )


def load_dir(path: Path) -> list[AgentDefinition]:
    return [parse(yaml.safe_load(p.read_text())) for p in sorted(Path(path).glob("*.yaml"))]


def register(r: Any) -> None:
    for d in load_dir(Path(__file__).parent):
        r.agent_definitions.register(d.name, d)
