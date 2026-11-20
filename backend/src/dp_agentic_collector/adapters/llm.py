"""LLM provider adapters: Anthropic, OpenAI-compatible, and the deterministic FakeLlm.

Only this module imports provider SDKs. Everything else uses the internal
``LlmMessage`` / ``ToolCall`` / ``LlmResponse`` format (ADR-0008).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from ..extensions.interfaces import LlmMessage, LlmProvider, LlmResponse, LlmToolSpec, ToolCall
from ..observability import span

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"


# --- Anthropic ----------------------------------------------------------------------------

class AnthropicProvider(LlmProvider):
    """Messages API with client tools. Notes for current models: no sampling parameters,
    no forced ``tool_choice`` (``auto`` only), and assistant content blocks (including thinking
    blocks) are replayed unchanged via ``LlmMessage.raw``."""

    name = "anthropic"

    def __init__(self, api_key: str | None = None, client: Any = None, effort: str | None = None) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.client = client
        self.effort = effort

    def _messages(self, messages: Sequence[LlmMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "assistant":
                if m.raw and m.raw_provider == self.name:
                    content: list[dict[str, Any]] = [dict(b) for b in m.raw]
                else:
                    content = []
                    if m.content:
                        content.append({"type": "text", "text": m.content})
                    for tc in m.tool_calls:
                        content.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
                out.append({"role": "assistant", "content": content or [{"type": "text", "text": ""}]})
            elif m.role == "tool":
                block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content,
                         "is_error": m.is_error}
                # all tool results of one turn go into a single user message
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) and \
                        all(b.get("type") == "tool_result" for b in out[-1]["content"]):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            else:
                parts: list[dict[str, Any]] = []
                for p in m.parts:
                    if p.type == "image":
                        parts.append({"type": "image", "source": {"type": "base64", "media_type": p.media_type,
                                                                   "data": p.data_b64}})
                    else:
                        parts.append({"type": "text", "text": p.text})
                if m.content:
                    parts.append({"type": "text", "text": m.content})
                out.append({"role": "user", "content": parts or [{"type": "text", "text": ""}]})
        return out

    def complete(self, *, model: str, system: str, messages: Sequence[LlmMessage],
                 tools: Sequence[LlmToolSpec], max_tokens: int = 16000,
                 temperature: float | None = None) -> LlmResponse:
        kwargs: dict[str, Any] = {
            "model": model or DEFAULT_ANTHROPIC_MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": self._messages(messages),
        }
        if tools:
            kwargs["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                               for t in tools]
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        with span("llm.call", provider=self.name, model=kwargs["model"]):
            resp = self.client.messages.create(**kwargs)
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        raw: list[dict[str, Any]] = []
        for block in resp.content:
            raw.append(block.model_dump(exclude_none=True))
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {})))
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        return LlmResponse(text="".join(text_parts), tool_calls=tuple(calls), stop_reason=str(resp.stop_reason),
                           usage=usage, raw=tuple(raw), raw_provider=self.name)


# --- OpenAI-compatible --------------------------------------------------------------------

class OpenAICompatibleProvider(LlmProvider):
    """Chat Completions API: OpenAI, Azure OpenAI, Mistral, Ollama, vLLM (via ``base_url``)."""

    name = "openai_compatible"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, client: Any = None) -> None:
        if client is None:
            import openai

            client = openai.OpenAI(api_key=api_key or "unused", base_url=base_url)
        self.client = client

    @staticmethod
    def _messages(system: str, messages: Sequence[LlmMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
        for m in messages:
            if m.role == "system":
                out.append({"role": "system", "content": m.content})
            elif m.role == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.content or None}
                if m.tool_calls:
                    msg["tool_calls"] = [{"id": tc.id, "type": "function",
                                          "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                                         for tc in m.tool_calls]
                out.append(msg)
            elif m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id,
                            "content": ("ERROR: " if m.is_error else "") + m.content})
            else:
                if m.parts:
                    parts: list[dict[str, Any]] = []
                    for p in m.parts:
                        if p.type == "image":
                            parts.append({"type": "image_url",
                                          "image_url": {"url": f"data:{p.media_type};base64,{p.data_b64}"}})
                        else:
                            parts.append({"type": "text", "text": p.text})
                    if m.content:
                        parts.append({"type": "text", "text": m.content})
                    out.append({"role": "user", "content": parts})
                else:
                    out.append({"role": "user", "content": m.content})
        return out

    def complete(self, *, model: str, system: str, messages: Sequence[LlmMessage],
                 tools: Sequence[LlmToolSpec], max_tokens: int = 4096,
                 temperature: float | None = None) -> LlmResponse:
        kwargs: dict[str, Any] = {"model": model, "messages": self._messages(system, messages),
                                  "max_tokens": max_tokens}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                                  "parameters": t.input_schema}} for t in tools]
        if temperature is not None:
            kwargs["temperature"] = temperature
        with span("llm.call", provider=self.name, model=model):
            resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        calls = []
        for tc in choice.message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        usage = {}
        if getattr(resp, "usage", None):
            usage = {"input_tokens": resp.usage.prompt_tokens, "output_tokens": resp.usage.completion_tokens}
        stop = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens"}.get(
            choice.finish_reason or "stop", str(choice.finish_reason))
        return LlmResponse(text=choice.message.content or "", tool_calls=tuple(calls), stop_reason=stop, usage=usage)


# --- FakeLlm ----------------------------------------------------------------------------------

Script = Callable[[str, Sequence[LlmMessage], Sequence[LlmToolSpec]], LlmResponse]
_FIELD_LINE = re.compile(r"^\s*([A-Za-z_][\w.\[\]]*)\s*[:=]\s*(.+?)\s*$")


class FakeLlm(LlmProvider):
    """Deterministic provider for tests and demos.

    * ``script``: queue of responses or a callable; used first when present.
    * Otherwise a rule-based collector behaviour:
      - user lines ``field: value`` for known schema fields → ``propose_facts``
      - "look up"/"check" → first available read tool whose name starts with ``mcp__``
      - "use attachment"/"from the file" → ``read_attachment`` for the newest attachment
      - "write"/"update source" → first write tool (needs confirmation)
      - after tool results → short summary text.
    """

    name = "fake"

    def __init__(self, script: list[LlmResponse] | Script | None = None) -> None:
        self.script = script
        self.calls: list[dict[str, Any]] = []
        self._n = 0

    def complete(self, *, model: str, system: str, messages: Sequence[LlmMessage],
                 tools: Sequence[LlmToolSpec], max_tokens: int = 2048,
                 temperature: float | None = None) -> LlmResponse:
        self.calls.append({"model": model, "system": system, "messages": list(messages), "tools": list(tools)})
        with span("llm.call", provider=self.name, model=model):
            if callable(self.script):
                return self.script(system, messages, tools)
            if isinstance(self.script, list) and self.script:
                return self.script.pop(0)
            return self._rules(system, messages, tools)

    def _id(self) -> str:
        self._n += 1
        return f"fake_{self._n}"

    def _rules(self, system: str, messages: Sequence[LlmMessage], tools: Sequence[LlmToolSpec]) -> LlmResponse:
        names = {t.name for t in tools}
        last = messages[-1] if messages else LlmMessage(role="user")
        if last.role == "tool":
            # summarize the tool round
            results = [m for m in reversed(messages) if m.role == "tool"]
            if any(m.is_error for m in results[:1]):
                return LlmResponse(text=f"That did not work: {results[0].content[:200]}")
            if "propose_facts" in names and last.content.startswith("{"):
                facts, source_id = _facts_from_tool_result(last.content)
                if facts:
                    return LlmResponse(text="", tool_calls=(ToolCall(self._id(), "propose_facts", {
                        "facts": [{"path": k, "value": v, "rationale": "found in source", "source_id": source_id}
                                  for k, v in facts.items()],
                    }),), stop_reason="tool_use")
            return LlmResponse(text="Thanks — I have recorded this. Please review the proposed facts and "
                                    "confirm them, or tell me what to change.")
        text = last.content or ""
        if text.startswith("[system notice]"):
            if "rejected" in text:
                return LlmResponse(text="Understood, I did not write anything.")
            return LlmResponse(text="Thanks — the write was carried out. What else can you tell me?")
        lower = text.lower()
        fields = _schema_fields(system)
        facts = []
        for line in text.splitlines():
            m = _FIELD_LINE.match(line)
            if m and (not fields or m.group(1).split(".")[0].split("[")[0] in fields):
                facts.append({"path": m.group(1), "value": _coerce(m.group(2)), "rationale": "stated by respondent"})
        if facts and "propose_facts" in names:
            return LlmResponse(text="", tool_calls=(ToolCall(self._id(), "propose_facts", {"facts": facts}),),
                               stop_reason="tool_use")
        if ("write" in lower or "update source" in lower):
            write_tools = [t for t in tools if t.name.startswith("mcp__") and "[write]" in t.description]
            if write_tools:
                return LlmResponse(text="", tool_calls=(ToolCall(self._id(), write_tools[0].name,
                                                                 {"record": text}),), stop_reason="tool_use")
        if ("look up" in lower or "check" in lower or "search" in lower):
            read_tools = [t for t in tools if t.name.startswith("mcp__") and "[read]" in t.description]
            if read_tools:
                return LlmResponse(text="", tool_calls=(ToolCall(self._id(), read_tools[0].name,
                                                                 {"query": text}),), stop_reason="tool_use")
            if any(t.name.startswith("mcp__") for t in tools) is False and ("look up" in lower):
                return LlmResponse(text="I have no connected source available to look this up.")
        if ("attachment" in lower or "file" in lower) and "read_attachment" in names:
            ids = re.findall(r"att_[0-9a-f]{32}", "\n".join(m.content for m in messages))
            if ids:
                return LlmResponse(text="", tool_calls=(ToolCall(self._id(), "read_attachment",
                                                                 {"attachment_id": ids[-1]}),), stop_reason="tool_use")
        if "previous" in lower and "get_previous_version" in names:
            return LlmResponse(text="", tool_calls=(ToolCall(self._id(), "get_previous_version", {}),),
                               stop_reason="tool_use")
        return LlmResponse(text="Hello! I am collecting information for this subject. Tell me the facts as "
                                "'field: value' lines, ask me to look something up in your connected sources, "
                                "or upload a file.")


def _facts_from_tool_result(content: str) -> tuple[dict[str, Any], str | None]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {}, None
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return {}, None
    facts = result.get("facts") if isinstance(result, dict) else None
    sid = payload.get("source_id")
    return (facts if isinstance(facts, dict) else {}), (sid if isinstance(sid, str) else None)


def _schema_fields(system: str) -> set[str]:
    m = re.search(r"<schema_fields>(.*?)</schema_fields>", system, re.S)
    return {f.strip() for f in m.group(1).split(",") if f.strip()} if m else set()


def _coerce(v: str) -> Any:
    v = v.strip()
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if v.startswith("[") or v.startswith("{"):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            pass
    if "," in v:
        return [p.strip() for p in v.split(",") if p.strip()]
    return v


def register(r: Any) -> None:
    r.llm_providers.register("anthropic", lambda app: AnthropicProvider(api_key=app.settings.anthropic_api_key))
    r.llm_providers.register("openai_compatible", lambda app: OpenAICompatibleProvider(
        api_key=app.settings.openai_api_key, base_url=app.settings.openai_base_url))
    r.llm_providers.register("fake", lambda app: FakeLlm())
