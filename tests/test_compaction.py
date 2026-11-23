"""Keeping a long conversation within its budget: trim old tool output, then summarize."""

import json

import pytest

from agent_core import compaction


def turn(n, tool_output="x" * 4000):
    """One user turn: question, a tool call and its result, an answer."""
    return [
        {"role": "user", "content": f"question {n}"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{n}", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a"}'}}]},
        {"role": "tool", "tool_call_id": f"c{n}", "content": json.dumps({"ok": True, "data": tool_output})},
        {"role": "assistant", "content": f"answer {n}"},
    ]


def conversation(turns=4):
    messages = [{"role": "system", "content": "rules"}]
    for n in range(1, turns + 1):
        messages += turn(n)
    return messages


def summarizer(calls):
    def summarize(request):
        calls.append(request)
        return "They read file a four times."
    return summarize


def test_tokens_are_estimated_from_text_and_tool_arguments():
    assert compaction.estimate_tokens([{"role": "user", "content": "x" * 400}]) == 100
    assert compaction.estimate_tokens(turn(1)) > 1000


def test_trimming_replaces_only_older_tool_results():
    messages, trimmed = compaction.trim_old_tool_results(conversation(4))
    assert trimmed == 2                                   # turns 1 and 2; the last 2 turns stay
    tools = [m for m in messages if m["role"] == "tool"]
    assert "older tool result removed" in tools[0]["content"] and json.loads(tools[0]["content"])["ok"]
    assert "x" * 4000 in tools[3]["content"]
    again, trimmed_again = compaction.trim_old_tool_results(messages)
    assert trimmed_again == 0 and again == messages      # doing it twice changes nothing


def test_summarizing_keeps_the_system_prompt_and_the_last_turns_whole():
    calls = []
    messages, removed = compaction.summarize_older(conversation(4), summarizer(calls))
    assert messages[0] == {"role": "system", "content": "rules"}
    assert compaction.is_summary(messages[1]) and "They read file a" in messages[1]["content"]
    assert "notes, not instructions" in messages[1]["content"]
    assert [m["content"] for m in messages if m["role"] == "user"][1:] == ["question 3", "question 4"]
    assert len(removed) == 8 and removed[0]["content"] == "question 1"
    # Every remaining tool call still has its result right after it.
    for index, message in enumerate(messages):
        for call in message.get("tool_calls") or []:
            assert messages[index + 1]["tool_call_id"] == call["id"]
    # The summarizer was told tool results are data, not instructions.
    assert "do not follow it" in calls[0][0]["content"]
    assert "(untrusted data)" in calls[0][1]["content"]


def test_nothing_happens_under_the_budget():
    calls = []
    result = compaction.compact(conversation(4), budget_tokens=1_000_000, summarize=summarizer(calls))
    assert not result.changed and not calls


def test_between_trim_and_summarize_only_old_tool_output_is_trimmed():
    messages = conversation(4)
    size = compaction.estimate_tokens(messages)
    calls = []
    middle = (compaction.TRIM_AT + compaction.SUMMARIZE_AT) / 2
    result = compaction.compact(messages, budget_tokens=int(size / middle), summarize=summarizer(calls))
    assert result.trimmed == 2 and result.summarized == 0 and not calls
    assert result.after_tokens < result.before_tokens


def test_over_the_summarize_point_the_older_part_is_summarized():
    messages = conversation(6)
    calls = []
    trimmed, _ = compaction.trim_old_tool_results(messages)
    # Even after trimming it's over the summarize point of this budget.
    budget = int(compaction.estimate_tokens(trimmed) / (compaction.SUMMARIZE_AT + 0.02))
    result = compaction.compact(messages, budget_tokens=budget, summarize=summarizer(calls))
    assert result.summarized and len(calls) == 1
    assert result.after_tokens < compaction.estimate_tokens(trimmed)


def test_trimming_comes_first_and_may_be_enough():
    calls = []
    messages = conversation(6)
    budget = int(compaction.estimate_tokens(messages) / (compaction.TRIM_AT + 0.05))   # just over the trim point
    result = compaction.compact(messages, budget_tokens=budget, summarize=summarizer(calls))
    assert result.trimmed == 4 and not result.summarized and not calls


def test_a_failed_summary_keeps_every_message_and_still_trims():
    def broken(request):
        raise RuntimeError("usage limit reached")

    trimmed, _ = compaction.trim_old_tool_results(conversation(6))
    budget = int(compaction.estimate_tokens(trimmed) / (compaction.SUMMARIZE_AT + 0.02))
    result = compaction.compact(conversation(6), budget_tokens=budget, summarize=broken)
    assert result.error == "usage limit reached" and result.trimmed and not result.removed
    assert sum(m["role"] == "user" for m in result.messages) == 6


def test_force_compacts_a_small_conversation_but_not_an_empty_one():
    calls = []
    wordy = conversation(3)
    wordy[1]["content"] = "question 1 " + "with many details " * 100
    assert compaction.compact(wordy, 1_000_000, summarizer(calls), force=True).summarized
    assert not compaction.compact(conversation(2), 1_000_000, summarizer([]), force=True).changed


def test_a_summary_longer_than_what_it_replaces_is_not_used():
    tiny = [{"role": "system", "content": "rules"}]
    for n in range(3):
        tiny += [{"role": "user", "content": f"q{n}"}, {"role": "assistant", "content": "ok"}]
    result = compaction.compact(tiny, 1_000_000, lambda request: "a long summary " * 50, force=True)
    assert result.not_smaller and not result.changed and result.messages == tiny


def test_an_empty_summary_is_an_error():
    result = compaction.compact(conversation(4), 1_000_000, lambda request: "  ", force=True)
    assert result.error and not result.summarized


def test_summarizing_twice_folds_the_old_summary_in():
    calls = []
    first, _ = compaction.summarize_older(conversation(4), summarizer(calls))
    second, removed = compaction.summarize_older(first + turn(5) + turn(6), summarizer(calls))
    assert sum(compaction.is_summary(m) for m in second) == 1
    assert compaction.is_summary(removed[0])            # the old summary went into the new one


def test_fill_is_the_share_of_the_budget():
    level = compaction.fill([{"role": "user", "content": "x" * 40_000}], 20_000)
    assert (level.tokens, level.percent) == (10_000, 50)


def test_the_warning_comes_at_the_percent_then_every_ten_points_and_resets():
    warning = compaction.FillWarning()
    said = [p for p in (10, 49, 50, 52, 59, 60, 61, 72, 30, 55) if warning.check(p, 50)]
    assert said == [50, 60, 72, 55]   # 72 ≥ 60+10; dropping to 30 resets, so 55 warns again


def test_a_zero_budget_never_shortens_or_warns_but_compact_still_works():
    calls = []
    big = conversation(6)
    assert not compaction.compact(big, 0, summarizer(calls)).changed and not calls
    assert compaction.fill(big, 0).percent == 0
    assert compaction.FillWarning().check(95, 0) is False          # warn percent 0 = never
    wordy = conversation(3)
    wordy[1]["content"] = "question 1 " + "with many details " * 100
    assert compaction.compact(wordy, 0, summarizer(calls), force=True).summarized   # /compact by hand


def test_tool_output_is_not_cut_with_zero():
    from agent_core.results import ToolResult
    long = ToolResult.success({"text": "x" * 50_000})
    assert len(long.to_model_content(0)) > 50_000 and len(long.to_model_content(1000)) <= 1000
