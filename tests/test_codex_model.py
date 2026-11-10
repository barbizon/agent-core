"""CodexCLIModel tests. A recorded-output runner replaces subprocess.run, so `codex` is never called."""

import json
import subprocess

import pytest

from agent_core.errors import ModelError
from agent_core.llm.codex_model import CodexCLIModel, build_prompt, parse_decision

TOOLS = [{"type": "function", "function": {
    "name": "echo", "description": "Echo text.",
    "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                   "required": ["text"], "additionalProperties": False}}}]


def codex_stdout(decision: dict) -> str:
    events = [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(decision)}},
        {"type": "turn.completed"},
    ]
    return "\n".join(json.dumps(e) for e in events)


class RecordedRunner:
    def __init__(self, stdout="", returncode=0, stderr="", raises=None):
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.raises = raises
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.raises:
            raise self.raises
        return self.result


def test_final_answer_and_isolation_flags():
    runner = RecordedRunner(codex_stdout(
        {"type": "final_answer", "answer": "Hi!", "tool": "", "arguments_json": ""}))
    response = CodexCLIModel(model="m", runner=runner).complete(
        [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hello"}], [])
    assert response.text == "Hi!" and response.tool_calls == []

    command, kwargs = runner.calls[0]
    for flag in ("--sandbox", "read-only", "--ephemeral", "--ignore-user-config",
                 "--ignore-rules", "--output-schema", "--json"):
        assert flag in command
    assert command[-1] == "-" and command[command.index("--model") + 1] == "m"
    assert "hello" in kwargs["input"] and "SYS" in kwargs["input"]


def test_tool_call_decision():
    runner = RecordedRunner(codex_stdout(
        {"type": "tool_call", "answer": "", "tool": "echo", "arguments_json": '{"text": "x"}'}))
    response = CodexCLIModel(runner=runner).complete([{"role": "user", "content": "x"}], TOOLS)
    call = response.tool_calls[0]
    assert (call.name, json.loads(call.arguments_json)) == ("echo", {"text": "x"})
    assert call.id.startswith("call_")
    assert "--model" not in runner.calls[0][0]


@pytest.mark.parametrize("runner", [
    RecordedRunner(returncode=1, stderr="not logged in"),
    RecordedRunner(stdout='{"type":"turn.completed"}'),
    RecordedRunner(raises=FileNotFoundError()),
    RecordedRunner(raises=subprocess.TimeoutExpired("codex", 1)),
])
def test_failures_become_model_errors(runner):
    with pytest.raises(ModelError):
        CodexCLIModel(runner=runner).complete([{"role": "user", "content": "x"}], [])


@pytest.mark.parametrize("text", [
    "not json", "[]", '{"type": "other"}', '{"type": "tool_call", "tool": ""}'])
def test_invalid_decisions(text):
    with pytest.raises(ModelError):
        parse_decision(text)


def test_prompt_contains_tools_transcript_and_marks_results_untrusted():
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "echo hi"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": '{"text":"hi"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'},
    ]
    prompt = build_prompt(messages, TOOLS)
    assert "- echo: Echo text." in prompt
    assert "[assistant → tool call c1] echo" in prompt
    assert "untrusted data" in prompt and '<tool_result>\n{"ok": true}' in prompt
    assert "(none: answer directly" in build_prompt(messages[:2], [])
