"""The CLI screens: plain text without colors, every command/tool/setting shown."""

from pathlib import Path

from agent_core import screens
from agent_core.permissions import PermissionPolicy
from agent_core.tools import build_registry
from agent_core.ui import Style, pad, width

PLAIN, COLOR = Style(color=False), Style(color=True)
ROOT = Path(__file__).resolve().parents[1]


def test_help_lists_every_command_and_how_approval_works():
    text = screens.help_screen(".orwick/config.json", PLAIN)
    for command in ("/tools", "/policy", "/config", "/debug", "/clear", "/reset", "/help", "/exit"):
        assert command in text
    assert "Only y approves" in text and "sandbox" in text


def test_tools_screen_shows_each_tool_with_its_risk():
    registry = build_registry(ROOT / "src/agent_core/tools/builtin")
    text = screens.tools_screen(registry, PLAIN)
    for tool in registry.list_tools():
        assert tool.name in text
    assert "💻 run_command" in text and "dangerous · asks you every time" in text
    flat = " ".join(text.replace("│", " ").split())  # undo line wrapping
    assert "e.g. tests or git status." in flat and "It runs inside" not in flat  # not cut at "e.g."


def test_policy_screen_marks_blocked_levels():
    text = screens.policy_screen(PermissionPolicy(), PLAIN)
    assert "runs without asking" in text and text.count("blocked") == 2 and "⛔" in text


def test_colors_only_when_asked():
    assert "\033[" not in screens.help_screen("c.json", PLAIN)
    assert "\033[" in screens.help_screen("c.json", COLOR)


def test_width_counts_emoji_as_two_and_ignores_colors():
    assert width("🧰 ab") == 5
    assert width(COLOR("ab", "red")) == 2
    assert width(pad(COLOR("ab", "red"), 6)) == 6


def test_chat_labels_have_icons_only_with_colors():
    from agent_core.ui import Style
    assert screens.user_prompt(Style(color=False)) == "You: "
    assert screens.agent_label(Style(color=False)) == "Orwick: "
    colored = screens.user_prompt(Style(color=True))
    assert colored.startswith("👤 ") and "You" in colored and "›" in colored
    assert screens.agent_label(Style(color=True)).startswith("🤖 ")


def test_a_saved_conversation_is_drawn_like_the_live_chat():
    from agent_core.ui import Style
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "fix the login bug"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "auth.py"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true, "data": {"content": "def login(): ..."}}'},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c2", "type": "function", "function": {"name": "write_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c2", "content": '{"ok": false, "error": {"type": "approval_denied", "message": "You said no"}}'},
        {"role": "assistant", "content": "I couldn't change it: you said no."},
        {"role": "user", "content": "ok, thanks"},
        {"role": "tool", "tool_call_id": "c3", "content": '{"ok": true, "data": "cut sho'},
    ]
    text = screens.conversation_screen(messages, Style(color=False))
    lines = text.splitlines()
    assert lines[0] == "── Earlier in this session ──" and lines[-1] == "── Continue below ──"
    assert "You: fix the login bug" in lines
    assert '  → read_file({"path": "auth.py"})' in lines
    assert any(l.startswith('  ← ok: {"content": "def login(): ..."}') for l in lines)
    assert "  ← error [approval_denied]: You said no" in lines
    assert "Orwick: I couldn't change it: you said no." in lines
    assert '  ← {"ok": true, "data": "cut sho' in lines   # a result saved cut short still shows
    assert "rules" not in text                             # never the system prompt


def test_an_empty_conversation_says_so():
    from agent_core.ui import Style
    assert "No messages yet" in screens.conversation_screen([{"role": "system", "content": "x"}], Style(color=False))



def test_the_banner_counts_orwicks_tools_and_the_ais_own():
    from agent_core.ui import Style
    plain = Style(color=False)
    assert screens.tool_summary(6, "claude", ["WebSearch", "WebFetch"], plain) == \
        "6 Orwick tools + 2 Claude tools (WebSearch, WebFetch)"
    assert screens.tool_summary(6, "codex", [], plain) == "6 Orwick tools"
    assert screens.tool_summary(6, "codex", ["web_search"], plain, mcp_count=3) == \
        "6 Orwick tools + 3 MCP tools + 1 Codex tool (web_search)"
    assert screens.tool_summary(1, "codex", ["web_search"], plain) == "1 Orwick tool + 1 Codex tool (web_search)"
    assert "⚠ 2 Claude tools (WebSearch, Bash)" in screens.tool_summary(6, "claude", ["WebSearch", "Bash"], plain)


def test_tools_shows_the_ais_own_tools_with_their_risk():
    from agent_core.ui import Style
    plain = Style(color=False)
    text = screens.own_tools_screen("claude", ["WebSearch", "Bash"], plain)
    assert "Claude's own tools (2)" in text and "WebSearch  safe" in text
    assert "Bash  ⚠ dangerous · works without asking you" in text
    assert "All off" in screens.own_tools_screen("codex", [], plain)
    assert screens.own_tools_screen("openai", [], plain) == ""
