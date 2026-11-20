# ADR-0009: Official MCP Python SDK behind an adapter; tool classification

* Status: accepted
* Date: 2026-11-20

## Decision
`adapters/mcp_client.py` wraps the `mcp` SDK (stdio and streamable HTTP transports). Tool
classification: `annotations.readOnlyHint == true` → `read`, else `write`; admin overrides in
`McpServerDefinition.tool_classification` win; unknown → `write`. Each call opens a short-lived
client session with the acting user's credential (no shared pooled sessions across users).
The Collector's own MCP server uses the SDK's server (`FastMCP`, streamable HTTP) mounted into
the FastAPI app.

## Exit path
Only the adapter imports `mcp`; the domain sees `ToolDescriptor` / `ToolResult`.
