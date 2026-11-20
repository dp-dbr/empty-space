"""Relational projection of data files. Keeps every version for history queries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import and_, insert, select, update

from ..domain.artifacts import ArtifactVersion
from ..domain.datapath import get_path
from ..extensions.interfaces import Projection
from .sql.db import Database
from .sql.schema import artifact_projection as P


class SqlProjection(Projection):
    def __init__(self, db: Database) -> None:
        self.db = db

    def project(self, version: ArtifactVersion, subject_type: str, lineage_key: str) -> None:
        h = version.header
        with self.db.engine.begin() as conn:
            exists = conn.execute(select(P.c.id).where(and_(
                P.c.tenant_id == h.tenant_id, P.c.subject_id == h.subject_id, P.c.version == h.version))).first()
            if exists:
                return
            conn.execute(update(P).where(and_(P.c.tenant_id == h.tenant_id, P.c.subject_id == h.subject_id))
                         .values(is_latest=False))
            conn.execute(insert(P).values(
                tenant_id=h.tenant_id, subject_id=h.subject_id, subject_type=subject_type,
                lineage_key=lineage_key, cycle_id=h.cycle, version=h.version, based_on=h.based_on,
                status=str(h.status), schema_version=h.schema_version, contributor_id=h.contributor.user_id,
                agent_run_id=h.contributor.agent_run_id, created=h.created, content_hash=h.content_hash,
                change_reason=h.change_reason, data=version.data, is_latest=True,
            ))

    @staticmethod
    def _row(r: Any) -> dict[str, Any]:
        d = dict(r._mapping)
        d.pop("id", None)
        return d

    def query(self, tenant_id: str, cycle_id: str | None = None, subject_type: str | None = None,
              latest_only: bool = True, where: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        conds = [P.c.tenant_id == tenant_id]
        if cycle_id:
            conds.append(P.c.cycle_id == cycle_id)
        if subject_type:
            conds.append(P.c.subject_type == subject_type)
        if latest_only:
            conds.append(P.c.is_latest.is_(True))
        with self.db.engine.connect() as conn:
            rows = [self._row(r) for r in conn.execute(select(P).where(and_(*conds)).order_by(P.c.subject_id, P.c.version))]
        if where:
            rows = [r for r in rows if all(get_path(r["data"], k) == v for k, v in where.items())]
        return rows

    def history(self, tenant_id: str, subject_id: str) -> list[dict[str, Any]]:
        with self.db.engine.connect() as conn:
            return [self._row(r) for r in conn.execute(
                select(P).where(and_(P.c.tenant_id == tenant_id, P.c.subject_id == subject_id)).order_by(P.c.version))]
