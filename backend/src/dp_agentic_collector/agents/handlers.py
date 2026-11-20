"""Wires outbox event types to the orchestrator."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..container import Collector


def register_handlers(app: Collector) -> None:
    from .orchestrator import Orchestrator

    app.orchestrator = Orchestrator(app)
    app.orchestrator.register(app.queue)
