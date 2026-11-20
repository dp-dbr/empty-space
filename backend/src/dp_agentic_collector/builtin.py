"""Registers the built-in example implementations for all 13 extension points."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .extensions.registry import Registries


def register(r: Registries) -> None:
    from .adapters.attachment_store import FilesystemAttachmentStore
    from .adapters.git_store import GitArtifactStore
    from .adapters.identity import LocalIdentityProvider
    from .adapters.projection import SqlProjection
    from .adapters.task_queue import InProcessTaskQueue
    from .adapters.vault import DbCredentialVault, load_keys

    # 4 ArtifactStore, 5 Projection, 9 IdentityProvider, 10 CredentialVault, 11 AttachmentStore, 13 TaskQueue
    r.artifact_stores.register("git", lambda app: GitArtifactStore(Path(app.settings.data_dir) / "artifacts"))
    r.projections.register("sql", lambda app: SqlProjection(app.db))
    r.identity_providers.register("local", lambda app: LocalIdentityProvider(app.db))
    r.credential_vaults.register(
        "db", lambda app: DbCredentialVault(app.db, load_keys(app.settings.vault_keys, Path(app.settings.data_dir))))
    r.attachment_stores.register(
        "filesystem", lambda app: FilesystemAttachmentStore(Path(app.settings.data_dir) / "attachments"))
    r.task_queues.register(
        "in_process", lambda app: InProcessTaskQueue(app.db, poll_seconds=app.settings.worker_poll_seconds))

    _register_optional(r)


def _register_optional(r: Any) -> None:
    """Extension points implemented in later modules; imported lazily to keep startup light."""
    import importlib

    for mod in ("adapters.llm", "adapters.channels", "adapters.connectors", "adapters.file_processors",
                "agents.definitions"):
        try:
            module = importlib.import_module(f"{__package__}.{mod}")
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.startswith(f"{__package__}."):
                continue
            raise
        module.register(r)
