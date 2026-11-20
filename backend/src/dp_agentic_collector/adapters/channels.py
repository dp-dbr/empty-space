"""Channel adapters: web (primary, in-app conversation notices) and CLI (stdout)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..extensions.interfaces import ChannelAdapter, OutboundMessage

if TYPE_CHECKING:
    from ..container import Collector

log = logging.getLogger(__name__)


class WebChannel(ChannelAdapter):
    """Web is the primary channel: the conversation lives in the UI. Notifications are kept as an
    in-app inbox (``sent``) and surfaced through the jobs list; e-mail is a future adapter."""

    name = "web"

    def __init__(self, app: Collector | None = None) -> None:
        self.app = app
        self.sent: list[OutboundMessage] = []

    def send(self, message: OutboundMessage) -> None:
        self.sent.append(message)
        log.info("web notification for user %s (job %s)", message.user_id, message.job_id)


class CliChannel(ChannelAdapter):
    name = "cli"

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []

    def send(self, message: OutboundMessage) -> None:
        self.sent.append(message)
        print(f"[dpac] to {message.user_id}: {message.text} {message.link or ''}")


def register(r: Any) -> None:
    r.channels.register("web", WebChannel())
    r.channels.register("cli", CliChannel())
