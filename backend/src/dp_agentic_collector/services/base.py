from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..adapters.sql.db import UnitOfWork
from ..domain.entities import AuditEvent
from ..domain.permissions import ActorContext
from ..observability import current_trace_id

if TYPE_CHECKING:
    from ..container import Collector

log = logging.getLogger("dpac.audit")


class ServiceBase:
    def __init__(self, app: Collector) -> None:
        self.app = app
        self.db = app.db

    @property
    def services(self) -> Any:
        return self.app.services

    @staticmethod
    def audit(uow: UnitOfWork, actor: ActorContext | None, action: str, resource: str,
              outcome: str = "ok", **details: Any) -> None:
        tenant_id = actor.tenant_id if actor else uow.tenant_id
        assert tenant_id is not None
        ev = AuditEvent(tenant_id=tenant_id, actor_id=actor.user_id if actor else None, action=action,
                        resource=resource, outcome=outcome,
                        agent_run_id=actor.agent_run_id if actor else None,
                        trace_id=(actor.trace_id if actor else None) or current_trace_id(), details=details)
        uow.repo(AuditEvent).add(ev)
        log.info("audit %s %s %s", action, resource, outcome)

    def audit_now(self, actor: ActorContext, action: str, resource: str, outcome: str = "ok",
                  **details: Any) -> None:
        with self.db.uow(actor.tenant_id) as uow:
            self.audit(uow, actor, action, resource, outcome, **details)
