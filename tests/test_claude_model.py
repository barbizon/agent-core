"""Claude backend: one long-running clean `claude -p` process, tested with a fake process."""

import json
import queue

import pytest

from agent_core.errors import ModelError
from agent_core.llm.claude_model import ClaudeCLIModel

TOOLS = [{"type": "function", "function": {
    "name": "read_file", "description": "Read a file.",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]


class FakeStdin:
    def __init__(self, process):
        self.process = process

    def write(self, text):
        self.process.sent.append(json.loads(text)["message"]["content"])
        reply = self.process.replies.pop(0)
        for event in reply:
            self.process.out.put(json.dumps(event) + "\n")

    def flush(self):
        pass

    def close(self):
        self.process.out.put(None)
        self.process.closed = True


class FakeProcess:
    def __init__(self, command, replies, **kwargs):
        self.command, self.kwargs = command, kwargs
        self.replies, self.sent = replies, []
        self.out: queue.Queue = queue.Queue()
        self.stdin = FakeStdin(self)
        self.stdout = iter(self.out.get, None)
        self.stderr = iter([])
        self.closed = False
        self.pid = 999999

    def poll(self):
        return 0 if self.closed else None

    def wait(self, timeout=None):
        return 0


def decision(**fields):
    base = {"type": "final_answer", "answer": "", "tool": "", "arguments_json": ""}
    return {"type": "result", "subtype": "success", "is_error": False, "result": "",
            "structured_output": {**base, **fields}}


def make_model(*replies):
    started = []

    def popen(command, **kwargs):
        process = FakeProcess(command, list(replies), **kwargs)
        started.append(process)
        return process

    return ClaudeCLIModel(popen=popen, timeout_seconds=2), started


SYSTEM = {"role": "system", "content": "You are Orwick."}


def test_the_process_is_clean_and_uses_the_subscription(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-paid")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    model, started = make_model([decision(answer="hi")])
    model.complete([SYSTEM, {"role": "user", "content": "hello"}], TOOLS)
    command, kwargs = started[0].command, started[0].kwargs
    for flag in ("--safe-mode", "--strict-mcp-config", "--no-session-persistence",
                 "--disable-slash-commands"):
        assert flag in command
    assert command[command.index("--tools") + 1] == ""          # no built-in tools
    assert command[command.index("--setting-sources") + 1] == ""  # no settings files
    assert "--json-schema" in command
    system = command[command.index("--system-prompt") + 1]
    assert "You are Orwick." in system and "read_file" in system
    assert "ANTHROPIC_API_KEY" not in kwargs["env"] and "ANTHROPIC_AUTH_TOKEN" not in kwargs["env"]
    assert kwargs["cwd"].split("/")[-1].startswith("orwick-claude-")


def test_one_process_and_only_new_input_per_step():
    model, started = make_model(
        [decision(type="tool_call", tool="read_file", arguments_json='{"path": "a.txt"}')],
        [decision(answer="It says hi.")],
    )
    history = [SYSTEM, {"role": "user", "content": "what is in a.txt?"}]
    first = model.complete(history, TOOLS)
    assert first.tool_calls[0].name == "read_file"
    history += [first.to_message(),
                {"role": "tool", "tool_call_id": first.tool_calls[0].id, "content": '{"content": "hi"}'}]
    second = model.complete(history, TOOLS)
    assert second.text == "It says hi."
    assert len(started) == 1  # the same process for both steps
    assert started[0].sent[0].startswith("what is in a.txt?")
    assert "tool result" in started[0].sent[1] and "what is in a.txt?" not in started[0].sent[1]


def test_reset_restarts_and_a_new_process_gets_the_whole_conversation():
    model, started = make_model([decision(answer="one")])
    model.complete([SYSTEM, {"role": "user", "content": "first"}], TOOLS)
    started_again = []

    def popen(command, **kwargs):
        process = FakeProcess(command, [[decision(answer="two")]], **kwargs)
        started_again.append(process)
        return process

    model._popen = popen
    # /reset: the history no longer starts with what the process saw.
    model.complete([SYSTEM, {"role": "user", "content": "new chat"}], TOOLS)
    assert started[0].closed and len(started_again) == 1
    # Switching to claude mid-chat: the whole earlier conversation is replayed once.
    model.close()
    model._popen = lambda command, **kw: started_again.append(
        FakeProcess(command, [[decision(answer="three")]], **kw)) or started_again[-1]
    history = [SYSTEM, {"role": "user", "content": "earlier question"},
               {"role": "assistant", "content": "earlier answer"},
               {"role": "user", "content": "follow-up"}]
    assert model.complete(history, TOOLS).text == "three"
    replay = started_again[-1].sent[0]
    assert "earlier question" in replay and "earlier answer" in replay and "follow-up" in replay


def test_an_error_result_is_a_model_error_and_closes_the_process():
    model, started = make_model([{"type": "result", "subtype": "error_during_execution",
                                  "is_error": True, "result": "usage limit reached"}])
    with pytest.raises(ModelError, match="usage limit reached"):
        model.complete([SYSTEM, {"role": "user", "content": "x"}], TOOLS)
    assert started[0].closed and model._process is None


def test_no_reply_in_time_is_a_timeout():
    model, started = make_model()
    model.timeout_seconds = 0.2
    original = FakeStdin.write
    FakeStdin.write = lambda self, text: None
    try:
        with pytest.raises(ModelError, match="timed out"):
            model.complete([SYSTEM, {"role": "user", "content": "x"}], TOOLS)
    finally:
        FakeStdin.write = original


def test_missing_claude_cli_is_explained():
    def popen(command, **kwargs):
        raise FileNotFoundError(command[0])

    model = ClaudeCLIModel(popen=popen)
    with pytest.raises(ModelError, match="Install Claude Code"):
        model.complete([SYSTEM, {"role": "user", "content": "x"}], TOOLS)
