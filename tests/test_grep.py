"""Roadmap step 2: grep finds text, stops at max_matches, and never reads secrets."""

import pytest

from agent_core.errors import PermanentToolError
from agent_core.tools import files


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv(files.WORKSPACE_ENV, str(tmp_path))
    monkeypatch.delenv(files.PROTECTED_PATHS_ENV, raising=False)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def send():\n    return 1\n\nsend()\n")
    (tmp_path / "src" / "notes.md").write_text("Remember to SEND the report\n")
    (tmp_path / "README.txt").write_text("nothing here\n")
    return tmp_path


def lines(result):
    return [(m["path"], m["line"]) for m in result["matches"]]


def test_finds_matches_with_paths_and_line_numbers(ws):
    result = files.grep("send")
    assert lines(result) == [("src/app.py", 1), ("src/app.py", 4), ("src/notes.md", 1)]
    assert result["truncated"] is False and result["note"] == "All 3 matches shown."


def test_case_glob_path_and_regex_options(ws):
    assert lines(files.grep("SEND", ignore_case=False)) == [("src/notes.md", 1)]
    assert lines(files.grep("send", glob="*.py")) == [("src/app.py", 1), ("src/app.py", 4)]
    assert lines(files.grep("send", path="src/notes.md")) == [("src/notes.md", 1)]
    assert lines(files.grep(r"^def \w+", regex=True)) == [("src/app.py", 1)]
    # without regex=True, special characters are plain text: "send()" is not "send"
    assert lines(files.grep("send()")) == [("src/app.py", 1), ("src/app.py", 4)]


def test_stops_at_max_matches_and_says_so(ws):
    (ws / "big.txt").write_text("hit\n" * 100)
    result = files.grep("hit", max_matches=5)
    assert len(result["matches"]) == 5 and result["truncated"] is True
    assert "Stopped after 5 matches" in result["note"]
    assert len(files.grep("hit", max_matches=10_000)["matches"]) == 100  # capped at 500


def test_never_reads_secrets_binaries_or_noise_folders(ws):
    (ws / ".env").write_text("API_KEY=send-me")
    (ws / ".git").mkdir()
    (ws / ".git" / "objects.txt").write_text("send")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "lib.js").write_text("send")
    (ws / "image.bin").write_bytes(b"send\0\0binary")
    paths = {m["path"] for m in files.grep("send")["matches"]}
    assert paths == {"src/app.py", "src/notes.md"}


def test_symlink_pointing_outside_is_not_followed(ws, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret.txt").write_text("send")
    (ws / "link.txt").symlink_to(outside / "secret.txt")
    assert "link.txt" not in {m["path"] for m in files.grep("send")["matches"]}


def test_errors(ws):
    with pytest.raises(PermanentToolError, match="empty"):
        files.grep("")
    with pytest.raises(PermanentToolError, match="Invalid regular expression"):
        files.grep("(", regex=True)
    with pytest.raises(PermanentToolError, match="outside the workspace"):
        files.grep("x", path="..")
