# ADR-0018: Deterministic FakeLlm for tests, demos and E2E

* Status: accepted
* Date: 2026-11-21

## Decision
`FakeLlm` supports scripted responses (list or callable) for precise unit/contract tests and a
small rule set for end-to-end flows without network access: `field: value` lines → `propose_facts`
(coerced via the schema in the system prompt), "look up/check/search" → first `[read]` MCP tool,
"write/update source" → first `[write]` tool, attachment mentions → `read_attachment`, tool results
with `facts` → proposals with `source_id`. It is selected with `DP_AC_DEFAULT_LLM_PROVIDER=fake`.

## Consequences
The same agent loop, tool runtime and permission checks run in tests as with real providers; the
contract tests (`tests/contract/test_llm_adapters.py`) prove that the Anthropic and
OpenAI-compatible adapters produce the same domain outcome.
