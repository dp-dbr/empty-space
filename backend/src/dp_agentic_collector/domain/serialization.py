"""Generic, stdlib-only conversion between dataclasses and JSON-compatible values."""

from __future__ import annotations

import dataclasses
import types
import typing
from datetime import datetime
from enum import Enum
from functools import cache
from typing import Any, Union, get_args, get_origin


def to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [to_jsonable(v) for v in value]
    return value


@cache
def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def from_jsonable(tp: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return from_jsonable(args[0], value)
        return value
    if origin in (list, tuple, set, frozenset):
        inner = (get_args(tp) or (Any,))[0]
        return [from_jsonable(inner, v) for v in value]
    if origin is dict:
        dargs = get_args(tp)
        inner = dargs[1] if len(dargs) == 2 else Any
        return {k: from_jsonable(inner, v) for k, v in value.items()}
    if tp is Any:
        return value
    if isinstance(tp, type):
        if dataclasses.is_dataclass(tp):
            return dataclass_from_dict(tp, value)
        if issubclass(tp, Enum):
            return value if isinstance(value, tp) else tp(value)
        if tp is datetime:
            return value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return value


def dataclass_from_dict(cls: type, data: dict[str, Any]) -> Any:
    hints = _hints(cls)
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name in data:
            kwargs[f.name] = from_jsonable(hints[f.name], data[f.name])
    return cls(**kwargs)
