"""Changes that keep the conversation small: several calls per reply, raw text in
results, big files read in parts, compact MCP JSON, and a feature change that
doesn't restart the claude process."""

import json

import pytest

from agent_core import cli
from agent_core.errors import ModelError
from agent_core.llm import MAX_CALLS_PER_REPLY, ModelResponse, ToolCall
from agent_core.llm.claude_model import build_system_prompt
from agent_core.llm.codex_model import CLAUDE_DECISION_SCHEMA, DECISION_SCHEMA, parse_decision
from agent_core.mcp import compact_json_text
from agent_core.results import ToolResult
from agent_core.tools import files


# --- several calls per reply ------------------------------------------------------

def test_claude_calls_carry_real_json_arguments():
    response = parse_decision(json.dumps({"type": "tool_calls", "answer": "", "calls": [
        {"tool": "read_file", "arguments": {"path": "a.go"}},
        {"tool": "write_session_doc", "arguments": {"name": "flows", "content": 'A | "B"\n1 | 2'}},
    ]}), source="claude")
    assert [c.name for c in response.tool_calls] == ["read_file", "write_session_doc"]
    assert json.loads(response.tool_calls[1].arguments_json)["content"] == 'A | "B"\n1 | 2'
    assert len({c.id for c in response.tool_calls}) == 2 and response.text is None


def test_codex_calls_carry_a_json_string_and_the_old_single_form_still_works():
    many = parse_decision(json.dumps({"type": "tool_calls", "answer": "", "calls": [
        {"tool": "grep", "arguments_json": '{"pattern": "x"}'},
        {"tool": "read_file", "arguments_json": '{"path": "a"}'}]}))
    assert [c.name for c in many.tool_calls] == ["grep", "read_file"]
    old = parse_decision(json.dumps({"type": "tool_call", "answer": "", "tool": "echo",
                                     "arguments_json": '{"text": "x"}'}))
    assert old.tool_calls[0].name == "echo"


@pytest.mark.parametrize("decision", [
    {"type": "tool_calls", "answer": "", "calls": []},
    {"type": "tool_calls", "answer": "", "calls": [{"tool": ""}]},
    {"type": "tool_calls", "answer": ""},
])
def test_bad_call_lists_are_model_errors(decision):
    with pytest.raises(ModelError):
        parse_decision(json.dumps(decision))


def test_schemas_and_prompt_ask_for_several_calls():
    assert DECISION_SCHEMA["properties"]["calls"]["items"]["required"] == ["tool", "arguments_json"]
    assert CLAUDE_DECISION_SCHEMA["properties"]["calls"]["items"]["properties"]["arguments"] == {"type": "object"}
    system = build_system_prompt([{"role": "system", "content": "S"}], [])
    assert '"arguments": {' in system and "SAME reply" in system and "arguments_json" not in system


def test_agent_runs_every_call_of_a_reply_and_refuses_the_extra_ones(make_gateway, audit):
    from tests.conftest import ScriptedModel
    from tests.test_agent import make_agent
    calls = [ToolCall(id=f"c{i}", name="echo", arguments_json=json.dumps({"text": str(i)}))
             for i in range(MAX_CALLS_PER_REPLY + 1)]
    model = ScriptedModel([ModelResponse(None, calls), ModelResponse("done")])
    agent, _ = make_agent(model, make_gateway, audit, max_steps=0)
    assert agent.send("go") == "done"
    results = [m for m in agent.messages if m["role"] == "tool"]
    assert len(results) == MAX_CALLS_PER_REPLY + 1          # every call id gets a result
    assert all('"ok": true' in r["content"] for r in results[:-1])
    assert "too_many_calls" in results[-1]["content"]


# --- results as raw text -----------------------------------------------------------

GO = 'package a\n\nfunc X() string {\n\treturn "q"\n}\n' * 20


def test_long_text_follows_the_json_unescaped():
    content = ToolResult.success({"path": "a.go", "version": "v1", "content": GO}).to_model_content(0)
    header, rest = content.split("\n", 1)
    assert json.loads(header) == {"ok": True, "data": {"path": "a.go", "version": "v1",
                                                       "content": "<below: content>"}}
    assert rest == f"--- content ---\n{GO}\n--- end of content ---"
    assert "\\t" not in content and '\\"' not in content


def test_short_text_and_errors_stay_plain_json():
    assert json.loads(ToolResult.success({"path": "a", "content": "hi"}).to_model_content(0)) == \
        {"ok": True, "data": {"path": "a", "content": "hi"}}
    assert json.loads(ToolResult.failure("x", "y" * 500).to_model_content(0))["ok"] is False


def test_history_screen_still_reads_a_result_with_raw_text():
    from agent_core import screens
    line = screens._tool_result_line(ToolResult.success({"content": GO}).to_model_content(0))
    assert line.startswith("  ← ok:")


def test_whole_read_of_a_big_file_returns_whole_lines_and_a_note(tmp_path, monkeypatch):
    monkeypatch.setenv(files.WORKSPACE_ENV, str(tmp_path))
    lines = [f"line {i:05d} " + "x" * 90 + "\n" for i in range(1, 1001)]   # 100 KB
    (tmp_path / "big.go").write_text("".join(lines))
    result = files.read_file("big.go")
    assert len(result["content"]) <= files.READ_WHOLE_MAX_CHARS
    assert result["content"].endswith("\n") and result["total_lines"] == 1000
    assert result["end_line"] == result["content"].count("\n") and "start_line" in result["note"]
    assert files.read_file("big.go", start_line=900, line_count=2)["content"] == "".join(lines[899:901])
    (tmp_path / "small.go").write_text("ok\n")
    small = files.read_file("small.go")
    assert small["content"] == "ok\n" and "note" not in small and "total_lines" not in small


# --- compact MCP JSON ---------------------------------------------------------------

def test_mcp_json_loses_indentation_and_empty_fields_but_no_values():
    pretty = json.dumps({"key": "PROJ-1", "fields": {"summary": "S", "labels": [], "due": None,
                                                   "desc": "", "n": 0, "ok": False, "sub": {"x": None}},
                         "list": [{"a": None, "b": 1}]}, indent=2)
    assert json.loads(compact_json_text(pretty)) == {
        "key": "PROJ-1", "fields": {"summary": "S", "n": 0, "ok": False}, "list": [{"b": 1}]}
    assert "\n" not in compact_json_text(pretty) and ": " not in compact_json_text(pretty)
    assert compact_json_text("not json\n  kept as is") == "not json\n  kept as is"


# --- a feature change doesn't change the system prompt mid-conversation ---------------

class Bare:
    skills_on = []

    def __init__(self, messages):
        self.messages = messages


def test_feature_before_the_first_message_goes_into_the_system_prompt():
    agent = Bare([{"role": "system", "content": "old"}])
    assert cli.link_feature(agent, "PROJ-7") is None
    assert "PROJ-7" in agent.messages[0]["content"] and agent.prompt_feature == "PROJ-7"


def test_feature_mid_conversation_becomes_a_note_and_keeps_the_prompt():
    agent = Bare([{"role": "system", "content": "old"}, {"role": "user", "content": "hi"}])
    note = cli.link_feature(agent, "PROJ-7")
    assert "PROJ-7" in note and agent.messages[0]["content"] == "old" and agent.feature == "PROJ-7"
    assert "no longer linked" in cli.link_feature(agent, None)
