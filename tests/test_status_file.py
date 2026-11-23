"""--status-file: a JSON copy of /status for the Mac app; never the conversation."""

import json
import stat

from agent_core import status_file
from agent_core.usage import LimitWindow


def test_write_replaces_the_file_and_only_you_can_read_it(tmp_path):
    path = tmp_path / "app" / "status.json"
    status_file.write(path, {"a": 1})
    status_file.write(path, {"a": 2})
    assert json.loads(path.read_text()) == {"a": 2}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["status.json"]  # no temp files left


def test_codex_limits_are_read_at_most_every_few_minutes():
    calls = []
    cache = status_file.CodexLimitsCache(lambda: calls.append(1) or ([LimitWindow("weekly", 3)], 100.0), every=300)
    cache.get(now=0)
    cache.get(now=299)
    assert len(calls) == 1
    cache.get(now=300)
    assert len(calls) == 2


class FakeAgent:
    def __init__(self):
        from agent_core.tools.registry import ToolRegistry
        from agent_core.usage import SessionUsage
        self.session = SessionUsage()
        self.registry = ToolRegistry()
        self.messages = [{"role": "system", "content": "rules"},
                         {"role": "user", "content": "my secret question"},
                         {"role": "assistant", "content": "the private answer"}]


def test_snapshot_has_counts_and_usage_but_never_the_conversation(tmp_path):
    agent = FakeAgent()
    agent.session.stats.count("agent.started", {})
    agent.session.stats.count("tool.requested", {"tool": "read_file"})
    usage = agent.session.for_backend("claude")
    usage.add_request(2.0, input_tokens=1000, cached_tokens=800, output_tokens=50, cost_usd=0.01)
    usage.limits = [LimitWindow("5-hour", 18.0, resets_at=123.0, status="allowed")]
    data = status_file.snapshot(agent, "claude", "claude default", tmp_path, False, now=10.0)
    assert data["session"]["messages"] == 1 and data["session"]["tool_calls"] == 1
    assert data["session"]["history_messages"] == 3
    claude = data["usage"]["claude"]
    assert (claude["input_tokens"], claude["cached_tokens"], claude["output_tokens"]) == (1000, 800, 50)
    assert claude["limits"] == [{"name": "5-hour", "used_percent": 18.0, "resets_at": 123.0, "status": "allowed"}]
    text = json.dumps(data)
    assert "my secret question" not in text and "the private answer" not in text


def test_codex_last_known_limits_are_included(tmp_path):
    data = status_file.snapshot(FakeAgent(), "codex", "codex default", tmp_path, False,
                                codex_limits=([LimitWindow("weekly", 7.0)], 50.0))
    assert data["usage"]["codex"]["limits"][0]["name"] == "weekly"
    assert data["usage"]["codex"]["limits_note"] == "last known"
