"""Wires outbox event types to orchestrator/agent handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..container import Collector


def register_handlers(app: Collector) -> None:
    try:
        from .orchestrator import Orchestrator
    except ModuleNotFoundError:
        return
    Orchestrator(app).register(app.queue)
