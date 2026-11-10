import io

from agent_core.agent import Agent
from agent_core.debug import Tracer, pretty_json
from agent_core.llm import ModelResponse, ToolCall
from agent_core.llm.codex_model import CodexCLIModel
from agent_core.permissions import RiskLevel
from agent_core.tools.registry import ToolDefinition
from tests import tool_fixtures as fx
from tests.conftest import ScriptedModel
from tests.test_codex_model import RecordedRunner, codex_stdout


def tracer(width=40):
    stream = io.StringIO()
    return Tracer(enabled=True, stream=stream, width=width, color=False), stream


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
    t.frame("A", "b")
    t.event("tool.requested", {"tool": "x"})
    assert out.getvalue() == ""


def test_events_redact_secrets_and_errors_use_red():
    t, out = tracer()
    t.event("tool.requested", {"arguments": {"api_key": "sk-123"}})
    assert "sk-123" not in out.getvalue() and "REDACTED" in out.getvalue()

    colored = io.StringIO()
    Tracer(enabled=True, stream=colored, width=40, color=True).event("tool.approval_denied", {})
    assert "\033[31m" in colored.getvalue()


def test_pretty_json():
    assert pretty_json('{"a":1}') == '{\n  "a": 1\n}'
    assert pretty_json("not json") == "not json"


def test_agent_traces_the_full_flow(make_gateway, audit):
    t, out = tracer(width=80)
    echo = ToolDefinition(name="echo", description="Echo.", function=fx.echo,
                          risk_level=RiskLevel.READ, parameters={"text": {"type": "string"}})
    gateway = make_gateway([echo])
    gateway.tracer = t
    model = ScriptedModel([ModelResponse(None, [ToolCall("c1", "echo", '{"text": "hi"}')]),
                           ModelResponse("done")])
    Agent(model=model, registry=gateway.registry, gateway=gateway,
          audit_logger=audit, tracer=t).send("echo hi")

    text = out.getvalue()
    for title in ("agent.started", "model.requested", "tool.requested",
                  "tool.permission_checked", "tool.execution_succeeded",
                  "TOOL RESULT → model · echo", "agent.finished"):
        assert f"─ {title} " in text, title


def test_codex_traces_exact_prompt_and_raw_reply():
    t, out = tracer(width=100)
    runner = RecordedRunner(codex_stdout(
        {"type": "final_answer", "answer": "Hi!", "tool": "", "arguments_json": ""}))
    CodexCLIModel(runner=runner, tracer=t).complete([{"role": "user", "content": "hello there"}], [])
    text = out.getvalue()
    assert "MODEL INPUT · codex exec" in text and "$ codex exec --json" in text
    assert "hello there" in text
    assert "MODEL OUTPUT · codex" in text and '"answer": "Hi!"' in text
