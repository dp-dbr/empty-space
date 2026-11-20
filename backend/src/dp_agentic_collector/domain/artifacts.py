"""Artifact versions: a factsheet (Markdown) + data file (JSON) sharing one header."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from .enums import ArtifactStatus
from .errors import ValidationFailed
from .serialization import dataclass_from_dict, to_jsonable

FRONT_MATTER = "---"


@dataclass(frozen=True)
class Contributor:
    user_id: str
    agent_run_id: str | None = None


@dataclass(frozen=True)
class SourceRef:
    """Provenance of an externally obtained fact."""

    kind: str  # "mcp" | "attachment" | "artifact" | "file" | "user"
    on_behalf_of: str
    retrieved_at: str
    server: str | None = None
    tool: str | None = None
    reference: str | None = None
    attachment_id: str | None = None
    content_hash: str | None = None
    location: str | None = None
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArtifactHeader:
    tenant_id: str
    subject_id: str
    cycle: str
    version: int
    based_on: int | None
    schema_version: str
    locale: str
    status: ArtifactStatus
    contributor: Contributor
    created: datetime
    content_hash: str
    change_reason: str
    sources: tuple[SourceRef, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ArtifactHeader:
        d = dict(d)
        d["sources"] = tuple(source_from_dict(s) for s in d.get("sources", []))
        d["contributor"] = Contributor(**d["contributor"])
        d["status"] = ArtifactStatus(d["status"])
        d["created"] = datetime.fromisoformat(d["created"])
        return ArtifactHeader(**d)


def source_from_dict(d: dict[str, Any]) -> SourceRef:
    src = dataclass_from_dict(SourceRef, d)
    return replace(src, fields=tuple(src.fields or ()))


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_content_hash(data: dict[str, Any], factsheet_body: str) -> str:
    h = hashlib.sha256()
    h.update(canonical_json(data).encode("utf-8"))
    h.update(b"\n")
    h.update(factsheet_body.encode("utf-8"))
    return "sha256:" + h.hexdigest()


@dataclass(frozen=True)
class ArtifactVersion:
    """Immutable pair. ``factsheet_body`` excludes the front matter header."""

    header: ArtifactHeader
    data: dict[str, Any]
    factsheet_body: str

    def verify(self) -> bool:
        return compute_content_hash(self.data, self.factsheet_body) == self.header.content_hash

    # --- file representations ------------------------------------------------
    def data_file(self) -> str:
        return json.dumps(
            {"header": self.header.to_dict(), "data": self.data}, indent=2, ensure_ascii=False, sort_keys=False
        ) + "\n"

    def factsheet_file(self) -> str:
        header = json.dumps(self.header.to_dict(), indent=2, ensure_ascii=False)
        return f"{FRONT_MATTER}\n{header}\n{FRONT_MATTER}\n{self.factsheet_body}"

    @staticmethod
    def from_files(data_file: str, factsheet_file: str) -> ArtifactVersion:
        payload = json.loads(data_file)
        header = ArtifactHeader.from_dict(payload["header"])
        fs_header, body = split_front_matter(factsheet_file)
        if fs_header != payload["header"]:
            raise ValidationFailed("factsheet and data file headers differ")
        return ArtifactVersion(header=header, data=payload["data"], factsheet_body=body)


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith(FRONT_MATTER + "\n"):
        raise ValidationFailed("factsheet has no front matter")
    end = text.index("\n" + FRONT_MATTER + "\n", len(FRONT_MATTER))
    header = json.loads(text[len(FRONT_MATTER) + 1 : end])
    body = text[end + len(FRONT_MATTER) + 2 :]
    return header, body


@dataclass(frozen=True)
class NewArtifact:
    """Input to ArtifactStore.put — the store assigns version and created timestamp."""

    tenant_id: str
    subject_id: str
    cycle: str
    based_on: int | None
    schema_version: str
    locale: str
    status: ArtifactStatus
    contributor: Contributor
    change_reason: str
    data: dict[str, Any]
    factsheet_body: str
    sources: tuple[SourceRef, ...] = ()

    def build(self, version: int, created: datetime) -> ArtifactVersion:
        header = ArtifactHeader(
            tenant_id=self.tenant_id,
            subject_id=self.subject_id,
            cycle=self.cycle,
            version=version,
            based_on=self.based_on,
            schema_version=self.schema_version,
            locale=self.locale,
            status=self.status,
            contributor=self.contributor,
            created=created,
            content_hash=compute_content_hash(self.data, self.factsheet_body),
            change_reason=self.change_reason,
            sources=tuple(self.sources),
        )
        return ArtifactVersion(header=header, data=self.data, factsheet_body=self.factsheet_body)


@dataclass(frozen=True)
class VersionSelector:
    """Selects a version: explicit number, latest, or a named snapshot."""

    version: int | None = None
    snapshot: str | None = None  # "<cycle_id>/<name>"

    @staticmethod
    def latest() -> VersionSelector:
        return VersionSelector()

    @staticmethod
    def parse(value: str | int | None) -> VersionSelector:
        if value is None or value == "latest":
            return VersionSelector()
        if isinstance(value, int) or str(value).isdigit():
            return VersionSelector(version=int(value))
        if str(value).startswith("snapshot:"):
            return VersionSelector(snapshot=str(value)[len("snapshot:") :])
        raise ValidationFailed(f"invalid version selector {value!r}")


@dataclass(frozen=True)
class FieldChange:
    path: str
    change: str  # added | removed | changed
    old: Any = None
    new: Any = None


@dataclass(frozen=True)
class ArtifactDiff:
    from_ref: str
    to_ref: str
    field_changes: tuple[FieldChange, ...]
    factsheet_diff: str

    @property
    def empty(self) -> bool:
        return not self.field_changes and not self.factsheet_diff


@dataclass(frozen=True)
class Snapshot:
    tenant_id: str
    cycle_id: str
    name: str
    created: datetime
    created_by: str
    versions: dict[str, int]  # subject_id -> version
