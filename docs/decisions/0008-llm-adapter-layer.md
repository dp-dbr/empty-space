# ADR-0008: Provider-neutral LLM layer; Anthropic + OpenAI-compatible + FakeLLM

* Status: accepted
* Date: 2026-11-20

## Decision
Internal types `LlmMessage`, `LlmToolSpec`, `ToolCall`, `LlmResponse`. Adapters translate
to/from the official `anthropic` and `openai` SDKs (the OpenAI adapter takes `base_url` and so
covers Azure OpenAI, Mistral, Ollama, vLLM). `FakeLlm` is scripted/deterministic for tests.
Provider + model are selected per AgentDefinition (`llm.provider`, `llm.model`), overridable
per tenant config. No LangChain/LangGraph.

## Exit path
New provider = new `LlmProvider` implementation + contract test fixture.
