"""Drive the real chat loop with typed commands (no AI calls: only slash commands are sent)."""

import json
from pathlib import Path
import os

import pytest

from agent_core import cli
from agent_core.tools import files


class EchoModel:
    """A fake AI for chat tests: answers every message at once, no network."""

    def complete(self, messages, tools):
        from agent_core.llm import ModelResponse
        if messages[0]["content"].startswith("You compress a conversation"):
            return ModelResponse(text="Short summary of the earlier talk.")
        last = [m for m in messages if m["role"] == "user"][-1]["content"]
        return ModelResponse(text=f"echo: {last}")

    def close(self):
        pass


@pytest.fixture
def run_cli(tmp_path, monkeypatch, capsys):
    """Run cli.main with the given typed lines; return everything it printed."""
    monkeypatch.chdir(tmp_path)  # logs/ and the config file go to a temp folder
    monkeypatch.delenv(files.PROTECTED_PATHS_ENV, raising=False)
    (tmp_path / ".orwick").mkdir()
    monkeypatch.delenv(files.WORKSPACE_ENV, raising=False)
    monkeypatch.delenv("AGENT_DEBUG", raising=False)
    monkeypatch.setattr(cli, "build_model", lambda settings, tracer, usage=None: EchoModel())

    def run(lines, *flags, config=None, edits=None, main_edits=None):
        if config is not None:
            (tmp_path / ".orwick" / "config.json").write_text(json.dumps(config))
        typed = iter(lines)
        edits = edits or {}

        def fake_input(prompt=""):
            line = next(typed)  # StopIteration never happens: tests end with /exit
            if line in edits:  # simulate editing the config file before this line
                (tmp_path / ".orwick" / "config.json").write_text(json.dumps(edits[line]))
            if line in (main_edits or {}):  # the same for the main settings
                cli.USER_CONFIG_FILE.write_text(json.dumps(main_edits[line]))
            return line

        monkeypatch.setattr("builtins.input", fake_input)
        code = cli.main(["--env-file", str(tmp_path / "none.env"), *flags])
        assert code == 0
        return capsys.readouterr().out

    return run


def debug_line(out: str) -> list[str]:
    """The /config rows for debug, as "value [source]"."""
    rows = []
    for line in out.splitlines():
        parts = line.removeprefix("│").split()
        if parts[:1] == ["debug"] and "◂" in parts:
            arrow = parts.index("◂")
            rows.append(f"{' '.join(parts[1:arrow])}   [{' '.join(parts[arrow + 1:])}]")
    return rows


def test_debug_command_turns_off_debug_started_with_flag(run_cli):
    out = run_cli(["/debug", "/debug off", "/config", "/debug", "/exit"], "-d")
    assert "Debug is on." in out
    assert "Debug is now off. The conversation is kept." in out
    assert debug_line(out) == ["off   [/debug command]"]
    assert "Debug is off." in out


def test_debug_command_turns_on_and_shows_the_intro(run_cli):
    out = run_cli(["/debug on", "/debug on", "/exit"])
    assert "Debug is now on." in out and cli.DEBUG_INTRO.splitlines()[0] in out
    assert "Debug is already on." in out


def test_debug_command_beats_later_config_file_changes(run_cli):
    out = run_cli(
        ["/debug off", "/config", "/exit"],
        config={"debug": False},
        edits={"/config": {"debug": True}},  # file switched on after /debug off
    )
    assert "[config] Reloaded: debug: False → True" in out
    assert "debug is set by /debug in this session" in out
    assert debug_line(out) == ["off   [/debug command]"]


def test_without_debug_command_the_file_controls_debug(run_cli):
    out = run_cli(["/config", "/exit"], config={"debug": False},
                  edits={"/config": {"debug": True}})
    assert debug_line(out) == ["on   [.orwick/config.json]"]


def test_debug_command_usage(run_cli):
    out = run_cli(["/debug maybe", "/debug on off", "/exit"])
    assert out.count("Usage: /debug, /debug on, /debug off") == 2


def test_help_lists_debug(run_cli):
    assert "/debug" in run_cli(["/help", "/exit"])


def test_backend_command_shows_and_switches_keeping_the_chat(run_cli):
    out = run_cli(["/backend", "/backend claude", "/config", "/backend gemini", "/backend openai", "/exit"])
    assert "AI backend" in out and "Claude subscription" in out
    assert "Now using claude · claude-opus-5-5 (default). The conversation is kept." in out
    assert any("claude" in line and "◂ /backend command" in line for line in out.splitlines())
    assert "Usage: /backend, /backend codex|claude|openai [model]" in out
    assert "OPENAI_API_KEY is not set" in out and "Still using claude" in out


def test_backend_switch_with_a_model(run_cli):
    out = run_cli(["/backend claude sonnet", "/exit"])
    assert "Now using claude · sonnet." in out


def test_rebuild_keeps_the_model_unless_backend_settings_change(tmp_path, monkeypatch):
    from agent_core.config import Settings
    from agent_core.debug import Tracer
    from agent_core.permissions import PermissionPolicy

    monkeypatch.chdir(tmp_path)
    closed = []

    class FakeModel:
        tracer = None

        def close(self):
            closed.append(self)

    settings = Settings(backend="claude")
    policy = PermissionPolicy()
    agent = cli.build_agent(settings, policy, Tracer(), model=FakeModel())
    same, tracer = cli.rebuild_agent(agent, settings, policy, debug=True)
    assert same.model is agent.model and same.model.tracer is tracer and not closed
    other, _ = cli.rebuild_agent(same, Settings(backend="codex"), policy, debug=True)
    assert other.model is not agent.model and closed == [agent.model]


def test_status_command_works_before_any_message(run_cli):
    out = run_cli(["/status", "/exit"])
    assert "📊 Session" in out and "No requests yet." in out



def source_row(out: str, name: str) -> str:
    """The /config row for one setting, "name value ◂ source" (a long source wraps to the next line)."""
    lines = [line.removeprefix("│") for line in out.splitlines()]
    for index, line in enumerate(lines):
        if line.split()[:1] == [name]:
            return line if "◂" in line else f"{line} {lines[index + 1].strip()}"
    raise AssertionError(f"no /config row for {name}")


def test_main_settings_are_used_and_shown_as_the_source(run_cli, main_config):
    main_config.parent.mkdir(parents=True)
    main_config.write_text(json.dumps({"max_steps": 4}))
    out = run_cli(["/config", "/exit"])
    assert "Main settings: " in out and "(loaded)" in out
    row = source_row(out, "max_steps")
    assert row_has(row, "4", str(main_config))


def test_the_folder_file_beats_the_main_settings(run_cli, main_config):
    main_config.parent.mkdir(parents=True)
    main_config.write_text(json.dumps({"max_steps": 4}))
    out = run_cli(["/config", "/exit"], config={"max_steps": 6})
    assert row_has(source_row(out, "max_steps"), "6", ".orwick/config.json")


def row_has(row: str, value: str, source: str) -> bool:
    return f" {value} " in f"{row} " and row.split("◂")[1].strip() == source


def test_editing_the_main_settings_reloads_them(run_cli, main_config):
    main_config.parent.mkdir(parents=True)
    main_config.write_text(json.dumps({"max_steps": 4}))
    out = run_cli(["/help", "/config", "/exit"], main_edits={"/config": {"max_steps": 7}})
    assert "[config] Reloaded: max_steps: 4 → 7" in out
    assert row_has(source_row(out, "max_steps"), "7", str(main_config))


def test_a_workspace_in_the_main_settings_stops_orwick(tmp_path, monkeypatch, capsys, main_config):
    from agent_core import cli as cli_module
    monkeypatch.chdir(tmp_path)
    main_config.parent.mkdir(parents=True)
    main_config.write_text(json.dumps({"workspace": "/"}))
    assert cli_module.main(["--env-file", "none.env"]) == 2
    assert "workspace can't be set in the main settings" in capsys.readouterr().err



def session_folders(tmp_path):
    root = tmp_path / ".orwick" / "sessions"
    return sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_birthtime) \
        if root.exists() else []


def read(path):
    return json.loads(path.read_text())


def test_a_session_is_saved_with_its_conversation(run_cli, tmp_path):
    out = run_cli(["hello there", "/exit"])
    assert "echo: hello there" in out
    [folder] = session_folders(tmp_path)
    assert f"Session: {Path('.orwick/sessions') / folder.name}" in out
    messages = read(folder / "conversation.json")["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]  # no system prompt saved
    meta = read(folder / "meta.json")
    assert (meta["title"], meta["messages"], meta["id"]) == ("hello there", 1, folder.name)
    status = read(folder / "status.json")
    assert status["session_id"] == folder.name and status["session"]["messages"] == 1
    assert "hello there" not in (folder / "status.json").read_text()  # status stays text-free
    assert not (folder / "lock").exists()                              # released on exit
    assert (tmp_path / ".orwick" / "sessions" / ".gitignore").read_text().strip().endswith("*")


def test_a_session_with_nothing_said_is_removed_on_exit(run_cli, tmp_path):
    out = run_cli(["/help", "/exit"])
    assert "Session: .orwick/sessions/" in out
    assert session_folders(tmp_path) == []


def test_the_app_can_choose_the_session_id(run_cli, tmp_path):
    run_cli(["hi", "/exit"], "--session-id", "2026-11-12_114105_a3f9")
    assert [p.name for p in session_folders(tmp_path)] == ["2026-11-12_114105_a3f9"]


def test_a_bad_session_id_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--env-file", "none.env", "--session-id", "../../etc"]) == 2
    assert "session id must look like" in capsys.readouterr().err
    assert not (tmp_path / ".orwick" / "sessions").exists()  # nothing created


def test_resume_continues_the_same_session(run_cli, tmp_path):
    run_cli(["first question", "/exit"])
    [folder] = session_folders(tmp_path)
    out = run_cli(["second question", "/exit"], "--resume", folder.name)
    assert "Resumed: “first question” · 1 messages" in out
    assert session_folders(tmp_path) == [folder]
    messages = read(folder / "conversation.json")["messages"]
    assert [m["content"] for m in messages if m["role"] == "user"] == ["first question", "second question"]
    assert read(folder / "meta.json")["title"] == "first question"


def test_resume_refuses_unknown_and_busy_sessions(run_cli, tmp_path, capsys):
    import os
    run_cli(["hi", "/exit"])
    [folder] = session_folders(tmp_path)
    assert cli.main(["--env-file", "none.env", "--resume", "2026-01-01_000000_beef"]) == 2
    assert "no saved session" in capsys.readouterr().err
    from tests.conftest import hold_lock
    fd = hold_lock(folder / "lock")  # another Orwick has it open
    try:
        assert cli.main(["--env-file", "none.env", "--resume", folder.name]) == 2
        assert "is open in another Orwick (process 4242)" in capsys.readouterr().err
    finally:
        os.close(fd)
    # Once that Orwick is gone (its lock freed by the system), the session opens again.
    assert "Resumed:" in run_cli(["/exit"], "--resume", folder.name)


def test_sessions_lists_and_resume_switches_inside_the_chat(run_cli, tmp_path):
    run_cli(["old work", "/exit"])
    [old] = session_folders(tmp_path)
    out = run_cli(["new work", "/sessions", f"/resume {old.name}", "more old work", "/exit"])
    assert "🗂 Saved sessions" in out and "old work" in out and "● this session" in out
    assert f"Resumed “old work” · 1 messages" in out
    old_users = [m["content"] for m in read(old / "conversation.json")["messages"] if m["role"] == "user"]
    assert old_users == ["old work", "more old work"]
    new = [f for f in session_folders(tmp_path) if f != old][0]
    assert read(new / "status.json")["continued_in"] == old.name   # the app can follow
    assert [m["content"] for m in read(new / "conversation.json")["messages"]
            if m["role"] == "user"] == ["new work"]


def test_audit_log_goes_to_the_session_unless_set(run_cli, tmp_path):
    out = run_cli(["hi", "/config", "/exit"])
    [folder] = session_folders(tmp_path)
    assert (folder / "audit.jsonl").is_file()
    assert source_row(out, "audit_log").split("◂")[0].split()[1].endswith("/audit.jsonl")
    assert ".orwick/sessions/" in source_row(out, "audit_log")
    out = run_cli(["/config", "/exit"], config={"audit_log": "shared.jsonl"})
    assert source_row(out, "audit_log").split("◂")[0].split()[1] == "shared.jsonl"



def test_clear_starts_a_new_session_in_the_same_folder(run_cli, tmp_path):
    out = run_cli(["first", "/debug on", "/backend claude", "/clear", "/config", "second", "/exit"])
    old, new = session_folders(tmp_path)
    assert f"New session: {Path('.orwick/sessions') / new.name}" in out
    assert read(old / "status.json")["continued_in"] == new.name   # apps can follow
    assert read(new / "status.json")["continued_in"] is None
    assert read(new / "status.json")["backend"] == "codex"         # /backend was for the old session
    assert debug_line(out)[-1] == "off   [default]"               # and so was /debug
    assert read(old / "meta.json")["title"] == "first" and read(new / "meta.json")["title"] == "second"



def test_a_failed_resume_keeps_the_current_session(run_cli, tmp_path):
    run_cli(["old work", "/exit"])
    [old] = session_folders(tmp_path)
    (old / "conversation.json").write_text("{broken")
    out = run_cli(["hi", f"/resume {old.name}", "/resume 2026-01-01_000000_beef", "/sessions", "/exit"])
    assert "can't read session" in out and "no saved session" in out
    assert "● this session" in out           # still in the new session
    assert not (old / "lock").exists()       # the unreadable one was let go



def test_resume_shows_the_whole_conversation_and_history_shows_it_again(run_cli, tmp_path):
    run_cli(["first question", "second question", "/exit"])
    [folder] = session_folders(tmp_path)
    out = run_cli(["/history", "/exit"], "--resume", folder.name)
    assert out.count("── Earlier in this session ──") == 2          # on resume, and for /history
    assert out.count("Orwick: echo: first question") == 2
    assert out.count("You: second question") == 2



def test_compact_summarizes_and_the_whole_conversation_is_still_shown(run_cli, tmp_path):
    first = ("one " + "detail " * 200).strip()   # long enough that the summary is shorter
    out = run_cli([first, "two", "three", "/compact", "/history", "/exit"])
    assert "[compact] Summarized 2 earlier messages" in out
    assert "── compacted here: the AI now sees a summary of the 2 messages above ──" in out
    [folder] = session_folders(tmp_path)
    saved = read(folder / "conversation.json")
    assert [m["content"] for m in saved["earlier"]] == [first, f"echo: {first}"]
    assert saved["messages"][0]["content"].startswith("[Summary of the earlier conversation]")
    meta = read(folder / "meta.json")
    assert meta["title"].startswith("one detail") and meta["messages"] == 3   # the summary isn't counted
    # Resuming shows everything again, with the mark.
    resumed = run_cli(["/exit"], "--resume", folder.name)
    assert "You: one detail" in resumed and "compacted here" in resumed


def test_compact_with_too_little_to_compact(run_cli):
    out = run_cli(["only one", "/compact", "/exit"])
    assert "Nothing to compact yet" in out


def test_a_long_conversation_is_compacted_by_itself(run_cli, tmp_path):
    long = "y" * 12_000                          # ~3k tokens per message, 8k budget
    out = run_cli([long, long + "2", long + "3", "last", "/exit"], config={"history_budget_tokens": 8000})
    assert "[compact] Summarized" in out



def test_the_chat_warns_when_the_conversation_reaches_the_percent(run_cli, tmp_path):
    words = "w" * 4_000                       # ~1k tokens per message, ~2k per turn with the echo
    out = run_cli([words, words + "2", words + "3", "/status", "/exit"],
                  config={"history_budget_tokens": 10_000, "history_warn_percent": 30})
    assert out.count("📏 This conversation is at") == 2      # crossed 30%, then 10 points more
    assert "📏 conversation" in out and "of its budget" in out   # the /status bar
    [folder] = session_folders(tmp_path)
    level = read(folder / "status.json")["conversation"]
    assert level["budget"] == 10_000 and level["warn_percent"] == 30 and level["percent"] >= 50
    assert (level["trim_percent"], level["summarize_percent"]) == (80, 90)


def test_history_warn_percent_is_checked():
    from agent_core.config import Settings
    from agent_core.errors import ConfigError
    for bad in (-1, 101, "50"):
        with pytest.raises(ConfigError):
            Settings(history_warn_percent=bad)



@pytest.fixture
def with_tools():
    """The built-in tools are always there now; kept so the tests read the same."""


def test_a_tool_switched_off_is_not_offered_and_comes_back_live(run_cli, tmp_path, with_tools, monkeypatch):
    offered = []

    class RecordingModel(EchoModel):
        def complete(self, messages, tools):
            offered.append(sorted(t["function"]["name"] for t in tools))
            return super().complete(messages, tools)

    monkeypatch.setattr(cli, "build_model", lambda settings, tracer, usage=None: RecordingModel())
    out = run_cli(["first", "/tools", "second", "/exit"], config={"tools": {"run_command": False}},
                  edits={"second": {"tools": {}}})          # switched back on while the session runs
    assert "Switched off: run_command" in out
    assert "run_command" not in offered[0] and "read_file" in offered[0]   # never offered to the AI
    assert "[config] Reloaded: tools: run_command on" in out
    assert "run_command" in offered[1]                                     # offered again, same session


def test_status_json_lists_switched_off_tools(run_cli, tmp_path, with_tools):
    run_cli(["hi", "/exit"], config={"tools": {"run_command": False, "grep": False}})
    [folder] = session_folders(tmp_path)
    tools = {t["name"]: t["enabled"] for t in read(folder / "status.json")["tools"]}
    assert tools["run_command"] is False and tools["grep"] is False and tools["read_file"] is True
    assert len(tools) == 18



def test_tools_can_be_switched_for_this_session_only(run_cli, tmp_path, with_tools, monkeypatch, main_config):
    offered = []

    class RecordingModel(EchoModel):
        def complete(self, messages, tools):
            offered.append({t["function"]["name"] for t in tools})
            return super().complete(messages, tools)

    monkeypatch.setattr(cli, "build_model", lambda settings, tracer, usage=None: RecordingModel())
    main_config.parent.mkdir(parents=True)
    main_config.write_text(json.dumps({"tools": {"grep": False}}))   # the main list
    out = run_cli(["one", "/tools off run_command", "/tools on grep", "/tools", "two", "/exit"])
    assert "run_command is off for this session. The tool list in your settings is unchanged." in out
    assert "Switched off: run_command (this session)" in out
    assert "on for this session" in out                                  # grep
    assert "grep" not in offered[0] and "run_command" in offered[0]
    assert "grep" in offered[1] and "run_command" not in offered[1]
    assert json.loads(main_config.read_text()) == {"tools": {"grep": False}}   # main list untouched
    [folder] = session_folders(tmp_path)
    assert read(folder / "tools.json") == {"grep": True, "run_command": False}
    tools = {t["name"]: t for t in read(folder / "status.json")["tools"]}
    assert (tools["run_command"]["enabled"], tools["run_command"]["default"], tools["run_command"]["session"]) == (False, True, False)
    assert (tools["grep"]["enabled"], tools["grep"]["default"], tools["grep"]["session"]) == (True, False, True)
    assert tools["read_file"]["session"] is None

    # Resuming keeps the session's switches; /clear starts from the main list; reset drops them.
    out = run_cli(["/tools", "/tools reset", "/tools", "/exit"], "--resume", folder.name)
    assert "Switched off: run_command (this session)" in out.split("This session uses")[0]
    assert "This session uses the tool list from your settings again." in out
    assert not (folder / "tools.json").exists()


def test_switching_a_tool_back_to_the_settings_value_removes_the_override(run_cli, tmp_path, with_tools):
    run_cli(["hi", "/tools off run_command", "/tools on run_command", "/exit"])
    [folder] = session_folders(tmp_path)
    assert not (folder / "tools.json").exists()


def test_tools_command_mistakes(run_cli, with_tools):
    out = run_cli(["/tools off nonsense", "/tools maybe grep", "/exit"])
    assert "No tool named nonsense here." in out
    assert "Usage: /tools, /tools on <name>, /tools off <name>, /tools reset" in out


def test_a_broken_session_tools_file_is_ignored_with_a_note(run_cli, tmp_path, with_tools):
    run_cli(["hi", "/exit"])
    [folder] = session_folders(tmp_path)
    (folder / "tools.json").write_text("[1, 2]")
    out = run_cli(["/tools", "/exit"], "--resume", folder.name)
    assert "[tools]" in out and "ignoring it" in out and "Tools (18)" in out



def test_list_tools_prints_json_and_starts_no_session(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--list-tools"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == 1 and len(data["tools"]) == 18
    assert {t["origin"] for t in data["tools"]} == {"built-in"}
    claude = {t["name"]: (t["risk"], t["default"]) for t in data["own_tools"]["claude"]}
    assert claude["WebSearch"] == ("safe", True) and claude["Bash"] == ("dangerous", False)
    assert not (tmp_path / ".orwick").exists()


def test_a_folder_tool_with_a_built_in_name_is_skipped_with_a_note(run_cli, tmp_path):
    folder = tmp_path / ".orwick" / "tools" / "read_file"
    folder.mkdir(parents=True)
    (folder / "tool.json").write_text(json.dumps({
        "name": "read_file", "description": "x", "risk_level": "read", "parameters": {},
        "run": {"python": "agent_core.tools.files:list_folder"}}))
    out = run_cli(["/exit"])
    assert "[tools] .orwick/tools/read_file skipped: a built-in tool has that name" in out



def test_status_and_config_show_the_ais_own_tools(run_cli, tmp_path):
    out = run_cli(["hi", "/config", "/exit"], config={"backend": "claude", "claude_tools": ["WebSearch", "Bash"]})
    assert "WebSearch, Bash  ⚠ works without asking" in out
    [folder] = session_folders(tmp_path)
    own = read(folder / "status.json")["own_tools"]
    assert [(t["name"], t["risk"]) for t in own] == [("WebSearch", "safe"), ("Bash", "dangerous")]



def test_an_mcp_tool_is_called_through_orwicks_gateway(run_cli, tmp_path, monkeypatch):
    import sys
    from agent_core.llm import ModelResponse, ToolCall
    server = str(Path(__file__).parent / "fake_mcp_server.py")
    seen = []

    class CallsTheTool(EchoModel):
        def complete(self, messages, tools):
            seen.append(sorted(t["function"]["name"] for t in tools))
            if messages[-1]["role"] == "user":
                return ModelResponse(text=None, tool_calls=[ToolCall(
                    id="c1", name="mcp__fake__get_thing", arguments_json='{"key": "PROJ-1"}')])
            return ModelResponse(text="result: " + messages[-1]["content"])

    monkeypatch.setattr(cli, "build_model", lambda settings, tracer, usage=None: CallsTheTool())
    monkeypatch.setenv("FAKE_SECRET", "s3cret")
    config = {"mcpServers": {"fake": {"command": sys.executable, "args": [server],
                                      "env": {"FAKE_TOKEN": "${FAKE_SECRET}"}}},
              "tools": {"mcp__fake__get_thing": True}}
    out = run_cli(["read PROJ-1", "/tools", "/exit"], config=config)
    assert "mcp__fake__get_thing" in seen[0] and "mcp__fake__make_thing" not in seen[0]
    assert "thing PROJ-1 token-len=6" in out                     # the token reached only the server
    [folder] = session_folders(tmp_path)
    audit = (folder / "audit.jsonl").read_text()
    assert "mcp__fake__get_thing" in audit and "s3cret" not in audit
    assert "mcp__fake__get_thing" in out.split("Tools (")[1]   # /tools lists it


def test_mcp_tools_are_off_until_switched_on(run_cli, tmp_path, monkeypatch):
    import sys
    server = str(Path(__file__).parent / "fake_mcp_server.py")
    config = {"mcpServers": {"fake": {"command": sys.executable, "args": [server]}}}
    out = run_cli(["/tools on mcp__fake__get_thing", "/tools", "/exit"], config=config)
    assert "mcp__fake__get_thing is on for this session" in out
    assert "Tools (19)" in out



def test_a_session_can_be_linked_to_a_ticket(run_cli, tmp_path, monkeypatch):
    systems = []

    class SeesSystem(EchoModel):
        def complete(self, messages, tools):
            systems.append(messages[0]["content"])
            return super().complete(messages, tools)

    monkeypatch.setattr(cli, "build_model", lambda settings, tracer, usage=None: SeesSystem())
    out = run_cli(["/feature", "/feature proj-16765", "hello", "/sessions", "/exit"])
    assert "isn't linked to a feature" in out and "This session now works on PROJ-16765." in out
    assert "ticket PROJ-16765" in systems[0]                       # the AI is told
    [folder] = session_folders(tmp_path)
    assert read(folder / "meta.json")["feature"] == "PROJ-16765"
    assert read(folder / "status.json")["feature"] == "PROJ-16765"
    assert "PROJ-16765" in out.split("Saved sessions")[1]               # /sessions shows it
    # Resuming keeps the link; /feature off removes it.
    out = run_cli(["/feature", "/feature off", "/exit"], "--resume", folder.name)
    assert "This session works on PROJ-16765." in out and "The feature link is removed." in out
    assert read(folder / "meta.json")["feature"] is None


def test_a_session_can_be_named(run_cli, tmp_path):
    out = run_cli(["/name", "hello", "/name  Price lists  for PROJ-1", "/name", "/sessions", "/exit"])
    assert "has no name" in out and "now called “Price lists for PROJ-1”" in out
    assert "This session is called “Price lists for PROJ-1”." in out
    [folder] = session_folders(tmp_path)
    assert (folder / "name.txt").read_text().strip() == "Price lists for PROJ-1"
    assert read(folder / "status.json")["name"] == "Price lists for PROJ-1"
    assert "   Price lists for PROJ-1" in out                                   # /sessions shows it
    out = run_cli(["/name off", "/exit"], "--resume", folder.name)
    assert "The name is removed." in out and not (folder / "name.txt").exists()


def test_feature_on_the_command_line_and_bad_keys(run_cli, tmp_path, capsys, monkeypatch):
    run_cli(["hi", "/exit"], "--feature", "OPS-5191")
    [folder] = session_folders(tmp_path)
    assert read(folder / "meta.json")["feature"] == "OPS-5191"
    out = run_cli(["/feature not a key", "/feature x-1", "/exit"])
    assert out.count("Usage: /feature") == 2
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--env-file", "none.env", "--feature", "../etc"]) == 2
    assert "doesn't look like a ticket key" in capsys.readouterr().err



def test_skills_index_reaches_the_ai_and_a_doc_is_written_after_approval(run_cli, tmp_path, monkeypatch):
    from agent_core.llm import ModelResponse, ToolCall
    seen = {}

    class WritesADoc(EchoModel):
        def complete(self, messages, tools):
            seen["system"] = messages[0]["content"]
            seen["tools"] = sorted(t["function"]["name"] for t in tools)
            if messages[-1]["role"] == "user":
                return ModelResponse(text=None, tool_calls=[ToolCall(
                    id="d1", name="write_session_doc",
                    arguments_json=json.dumps({"name": "feature-docs", "content": "# PROJ-1 — Test\n"}))])
            return ModelResponse(text="saved")

    monkeypatch.setattr(cli, "build_model", lambda settings, tracer, usage=None: WritesADoc())
    from agent_core.approvals import TerminalApprovalProvider
    asked = []
    monkeypatch.setattr(cli, "TerminalApprovalProvider",
                        lambda: TerminalApprovalProvider(input_fn=lambda prompt: asked.append(prompt) or "y"))
    out = run_cli(["brief it", "/skills", "/exit"])
    assert asked and "APPROVAL REQUIRED" in out and "+# PROJ-1 — Test" in out   # you saw the change first
    assert "## Skills" in seen["system"] and "feature-docs:" in seen["system"]
    assert "use_skill" in seen["tools"] and "write_session_doc" in seen["tools"]
    assert "🎓 Skills (4 on)" in out
    [folder] = session_folders(tmp_path)
    assert (folder / "docs" / "feature-docs.md").read_text() == "# PROJ-1 — Test\n"
    assert read(folder / "status.json")["docs"] == ["feature-docs"]
    assert read(folder / "status.json")["skills"] == ["code-check", "feature-docs", "review-analytics", "service-map"]


def test_with_every_skill_off_use_skill_isnt_offered(run_cli, monkeypatch):
    offered = []

    class Records(EchoModel):
        def complete(self, messages, tools):
            offered.append((messages[0]["content"], sorted(t["function"]["name"] for t in tools)))
            return super().complete(messages, tools)

    monkeypatch.setattr(cli, "build_model", lambda settings, tracer, usage=None: Records())
    run_cli(["hi", "/exit"], config={"skills": {"feature-docs": False, "code-check": False, "service-map": False,
                                             "review-analytics": False}})
    system, tools = offered[0]
    assert "use_skill" not in tools and "## Skills" not in system



def test_an_empty_mcp_variable_is_explained(run_cli, monkeypatch):
    import sys
    monkeypatch.delenv("MISSING_TOKEN", raising=False)
    server = str(Path(__file__).parent / "fake_mcp_server.py")
    config = {"mcpServers": {"fake": {"command": sys.executable, "args": [server],
                                      "env": {"FAKE_TOKEN": "${MISSING_TOKEN}"}}},
              "tools": {"mcp__fake__get_thing": True}}
    out = run_cli(["/exit"], config=config)
    assert "[tools] MCP server fake: ${MISSING_TOKEN} is empty here" in out



def test_do_runs_a_skill_action_asking_for_its_inputs(run_cli, tmp_path, monkeypatch):
    sent = []

    class Records(EchoModel):
        def complete(self, messages, tools):
            sent.append((messages[0]["content"], messages[-1]["content"]))
            return super().complete(messages, tools)

    monkeypatch.setattr(cli, "build_model", lambda settings, tracer, usage=None: Records())
    out = run_cli(["/do", "/do feature-docs read-ticket", "not a key", "proj-16765", "/exit"])
    assert "/do feature-docs read-ticket  Read ticket" in out                  # /do lists the actions
    assert "That doesn't look right" in out                                    # the bad answer is checked
    assert "This session now works on PROJ-16765." in out                       # linked to the ticket
    assert "▶ Read ticket: Make the feature docs for PROJ-16765" in out
    system, prompt = sent[0]
    assert "ticket PROJ-16765" in system and prompt.startswith("Make the feature docs for PROJ-16765")
    [folder] = session_folders(tmp_path)
    status = read(folder / "status.json")
    assert status["state"] == "waiting"
    commands = [a["command"] for a in status["actions"]]
    assert commands == ["/do code-check review-code", "/do code-check grade-code",
                        "/do feature-docs read-ticket", "/do feature-docs update-docs", "/do service-map map"]
    assert status["actions"][2]["inputs"] == ["Ticket ID (e.g. PROJ-16765)"]
    assert all(a["hint"] for a in status["actions"])        # every button explains itself
    assert [(g["skill"], g["title"]) for g in status["action_groups"]] == [
        ("code-check", "Code review checklist"), ("feature-docs", "Feature docs"), ("service-map", "Service map")]
    assert all(g["hint"] for g in status["action_groups"])


def test_do_can_be_cancelled(run_cli, monkeypatch):
    out = run_cli(["/do feature-docs read-ticket", "", "/exit"])
    assert "Cancelled." in out


def test_the_status_says_working_while_answering(run_cli, tmp_path, monkeypatch):
    states = []

    class Peeks(EchoModel):
        def complete(self, messages, tools):
            [folder] = session_folders(tmp_path)
            states.append(read(folder / "status.json")["state"])
            return super().complete(messages, tools)

    monkeypatch.setattr(cli, "build_model", lambda settings, tracer, usage=None: Peeks())
    run_cli(["hello", "/exit"])
    assert states == ["working"]



def test_a_skill_action_gets_its_own_step_limit(run_cli, monkeypatch):
    limits = []
    original = cli.Agent.send

    def spy(self, text, max_steps=None):
        limits.append(max_steps)
        return original(self, text, max_steps=max_steps)

    monkeypatch.setattr(cli.Agent, "send", spy)
    run_cli(["hello", "/do feature-docs update-docs", "again", "/exit"])
    assert limits == [None, 40, None]                 # only the action gets more steps



def test_trace_shows_titles_never_raw_results(run_cli, monkeypatch):
    from agent_core.llm import ModelResponse, ToolCall

    class UsesATool(EchoModel):
        def complete(self, messages, tools):
            if messages[-1]["role"] == "user" and not messages[-1]["content"].startswith("[Orwick]"):
                return ModelResponse(text=None, tool_calls=[ToolCall(id="t1", name="list_folder",
                                                                      arguments_json='{"path": "."}')])
            return ModelResponse(text="done")

    monkeypatch.setattr(cli, "build_model", lambda settings, tracer, usage=None: UsesATool())
    out = run_cli(["/trace off", "look", "/trace on", "again", "/config", "/exit"])
    hidden, shown = out.split("Tool calls are shown again.")
    assert "▸ List folder ." not in hidden and "(1 tool call · /trace on to see them)" in hidden
    assert "▸ List folder ." in shown and "← ok" not in shown and "tool call · /trace" not in shown
    row = source_row(out, "trace")
    assert " on " in f"{row} " and "/trace command" in row


def test_trace_can_be_off_in_the_settings(run_cli):
    out = run_cli(["/trace", "/exit"], config={"trace": False})
    assert "Tool lines are off." in out


def test_results_stay_out_of_the_terminal_but_failures_get_a_line(capsys):
    from agent_core.agent import AgentEvent
    cli.print_event(AgentEvent("tool_result", {"name": "grep", "ok": True, "data": {"matches": []}}))
    assert capsys.readouterr().out == ""
    cli.print_event(AgentEvent("tool_result", {"name": "grep", "ok": False, "data": None,
                                               "error": {"type": "mcp_error", "message": "Read timed out"}}))
    assert capsys.readouterr().out.strip() == "✗ mcp_error: Read timed out"


def test_session_keeps_its_tool_calls_and_counts_when_resumed():
    from agent_core.usage import SessionUsage
    first = SessionUsage()
    first.stats.count("tool.requested", {"tool": "grep"})
    first.for_backend("claude").add_request(2.0, input_tokens=100, output_tokens=5)
    first.add_call("Search for “x”", "grep")
    first.finish_call("grep", True)
    saved = json.loads(json.dumps(first.to_json()))
    again = SessionUsage()
    held = again.for_backend("claude")          # the model holds this object
    again.restore(saved)
    assert again.stats.tool_calls == 1 and held.input_tokens == 100
    assert again.calls == [{"title": "Search for “x”", "tool": "grep", "ok": True, "at": first.calls[0]["at"]}]


def test_tool_calls_get_readable_titles():
    assert cli.describe_call("mcp__mcp-atlassian__jira_get_issue", {"issue_key": "PROJ-1"}) == "Read Jira issue PROJ-1"
    assert cli.describe_call("read_file", {"path": "a.py"}) == "Read a.py"
    assert cli.describe_call("grep", {"pattern": "x", "path": "src", "glob": "*.go"}) == "Search for “x” in src (*.go files)"
    assert cli.describe_call("read_file", {"path": "a.go", "start_line": 1, "line_count": 120}) == "Read a.go (lines 1–120)"
    assert cli.describe_call("mcp__srv__do_thing", {"id": 7}) == "srv: Do thing 7"
    assert cli.describe_call("custom_tool", None) == "Custom tool"


def test_progress_is_reported_while_answering(monkeypatch, capsys):
    from agent_core.agent import AgentEvent
    seen = []
    monkeypatch.setitem(cli.PROGRESS, "fn", lambda activity, whole: seen.append((activity, whole)))
    for kind, data in [("model_request", {"step": 1}), ("model_response", {"step": 1}),
                       ("tool_call", {"name": "read_file", "arguments": {"path": "a.py"}}),
                       ("step_done", {"step": 1})]:
        cli.print_event(AgentEvent(kind, data))
    assert seen == [("Thinking (step 1)", False), ("Read a.py", False), ("Step 1 done", True)]
    assert "▸ Read a.py" in capsys.readouterr().out



def test_status_and_list_tools_tell_the_app_about_models(run_cli, tmp_path, capsys, monkeypatch):
    run_cli(["hi", "/backend claude sonnet", "/exit"])
    [folder] = session_folders(tmp_path)
    status = read(folder / "status.json")
    assert (status["backend"], status["model_choice"]) == ("claude", "sonnet")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--list-tools"]) == 0
    models = json.loads(capsys.readouterr().out)["models"]
    assert [m["label"] for m in models["claude"]] == ["Claude Fable 5.2", "Claude Opus 5.5",
                                                       "Claude Sonnet 5.5", "Claude Haiku 5.5"]



def test_list_models_is_quick_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--list-models"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == 1 and "claude" in data["models"] and not (tmp_path / ".orwick").exists()



def test_sessions_delete_asks_first(run_cli, tmp_path):
    run_cli(["old one", "/exit"])
    [old] = session_folders(tmp_path)
    out = run_cli(["hi", f"/sessions delete {old.name}", "n", f"/sessions delete {old.name}", "y", "/exit"])
    assert "“old one” · 1 messages" in out and "Kept." in out and f"Deleted session {old.name}." in out
    assert not old.exists()


def test_the_current_session_cant_be_deleted_from_itself(run_cli, tmp_path):
    out = run_cli(["hi", "/sessions delete x", "/exit"])
    assert "No saved session x here" in out


def test_delete_session_for_the_app_doesnt_ask(run_cli, tmp_path, capsys, monkeypatch):
    run_cli(["old", "/exit"])
    [old] = session_folders(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--delete-session", old.name]) == 0
    assert "Deleted session" in capsys.readouterr().out and not old.exists()
    assert cli.main(["--delete-session", old.name]) == 2


def test_prompt_lines_go_to_the_history_without_repeats(tmp_path):
    class Editor:
        def __init__(self): self.items = []
        def get_current_history_length(self): return len(self.items)
        def get_history_item(self, i): return self.items[i - 1]
        def add_history(self, line): self.items.append(line)
        def write_history_file(self, path): Path(path).write_text("\n".join(self.items))
    editor, path = Editor(), tmp_path / "history"
    for line in ["hello", "hello", "", "/status"]:
        cli.remember_line(editor, line, path)
    assert editor.items == ["hello", "/status"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_prompt_colors_are_zero_width_for_readline():
    assert cli.readline_prompt("\x1b[36mYou\x1b[0m › ") == "\x01\x1b[36m\x02You\x01\x1b[0m\x02 › "


def test_clear_screen_only_touches_a_real_terminal(monkeypatch, capsys):
    cli.clear_screen()
    assert capsys.readouterr().out == ""                  # captured output isn't a terminal
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    cli.clear_screen()
    assert capsys.readouterr().out == "\033[H\033[2J\033[3J"


def test_live_line_shows_what_orwick_does_and_steps_aside_for_approvals(monkeypatch, capsys):
    live = cli.LiveLine()
    live.show("Read a.py")
    assert capsys.readouterr().out == ""                  # not a terminal: nothing
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    live._thread = object()                                # no redraw thread in a test
    live.show("Read Jira issue PROJ-1")
    shown = capsys.readouterr().out
    assert "Read Jira issue PROJ-1 · 0s" in shown and shown.startswith("\r\033[K")
    with live.paused():
        assert capsys.readouterr().out == "\r\033[K"       # wiped for the approval prompt
        live._draw()
        assert capsys.readouterr().out == ""               # and not redrawn over it
    live.clear()
    assert capsys.readouterr().out == ""                   # already wiped


def test_a_helper_agent_gets_a_fresh_conversation_and_read_only_tools(run_cli, tmp_path, monkeypatch):
    """run_subagent: the main AI hands off a task; the helper has its own conversation,
    only read tools (no run_subagent), and only its report comes back."""
    import json as _json
    from agent_core.llm import ModelResponse, ToolCall
    seen = []

    class Main(EchoModel):
        def complete(self, messages, tools):
            if "You are a HELPER agent" in messages[0]["content"]:          # the helper
                seen.append(([m["role"] for m in messages], sorted(t["function"]["name"] for t in tools),
                             messages[1]["content"]))
                return ModelResponse(text="Report: a.txt says hello (a.txt:1).")
            if messages[-1]["role"] == "tool":
                return ModelResponse(text="Done: " + _json.loads(messages[-1]["content"])["data"]["report"])
            return ModelResponse(None, [ToolCall("c1", "run_subagent", _json.dumps({"task": "Read a.txt and report."}))])

    monkeypatch.setattr(cli, "build_model", lambda settings, tracer, usage=None: Main())
    (tmp_path / "a.txt").write_text("hello")
    out = run_cli(["check a.txt", "/exit"])
    assert "Done: Report: a.txt says hello (a.txt:1)." in out
    [(roles, tools, task)] = seen
    assert roles == ["system", "user"] and task == "Read a.txt and report."      # nothing of the main talk
    assert "read_file" in tools and "grep" in tools
    assert not {"write_file", "edit_file", "run_command", "run_subagent"} & set(tools)



def test_claude_skills_tell_the_ai_which_orwick_tool_replaces_each_claude_tool(tmp_path, monkeypatch):
    from agent_core import skills as skills_module
    from agent_core.tools import knowledge
    claude = tmp_path / "claude-skills"
    (claude / "go-impl").mkdir(parents=True)
    (claude / "go-impl" / "SKILL.md").write_text("---\nname: go-impl\ndescription: Go.\n---\nUse Read, then Bash.\n")
    (tmp_path / "mine" / "notes").mkdir(parents=True)
    (tmp_path / "mine" / "notes" / "SKILL.md").write_text("---\nname: notes\ndescription: N.\n---\nBody.\n")
    monkeypatch.setattr(skills_module, "CLAUDE_SKILLS_DIR", claude)
    monkeypatch.setenv(knowledge.SKILLS_ENV, json.dumps({"go-impl": str(claude / "go-impl"),
                                                         "notes": str(tmp_path / "mine" / "notes")}))
    note = knowledge.use_skill("go-impl")["note"]
    assert "Read → read_file" in note and "Bash → run_command" in note and "run_subagent" in note
    assert "note" not in knowledge.use_skill("notes")


def test_a_skill_switched_on_mid_conversation_is_announced_not_put_in_the_system_prompt(run_cli, tmp_path, monkeypatch):
    """Session-only skill switches: before the first message the system prompt changes;
    after it, the AI gets a note with the next message and the system prompt stays the same
    (so the claude backend doesn't send the conversation again)."""
    from agent_core import skills as skills_module
    claude = tmp_path / "claude-skills" / "golang-testing"
    claude.mkdir(parents=True)
    (claude / "SKILL.md").write_text("---\nname: golang-testing\ndescription: Go tests.\n---\nBody.\n")
    monkeypatch.setattr(skills_module, "CLAUDE_SKILLS_DIR", tmp_path / "claude-skills")
    seen = []

    class Sees(EchoModel):
        def complete(self, messages, tools):
            seen.append((messages[0]["content"], messages[-1]["content"]))
            return super().complete(messages, tools)

    monkeypatch.setattr(cli, "build_model", lambda settings, tracer, usage=None: Sees())
    out = run_cli(["hello", "/skills on golang-testing", "write a test", "/skills off golang-testing",
                   "/skills on nope", "again", "/exit"])
    assert "golang-testing is on for this session (the AI is told with your next message)." in out
    assert "Usage: /skills on|off <name>" in out
    (first_system, _), (second_system, second_message), (third_system, third_message) = seen
    assert first_system == second_system == third_system and "golang-testing" not in first_system
    assert second_message.startswith("[Orwick] The skills for this session changed.")
    assert "- golang-testing: Go tests." in second_message and second_message.endswith("write a test")
    assert "No longer available, don't use: golang-testing." in third_message
    [folder] = session_folders(tmp_path)
    assert not (folder / "skills.json").exists()              # off again = the settings' value: no file
    status = read(folder / "status.json")
    row = next(r for r in status["skills_all"] if r["name"] == "golang-testing")
    assert row["on"] is False and row["setting"] is False and row["session"] is None


def test_a_skill_switched_before_the_first_message_changes_the_system_prompt(run_cli, tmp_path, monkeypatch):
    seen = []

    class Sees(EchoModel):
        def complete(self, messages, tools):
            seen.append((messages[0]["content"], messages[-1]["content"]))
            return super().complete(messages, tools)

    monkeypatch.setattr(cli, "build_model", lambda settings, tracer, usage=None: Sees())
    run_cli(["/skills off code-check", "hi", "/exit"])
    [(system, message)] = seen
    assert "code-check" not in system and "feature-docs" in system and message == "hi"


def test_buttons_of_every_skill_are_reported_and_a_skill_ticked_in_the_app_works_at_once(run_cli, tmp_path):
    """The app shows or hides a skill's buttons as soon as you tick it; Orwick lists every
    skill's buttons with "on", and /do reads this session's skills.json before running."""
    run_cli(["hi", "/skills off code-check", "/exit"])
    [folder] = session_folders(tmp_path)
    actions = read(folder / "status.json")["actions"]
    assert {(a["skill"], a["on"]) for a in actions} == {("code-check", False), ("feature-docs", True), ("service-map", True)}
    (folder / "skills.json").write_text('{"code-check": true}')      # ticked again in the app
    out = run_cli(["/do code-check review-code", "", "/exit"], "--resume", folder.name)     # Enter = yes
    assert "▶ Review code: Review this session's pull request with" in out


def test_a_button_asks_yes_or_no_and_the_next_question_only_after_a_no(run_cli):
    link = "https://bitbucket.org/acme/payments/pull-requests/12"
    out = run_cli(["/do code-check grade-code", "maybe", "n", "payments", link, "/exit"])
    assert "Answer y or n." in out and "That doesn't look right" in out
    assert f"▶ Code Scoring: Grade {link} against" in out
    out = run_cli(["/do service-map map", "y", "/exit"])
    assert "▶ Service map:" in out and "focus:" not in out
    out = run_cli(["/do service-map map", "n", "the refund job", "/exit"])
    assert "focus: the refund job" in out
    assert "Cancelled." in run_cli(["/do service-map map", "n", "", "/exit"])


def test_map_adds_related_folders_and_asks_for_the_service_map(run_cli, tmp_path):
    """/map lets the AI read the services beside this one and find the ones that take part;
    --with adds projects elsewhere. They stay with the session when it's resumed."""
    neighbour = tmp_path.parent / f"{tmp_path.name}-billing"
    neighbour.mkdir()
    code = tmp_path.parent / f"{tmp_path.name}-code"   # a folder of repos elsewhere: the code_root setting
    (code / "repo_x" / ".git").mkdir(parents=True)
    out = run_cli(["/map Price lists --with ../" + neighbour.name + " ../missing", "/exit"],
                  config={"code_root": str(code)})
    assert "[related] ../missing: no such folder" in out
    assert f"echo: Use the service-map skill. Find the flows this service offers" in out
    [folder] = session_folders(tmp_path)
    assert (f"focus: Price lists. The service: {tmp_path.name} (its folder: {tmp_path.resolve()}). "
            f"Map name: {folder.name}_map1. The other repos are beside it, in ../ (read only): "
            f"find yourself which ones take part. Also read: ../{neighbour.name}.") in out
    assert read(folder / "related.json") == [str(neighbour.resolve())]
    out = run_cli(["/map Refund job --name Refund --with ../" + neighbour.name, "/exit"])
    assert "Map name: refund. The other repos" in out
    out = run_cli(["/map --name refund --rebuild", "/exit"])
    assert "Map name: refund. Rebuild it: scan the code again from scratch" in out
    out = run_cli(["/related", "/related add ..", "/related clear", "/exit"], "--resume", folder.name)
    assert f"Related folders (read only): ../{neighbour.name}" in out
    assert "too wide" in out and "Related folders (read only): none" in out
    assert not (folder / "related.json").exists()


def test_your_own_env_file_is_read_for_every_folder(run_cli, main_config, monkeypatch):
    """~/.orwick/.env sets what the tools need; a value may point at a variable already set."""
    monkeypatch.setenv("MY_TOKEN", "t0ken")
    monkeypatch.delenv("BITBUCKET_TOKEN", raising=False)
    main_config.parent.mkdir(parents=True, exist_ok=True)
    (main_config.parent / ".env").write_text("BITBUCKET_TOKEN=${MY_TOKEN}\n")
    run_cli(["/exit"])
    assert os.environ.pop("BITBUCKET_TOKEN") == "t0ken"
