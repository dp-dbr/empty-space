"""MCP client adapter (official MCP Python SDK, ADR-0009).

Each call opens a short-lived client with the acting user's credential; no session is shared
between users. Only this module and ``mcp_server.py`` import ``mcp``.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import anyio

from ..domain.entities import McpServerDefinition
from ..domain.enums import McpAuthType, McpTransport
from ..domain.errors import ConnectionUnavailable
from ..observability import span

# Test/demo hook: endpoint "inproc://<name>" resolves to an in-process MCPServer instance.
INPROC_SERVERS: dict[str, Any] = {}


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only_hint: bool | None


@dataclass(frozen=True)
class ToolCallResult:
    content: Any
    is_error: bool


class McpUnauthorized(ConnectionUnavailable):
    code = "connection_unauthorized"


def _auth_headers(defn: McpServerDefinition, credential: Mapping[str, Any] | None) -> dict[str, str]:
    if defn.auth_type == McpAuthType.NONE or not credential:
        return {}
    if defn.auth_type == McpAuthType.API_KEY:
        header = str(defn.oauth.get("api_key_header", "Authorization"))
        key = str(credential.get("api_key", ""))
        return {header: f"Bearer {key}" if header.lower() == "authorization" else key}
    return {"Authorization": f"Bearer {credential.get('access_token', '')}"}


class McpClientAdapter:
    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self.timeout = timeout_seconds

    def _target(self, defn: McpServerDefinition, credential: Mapping[str, Any] | None,
                statuses: list[int]) -> tuple[Any, Any]:
        """Return (server-or-transport, http_client_to_close)."""
        from mcp import Client  # noqa: F401  (import check)

        if defn.endpoint.startswith("inproc://"):
            name = defn.endpoint[len("inproc://"):]
            if name not in INPROC_SERVERS:
                raise ConnectionUnavailable(f"in-process MCP server {name} not available")
            return INPROC_SERVERS[name], None
        if defn.transport == McpTransport.STDIO:
            from mcp import StdioServerParameters

            parts = shlex.split(defn.endpoint)
            env = {}
            if credential and defn.oauth.get("env_var"):
                env[str(defn.oauth["env_var"])] = str(credential.get("api_key") or credential.get("access_token", ""))
            return StdioServerParameters(command=parts[0], args=parts[1:], env=env or None), None
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        async def record(response: Any) -> None:
            statuses.append(response.status_code)

        http_client = httpx2.AsyncClient(headers=_auth_headers(defn, credential), timeout=self.timeout,
                                         event_hooks={"response": [record]})
        return streamable_http_client(defn.endpoint, http_client=http_client), http_client

    async def _with_client(self, defn: McpServerDefinition, credential: Mapping[str, Any] | None, fn: Any,
                           statuses: list[int]) -> Any:
        from mcp import Client

        target, http_client = self._target(defn, credential, statuses)
        try:
            async with Client(target, read_timeout_seconds=self.timeout) as client:
                return await fn(client)
        finally:
            if http_client is not None:
                await http_client.aclose()

    def _run(self, defn: McpServerDefinition, credential: Mapping[str, Any] | None, fn: Any) -> Any:
        statuses: list[int] = []
        try:
            return anyio.run(self._with_client, defn, credential, fn, statuses)
        except ConnectionUnavailable:
            raise
        except BaseException as exc:  # noqa: BLE001 - SDK raises exception groups
            if any(code in (401, 403) for code in statuses):
                raise McpUnauthorized(f"{defn.name}: credential rejected") from None
            raise ConnectionUnavailable(f"{defn.name}: {type(exc).__name__}") from None

    def list_tools(self, defn: McpServerDefinition, credential: Mapping[str, Any] | None) -> list[ToolDescriptor]:
        async def fn(client: Any) -> list[ToolDescriptor]:
            result = await client.list_tools()
            out = []
            for t in result.tools:
                ann = getattr(t, "annotations", None)
                out.append(ToolDescriptor(name=t.name, description=t.description or "",
                                          input_schema=dict(t.input_schema or {"type": "object"}),
                                          read_only_hint=getattr(ann, "read_only_hint", None) if ann else None))
            return out

        with span("mcp.list_tools", server=defn.name):
            return self._run(defn, credential, fn)  # type: ignore[no-any-return]

    def call_tool(self, defn: McpServerDefinition, credential: Mapping[str, Any] | None, tool: str,
                  arguments: Mapping[str, Any]) -> ToolCallResult:
        async def fn(client: Any) -> ToolCallResult:
            r = await client.call_tool(tool, dict(arguments))
            if getattr(r, "structured_content", None):
                content: Any = r.structured_content
            else:
                texts = [c.text for c in r.content if getattr(c, "type", "") == "text"]
                content = "\n".join(texts)
                try:
                    content = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    pass
            return ToolCallResult(content=content, is_error=bool(getattr(r, "is_error", False)))

        with span("mcp.call", server=defn.name, tool=tool):
            return self._run(defn, credential, fn)  # type: ignore[no-any-return]
