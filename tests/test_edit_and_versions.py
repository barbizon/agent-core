"""Roadmap step 3: edit_file, read-before-write (versions), and diff previews."""

import pytest

from agent_core.errors import PermanentToolError
from agent_core.tools import files


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv(files.WORKSPACE_ENV, str(tmp_path))
    monkeypatch.delenv(files.PROTECTED_PATHS_ENV, raising=False)
    (tmp_path / "app.py").write_text("x = 1\ny = 2\nx = 1\n")
    return tmp_path


def version(path="app.py"):
    return files.read_file(path)["version"]


def test_edit_replaces_exactly_one_occurrence(ws):
    result = files.edit_file("app.py", "y = 2", "y = 3", version())
    assert result["replacements"] == 1
    assert (ws / "app.py").read_text() == "x = 1\ny = 3\nx = 1\n"
    assert result["version"] == version()  # the new version is returned


def test_edit_refuses_ambiguous_or_missing_text(ws):
    with pytest.raises(PermanentToolError, match="appears 2 times"):
        files.edit_file("app.py", "x = 1", "x = 9", version())
    with pytest.raises(PermanentToolError, match="was not found"):
        files.edit_file("app.py", "z = 0", "z = 1", version())
    with pytest.raises(PermanentToolError, match="nothing would change"):
        files.edit_file("app.py", "y = 2", "y = 2", version())
    assert (ws / "app.py").read_text() == "x = 1\ny = 2\nx = 1\n"


def test_replace_all(ws):
    assert files.edit_file("app.py", "x = 1", "x = 9", version(), replace_all=True)["replacements"] == 2
    assert (ws / "app.py").read_text() == "x = 9\ny = 2\nx = 9\n"


def test_changing_an_existing_file_requires_reading_it_first(ws):
    with pytest.raises(PermanentToolError, match="Read it first"):
        files.write_file("app.py", "new")
    with pytest.raises(PermanentToolError, match="Read it first"):
        files.edit_file("app.py", "y = 2", "y = 3", "")


def test_a_file_changed_since_reading_is_refused(ws):
    seen = version()
    (ws / "app.py").write_text("someone else changed it\n")
    with pytest.raises(PermanentToolError, match="changed since you read it"):
        files.edit_file("app.py", "y = 2", "y = 3", seen)
    with pytest.raises(PermanentToolError, match="changed since you read it"):
        files.write_file("app.py", "mine", version=seen)


def test_new_files_need_no_version_and_edit_cannot_create(ws):
    assert files.write_file("new.txt", "hello")["created"] is True
    with pytest.raises(PermanentToolError, match="does not exist"):
        files.edit_file("missing.txt", "a", "b", "v")


def test_line_endings_are_kept_exactly(ws):
    (ws / "win.txt").write_bytes(b"a\r\nb\r\n")
    files.edit_file("win.txt", "b", "c", version("win.txt"))
    assert (ws / "win.txt").read_bytes() == b"a\r\nc\r\n"


def test_previews_show_a_diff_and_change_nothing(ws):
    diff = files.preview_edit("app.py", "y = 2", "y = 3", version())
    assert "-y = 2" in diff and "+y = 3" in diff and "app.py (after)" in diff
    new = files.preview_write("brand_new.md", "line 1\nline 2\n")
    assert "(new file)" in new and "+line 1" in new
    assert (ws / "app.py").read_text() == "x = 1\ny = 2\nx = 1\n"
    assert not (ws / "brand_new.md").exists()


def test_preview_fails_the_same_way_as_the_real_call(ws):
    with pytest.raises(PermanentToolError, match="was not found"):
        files.preview_edit("app.py", "nope", "x", version())


def test_long_diffs_are_cut(ws, monkeypatch):
    monkeypatch.setattr(files, "PREVIEW_MAX_LINES", 5)
    diff = files.preview_write("long.txt", "\n".join(str(i) for i in range(50)))
    assert diff.splitlines()[-1].startswith("... (")
