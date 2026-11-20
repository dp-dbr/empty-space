"""Registries for all extension points and the plugin loader (entry points + plugins dir)."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, TypeVar

from ..domain.errors import NotFound
from .interfaces import (
    AgentDefinition,
    ArtifactStore,
    AttachmentStore,
    ChannelAdapter,
    CredentialVault,
    DataSourceConnector,
    FileProcessor,
    IdentityProvider,
    LlmProvider,
    Projection,
    SubjectType,
    TaskQueue,
    ValidationRule,
)

log = logging.getLogger(__name__)
T = TypeVar("T")
ENTRY_POINT_GROUP = "dp_agentic_collector.plugins"


class Registry(Generic[T]):
    """Named registry for one extension point; checks interface compatibility."""

    def __init__(self, point: str, interface: type | None = None, *, factory: bool = False) -> None:
        self.point = point
        self.interface = interface
        self.factory = factory  # items are factories ``(app) -> implementation``
        self._items: dict[str, T] = {}
        self._origin: dict[str, str] = {}

    def register(self, name: str, item: T, *, origin: str = "core", replace: bool = False) -> T:
        if name in self._items and not replace:
            raise ValueError(f"{self.point}: '{name}' already registered by {self._origin[name]}")
        if self.interface is not None and not self.factory:
            target = item if isinstance(item, type) else type(item)
            if isinstance(target, type) and not issubclass(target, self.interface):
                raise TypeError(f"{self.point}: {name} does not implement {self.interface.__name__}")
            want = getattr(self.interface, "INTERFACE_VERSION", None)
            have = getattr(item, "INTERFACE_VERSION", want)
            if want is not None and have != want:
                raise TypeError(f"{self.point}: {name} implements v{have}, core expects v{want}")
        self._items[name] = item
        self._origin[name] = origin
        return item

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError:
            raise NotFound(f"{self.point} '{name}' is not registered") from None

    def __contains__(self, name: str) -> bool:
        return name in self._items

    def names(self) -> list[str]:
        return sorted(self._items)

    def items(self) -> Iterator[tuple[str, T]]:
        return iter(sorted(self._items.items()))

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "point": self.point,
                "name": n,
                "origin": self._origin[n],
                "interface_version": getattr(self.interface, "INTERFACE_VERSION", None),
            }
            for n in self.names()
        ]


@dataclass
class Registries:
    """All 13 extension-point registries.

    Singleton infrastructure points (ArtifactStore, Projection, CredentialVault,
    AttachmentStore, TaskQueue, IdentityProvider) register *factories* taking the
    application container; the active one is chosen by configuration.
    """

    subject_types: Registry[SubjectType] = field(default_factory=lambda: Registry("subject_type", SubjectType))
    channels: Registry[ChannelAdapter] = field(default_factory=lambda: Registry("channel", ChannelAdapter))
    connectors: Registry[DataSourceConnector] = field(
        default_factory=lambda: Registry("data_source_connector", DataSourceConnector))
    artifact_stores: Registry[Callable[..., ArtifactStore]] = field(default_factory=lambda: Registry("artifact_store", ArtifactStore, factory=True))
    projections: Registry[Callable[..., Projection]] = field(default_factory=lambda: Registry("projection", Projection, factory=True))
    validation_rules: Registry[ValidationRule] = field(
        default_factory=lambda: Registry("validation_rule", ValidationRule))
    llm_providers: Registry[Callable[..., LlmProvider]] = field(default_factory=lambda: Registry("llm_provider", LlmProvider, factory=True))
    agent_definitions: Registry[AgentDefinition] = field(
        default_factory=lambda: Registry("agent_definition", AgentDefinition))
    identity_providers: Registry[Callable[..., IdentityProvider]] = field(
        default_factory=lambda: Registry("identity_provider", IdentityProvider, factory=True))
    credential_vaults: Registry[Callable[..., CredentialVault]] = field(
        default_factory=lambda: Registry("credential_vault", CredentialVault, factory=True))
    attachment_stores: Registry[Callable[..., AttachmentStore]] = field(
        default_factory=lambda: Registry("attachment_store", AttachmentStore, factory=True))
    file_processors: Registry[FileProcessor] = field(
        default_factory=lambda: Registry("file_processor", FileProcessor))
    task_queues: Registry[Callable[..., TaskQueue]] = field(default_factory=lambda: Registry("task_queue", TaskQueue, factory=True))
    loaded_plugins: list[str] = field(default_factory=list)
    app: Any = None  # the owning application container (set by Collector)

    def all(self) -> list[Registry[Any]]:
        return [v for v in vars(self).values() if isinstance(v, Registry)]

    def describe(self) -> list[dict[str, Any]]:
        return [row for reg in self.all() for row in reg.describe()]

    # --- plugin loading ---------------------------------------------------------------
    def load_entry_points(self, group: str = ENTRY_POINT_GROUP, exclude: set[str] | None = None) -> None:
        for ep in importlib.metadata.entry_points(group=group):
            if exclude and ep.name in exclude:
                continue
            register = ep.load()
            register(self)
            self.loaded_plugins.append(f"entrypoint:{ep.name}")

    def load_plugins_dir(self, path: Path | str) -> None:
        """Load folder plugins: ``plugin.py`` with ``register(registries)`` or a
        declarative ``subject_type.yaml`` (handled by the folder SubjectType loader)."""
        root = Path(path)
        if not root.is_dir():
            return
        for folder in sorted(p for p in root.rglob("*") if p.is_dir()):
            plugin_py = folder / "plugin.py"
            manifest = folder / "subject_type.yaml"
            if plugin_py.exists():
                mod_name = "dpac_plugin_" + "_".join(folder.relative_to(root).parts)
                spec = importlib.util.spec_from_file_location(mod_name, plugin_py)
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = module
                spec.loader.exec_module(module)
                module.register(self)
                self.loaded_plugins.append(f"dir:{folder}")
            elif manifest.exists():
                from ..adapters.subject_type_folder import FolderSubjectType

                st = FolderSubjectType.load(folder)
                self.subject_types.register(st.name, st, origin=f"dir:{folder}")
                self.loaded_plugins.append(f"dir:{folder}")
