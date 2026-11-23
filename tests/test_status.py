"""/status: session counters, per-backend usage, limits, and the screen."""

import json
import time
from types import SimpleNamespace

from agent_core import screens
from agent_core.audit import InMemoryAuditLogger
from agent_core.llm.codex_model import turn_usage
from agent_core.ui import Style
from agent_core.usage import (
    CountingAuditLogger, LimitWindow, SessionUsage, UsageStats, claude_windows,
    codex_last_known_limits,
)
from tests.test_claude_model import SYSTEM, TOOLS, decision, make_model

PLAIN = Style(color=False)
NOW = 1_800_000_000.0


def test_audit_events_are_counted_and_still_logged():
    inner, session = InMemoryAuditLogger(), SessionUsage()
    logger = CountingAuditLogger(inner, session.stats)
    for event, details in [("agent.started", {}), ("model.requested", {}), ("model.requested", {}),
                           ("tool.requested", {"tool": "grep"}), ("tool.approval_requested", {}),
                           ("tool.approval_denied", {}), ("tool.requested", {"tool": "grep"}),
                           ("tool.execution_succeeded", {})]:
        logger.record("run", event, details)
    stats = session.stats
    assert (stats.messages, stats.steps, stats.tool_calls) == (1, 2, 2)
    assert (stats.approvals_asked, stats.approvals_yes, stats.approvals_no) == (1, 0, 1)
    assert stats.tools["grep"] == 2 and len(inner.types()) == 8


def test_claude_limits_come_from_the_stream():
    windows = claude_windows({"unifiedWindows": {
        "five_hour": {"utilization": 0.18, "resetsAt": NOW + 3600, "status": "allowed"},
        "seven_day": {"utilization": 0.06, "resetsAt": NOW + 86400 * 3}}})
    assert [(w.name, round(w.used_percent)) for w in windows] == [("5-hour", 18), ("7-day", 6)]


def test_claude_adapter_records_tokens_cost_limits_and_context():
    limits = {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
        "five_hour": {"utilization": 0.5, "resetsAt": NOW}}}}
    result = decision(answer="hi")
    result.update(usage={"input_tokens": 10, "cache_read_input_tokens": 990,
                         "cache_creation_input_tokens": 0, "output_tokens": 20},
                  total_cost_usd=0.01, modelUsage={"claude-opus-5-5": {"contextWindow": 1_000_000}})
    model, _ = make_model([limits, result])
    model.complete([SYSTEM, {"role": "user", "content": "x"}], TOOLS)
    usage = model.usage
    assert (usage.requests, usage.input_tokens, usage.cached_tokens, usage.output_tokens) == (1, 1000, 990, 20)
    assert usage.cost_usd == 0.01 and usage.model == "claude-opus-5-5"
    assert usage.context_tokens == 1000 and usage.context_window == 1_000_000
    assert usage.limits[0].used_percent == 50 and usage.limits_note.startswith("live")


def test_claude_cost_is_a_running_total_so_each_reply_counts_its_own_part():
    first, second = decision(answer="a"), decision(answer="b")
    first.update(usage={"input_tokens": 1, "output_tokens": 1}, total_cost_usd=0.40)
    second.update(usage={"input_tokens": 1, "output_tokens": 1}, total_cost_usd=0.50)   # 0.40 + 0.10
    model, _ = make_model([first], [second])
    model.complete([SYSTEM, {"role": "user", "content": "x"}], TOOLS)
    model.complete([SYSTEM, {"role": "user", "content": "x"}, {"role": "assistant", "content": "a"},
                    {"role": "user", "content": "y"}], TOOLS)
    assert round(model.usage.cost_usd, 2) == 0.50          # not 0.90


def test_codex_tokens_from_turn_completed():
    stdout = "\n".join([json.dumps({"type": "turn.started"}), json.dumps(
        {"type": "turn.completed", "usage": {"input_tokens": 25000, "cached_input_tokens": 12000,
                                             "output_tokens": 5}})])
    assert turn_usage(stdout) == {"input_tokens": 25000, "cached_input_tokens": 12000, "output_tokens": 5}


def test_codex_last_known_limits_from_its_own_session_files(tmp_path):
    day = tmp_path / "2026" / "11" / "10"
    day.mkdir(parents=True)
    snapshot = {"primary": {"used_percent": 12.0, "window_minutes": 10080, "resets_at": NOW},
                "secondary": {"used_percent": 40.0, "window_minutes": 300, "resets_at": NOW}}
    lines = [json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "private"}}),
             json.dumps({"type": "event_msg", "payload": {"type": "token_count", "rate_limits": snapshot}})]
    (day / "rollout-x.jsonl").write_text("\n".join(lines))
    windows, when = codex_last_known_limits(tmp_path)
    assert [(w.name, w.used_percent) for w in windows] == [("weekly", 12.0), ("5-hour", 40.0)]
    assert when is not None
    assert codex_last_known_limits(tmp_path / "missing") == ([], None)


def fake_agent(session):
    return SimpleNamespace(session=session, messages=[{"role": "system", "content": "x" * 400}])


def test_status_screen_before_any_request():
    text = screens.status_screen(fake_agent(SessionUsage()), "claude", "claude default", ([], None), PLAIN)
    assert "📊 Session" in text and "0 messages" in text
    assert "Claude" in text and "Shown after your first message" in text and "No requests yet." in text


def test_status_screen_with_usage_limits_and_other_backends():
    session = SessionUsage()
    session.stats.started = NOW - 125
    claude = session.for_backend("claude")
    claude.add_request(2.0, 10_000, 9_000, 500, 0.04, cache_write_tokens=800)
    claude.context_window, claude.model = 1_000_000, "claude-opus-5-5"
    claude.limits = [LimitWindow("5-hour", 85, NOW + 3600)]
    claude.limits_note = "live, from Claude"
    session.for_backend("codex").add_request(8.0, 27_000, 23_000, 38)
    text = screens.status_screen(fake_agent(session), "claude", "claude default", ([], None), PLAIN, now=NOW)
    assert "2m 05s running" in text
    assert "claude-opus-5-5" in text and "5-hour" in text and "85%" in text and "in 1h 00m" in text
    assert "new in 1.0k (800 cache write) · out 500" in text and "$0.04 at API prices" in text
    assert "9.0k re-read from the cache" in text and "10k total" in text
    assert "context 10k of 1M" in text and " 1%" in text
    assert "Also used this session" in text and "codex" in text and "1 request ·" in text


def test_codex_screen_labels_limits_as_last_known():
    windows = [LimitWindow("weekly", 1, NOW + 86400 * 3)]
    text = screens.status_screen(fake_agent(SessionUsage()), "codex", "codex default",
                                 (windows, NOW - 3700), PLAIN, now=NOW)
    assert "last known · from a Codex session 1h 01m ago" in text and "weekly" in text and "1%" in text
