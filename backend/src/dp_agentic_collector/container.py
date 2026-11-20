"""Application container: wires settings, registries and the active infrastructure adapters.

The core is usable as a library: ``Collector.create(settings)`` gives access to all services.
"""

from __future__ import annotations

import logging
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .adapters.sql.db import Database
from .config import Settings
from .extensions.interfaces import (
    ArtifactStore,
    AttachmentStore,
    CredentialVault,
    IdentityProvider,
    LlmProvider,
    Projection,
    TaskQueue,
)
from .extensions.registry import Registries

if TYPE_CHECKING:
    from .services import Services

log = logging.getLogger(__name__)


class Collector:
    def __init__(self, settings: Settings, registries: Registries) -> None:
        self.settings = settings
        self.registries = registries
        self.db = Database(settings.database_url)
        self._llm_cache: dict[str, LlmProvider] = {}
        self.overrides: dict[str, Any] = {}

    @classmethod
    def create(cls, settings: Settings | None = None, *, create_schema: bool = True,
               load_plugins: bool = True) -> Collector:
        settings = settings or Settings()
        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
        registries = Registries()
        from . import builtin

        builtin.register(registries)
        if load_plugins:
            registries.load_entry_points(exclude={"builtin"})
            for d in settings.plugins_dirs:
                registries.load_plugins_dir(d)
        app = cls(settings, registries)
        if create_schema:
            app.db.create_all()
        from .agents.handlers import register_handlers

        register_handlers(app)
        return app

    # --- active infrastructure --------------------------------------------------------
    @cached_property
    def artifact_store(self) -> ArtifactStore:
        return self.registries.artifact_stores.get(self.settings.artifact_store)(self)

    @cached_property
    def projection(self) -> Projection:
        return self.registries.projections.get(self.settings.projection)(self)

    @cached_property
    def attachment_store(self) -> AttachmentStore:
        return self.registries.attachment_stores.get(self.settings.attachment_store)(self)

    @cached_property
    def vault(self) -> CredentialVault:
        return self.registries.credential_vaults.get(self.settings.credential_vault)(self)

    @cached_property
    def queue(self) -> TaskQueue:
        return self.registries.task_queues.get(self.settings.task_queue)(self)

    @cached_property
    def identity(self) -> IdentityProvider:
        return self.registries.identity_providers.get(self.settings.identity_provider)(self)

    def llm(self, provider: str) -> LlmProvider:
        if provider in self.overrides.get("llm", {}):
            return self.overrides["llm"][provider]  # type: ignore[no-any-return]
        if provider not in self._llm_cache:
            self._llm_cache[provider] = self.registries.llm_providers.get(provider)(self)
        return self._llm_cache[provider]

    @cached_property
    def services(self) -> Services:
        from .services import Services

        return Services(self)

    def drain(self) -> int:
        """Synchronously process all pending events (tests, CLI)."""
        q = self.queue
        return q.drain() if hasattr(q, "drain") else q.run_pending()

