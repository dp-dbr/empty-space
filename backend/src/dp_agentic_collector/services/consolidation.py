"""Consolidation: gaps and contradictions across the subjects of a Cycle."""

from __future__ import annotations

from typing import Any

from ..adapters.schema_validation import validate_schema
from ..domain.entities import CollectionJob, Cycle, Subject
from ..domain.enums import JobState
from ..domain.permissions import ActorContext, can_manage_cycle
from ..domain.serialization import to_jsonable
from ..extensions.interfaces import ValidationContext
from .base import ServiceBase


class ConsolidationService(ServiceBase):
    def report(self, actor: ActorContext, cycle_id: str) -> dict[str, Any]:
        with self.db.uow(actor.tenant_id) as uow:
            cycle = uow.repo(Cycle).get(cycle_id)
            can_manage_cycle(actor, cycle).enforce()
            subjects = uow.repo(Subject).list(cycle_id=cycle_id, order_by="name")
            jobs = {j.subject_id: j for j in uow.repo(CollectionJob).list(cycle_id=cycle_id)}
        st = self.app.registries.subject_types.get(cycle.subject_type)
        schema = st.json_schema()
        fields = list(schema.get("properties", {}).keys())
        required = set(schema.get("required", []))
        rows = {r["subject_id"]: r for r in self.app.projection.query(actor.tenant_id, cycle_id=cycle_id)}
        latest = {s.lineage_key: rows[s.subject_id]["data"] for s in subjects if s.subject_id in rows}
        gaps: list[dict[str, Any]] = []
        contradictions: list[dict[str, Any]] = []
        coverage = {f: 0 for f in fields}
        for s in subjects:
            job = jobs.get(s.subject_id)
            row = rows.get(s.subject_id)
            if row is None:
                gaps.append({"check": "no_submission", "subjects": [s.lineage_key], "path": "$",
                             "message": f"{s.name}: nothing submitted yet"
                                        + (f" (job {job.state})" if job else "")})
                continue
            data = row["data"]
            for f in fields:
                if data.get(f) not in (None, "", []):
                    coverage[f] += 1
            missing = [f for f in required if data.get(f) in (None, "", [])]
            for f in missing:
                gaps.append({"check": "missing_required", "subjects": [s.lineage_key], "path": f,
                             "message": f"{s.name}: required field '{f}' is empty"})
            if row["status"] not in ("validated", "approved"):
                for v in validate_schema(schema, data):
                    gaps.append({"check": "schema", "subjects": [s.lineage_key], "path": v.path, "message": v.message})
                ctx = ValidationContext(actor.tenant_id, cycle_id, s.subject_id,
                                        {k: d for k, d in latest.items() if k != s.lineage_key})
                for rule in st.rules():
                    for v in rule.check(data, ctx):
                        contradictions.append({"check": v.rule, "subjects": [s.lineage_key], "path": v.path,
                                               "message": v.message})
            if job and job.state in (JobState.OVERDUE, JobState.ESCALATED):
                gaps.append({"check": "overdue", "subjects": [s.lineage_key], "path": "$",
                             "message": f"{s.name}: job is {job.state}"})
        for check in st.consolidation_checks():
            for issue in check(latest):
                target = contradictions if issue.kind == "contradiction" else gaps
                target.append({"check": issue.check, "subjects": list(issue.subjects), "path": issue.path,
                               "message": issue.message})
        n = len(latest)
        return {
            "cycle_id": cycle_id, "subjects": len(subjects), "with_data": n,
            "coverage": {f: (round(c / n, 2) if n else 0.0) for f, c in coverage.items()},
            "gaps": gaps, "contradictions": contradictions,
            "statuses": to_jsonable({r["status"]: sum(1 for x in rows.values() if x["status"] == r["status"])
                                     for r in rows.values()}),
        }
