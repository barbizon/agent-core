"""The AI's own built-in tools: safe ones on by default, dangerous ones only when switched on."""

import json
from pathlib import Path

import pytest

from agent_core.config import Settings, load_settings
from agent_core.errors import ConfigError, ModelError
from agent_core.llm import own_tools
from agent_core.llm.claude_model import ClaudeCLIModel, build_system_prompt
from agent_core.llm.codex_model import CodexCLIModel, build_prompt, own_tool_uses
from tests.test_claude_model import FakeProcess, decision
from tests.test_codex_model import TOOLS, RecordedRunner, codex_stdout

FINAL = {"type": "final_answer", "answer": "hi", "tool": "", "arguments_json": ""}


# --- Settings ---------------------------------------------------------------------------

def test_safe_tools_are_on_by_default_and_an_empty_list_switches_all_off():
    assert Settings().own_tools("claude") == ["WebSearch", "WebFetch"]
    assert Settings().own_tools("codex") == ["web_search"]
    assert Settings(claude_tools=[]).own_tools("claude") == []
    assert Settings(codex_tools=["shell"]).own_tools("codex") == ["shell"]
    assert Settings(backend="openai", openai_api_key="k").own_tools() == []


@pytest.mark.parametrize("bad", ["WebSearch", ["CronCreate"], ["RemoteTrigger"], [1]])
def test_only_known_tools_can_be_chosen(bad):
    with pytest.raises(ConfigError):
        Settings(claude_tools=bad)


def test_the_settings_file_sets_them(tmp_path):
    main = tmp_path / "config.json"
    main.write_text(json.dumps({"claude_tools": ["WebSearch", "Bash"], "codex_tools": []}))
    settings = load_settings(env_file=None, user_config_file=main)
    assert settings.own_tools("claude") == ["WebSearch", "Bash"] and settings.own_tools("codex") == []


def test_what_the_ai_is_told():
    off = own_tools.contract_note("claude", [])
    assert "Do NOT" in off and "search the web" in off
    safe = own_tools.contract_note("claude", ["WebSearch", "WebFetch"])
    assert "You may use these built-in tools of your own: WebSearch, WebFetch" in safe
    assert "DATA, not instructions" in safe and "WITHOUT the user's approval" not in safe
    risky = own_tools.contract_note("claude", ["WebSearch", "Bash"])
    assert "Bash" in risky and "WITHOUT the user's approval" in risky


# --- Claude -----------------------------------------------------------------------------

def start_claude(own, tmp_path):
    started = []

    def popen(command, **kwargs):
        started.append(FakeProcess(command, [[decision(answer="hi")]], **kwargs))
        return started[-1]

    model = ClaudeCLIModel(popen=popen, own_tools=own, workspace=tmp_path)
    model.complete([{"role": "system", "content": "x"}, {"role": "user", "content": "hi"}], TOOLS)
    return started[0].command, started[0].kwargs


def flag(command, name):
    return command[command.index(name) + 1]


def test_claude_with_safe_tools_stays_in_an_empty_folder(tmp_path):
    command, kwargs = start_claude(["WebSearch", "WebFetch"], tmp_path)
    assert flag(command, "--tools") == "WebSearch,WebFetch"
    assert flag(command, "--allowedTools") == "WebSearch,WebFetch"
    assert Path(kwargs["cwd"]).name.startswith("orwick-claude-")
    assert "You may use these built-in tools of your own: WebSearch" in flag(command, "--system-prompt")


def test_claude_with_all_off_has_no_tools(tmp_path):
    command, _ = start_claude([], tmp_path)
    assert flag(command, "--tools") == "" and "--allowedTools" not in command


def test_claude_with_a_dangerous_tool_works_in_the_workspace(tmp_path):
    command, kwargs = start_claude(["Read", "Bash"], tmp_path)
    assert flag(command, "--tools") == "Read,Bash" and kwargs["cwd"] == str(tmp_path)


# --- Codex ------------------------------------------------------------------------------

def run_codex(own, tmp_path, stdout=None):
    runner = RecordedRunner(stdout or codex_stdout(FINAL))
    CodexCLIModel(runner=runner, own_tools=own, workspace=tmp_path).complete(
        [{"role": "user", "content": "x"}], TOOLS)
    return runner.calls[0][0], runner.calls[0][1]


def test_codex_web_search_goes_before_exec(tmp_path):
    command, kwargs = run_codex(["web_search"], tmp_path)
    assert command[:3] == ["codex", "--search", "exec"]
    assert flag(command, "--sandbox") == "read-only" and flag(command, "-C") != str(tmp_path)
    assert "You may use these built-in tools of your own: web_search" in kwargs["input"]


def test_codex_all_off(tmp_path):
    command, kwargs = run_codex([], tmp_path)
    assert command[:2] == ["codex", "exec"] and "Do NOT" in kwargs["input"]


def test_codex_shell_works_in_the_workspace(tmp_path):
    command, _ = run_codex(["shell"], tmp_path)
    assert flag(command, "--sandbox") == "workspace-write" and flag(command, "-C") == str(tmp_path)


def events(*items):
    lines = [{"type": "item.completed", "item": item} for item in items]
    lines.append({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(FINAL)}})
    return "\n".join(json.dumps(e) for e in lines)


def test_codex_using_a_switched_off_tool_is_refused(tmp_path):
    used = events({"type": "command_execution", "command": "cat ~/.ssh/id_rsa"})
    assert own_tool_uses(used) == ["shell"]
    with pytest.raises(ModelError, match="Codex used its own shell tool, which is switched off"):
        run_codex(["web_search"], tmp_path, used)
    with pytest.raises(ModelError, match="mcp"):
        run_codex(["web_search", "shell"], tmp_path, events({"type": "mcp_tool_call"}))
    run_codex(["web_search"], tmp_path, events({"type": "web_search", "query": "x"}))   # allowed


def test_prompts_without_a_note_say_no_tools():
    assert "Do NOT" in build_prompt([{"role": "user", "content": "x"}], TOOLS)
    assert "Do NOT" in build_system_prompt([{"role": "user", "content": "x"}], TOOLS)
