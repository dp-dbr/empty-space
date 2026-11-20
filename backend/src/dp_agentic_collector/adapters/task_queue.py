"""In-process TaskQueue backed by the transactional outbox table (ADR-0006)."""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import and_, select, update

from ..domain.events import Event
from ..domain.ids import utcnow
from ..extensions.interfaces import EventHandler, TaskQueue
from ..observability import log_context, span
from .sql.db import Database
from .sql.schema import outbox

log = logging.getLogger(__name__)


class InProcessTaskQueue(TaskQueue):
    def __init__(self, db: Database, poll_seconds: float = 0.5, max_attempts: int = 5) -> None:
        self.db = db
        self.poll_seconds = poll_seconds
        self.max_attempts = max_attempts
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def register(self, event_type: str, handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)

    def _claim(self, limit: int) -> list[Event]:
        now = utcnow()
        with self.db.engine.begin() as conn:
            rows = conn.execute(
                select(outbox).where(and_(outbox.c.status == "pending", outbox.c.available_at <= now))
                .order_by(outbox.c.event_id).limit(limit)
            ).fetchall()
            claimed = []
            for r in rows:
                # optimistic claim: bump attempts only if still pending with same attempts
                res = conn.execute(
                    update(outbox).where(and_(outbox.c.event_id == r.event_id, outbox.c.status == "pending",
                                              outbox.c.attempts == r.attempts))
                    .values(attempts=r.attempts + 1, available_at=now + timedelta(minutes=5))
                )
                if res.rowcount:
                    claimed.append(Event(type=r.type, tenant_id=r.tenant_id, actor_id=r.actor_id,
                                         payload=r.payload, trace_id=r.trace_id, event_id=r.event_id,
                                         attempts=r.attempts + 1))
            return claimed

    def _finish(self, ev: Event, error: str | None) -> None:
        with self.db.engine.begin() as conn:
            if error is None:
                conn.execute(update(outbox).where(outbox.c.event_id == ev.event_id).values(status="done", last_error=None))
            elif ev.attempts >= self.max_attempts:
                conn.execute(update(outbox).where(outbox.c.event_id == ev.event_id).values(status="dead", last_error=error))
            else:
                backoff = timedelta(seconds=min(60, 2 ** ev.attempts))
                conn.execute(update(outbox).where(outbox.c.event_id == ev.event_id)
                             .values(status="pending", available_at=utcnow() + backoff, last_error=error))

    def dispatch(self, ev: Event) -> None:
        with span(f"outbox.dispatch {ev.type}", trace_id=ev.trace_id, tenant_id=ev.tenant_id,
                  event_type=ev.type), log_context(tenant_id=ev.tenant_id, user_id=ev.actor_id, event=ev.type):
            for handler in self._handlers.get(ev.type, []):
                handler(ev)

    def run_pending(self, max_events: int = 100) -> int:
        with self._lock:
            events = self._claim(max_events)
            for ev in events:
                try:
                    self.dispatch(ev)
                except Exception as exc:  # noqa: BLE001 - handlers are isolated
                    log.exception("event %s failed", ev.type)
                    self._finish(ev, repr(exc))
                else:
                    self._finish(ev, None)
            return len(events)

    def drain(self, max_rounds: int = 50) -> int:
        """Process until no pending (immediately available) events remain. Used in tests/CLI."""
        total = 0
        for _ in range(max_rounds):
            n = self.run_pending()
            total += n
            if n == 0:
                break
        return total

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                n = self.run_pending()
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                n = 0
            if n == 0:
                self._stop.wait(self.poll_seconds)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="dpac-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
