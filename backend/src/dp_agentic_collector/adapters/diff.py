"""Field-level diff of data files (deepdiff) and text diff of factsheets (difflib)."""

from __future__ import annotations

import difflib
from typing import Any

from deepdiff import DeepDiff
from deepdiff.helper import NotPresent

from ..domain.artifacts import ArtifactDiff, ArtifactVersion, FieldChange

_KIND = {
    "dictionary_item_added": "added",
    "iterable_item_added": "added",
    "dictionary_item_removed": "removed",
    "iterable_item_removed": "removed",
    "values_changed": "changed",
    "type_changes": "changed",
}


def _path(parts: list[Any]) -> str:
    out = ""
    for p in parts:
        if isinstance(p, int):
            out += f"[{p}]"
        else:
            out += ("." if out else "") + str(p)
    return out or "$"


def _clean(v: Any) -> Any:
    return None if isinstance(v, NotPresent) else v


def field_changes(old: dict[str, Any], new: dict[str, Any]) -> list[FieldChange]:
    dd = DeepDiff(old, new, view="tree", ignore_order=False)
    changes: list[FieldChange] = []
    for kind, items in dd.items():
        mapped = _KIND.get(kind)
        if mapped is None:
            continue
        for item in items:
            changes.append(FieldChange(path=_path(item.path(output_format="list")), change=mapped,
                                       old=_clean(item.t1), new=_clean(item.t2)))
    return sorted(changes, key=lambda c: c.path)


def text_diff(old: str, new: str, a: str = "a", b: str = "b") -> str:
    return "".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                        fromfile=a, tofile=b))


def diff_versions(a: ArtifactVersion, b: ArtifactVersion) -> ArtifactDiff:
    ra = f"{a.header.subject_id}@v{a.header.version}"
    rb = f"{b.header.subject_id}@v{b.header.version}"
    return ArtifactDiff(from_ref=ra, to_ref=rb, field_changes=tuple(field_changes(a.data, b.data)),
                        factsheet_diff=text_diff(a.factsheet_body, b.factsheet_body, ra, rb))
