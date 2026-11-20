"""Generic mapping between domain dataclasses and SQLAlchemy tables.

Columns are derived from dataclass fields: str→String/Text, int→Integer, bool→Boolean,
datetime→UTC DateTime, enums→String, everything else→JSON (via stdlib serialization).
"""

from __future__ import annotations

import dataclasses
import types
import typing
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Union, get_args, get_origin

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.types import TypeDecorator

from ...domain.serialization import from_jsonable, to_jsonable


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _unwrap_optional(tp: Any) -> tuple[Any, bool]:
    if get_origin(tp) in (Union, types.UnionType):
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return tp, False


LONG_TEXT_FIELDS = {"text", "extracted_text", "password_hash", "summary", "error", "scope", "description"}


def column_type(name: str, tp: Any) -> Any:
    base, _ = _unwrap_optional(tp)
    if base is str:
        return Text() if name in LONG_TEXT_FIELDS else String(255)
    if base is bool:
        return Boolean()
    if base is int:
        return Integer()
    if base is datetime:
        return UTCDateTime()
    if isinstance(base, type) and issubclass(base, Enum):
        return String(64)
    return JSON()


@dataclasses.dataclass
class EntityMapping:
    cls: type
    table: Table
    pk: str
    hints: dict[str, Any]

    def to_row(self, entity: Any) -> dict[str, Any]:
        row: dict[str, Any] = {}
        for f in dataclasses.fields(self.cls):
            v = getattr(entity, f.name)
            ctype = self.table.c[f.name].type
            if isinstance(ctype, JSON):
                row[f.name] = to_jsonable(v)
            elif isinstance(v, Enum):
                row[f.name] = v.value
            else:
                row[f.name] = v
        return row

    def from_row(self, row: Any) -> Any:
        m = row._mapping if hasattr(row, "_mapping") else row
        kwargs = {}
        for f in dataclasses.fields(self.cls):
            kwargs[f.name] = from_jsonable(self.hints[f.name], m[f.name])
        return self.cls(**kwargs)


def build_mapping(metadata: MetaData, cls: type, table_name: str, pk: str,
                  indexes: list[tuple[str, ...]] | None = None) -> EntityMapping:
    hints = typing.get_type_hints(cls)
    cols = []
    for f in dataclasses.fields(cls):
        _, optional = _unwrap_optional(hints[f.name])
        cols.append(Column(f.name, column_type(f.name, hints[f.name]), primary_key=(f.name == pk),
                           nullable=optional or f.name != pk and not _required(f)))
    table = Table(table_name, metadata, *cols)
    if "tenant_id" in table.c and pk != "tenant_id":
        Index(f"ix_{table_name}_tenant", table.c.tenant_id)
    for idx in indexes or []:
        Index(f"ix_{table_name}_{'_'.join(idx)}", *(table.c[c] for c in idx))
    return EntityMapping(cls, table, pk, hints)


def _required(f: dataclasses.Field[Any]) -> bool:
    return f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING  # type: ignore[misc]
