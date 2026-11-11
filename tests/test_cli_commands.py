"""Drive the real chat loop with typed commands (no AI calls: only slash commands are sent)."""

import json

import pytest

from agent_core import cli
from agent_core.tools import files


@pytest.fixture
def run_cli(tmp_path, monkeypatch, capsys):
    """Run cli.main with the given typed lines; return everything it printed."""
    monkeypatch.chdir(tmp_path)  # logs/ and the config file go to a temp folder
    monkeypatch.delenv(files.PROTECTED_PATHS_ENV, raising=False)
    (tmp_path / ".orvik").mkdir()
    monkeypatch.delenv(files.WORKSPACE_ENV, raising=False)
    monkeypatch.delenv("AGENT_DEBUG", raising=False)

    def run(lines, *flags, config=None, edits=None):
        if config is not None:
            (tmp_path / ".orvik" / "config.json").write_text(json.dumps(config))
        typed = iter(lines)
        edits = edits or {}

        def fake_input(prompt=""):
            line = next(typed)  # StopIteration never happens: tests end with /exit
            if line in edits:  # simulate editing the config file before this line
                (tmp_path / ".orvik" / "config.json").write_text(json.dumps(edits[line]))
            return line

        monkeypatch.setattr("builtins.input", fake_input)
        code = cli.main(["--env-file", str(tmp_path / "none.env"), *flags])
        assert code == 0
        return capsys.readouterr().out

    return run


def debug_line(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("debug = ")]


def test_debug_command_turns_off_debug_started_with_flag(run_cli):
    out = run_cli(["/debug", "/debug off", "/config", "/debug", "/exit"], "-d")
    assert "Debug is on." in out
    assert "Debug is now off. The conversation is kept." in out
    assert debug_line(out) == ["debug = off   [/debug command]"]
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
    assert debug_line(out) == ["debug = off   [/debug command]"]


def test_without_debug_command_the_file_controls_debug(run_cli):
    out = run_cli(["/config", "/exit"], config={"debug": False},
                  edits={"/config": {"debug": True}})
    assert debug_line(out) == ["debug = on   [.orvik/config.json]"]


def test_debug_command_usage(run_cli):
    out = run_cli(["/debug maybe", "/debug on off", "/exit"])
    assert out.count("Usage: /debug, /debug on, /debug off") == 2


def test_help_lists_debug(run_cli):
    assert "/debug" in run_cli(["/help", "/exit"])
