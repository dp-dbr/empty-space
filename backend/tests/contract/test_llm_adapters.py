"""Contract tests: the same agent runs unchanged with every LLM adapter.

The provider SDK clients are replaced by recording fakes that return genuine SDK response
objects, so request/response translation of each adapter is exercised end to end.
"""

import json

import pytest
from anthropic.types import Message as AnthropicMessage
from openai.types.chat import ChatCompletion

from dp_agentic_collector.adapters.llm import AnthropicProvider, OpenAICompatibleProvider
from dp_agentic_collector.domain.enums import AgentRunStatus
from tests.conftest import confirm_all

# Scripted "model" behaviour, provider independent: step 1 propose facts, step 2 answer.
STEPS = [
    {"tool": ("propose_facts", {"facts": [{"path": "name", "value": "CRM"},
                                          {"path": "criticality", "value": "medium"},
                                          {"path": "business_owner", "value": "Sales"}]})},
    {"text": "Thanks, please confirm the proposed facts."},
]


class FakeAnthropicClient:
    def __init__(self):
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        step = STEPS[min(len(self.requests) - 1, len(STEPS) - 1)]
        if "tool" in step:
            name, args = step["tool"]
            content = [{"type": "thinking", "thinking": "", "signature": "sig-1"},
                       {"type": "tool_use", "id": f"toolu_{len(self.requests)}", "name": name, "input": args}]
            stop = "tool_use"
        else:
            content, stop = [{"type": "text", "text": step["text"]}], "end_turn"
        return AnthropicMessage.model_validate({
            "id": "msg_1", "type": "message", "role": "assistant", "model": kwargs["model"], "content": content,
            "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5}})


class FakeOpenAIClient:
    def __init__(self):
        self.requests = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        step = STEPS[min(len(self.requests) - 1, len(STEPS) - 1)]
        if "tool" in step:
            name, args = step["tool"]
            msg = {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"call_{len(self.requests)}", "type": "function",
                 "function": {"name": name, "arguments": json.dumps(args)}}]}
            finish = "tool_calls"
        else:
            msg, finish = {"role": "assistant", "content": step["text"]}, "stop"
        return ChatCompletion.model_validate({
            "id": "c", "object": "chat.completion", "created": 0, "model": kwargs["model"],
            "choices": [{"index": 0, "finish_reason": finish, "message": msg}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})


@pytest.mark.parametrize("provider_name", ["anthropic", "openai_compatible"])
def test_same_agent_runs_unchanged_with_each_adapter(app, world, cycle_setup, provider_name):
    if provider_name == "anthropic":
        client = FakeAnthropicClient()
        provider = AnthropicProvider(client=client)
    else:
        client = FakeOpenAIClient()
        provider = OpenAICompatibleProvider(client=client)
    app.settings.default_llm_provider = provider_name
    app.settings.default_llm_model = "test-model"
    app.overrides["llm"] = {provider_name: provider}
    _, subject, job = cycle_setup
    alice = world.actor(world.alice)

    app.services.conversations.post_user_message(alice, job.job_id, "Our CRM is medium critical, owned by Sales.")
    app.drain()

    # identical domain outcome for every provider
    props = app.services.conversations.proposals(alice, job.job_id, status="proposed")
    assert {(p.path, p.value) for p in props} == {("name", "CRM"), ("criticality", "medium"),
                                                  ("business_owner", "Sales")}
    texts = [m.text for m in app.services.conversations.messages(alice, job.job_id)]
    assert texts[-1] == "Thanks, please confirm the proposed facts."
    from dp_agentic_collector.domain.entities import AgentRun

    with app.db.uow(world.tenant.tenant_id) as uow:
        run = uow.repo(AgentRun).list(job_id=job.job_id)[-1]
    assert run.status == AgentRunStatus.COMPLETED and run.provider == provider_name
    confirm_all(app, alice, job.job_id)
    assert app.services.artifacts.submit(alice, job.job_id).version.header.version == 1

    # request translation per provider
    first, second = client.requests[0], client.requests[1]
    assert "propose_facts" in json.dumps(first["tools"])
    if provider_name == "anthropic":
        assert "temperature" not in first and "tool_choice" not in first
        assert first["system"].startswith("You are the Collector")
        # thinking block replayed unchanged, tool result in a user message
        assert second["messages"][-2]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig-1"}
        assert second["messages"][-1]["content"][0]["type"] == "tool_result"
    else:
        assert first["messages"][0]["role"] == "system"
        assert second["messages"][-1]["role"] == "tool"
        assert second["messages"][-2]["tool_calls"][0]["function"]["name"] == "propose_facts"


def test_fake_llm_is_deterministic():
    from dp_agentic_collector.adapters.llm import FakeLlm
    from dp_agentic_collector.extensions.interfaces import LlmMessage, LlmToolSpec

    tools = [LlmToolSpec("propose_facts", "", {"type": "object"})]
    a = FakeLlm().complete(model="x", system="<schema_fields>name</schema_fields>",
                           messages=[LlmMessage(role="user", content="name: CRM")], tools=tools)
    b = FakeLlm().complete(model="x", system="<schema_fields>name</schema_fields>",
                           messages=[LlmMessage(role="user", content="name: CRM")], tools=tools)
    assert a.tool_calls[0].arguments == b.tool_calls[0].arguments == {
        "facts": [{"path": "name", "value": "CRM", "rationale": "stated by respondent"}]}
