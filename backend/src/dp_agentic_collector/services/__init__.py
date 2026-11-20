"""Application services. Every public method takes an ActorContext and enforces permissions."""

from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..container import Collector
    from .artifacts import ArtifactService
    from .attachments import AttachmentService
    from .auth import AuthService
    from .connections import ConnectionService
    from .consolidation import ConsolidationService
    from .conversations import ConversationService
    from .cycles import CycleService
    from .tenancy import TenancyService


class Services:
    def __init__(self, app: Collector) -> None:
        self.app = app

    @cached_property
    def tenancy(self) -> TenancyService:
        from .tenancy import TenancyService

        return TenancyService(self.app)

    @cached_property
    def auth(self) -> AuthService:
        from .auth import AuthService

        return AuthService(self.app)

    @cached_property
    def cycles(self) -> CycleService:
        from .cycles import CycleService

        return CycleService(self.app)

    @cached_property
    def artifacts(self) -> ArtifactService:
        from .artifacts import ArtifactService

        return ArtifactService(self.app)

    @cached_property
    def conversations(self) -> ConversationService:
        from .conversations import ConversationService

        return ConversationService(self.app)

    @cached_property
    def attachments(self) -> AttachmentService:
        from .attachments import AttachmentService

        return AttachmentService(self.app)

    @cached_property
    def connections(self) -> ConnectionService:
        from .connections import ConnectionService

        return ConnectionService(self.app)

    @cached_property
    def consolidation(self) -> ConsolidationService:
        from .consolidation import ConsolidationService

        return ConsolidationService(self.app)
