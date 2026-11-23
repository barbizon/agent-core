"""One folder per Orwick run: .orwick/sessions/<id>/, with cleanup of old ones."""

import pytest

from agent_core import sessions
from agent_core.errors import ConfigError


def test_new_ids_sort_by_time_and_are_valid():
    first, second = sessions.new_id(now=1_000_000_000), sessions.new_id(now=1_000_000_060)
    assert sessions.SESSION_ID.match(first) and sessions.SESSION_ID.match(second)
    assert first < second


def test_open_creates_the_folder_and_a_gitignore(tmp_path):
    folder = sessions.open_session("2026-11-12_114105_a3f9", root=tmp_path / "sessions")
    assert folder.path.is_dir() and folder.id == "2026-11-12_114105_a3f9"
    assert folder.status_file.name == "status.json" and folder.audit_log.name == "audit.jsonl"
    assert (tmp_path / "sessions" / ".gitignore").read_text().strip().endswith("*")


@pytest.mark.parametrize("bad", ["../x", "a/b", "2026-11-12_114105", "x" * 30, ""])
def test_ids_that_are_not_session_ids_are_refused(tmp_path, bad):
    if bad == "":
        return  # empty means "make a new id"
    with pytest.raises(ConfigError):
        sessions.open_session(bad, root=tmp_path / "sessions")


def test_only_the_newest_sessions_are_kept(tmp_path):
    root = tmp_path / "sessions"
    for minute in range(5):
        sessions.open_session(f"2026-11-12_10{minute:02d}00_aaaa", root=root, keep=99)
    (root / "notes").mkdir()                       # not a session: never touched
    (root / "2026-01-01_000000_dead").symlink_to(tmp_path)  # a symlink: never followed or removed
    sessions.open_session("2026-11-12_110000_bbbb", root=root, keep=3)
    names = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink())
    assert names == ["2026-11-12_100300_aaaa", "2026-11-12_100400_aaaa", "2026-11-12_110000_bbbb", "notes"]
    assert (root / "2026-01-01_000000_dead").is_symlink() and tmp_path.exists()


def test_the_current_session_is_never_removed_even_if_it_sorts_oldest(tmp_path):
    root = tmp_path / "sessions"
    for minute in range(3):
        sessions.open_session(f"2026-11-12_10{minute:02d}00_aaaa", root=root, keep=99)
    sessions.open_session("2020-01-01_000000_cccc", root=root, keep=2)
    assert (root / "2020-01-01_000000_cccc").is_dir()


def test_sessions_started_in_the_same_second_keep_their_real_order(tmp_path):
    root = tmp_path / "sessions"
    sessions.open_session("2026-11-12_100000_ffff", root=root, keep=99)   # made first
    sessions.open_session("2026-11-12_100000_0000", root=root, keep=99)   # made second, sorts lower by name
    sessions.open_session("2026-11-12_100001_aaaa", root=root, keep=2)
    names = sorted(p.name for p in root.iterdir() if p.is_dir())
    assert names == ["2026-11-12_100000_0000", "2026-11-12_100001_aaaa"]


def test_cleanup_keeps_the_most_recently_used_and_never_one_open_elsewhere(tmp_path):
    import json
    import os
    root = tmp_path / "sessions"
    for minute in range(4):
        folder = sessions.open_session(f"2026-11-12_10{minute:02d}00_aaaa", root=root, keep=99)
        (folder.path / "meta.json").write_text(json.dumps({"updated_at": minute, "messages": 1}))
        sessions.release(folder)
    # The oldest by name was used most recently; the second oldest is open in another Orwick.
    (root / "2026-11-12_100000_aaaa" / "meta.json").write_text(json.dumps({"updated_at": 9e9, "messages": 1}))
    from tests.conftest import hold_lock
    fd = hold_lock(root / "2026-11-12_100100_aaaa" / "lock")
    try:
        sessions.open_session("2026-11-12_110000_bbbb", root=root, keep=2)
    finally:
        os.close(fd)
    names = sorted(p.name for p in root.iterdir() if p.is_dir())
    assert "2026-11-12_100000_aaaa" in names          # recently used: kept
    assert "2026-11-12_100100_aaaa" in names          # open elsewhere: never removed
    assert "2026-11-12_110000_bbbb" in names          # current
    assert "2026-11-12_100200_aaaa" not in names


def test_empty_sessions_left_by_a_killed_orwick_are_removed_at_the_next_start(tmp_path):
    import json
    root = tmp_path / "sessions"
    for name, messages in [("2026-11-12_100000_aaaa", 0), ("2026-11-12_100100_aaaa", 2)]:
        folder = sessions.open_session(name, root=root, keep=99)
        (folder.path / "meta.json").write_text(json.dumps({"updated_at": 1, "messages": messages}))
        sessions.release(folder)
        (folder.path / "lock").write_text("123")              # killed: the lock file stays, nobody holds it
    sessions.open_session("2026-11-12_110000_bbbb", root=root, keep=99)
    assert sorted(p.name for p in root.iterdir() if p.is_dir()) == ["2026-11-12_100100_aaaa", "2026-11-12_110000_bbbb"]


def test_list_shows_only_sessions_with_messages_newest_first(tmp_path):
    root = tmp_path / "sessions"
    for name, used, count in [("2026-11-12_100000_aaaa", 5, 1), ("2026-11-12_100100_aaaa", 9, 2),
                              ("2026-11-12_100200_aaaa", 7, 0)]:
        folder = sessions.open_session(name, root=root, keep=99)
        sessions.save(folder, [{"role": "user", "content": "x"}] * count, "codex", "m", tmp_path, now=used)
        sessions.release(folder)
    assert [m["id"] for m in sessions.list_sessions(root)] == ["2026-11-12_100100_aaaa", "2026-11-12_100000_aaaa"]


def test_titles_are_one_short_line():
    long = "fix   the\nlogin " + "x" * 200
    title = sessions.title_of([{"role": "system", "content": "rules"}, {"role": "user", "content": long}])
    assert "\n" not in title and len(title) == sessions.TITLE_LENGTH and title.endswith("…")


def test_a_lock_is_freed_when_its_holder_goes_away(tmp_path):
    """The old way (a process id in a file) broke when that process was a zombie;
    an operating-system lock is freed as soon as the holder's files close."""
    import os
    from tests.conftest import hold_lock
    root = tmp_path / "sessions"
    folder = sessions.open_session("2026-11-12_100000_aaaa", root=root)
    sessions.release(folder)
    fd = hold_lock(folder.lock_file)
    assert sessions.is_open_elsewhere(folder.path)
    with pytest.raises(sessions.SessionBusy):
        sessions.acquire(folder)
    os.close(fd)                                  # the other Orwick ends
    assert not sessions.is_open_elsewhere(folder.path)
    sessions.acquire(folder)                      # now it's ours
    assert not sessions.is_open_elsewhere(folder.path)
    sessions.release(folder)
    assert not folder.lock_file.exists()


def test_a_dead_process_number_in_the_lock_file_does_not_block(tmp_path):
    root = tmp_path / "sessions"
    folder = sessions.open_session("2026-11-12_100000_aaaa", root=root)
    sessions.release(folder)
    folder.lock_file.write_text("51681")          # left by an Orwick that was killed
    sessions.acquire(folder)
    sessions.release(folder)


def test_a_session_being_closed_is_waited_for(tmp_path, monkeypatch):
    import os
    import threading
    from tests.conftest import hold_lock
    monkeypatch.setattr(sessions, "LOCK_WAIT_SECONDS", 2)
    root = tmp_path / "sessions"
    folder = sessions.open_session("2026-11-12_100000_aaaa", root=root)
    sessions.release(folder)
    fd = hold_lock(folder.lock_file)
    threading.Timer(0.3, os.close, [fd]).start()   # the other Orwick finishes saving
    sessions.acquire(folder)                        # waits instead of failing
    sessions.release(folder)


def test_deleting_a_session_removes_everything(tmp_path):
    root = tmp_path / "sessions"
    folder = sessions.open_session("2026-11-12_100000_aaaa", root=root)
    sessions.save(folder, [{"role": "user", "content": "hi"}], "codex", "m", tmp_path)
    (folder.path / "docs").mkdir()
    (folder.path / "docs" / "todo.md").write_text("x")
    sessions.release(folder)
    gone = sessions.delete_session("2026-11-12_100000_aaaa", root)
    assert gone == {"id": "2026-11-12_100000_aaaa", "title": "hi", "messages": 1, "docs": ["todo"]}
    assert not folder.path.exists()


def test_a_session_open_elsewhere_or_a_bad_id_is_not_deleted(tmp_path):
    import os
    from tests.conftest import hold_lock
    root = tmp_path / "sessions"
    folder = sessions.open_session("2026-11-12_100000_aaaa", root=root)
    sessions.release(folder)
    fd = hold_lock(folder.lock_file)
    try:
        with pytest.raises(sessions.SessionBusy):
            sessions.delete_session("2026-11-12_100000_aaaa", root)
    finally:
        os.close(fd)
    assert folder.path.exists()
    for bad in ("../x", "2026-01-01_000000_beef"):
        with pytest.raises(ConfigError):
            sessions.delete_session(bad, root)


def test_a_session_name_is_kept_apart_from_meta_and_shown_instead_of_the_title(tmp_path):
    root = tmp_path / "sessions"
    folder = sessions.open_session("2026-11-12_100000_aaaa", root=root)
    sessions.save(folder, [{"role": "user", "content": "first message"}], "codex", "m", tmp_path)
    assert sessions.read_name(folder.path) == ""
    assert sessions.write_name(folder.path, "  PROJ-16930   Price\nlists ") == "PROJ-16930 Price lists"
    sessions.save(folder, [{"role": "user", "content": "first message"}] * 2, "codex", "m", tmp_path)
    assert sessions.read_name(folder.path) == "PROJ-16930 Price lists"     # saving the meta keeps it
    assert sessions.list_sessions(root)[0]["name"] == "PROJ-16930 Price lists"
    assert sessions.display_title(folder.path, sessions.read_meta(folder.path)) == "PROJ-16930 Price lists"
    assert len(sessions.write_name(folder.path, "x" * 500)) == sessions.NAME_LENGTH
    sessions.write_name(folder.path, "")
    assert not folder.name_file.exists()
    assert sessions.display_title(folder.path, sessions.read_meta(folder.path)) == "first message"
    sessions.write_name(folder.path, "named")
    sessions.release(folder)
    assert sessions.delete_session("2026-11-12_100000_aaaa", root)["title"] == "named"
