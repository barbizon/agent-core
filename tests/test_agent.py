import json

import pytest

from agent_core.agent import Agent
from agent_core.errors import StepLimitExceeded
from agent_core.llm import ModelResponse, ToolCall
from agent_core.permissions import RiskLevel
from agent_core.tools.registry import ToolDefinition
from tests import tool_fixtures as fx
from tests.conftest import ScriptedModel

ECHO = ToolDefinition(name="echo", description="Echo.", function=fx.echo,
                      risk_level=RiskLevel.READ, parameters={"text": {"type": "string"}})


def make_agent(model, make_gateway, audit, tools=(ECHO,), **kw):
    gateway = make_gateway(list(tools))
    events = []
    agent = Agent(model=model, registry=gateway.registry, gateway=gateway,
                  audit_logger=audit, on_event=events.append, **kw)
    return agent, events


def call(id_, name="echo", args='{"text": "hi"}'):
    return ToolCall(id=id_, name=name, arguments_json=args)


def test_plain_chat_sends_no_tools_and_keeps_history(make_gateway, audit):
    model = ScriptedModel([ModelResponse("Hello!"), ModelResponse("Again!")])
    agent, _ = make_agent(model, make_gateway, audit, tools=())
    assert agent.send("hi") == "Hello!"
    assert agent.send("again") == "Again!"
    messages, tools = model.calls[1]
    assert tools == []
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]


def test_tool_call_round_trip(make_gateway, audit):
    model = ScriptedModel([ModelResponse(None, [call("c1")]), ModelResponse("done")])
    agent, events = make_agent(model, make_gateway, audit)
    assert agent.send("echo hi") == "done"

    messages, tools = model.calls[1]
    assert tools[0]["function"]["name"] == "echo"
    assert messages[-2]["tool_calls"][0]["id"] == "c1"
    tool_msg = messages[-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1"
    assert json.loads(tool_msg["content"]) == {"ok": True, "data": {"echo": "hi"}}
    assert [e.kind for e in events] == ["tool_call", "tool_result"]


def test_malformed_arguments_become_structured_error(make_gateway, audit):
    model = ScriptedModel([ModelResponse(None, [call("c1", args="{not json")]), ModelResponse("ok")])
    agent, _ = make_agent(model, make_gateway, audit)
    agent.send("x")
    content = json.loads(model.calls[1][0][-1]["content"])
    assert content["error"]["type"] == "invalid_arguments"


def test_step_limit(make_gateway, audit):
    model = ScriptedModel([ModelResponse(None, [call(f"c{i}")]) for i in range(3)])
    agent, _ = make_agent(model, make_gateway, audit, max_steps=2)
    with pytest.raises(StepLimitExceeded):
        agent.send("loop")
    assert "agent.failed" in audit.types()


def test_interrupt_leaves_history_valid(make_gateway, audit):
    class Interrupting:
        def execute(self, run_id, name, args):
            raise KeyboardInterrupt

    model = ScriptedModel([ModelResponse(None, [call("c1"), call("c2")])])
    agent, _ = make_agent(model, make_gateway, audit)
    agent.gateway = Interrupting()
    with pytest.raises(KeyboardInterrupt):
        agent.send("x")
    tool_ids = [m["tool_call_id"] for m in agent.messages if m["role"] == "tool"]
    assert tool_ids == ["c1", "c2"]


def test_reset_clears_conversation(make_gateway, audit):
    agent, _ = make_agent(ScriptedModel([ModelResponse("hi")]), make_gateway, audit)
    agent.send("x")
    agent.reset()
    assert [m["role"] for m in agent.messages] == ["system"]
