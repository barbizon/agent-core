"""Roadmap step 4: run_command runs only after approval, inside the OS sandbox."""

import sys
from pathlib import Path

import pytest

from agent_core import sandbox
from agent_core.errors import PermanentToolError
from agent_core.permissions import PermissionPolicy
from agent_core.tools import commands, files
from agent_core.tools.manifest import load_manifest
from tests.conftest import RecordingApprover

ROOT = Path(__file__).resolve().parents[1]


def _sandbox_works() -> bool:
    try:
        sandbox.check_available()
        return True
    except sandbox.SandboxUnavailable:
        return False


needs_sandbox = pytest.mark.skipif(not _sandbox_works(), reason="macOS sandbox not available")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "ws"
    root.mkdir()
    (root / ".orwick").mkdir()
    (root / ".orwick" / ".env").write_text("OPENAI_API_KEY=sk-agent")
    (root / ".orwick" / ".env.example").write_text("OPENAI_API_KEY=")
    (root / ".env").write_text("TOKEN=top-secret")
    (root / ".env.example").write_text("TOKEN=")
    (root / "a.txt").write_text("hello")
    monkeypatch.setenv(files.WORKSPACE_ENV, str(root))
    monkeypatch.setenv(files.PROTECTED_PATHS_ENV, str(root / ".orwick"))
    return root


def run(command, **kw):
    return commands.run_command(command, **kw)


# --- argument checks (any platform) ---------------------------------------------

@pytest.mark.parametrize("kwargs, message", [
    ({"command": "  "}, "empty"),
    ({"command": "x" * 5000}, "longer than"),
    ({"command": "ls\x00"}, "NUL"),
    ({"command": "ls", "timeout_seconds": 0}, "between"),
    ({"command": "ls", "timeout_seconds": 301}, "between"),
    ({"command": "ls", "timeout_seconds": 1.5}, "whole number"),
    ({"command": "ls", "cwd": ".."}, "outside the workspace"),
    ({"command": "ls", "cwd": ".orwick"}, "belongs to the agent"),
    ({"command": "ls", "cwd": "a.txt"}, "not a folder"),
])
@needs_sandbox
def test_bad_arguments_are_refused_before_anything_runs(ws, kwargs, message):
    with pytest.raises(PermanentToolError, match=message):
        commands.preview_command(**kwargs)


def test_without_a_sandbox_nothing_runs(ws, monkeypatch):
    def unavailable():
        raise sandbox.SandboxUnavailable("Commands are disabled: no sandbox")
    monkeypatch.setattr(sandbox, "check_available", unavailable)
    with pytest.raises(PermanentToolError, match="Commands are disabled"):
        commands.preview_command("touch made.txt")
    with pytest.raises(PermanentToolError, match="Commands are disabled"):
        run("touch made.txt")
    assert not (ws / "made.txt").exists()


@needs_sandbox
def test_preview_shows_the_full_command_and_hidden_characters(ws):
    text = commands.preview_command("echo ok\x1b[2K; rm -rf x\n" + "y" * 300, timeout_seconds=30)
    assert "$ echo ok\\x1b[2K; rm -rf x" in text
    assert "y" * 300 in text  # long commands are never shortened in the preview
    assert "Timeout:  30s" in text and "no network" in text


# --- the profile (any platform: it's just text) ------------------------------------

def test_profile_starts_closed_and_protects_secrets(tmp_path):
    ws, tmp = tmp_path / "ws", tmp_path / "tmp"
    ws.mkdir(), tmp.mkdir()
    profile = sandbox.build_profile(ws, tmp, [ws / ".orwick"], home=tmp_path / "home")
    assert "(deny default)" in profile
    assert "network" not in profile.replace("No network", "")  # nothing allows it
    assert '[eE][nN][vV]$"' in profile  # .env, any letter case
    assert '/\\.[gG][iI][tT]/[hH][oO][oO][kK][sS](/|$)' in profile
    assert profile.count("(") == profile.count(")")


# --- real runs in the sandbox (macOS) ----------------------------------------------

@needs_sandbox
def test_runs_in_the_workspace_and_returns_exit_code_and_output(ws):
    result = run("cat a.txt; echo err >&2; exit 3")
    assert result["exit_code"] == 3 and not result["timed_out"]
    assert "hello" in result["output"] and "err" in result["output"]
    assert result["cwd"] == "."


@needs_sandbox
def test_can_write_inside_the_workspace(ws):
    assert run("mkdir -p out && echo x > out/b.txt")["exit_code"] == 0
    assert (ws / "out" / "b.txt").read_text() == "x\n"


@needs_sandbox
@pytest.mark.parametrize("command", [
    "cat .env", "cat .ENV", "cat .orwick/.env", "cat .ORWICK/.env", "cat .orwick/.env.example",
    "ln -s .env link && cat link", "ls .orwick",
])
def test_secrets_and_the_agent_folder_cannot_be_read(ws, command):
    result = run(command)
    assert result["exit_code"] != 0
    assert "top-secret" not in result["output"] and "sk-agent" not in result["output"]
    assert "Operation not permitted" in result["output"]


@needs_sandbox
def test_templates_stay_readable(ws):
    assert run("cat .env.example")["output"].strip() == "TOKEN="


@needs_sandbox
@pytest.mark.parametrize("target", [
    ".env", ".orwick/config.json", ".git/hooks/pre-commit", ".git/config", ".envrc",
    ".vscode/tasks.json", ".github/workflows/ci.yml", ".zshrc",
])
def test_protected_files_cannot_be_written(ws, target):
    (ws / ".git" / "hooks").mkdir(parents=True)
    (ws / ".vscode").mkdir()
    (ws / ".github" / "workflows").mkdir(parents=True)
    result = run(f"echo pwned > {target}")
    assert result["exit_code"] != 0
    assert not (ws / target).exists() or "pwned" not in (ws / target).read_text()


@needs_sandbox
def test_nothing_outside_the_workspace_can_be_written(ws, tmp_path):
    outside = tmp_path / "outside.txt"
    result = run(f"echo x > {outside}; echo x > /tmp/orwick-escape-test")
    assert not outside.exists() and not Path("/tmp/orwick-escape-test").exists()
    assert "Operation not permitted" in result["output"]


@needs_sandbox
def test_the_home_folder_cannot_be_read(ws):
    result = run(f"ls {Path.home()}; cat {Path.home()}/.zshrc")
    assert result["output"].count("Operation not permitted") == 2


@needs_sandbox
def test_there_is_no_network(ws):
    code = "import socket; socket.create_connection(('1.1.1.1', 80), timeout=3)"
    result = run(f"{sys.executable} -c \"{code}\"")
    assert result["exit_code"] != 0 and "Operation not permitted" in result["output"]


@needs_sandbox
def test_secrets_in_the_environment_are_not_passed(ws, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_leak")
    result = run("env")
    assert "leak" not in result["output"]
    assert "HOME=" in result["output"] and "orwick-cmd-" in result["output"]


@needs_sandbox
def test_the_temp_folder_is_private_and_removed(ws):
    result = run('echo x > "$TMPDIR/f" && mkdir "$HOME/.ssh" && echo k > "$HOME/.ssh/id_rsa" && echo $TMPDIR')
    assert result["exit_code"] == 0
    assert not Path(result["output"].strip()).exists()


@needs_sandbox
def test_a_slow_command_is_stopped_with_its_children(ws):
    result = run("(sleep 30; echo late > late.txt) & sleep 30", timeout_seconds=1)
    assert result["timed_out"] and result["duration_seconds"] < 10
    assert not (ws / "late.txt").exists()


@needs_sandbox
def test_ctrl_c_stops_a_running_command_and_its_children(ws):
    import threading, time
    from agent_core.errors import PermanentToolError
    from agent_core.tools import commands
    outcome = {}

    def work():
        try:
            outcome["result"] = run("(sleep 5; echo late > late.txt) & sleep 30", timeout_seconds=60)
        except PermanentToolError as exc:
            outcome["error"] = str(exc)

    thread = threading.Thread(target=work)
    started = time.monotonic()
    thread.start()
    while not commands._RUNNING and time.monotonic() - started < 5:
        time.sleep(0.05)
    assert commands.stop_all() == 1
    thread.join(10)
    assert outcome["error"] == "The user stopped this command (Ctrl+C)."
    assert time.monotonic() - started < 10 and commands.stop_all() == 0
    time.sleep(5.5)
    assert not (ws / "late.txt").exists()                  # its background child was stopped too


@needs_sandbox
def test_long_output_keeps_its_start_and_end(ws):
    result = run("echo FIRST; seq 1 100000; echo LAST")
    assert result["output_truncated"]
    assert result["output"].startswith("FIRST") and result["output"].rstrip().endswith("LAST")
    assert "bytes of output not shown" in result["output"]
    assert len(result["output"]) < commands.OUTPUT_HEAD_CHARS + commands.OUTPUT_TAIL_CHARS + 100


@needs_sandbox
def test_commands_get_no_input(ws):
    assert run("cat")["exit_code"] == 0  # reads an empty stdin instead of waiting


@needs_sandbox
def test_git_works_when_its_config_has_no_credentials(ws):
    assert run("git init -q && git status --short")["exit_code"] != 0  # init writes .git/config: refused
    (ws / ".git").mkdir(exist_ok=True)
    import subprocess
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    result = run("git status --short")
    assert result["exit_code"] == 0, result["output"]
    assert run("git config user.name x")["exit_code"] != 0  # still not writable


@needs_sandbox
def test_git_works_in_a_repo_inside_the_workspace(ws):
    import subprocess
    subprocess.run(["git", "init", "-q", str(ws / "service")], check=True)
    (ws / "service" / "pkg").mkdir()
    result = run("git status --short", cwd="service/pkg")   # a workspace of several repos
    assert result["exit_code"] == 0, result["output"]
    assert run("git config user.name x", cwd="service")["exit_code"] != 0  # still not writable


@needs_sandbox
def test_git_config_with_a_token_stays_blocked(ws):
    import subprocess
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(["git", "-C", str(ws), "remote", "add", "origin",
                    "https://x-access-token:ghp_abcdefghijklmnop@github.com/a/b.git"], check=True)
    result = run("cat .git/config; git remote -v")
    assert "ghp_" not in result["output"]
    subprocess.run(["git", "init", "-q", str(ws / "inner")], check=True)
    subprocess.run(["git", "-C", str(ws / "inner"), "remote", "add", "origin",
                    "https://x-access-token:ghp_abcdefghijklmnop@github.com/a/c.git"], check=True)
    assert "ghp_" not in run("cat .git/config; git remote -v", cwd="inner")["output"]


# --- through the gateway ------------------------------------------------------------

@needs_sandbox
def test_a_denied_command_never_runs(ws, make_gateway, audit):
    tool = load_manifest(ROOT / "src/agent_core/tools/builtin/run_command/tool.json", ("agent_core.tools.",))
    approver = RecordingApprover(False)
    gateway = make_gateway([tool], policy=PermissionPolicy(allow_dangerous=True), approver=approver)
    result = gateway.execute("c1", "run_command", {"command": "touch made.txt"})
    assert not result.ok
    assert "$ touch made.txt" in approver.requests[0].preview
    assert not (ws / "made.txt").exists()


@needs_sandbox
def test_an_approved_command_runs(ws, make_gateway):
    tool = load_manifest(ROOT / "src/agent_core/tools/builtin/run_command/tool.json", ("agent_core.tools.",))
    gateway = make_gateway([tool], policy=PermissionPolicy(allow_dangerous=True),
                           approver=RecordingApprover(True))
    result = gateway.execute("c1", "run_command", {"command": "touch made.txt"})
    assert result.ok and result.data["exit_code"] == 0
    assert (ws / "made.txt").exists()


@needs_sandbox
def test_the_folders_of_switched_on_skills_are_readable_and_runnable_but_not_writable(ws, tmp_path, monkeypatch):
    import json
    from agent_core.tools.knowledge import SKILLS_ENV
    root = tmp_path / "claude-skills"
    (root / "go-impl" / "scripts").mkdir(parents=True)
    (root / "go-impl" / "scripts" / "check.sh").write_text("echo checked\n")
    (root / "golang-shared").mkdir()
    (root / "golang-shared" / "rules.md").write_text("Rule 1.")
    (root / "go-impl" / "token.pem").write_text("secret")
    command = (f"sh {root}/go-impl/scripts/check.sh; cat {root}/golang-shared/rules.md; "
               f"cat {root}/go-impl/token.pem; echo x > {root}/go-impl/new.txt")
    monkeypatch.delenv(SKILLS_ENV, raising=False)
    off = run(command)["output"]
    assert "checked" not in off and "Rule 1." not in off                 # skill off: unreadable
    monkeypatch.setenv(SKILLS_ENV, json.dumps({"go-impl": str(root / "go-impl")}))
    on = run(command)["output"]
    assert "checked" in on and "Rule 1." in on                           # its folder and the shared one
    assert "secret" not in on and not (root / "go-impl" / "new.txt").exists()
