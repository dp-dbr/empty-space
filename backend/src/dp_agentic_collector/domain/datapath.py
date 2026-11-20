"""Dotted paths into data files (``a.b``, ``list[0].c``) and three-way merge for conflicts."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
_MISSING = object()


def parse_path(path: str) -> list[str | int]:
    parts: list[str | int] = []
    for name, idx in _TOKEN.findall(path):
        parts.append(int(idx) if idx else name)
    if not parts:
        raise ValueError(f"empty path {path!r}")
    return parts


def get_path(data: Any, path: str, default: Any = None) -> Any:
    cur = data
    for p in parse_path(path):
        try:
            cur = cur[p]
        except (KeyError, IndexError, TypeError):
            return default
    return cur


def set_path(data: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    out = copy.deepcopy(data)
    parts = parse_path(path)
    cur: Any = out
    for i, p in enumerate(parts[:-1]):
        nxt = parts[i + 1]
        if isinstance(p, int):
            while len(cur) <= p:
                cur.append({} if isinstance(nxt, str) else [])
            cur = cur[p]
        else:
            if p not in cur or cur[p] is None:
                cur[p] = [] if isinstance(nxt, int) else {}
            cur = cur[p]
    last = parts[-1]
    if isinstance(last, int):
        while len(cur) <= last:
            cur.append(None)
    cur[last] = value
    return out


def flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten dicts to dotted paths; lists are treated as leaf values."""
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for k, v in data.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict) and v:
                out.update(flatten(v, key))
            else:
                out[key] = v
        return out
    return {prefix: data} if prefix else {}


@dataclass(frozen=True)
class FieldConflict:
    path: str
    base: Any
    mine: Any
    theirs: Any


def three_way(
    base: dict[str, Any], mine: dict[str, Any], theirs: dict[str, Any]
) -> tuple[dict[str, Any], list[FieldConflict]]:
    """Merge non-conflicting changes; return conflicts for fields changed on both sides."""
    fb, fm, ft = flatten(base), flatten(mine), flatten(theirs)
    merged = copy.deepcopy(theirs)
    conflicts: list[FieldConflict] = []
    for path in sorted(set(fb) | set(fm) | set(ft)):
        b, m, t = fb.get(path, _MISSING), fm.get(path, _MISSING), ft.get(path, _MISSING)
        if m == b or m == t:
            continue  # mine unchanged or identical to theirs -> keep theirs
        if t == b:
            if m is _MISSING:
                merged = _delete(merged, path)
            else:
                merged = set_path(merged, path, m)
            continue
        conflicts.append(
            FieldConflict(path, None if b is _MISSING else b, None if m is _MISSING else m,
                          None if t is _MISSING else t)
        )
    return merged, conflicts


def _delete(data: dict[str, Any], path: str) -> dict[str, Any]:
    out = copy.deepcopy(data)
    parts = parse_path(path)
    cur: Any = out
    for p in parts[:-1]:
        cur = cur[p]
    cur.pop(parts[-1], None)
    return out
