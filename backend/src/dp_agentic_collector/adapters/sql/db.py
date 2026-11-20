"""Database access: engine, unit of work, tenant-scoped repositories, outbox publishing."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, Generic, TypeVar

from sqlalchemy import Engine, and_, create_engine, delete, event, insert, select, update
from sqlalchemy.engine import Connection

from ...domain.errors import NotFound, TenantViolation
from ...domain.ids import utcnow
from .schema import MAPPINGS, metadata, outbox

T = TypeVar("T")


def make_engine(url: str) -> Engine:
    kwargs: dict[str, Any] = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn: Any, _: Any) -> None:  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    return engine


class Repository(Generic[T]):
    """CRUD for one entity type, bound to one tenant. Every statement filters tenant_id."""

    def __init__(self, conn: Connection, cls: type[T], tenant_id: str | None) -> None:
        self.conn = conn
        self.m = MAPPINGS[cls]
        self.tenant_id = tenant_id
        self.t = self.m.table

    def _scope(self) -> list[Any]:
        if self.tenant_id is None or "tenant_id" not in self.t.c or self.m.pk == "tenant_id":
            return []
        return [self.t.c.tenant_id == self.tenant_id]

    def _check(self, entity: T) -> None:
        if self.tenant_id is not None and hasattr(entity, "tenant_id"):
            if entity.tenant_id != self.tenant_id:  # type: ignore[attr-defined]
                raise TenantViolation("entity belongs to another tenant")

    def add(self, entity: T) -> T:
        self._check(entity)
        self.conn.execute(insert(self.t).values(**self.m.to_row(entity)))
        return entity

    def save(self, entity: T) -> T:
        self._check(entity)
        pk = getattr(entity, self.m.pk)
        res = self.conn.execute(
            update(self.t).where(and_(self.t.c[self.m.pk] == pk, *self._scope())).values(**self.m.to_row(entity))
        )
        if res.rowcount == 0:
            raise NotFound(f"{self.t.name} {pk} not found")
        return entity

    def find(self, pk: str) -> T | None:
        row = self.conn.execute(select(self.t).where(and_(self.t.c[self.m.pk] == pk, *self._scope()))).first()
        return None if row is None else self.m.from_row(row)

    def get(self, pk: str) -> T:
        found = self.find(pk)
        if found is None:
            raise NotFound(f"{self.t.name} {pk} not found")
        return found

    def list(self, order_by: str | None = None, limit: int | None = None, **filters: Any) -> list[T]:
        conds = list(self._scope())
        for k, v in filters.items():
            col = self.t.c[k]
            conds.append(col.in_(v) if isinstance(v, list | tuple | set) else col == v)
        stmt = select(self.t).where(and_(*conds)) if conds else select(self.t)
        if order_by:
            desc = order_by.startswith("-")
            col = self.t.c[order_by.lstrip("-")]
            stmt = stmt.order_by(col.desc() if desc else col.asc())
        if limit:
            stmt = stmt.limit(limit)
        return [self.m.from_row(r) for r in self.conn.execute(stmt)]

    def first(self, **filters: Any) -> T | None:
        rows = self.list(limit=1, **filters)
        return rows[0] if rows else None

    def remove(self, pk: str) -> None:
        self.conn.execute(delete(self.t).where(and_(self.t.c[self.m.pk] == pk, *self._scope())))


class UnitOfWork:
    """One DB transaction. Repositories created here are bound to ``tenant_id``."""

    def __init__(self, conn: Connection, tenant_id: str | None) -> None:
        self.conn = conn
        self.tenant_id = tenant_id
        self._repos: dict[type, Repository[Any]] = {}

    def repo(self, cls: type[T]) -> Repository[T]:
        if cls not in self._repos:
            self._repos[cls] = Repository(self.conn, cls, self.tenant_id)
        return self._repos[cls]

    def publish(self, event_type: str, actor_id: str, payload: dict[str, Any] | None = None,
                trace_id: str | None = None, delay_seconds: float = 0) -> None:
        if self.tenant_id is None:
            raise TenantViolation("events must be tenant-scoped")
        now = utcnow()
        self.conn.execute(insert(outbox).values(
            tenant_id=self.tenant_id, type=event_type, actor_id=actor_id, payload=payload or {},
            trace_id=trace_id, status="pending", attempts=0,
            available_at=now + timedelta(seconds=delay_seconds), created_at=now,
        ))


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = make_engine(url)

    def create_all(self) -> None:
        metadata.create_all(self.engine)

    @contextmanager
    def uow(self, tenant_id: str | None) -> Iterator[UnitOfWork]:
        with self.engine.begin() as conn:
            yield UnitOfWork(conn, tenant_id)
