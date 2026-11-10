import io

from agent_core.agent import Agent
from agent_core.debug import Tracer, describe_event, pretty_json, summarize_messages
from agent_core.llm import ModelResponse, ToolCall
from agent_core.llm.codex_model import CodexCLIModel
from agent_core.permissions import PermissionPolicy, RiskLevel
from agent_core.tools.registry import ToolDefinition
from tests import tool_fixtures as fx
from tests.conftest import RecordingApprover, ScriptedModel
from tests.test_codex_model import RecordedRunner, codex_stdout


def tracer(width=100):
    stream = io.StringIO()
    return Tracer(enabled=True, stream=stream, width=width, color=False), stream


def in_order(text: str, *parts: str) -> bool:
    """True if every part appears in text, in this order."""
    position = 0
    for part in parts:
        position = text.find(part, position)
        if position < 0:
            print("missing or out of order:", part)
            return False
        position += len(part)
    return True


def test_frame_is_a_closed_box_and_wraps_long_lines():
    t, out = tracer(width=40)
    t.frame("TITLE", "short\n" + "x" * 80)
    lines = out.getvalue().splitlines()
    assert lines[0].startswith("┌─ TITLE ") and lines[0].endswith("┐")
    assert lines[-1].startswith("└") and lines[-1].endswith("┘")
    assert all(len(line) == 40 for line in lines)  # every line the same width
    assert sum("x" in line for line in lines) == 3  # 80 chars wrapped at 36 per line


def test_minimum_width_is_40():
    assert Tracer(enabled=True, width=10).width == 40


def test_disabled_tracer_prints_nothing():
    out = io.StringIO()
    t = Tracer(enabled=False, stream=out)
    t.begin_request("hi")
    t.point("A", "b")
    with t.collect_checks("checks"):
        t.event("tool.requested", {"tool": "x"})
    assert out.getvalue() == ""


def test_points_are_numbered_and_restart_per_request():
    t, out = tracer()
    t.begin_request("first")
    t.point("a", "1")
    t.point("b", "2")
    t.begin_request("second")
    t.point("c", "3")
    text = out.getvalue()
    assert in_order(text, 'REQUEST #1 · "first"', "① a", "② b", 'REQUEST #2 · "second"', "① c")


def test_checks_are_collected_into_one_point_with_secrets_hidden():
    t, out = tracer()
    with t.collect_checks("Gateway · safety checks"):
        t.event("tool.requested", {"tool": "x", "arguments": {"api_key": "sk-123"}})
        t.event("tool.permission_checked", {"risk_level": "read", "permitted": True})
    t.event("tool.requested", {"tool": "ignored"})  # outside collection: not shown
    text = out.getvalue()
    assert text.count("┌─") == 1
    assert "① Gateway · safety checks" in text
    assert "sk-123" not in text and "REDACTED" in text
    assert "✓ permission: risk 'read' is allowed" in text
    assert "ignored" not in text


def test_failed_check_marks_the_point_red():
    colored = io.StringIO()
    t = Tracer(enabled=True, stream=colored, width=60, color=True)
    with t.collect_checks("checks"):
        t.event("tool.approval_denied", {})
    assert "\033[31m" in colored.getvalue()
    assert describe_event("tool.approval_denied", {}) == ("✗ approval: denied", False)
    assert describe_event("something.new", {"a": 1})[0] == 'something.new: {"a": 1}'.join(["• ", ""])


def test_summarize_messages():
    text = summarize_messages([
        {"role": "system", "content": "be nice\nalways"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "function": {"name": "echo", "arguments": '{"text":"hi"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'},
    ])
    assert text.splitlines() == [
        "[system] be nice always",
        '[assistant → tool call c1] echo {"text":"hi"}',
        '[tool result c1] {"ok": true}',
    ]


def test_pretty_json():
    assert pretty_json('{"a":1}') == '{\n  "a": 1\n}'
    assert pretty_json("not json") == "not json"


def test_agent_traces_the_whole_pipeline_including_a_write_tool(make_gateway, audit):
    t, out = tracer()
    write = ToolDefinition(name="save", description="Save.", function=fx.echo,
                           risk_level=RiskLevel.WRITE, parameters={"text": {"type": "string"}})
    gateway = make_gateway([write], policy=PermissionPolicy(allow_write=True),
                           approver=RecordingApprover(True))
    gateway.tracer = t
    model = ScriptedModel([ModelResponse(None, [ToolCall("c1", "save", '{"text": "hi"}')]),
                           ModelResponse("saved")])
    Agent(model=model, registry=gateway.registry, gateway=gateway,
          audit_logger=audit, tracer=t).send("save hi")

    assert in_order(
        out.getvalue(),
        'REQUEST #1 · "save hi"',
        "① CLI → Agent · user input", "save hi",
        "② Agent · history sent to model (2 messages)",
        "③ Agent → ScriptedModel · step 1", "tools offered: save",
        "④ Adapter → Agent · decision", 'tool_call: save {"text": "hi"}',
        "⑤ Agent → Gateway · tool call",
        "⑥ Gateway · safety checks for 'save'",
        "✓ permission: risk 'write' is allowed",
        "? approval: asking the human", "✓ approval: granted",
        "✓ one-time token issued", "▶ executing (mode: thread)", "✓ done",
        "⑦ Gateway → Agent · tool result", '"echo": "hi"',
        "⑧ Agent · history sent to model (4 messages)",
        "⑨ Agent → ScriptedModel · step 2",
        "⑩ Adapter → Agent · decision", "final_answer: saved",
        "⑪ Agent → CLI · final answer", "saved", "2 step(s)",
    )


def test_codex_adapter_traces_exact_request_and_raw_output():
    t, out = tracer()
    runner = RecordedRunner(codex_stdout(
        {"type": "final_answer", "answer": "Hi!", "tool": "", "arguments_json": ""}))
    CodexCLIModel(runner=runner, tracer=t).complete([{"role": "user", "content": "hello there"}], [])
    assert in_order(
        out.getvalue(),
        "① Codex adapter → codex exec · command + prompt (stdin)",
        "$ codex exec --json", "hello there",
        "② codex exec → Codex adapter · raw output", "exit 0",
        "thread.started", "item.completed (agent_message):", '"answer": "Hi!"',
        "turn.completed",
    )


def test_errors_are_traced(make_gateway, audit):
    t, out = tracer()

    class Broken:
        def complete(self, messages, tools):
            raise RuntimeError("boom")

    gateway = make_gateway([])
    agent = Agent(model=Broken(), registry=gateway.registry, gateway=gateway,
                  audit_logger=audit, tracer=t)
    try:
        agent.send("x")
    except RuntimeError:
        pass
    assert "Agent → CLI · error (RuntimeError)" in out.getvalue()
