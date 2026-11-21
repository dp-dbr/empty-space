# ADR-0019: MCP Python SDK 2.x specifics and fixture servers

* Status: accepted
* Date: 2026-11-21

## Context
The installed official SDK (`mcp` 2.x) provides `mcp.Client` (Streamable HTTP via
`streamable_http_client` with an `httpx2` client, stdio, or in-process servers) and
`mcp.server.mcpserver.MCPServer` with `TokenVerifier`/`AuthSettings`.

## Decision
* Client calls run via `anyio.run` in worker threads (no MCP calls in request handlers). HTTP
  status codes are captured with an `httpx2` response hook to detect 401/403 reliably (the SDK
  surfaces them as generic errors) and mark connections `expired`.
* The Collector's MCP server is stateless Streamable HTTP with JSON responses, mounted at `/mcp`,
  DNS-rebinding protection with an allow-list from `DP_AC_PUBLIC_BASE_URL` and
  `DP_AC_MCP_ALLOWED_HOSTS`, `validate_token_resource=True`.
* Fixture servers (`dp_agentic_collector.testing.mcp_fixtures`): read-only API-key registry,
  OAuth-protected ticket server with read and write tools, open catalog, and a minimal OAuth
  authorization server (code + PKCE). They contain no real data and power tests, demo and E2E.
