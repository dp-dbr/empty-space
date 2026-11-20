"""The 13 extension-point interfaces.

Each interface has ``INTERFACE_VERSION``; breaking changes require a new version
(e.g. ``ArtifactStoreV2``). Third-party types never appear in these signatures.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from ..domain.artifacts import ArtifactDiff, ArtifactVersion, NewArtifact, Snapshot, VersionSelector
from ..domain.events import Event
from ..domain.permissions import ActorContext

# 1. SubjectType ------------------------------------------------------------------------

@dataclass(frozen=True)
class RuleViolation:
    rule: str
    path: str
    message: str
    severity: Literal["error", "warning"] = "error"


@dataclass(frozen=True)
class ValidationContext:
    """What rules may look at besides the data file (read-only, tenant-scoped)."""

    tenant_id: str
    cycle_id: str
    subject_id: str
    # Latest data of other subjects in the same cycle, keyed by subject name/lineage_key.
    peer_data: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


class ValidationRule(ABC):
    """6. Plausibility check beyond the JSON Schema."""

    INTERFACE_VERSION = "1"
    name: str = "rule"

    @abstractmethod
    def check(self, data: Mapping[str, Any], ctx: ValidationContext) -> list[RuleViolation]: ...


@dataclass(frozen=True)
class SchemaMigration:
    from_version: str
    to_version: str
    migrate: Callable[[dict[str, Any]], dict[str, Any]]


class SubjectType(ABC):
    INTERFACE_VERSION = "1"

    name: str
    schema_version: str
    title: Mapping[str, str]

    @abstractmethod
    def json_schema(self) -> dict[str, Any]: ...

    @abstractmethod
    def render_factsheet(self, data: Mapping[str, Any], context: Mapping[str, Any], locale: str) -> str: ...

    @abstractmethod
    def rules(self) -> Sequence[ValidationRule]: ...

    @abstractmethod
    def conversation_guide(self, locale: str) -> str: ...

    def migrations(self) -> Sequence[SchemaMigration]:
        return ()

    def supported_locales(self) -> Sequence[str]:
        return ("en",)


# 2. ChannelAdapter ---------------------------------------------------------------------

@dataclass(frozen=True)
class OutboundMessage:
    tenant_id: str
    user_id: str
    subject: str
    text: str
    link: str | None = None
    job_id: str | None = None


class ChannelAdapter(ABC):
    INTERFACE_VERSION = "1"
    name: str

    @abstractmethod
    def send(self, message: OutboundMessage) -> None:
        """Deliver a notification/message to a person."""

    def receive(self) -> Iterable[tuple[str, str, str]]:
        """Optional pull of inbound messages: (tenant_id, job_id, text)."""
        return ()


# 3. DataSourceConnector -----------------------------------------------------------------

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    classification: Literal["read", "write"] = "read"


@dataclass(frozen=True)
class ToolOutput:
    content: Any
    sources: tuple[dict[str, Any], ...] = ()
    is_error: bool = False


@dataclass(frozen=True)
class ConnectorContext:
    actor: ActorContext
    job_id: str | None
    subject_id: str | None
    cycle_id: str | None
    lineage_key: str | None = None


class DataSourceConnector(ABC):
    """Prefill data for agents, exposed as tools. Always executed with the acting user's rights."""

    INTERFACE_VERSION = "1"
    name: str

    @abstractmethod
    def tools(self, ctx: ConnectorContext) -> Sequence[ToolSpec]: ...

    @abstractmethod
    def call(self, ctx: ConnectorContext, tool: str, arguments: Mapping[str, Any]) -> ToolOutput: ...

    def prefill(self, ctx: ConnectorContext) -> dict[str, Any]:
        return {}


# 4. ArtifactStore ---------------------------------------------------------------------

class ArtifactStore(ABC):
    INTERFACE_VERSION = "1"

    @abstractmethod
    def put(self, new: NewArtifact) -> ArtifactVersion:
        """Store atomically. Raises VersionConflict if ``new.based_on`` is not the latest."""

    @abstractmethod
    def get(self, tenant_id: str, subject_id: str, selector: VersionSelector | None = None) -> ArtifactVersion: ...

    @abstractmethod
    def latest_version(self, tenant_id: str, subject_id: str) -> int | None: ...

    @abstractmethod
    def history(self, tenant_id: str, subject_id: str) -> list[ArtifactVersion]: ...

    @abstractmethod
    def diff(self, a: ArtifactVersion, b: ArtifactVersion) -> ArtifactDiff: ...

    @abstractmethod
    def as_of(self, tenant_id: str, cycle_id: str, at: datetime) -> dict[str, ArtifactVersion]: ...

    @abstractmethod
    def verify(self, tenant_id: str, subject_id: str, version: int) -> bool: ...

    @abstractmethod
    def snapshot(self, tenant_id: str, cycle_id: str, name: str, created_by: str,
                 subject_ids: Iterable[str]) -> Snapshot: ...

    @abstractmethod
    def get_snapshot(self, tenant_id: str, cycle_id: str, name: str) -> Snapshot: ...

    @abstractmethod
    def list_snapshots(self, tenant_id: str, cycle_id: str) -> list[Snapshot]: ...


# 5. Projection ------------------------------------------------------------------------

class Projection(ABC):
    INTERFACE_VERSION = "1"

    @abstractmethod
    def project(self, version: ArtifactVersion, subject_type: str, lineage_key: str) -> None: ...

    @abstractmethod
    def query(self, tenant_id: str, cycle_id: str | None = None, subject_type: str | None = None,
              latest_only: bool = True, where: Mapping[str, Any] | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def history(self, tenant_id: str, subject_id: str) -> list[dict[str, Any]]: ...


# 7. LLMProvider -----------------------------------------------------------------------

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ContentPart:
    type: Literal["text", "image"]
    text: str = ""
    media_type: str = ""
    data_b64: str = ""


@dataclass(frozen=True)
class LlmMessage:
    """Provider-neutral message. role: system | user | assistant | tool."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    parts: tuple[ContentPart, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    is_error: bool = False


@dataclass(frozen=True)
class LlmToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class LlmResponse:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    stop_reason: str = "end_turn"
    usage: Mapping[str, int] = field(default_factory=dict)


class LlmProvider(ABC):
    INTERFACE_VERSION = "1"
    name: str

    @abstractmethod
    def complete(self, *, model: str, system: str, messages: Sequence[LlmMessage],
                 tools: Sequence[LlmToolSpec], max_tokens: int = 2048,
                 temperature: float | None = None) -> LlmResponse: ...


# 8. AgentDefinition -----------------------------------------------------------------------

@dataclass(frozen=True)
class AgentDefinition:
    name: str
    role: Literal["orchestrator", "collector"]
    system_prompt: str
    provider: str
    model: str
    allowed_tools: tuple[str, ...]
    subject_types: tuple[str, ...] = ("*",)
    escalation: Mapping[str, Any] = field(default_factory=dict)
    max_steps: int = 8
    api_version: str = "dpac/v1"


# 9. IdentityProvider ----------------------------------------------------------------------

@dataclass(frozen=True)
class AuthenticatedIdentity:
    tenant_id: str
    user_id: str
    provider: str
    external_subject: str | None = None


class IdentityProvider(ABC):
    INTERFACE_VERSION = "1"
    name: str

    @abstractmethod
    def authenticate(self, tenant_id: str, credentials: Mapping[str, str]) -> AuthenticatedIdentity: ...


# 10. CredentialVault ----------------------------------------------------------------------

class CredentialVault(ABC):
    INTERFACE_VERSION = "1"

    @abstractmethod
    def put(self, tenant_id: str, owner_id: str, secret: Mapping[str, Any]) -> str:
        """Store a secret, return an opaque credential reference."""

    @abstractmethod
    def get(self, tenant_id: str, ref: str) -> dict[str, Any]: ...

    @abstractmethod
    def replace(self, tenant_id: str, ref: str, secret: Mapping[str, Any]) -> None: ...

    @abstractmethod
    def delete(self, tenant_id: str, ref: str) -> None: ...


# 11. AttachmentStore ----------------------------------------------------------------------

class AttachmentStore(ABC):
    INTERFACE_VERSION = "1"

    @abstractmethod
    def put(self, tenant_id: str, content: bytes) -> str:
        """Store content, return content hash (``sha256:<hex>``). Idempotent."""

    @abstractmethod
    def get(self, tenant_id: str, content_hash: str) -> bytes: ...

    @abstractmethod
    def exists(self, tenant_id: str, content_hash: str) -> bool: ...

    @abstractmethod
    def erase(self, tenant_id: str, content_hash: str) -> None: ...


# 12. FileProcessor ----------------------------------------------------------------------

@dataclass(frozen=True)
class ExtractedContent:
    text: str
    image_b64: str | None = None
    media_type: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class FileProcessor(ABC):
    INTERFACE_VERSION = "1"
    name: str
    mime_types: tuple[str, ...]

    @abstractmethod
    def extract(self, content: bytes, filename: str, mime_type: str) -> ExtractedContent: ...


# 13. TaskQueue ----------------------------------------------------------------------------

EventHandler = Callable[[Event], None]


class TaskQueue(ABC):
    INTERFACE_VERSION = "1"

    @abstractmethod
    def register(self, event_type: str, handler: EventHandler) -> None: ...

    @abstractmethod
    def run_pending(self, max_events: int = 100) -> int:
        """Dispatch pending outbox events; return number processed."""

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...
