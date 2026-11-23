"""What the CLI shows: the startup banner, /help, /tools and /policy.

(/config lives in config_view.py.) Every screen is a function that returns
text, so it can be tested without a terminal. Pass a Style with colors off to
get plain text.
"""

import re
import json
import time
from pathlib import Path
from typing import Any

from agent_core.permissions import PermissionPolicy, RiskLevel
from agent_core.tools.registry import ToolRegistry
from agent_core.ui import Style, pad, panel, screen_width, wrap
from agent_core.usage import LimitWindow, SessionStats, UsageStats

COMMANDS = (
    ("🧰", "/tools [on|off <name>]", "the tools Orwick can use; switch one for this session"),
    ("🔒", "/policy", "what runs without asking, and what asks you"),
    ("🔧", "/config", "every setting, where it comes from, what it means"),
    ("🤖", "/backend [name]", "show or switch the AI service (codex, claude, openai)"),
    ("📊", "/status", "usage: this session, tokens, and your subscription limits"),
    ("🐞", "/debug [on|off]", "show or switch the step-by-step trace"),
    ("👣", "/trace [on|off]", "show or hide the short line per tool call"),
    ("✨", "/clear", "start a new session in this folder (this one stays saved)"),
    ("🗂", "/sessions [delete <id>]", "saved sessions in this folder; delete one (asks first)"),
    ("🎯", "/feature [KEY|off]", "link this session to a ticket, e.g. /feature PROJ-16765"),
    ("🗺", "/map [focus] [--name <n>] [--with <dirs>]", "find the flows this service offers and map how they connect (service map)"),
    ("🔗", "/related [add|remove <folders>|clear]", "other projects this session may read (never change)"),
    ("🏷", "/name [text|off]", "show or set this session's name (shown in /sessions and the app)"),
    ("↩️", "/resume <id>", "continue a saved session"),
    ("📜", "/history", "show this session's conversation again"),
    ("🎓", "/skills [on|off <name>]", "the skills Orwick can load; switch one for this session"),
    ("▶️", "/do <skill> <action>", "run a skill's action, e.g. /do feature-docs read-ticket"),
    ("🗜", "/compact", "shorten the conversation now (the AI summarizes the older part)"),
    ("🧹", "/reset", "clear the conversation, keep this session"),
    ("❓", "/help", "this help"),
    ("🚪", "/exit", "quit (Ctrl-D works too)"),
)

TOOL_ICONS = {
    "list_folder": "📂", "read_file": "📖", "grep": "🔍",
    "edit_file": "📝", "write_file": "💾", "run_command": "💻",
}
DEFAULT_TOOL_ICON = "🔧"

RISK_LOOK = {  # icon, color
    RiskLevel.READ: ("🟢", "green"),
    RiskLevel.WRITE: ("🟡", "yellow"),
    RiskLevel.DANGEROUS: ("🔴", "red"),
}


def tool_summary(tool_count: int, backend: str, own: list[str], style: Style, mcp_count: int = 0) -> str:
    """ "6 Orwick tools + 3 MCP tools + 2 Claude tools (WebSearch, WebFetch)", ⚠ if one of the AI's is dangerous."""
    from agent_core.llm.own_tools import any_dangerous
    text = f"{tool_count} Orwick {_plural(tool_count, 'tool')}"
    if mcp_count:
        text += f" + {mcp_count} MCP {_plural(mcp_count, 'tool')}"
    if own:
        risky = any_dangerous(backend, own)
        part = f"{len(own)} {backend.capitalize()} {_plural(len(own), 'tool')} ({', '.join(own)})"
        text += " + " + (style(f"⚠ {part}", "yellow") if risky else part)
    return text


def banner(name: str, backend: str, model: str, tool_count: int, debug: bool,
           workspace: Path, config_line: str, style: Style, own_tools: list[str] | None = None,
           mcp_count: int = 0) -> str:
    """The box printed at startup."""
    body = [
        f"🤖 {style(backend, 'bold')} · {model}    🐞 debug {style('on', 'magenta') if debug else 'off'}",
        f"🧰 {tool_summary(tool_count, backend, own_tools or [], style, mcp_count)}",
        f"📂 {style(_home_short(workspace), 'bold')}",
        f"🔧 {config_line}",
    ]
    return panel(f"⚓ {name}", body, style, footer="Type /help for commands.")


BACKEND_INFO = (
    ("codex", "ChatGPT subscription, through the codex CLI"),
    ("claude", "Claude subscription, through the claude CLI (clean: no skills, memory or hooks)"),
    ("openai", "OpenAI API, pay per token (needs OPENAI_API_KEY in .orwick/.env)"),
)


def backend_screen(current: str, model: str, style: Style) -> str:
    """/backend: which AI service answers, and the others to switch to."""
    body = []
    for name, info in BACKEND_INFO:
        if name == current:
            body.append(f"🟢 {style(pad(name, 8), 'bold', 'green')}{info}")
            body.append(f"   {style('in use · model: ' + model, 'dim')}")
        else:
            body.append(f"⚪ {pad(name, 8)}{style(info, 'dim')}")
    return panel("🤖 AI backend", body, style,
                 footer="Switch: /backend <name> [model]. The conversation is kept.")


def user_prompt(style: Style) -> str:
    """The label before what you type: "👤 You › " (plain "You: " without colors)."""
    return _speaker("👤", "You", "cyan", style)


def agent_label(style: Style) -> str:
    """The label before Orwick's answer: "🤖 Orwick › " (plain "Orwick: " without colors)."""
    return _speaker("🤖", "Orwick", "magenta", style)


def _speaker(icon: str, name: str, color: str, style: Style) -> str:
    """Icons only where colors are on, so logs and pipes stay plain text."""
    if not style.color:
        return f"{name}: "
    return f"{icon} {style(name, 'bold', color)} {style('›', 'dim')} "


def conversation_screen(messages: list[dict[str, Any]], style: Style,
                        earlier: list[dict[str, Any]] | None = None) -> str:
    """A saved conversation drawn the way it looked live: your messages, Orwick's
    answers, and a short line per tool call and result. Display only.

    ``earlier`` are messages compacting summarized for the AI; they are shown
    first, then a line marks where the AI's summary takes over.
    """
    from agent_core.compaction import is_summary

    out = [style("── Earlier in this session ──", "dim")]
    shown = 0
    marked = not earlier
    for message in [*(earlier or []), *messages]:
        role = message.get("role")
        content = str(message.get("content") or "")
        if is_summary(message) or (role == "assistant" and content == "Noted. I'll continue from there."):
            if not marked:
                out += ["", style(f"── compacted here: the AI now sees a summary of the "
                                  f"{len(earlier)} messages above ──", "dim")]
                marked = True
            continue
        if role == "user" and content.startswith("[Orwick] "):
            continue   # Orwick's own note to the AI (e.g. "wrap up"), not something you wrote
        if role == "user":
            out += ["", user_prompt(style) + content]
            shown += 1
        elif role == "assistant":
            if content and message.get("tool_calls"):
                out.append(style(f"  {_one_line(content, 300)}", "dim"))
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                out.append(f"  → {function.get('name', '?')}({_one_line(str(function.get('arguments') or ''), 300)})")
            if content and not message.get("tool_calls"):
                out += ["", agent_label(style) + content]
        elif role == "tool":
            out.append(_tool_result_line(content))
    if not shown:
        return style("No messages yet in this session.", "dim")
    out += ["", style("── Continue below ──", "dim")]
    return "\n".join(out)


def _tool_result_line(content: str) -> str:
    """The short "← ok: ..." / "← error [...]" line the chat prints for a tool result."""
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        try:   # long text follows the JSON line (results.py), or it was cut short
            result = json.loads(content.split("\n", 1)[0])
        except json.JSONDecodeError:
            return f"  ← {_one_line(content, 200)}"
    if isinstance(result, dict) and result.get("ok"):
        return f"  ← ok: {_one_line(json.dumps(result.get('data'), default=str, ensure_ascii=False), 200)}"
    if isinstance(result, dict) and isinstance(result.get("error"), dict):
        error = result["error"]
        return f"  ← error [{error.get('type')}]: {_one_line(str(error.get('message')), 200)}"
    return f"  ← {_one_line(content, 200)}"


def _one_line(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def sessions_screen(saved: list[dict[str, Any]], current: str, style: Style,
                    limit: int = 15, now: float | None = None) -> str:
    """/sessions: saved sessions in this folder, most recently used first."""
    now = time.time() if now is None else now
    if not saved:
        return panel("🗂 Saved sessions", [style("None yet. Sessions are saved as you chat.", "dim")], style)
    body = []
    for meta in saved[:limit]:
        mark = style("● this session", "green") if meta["id"] == current else \
            style("open in another Orwick", "yellow") if meta.get("open_elsewhere") else ""
        when = time.strftime("%b %d %H:%M", time.localtime(meta.get("updated_at") or 0))
        count = meta.get("messages", 0)
        key = f"  {style(meta['feature'], 'magenta')}" if meta.get("feature") else ""
        body.append(f"{style(meta['id'], 'cyan')}  {style(when, 'dim')}  "
                    f"{count} {_plural(count, 'message')}{key}  {mark}".rstrip())
        body.append(f"   {meta.get('name') or meta.get('title') or style('(no title)', 'dim')}")
    if len(saved) > limit:
        body.append(style(f"… and {len(saved) - limit} older", "dim"))
    return panel("🗂 Saved sessions", body, style) + "\n" + style("Continue one with /resume <id>.", "dim")


def help_screen(config_file: str, style: Style) -> str:
    """/help: the chat commands and how approval works."""
    size = max(len(cmd) for _, cmd, _ in COMMANDS) + 2
    commands = [f"{icon}  {style(pad(cmd, size), 'bold', 'cyan')}{text}" for icon, cmd, text in COMMANDS]
    approval = [
        f"🟢 {style('Looking', 'bold')} (list, read, search) runs without asking.",
        f"🟡 {style('Changing files', 'bold')} asks you first and shows the diff.",
        f"🔴 {style('Running commands', 'bold')} asks you first, then runs in a sandbox:",
        "   no internet, writes only in this folder, no home folder or secrets.",
        f"👉 Only {style('y', 'bold', 'green')} approves. Enter, n or Ctrl-C means no.",
    ]
    return "\n".join([
        panel("💬 Commands", commands, style),
        "",
        panel("🔒 What asks you first", approval, style,
              footer=f"Settings in {config_file} reload automatically."),
    ])


def tools_screen(registry: ToolRegistry, style: Style, disabled: Any = (),
                 session_changed: set[str] | frozenset[str] = frozenset()) -> str:
    """/tools: each tool with its risk, parameters and what it does; then the ones switched off.

    ``session_changed``: tools switched on or off for this session only.
    """
    tools = registry.list_tools()
    off_line = ""
    if disabled:
        off_line = style("⏸  Switched off: " + ", ".join(
            tool.name + (" (this session)" if tool.name in session_changed else "") for tool in disabled), "yellow")
    if not tools:
        message = "No tools." if not disabled else \
            "Every tool is switched off."
        return panel("🧰 Tools", [message] + ([off_line] if off_line else []), style)
    text_width = screen_width() - 8
    name_size = max(len(tool.name) for tool in tools) + 2
    body: list[str] = []
    for tool in tools:
        risk_icon, color = RISK_LOOK.get(tool.risk_level, ("⚪", "gray"))
        asks = "runs without asking" if tool.risk_level == RiskLevel.READ else "asks you every time"
        icon = TOOL_ICONS.get(tool.name, DEFAULT_TOOL_ICON)
        mark = style("  · on for this session", "magenta") if tool.name in session_changed else ""
        body.append(f"{icon} {style(pad(tool.name, name_size), 'bold')}"
                    f"{risk_icon} {style(str(tool.risk_level), color)} · {asks}{mark}")
        params = ", ".join(f"{name}{'?' if name in tool.optional else ''}" for name in tool.parameters)
        body.append(style(f"   ({params})", "cyan"))
        for line in wrap(_first_sentence(tool.description), text_width):
            body.append(style("   " + line, "dim"))
        body.append("")
    body.pop()
    if off_line:
        body += ["", off_line]
    return panel(f"🧰 Tools ({len(tools)})", body, style,
                 footer="? = optional. /tools off <name> or /tools on <name> changes this session only.")


def actions_screen(skills_on: Any, style: Style) -> str:
    """/do without a valid action: what can be run."""
    body = []
    for skill in skills_on:
        for action in skill.actions:
            asks = f"  asks: {', '.join(i.label for i in action.inputs)}" if action.inputs else ""
            body.append(f"{style(action.command(skill.name), 'bold')}  {action.label}{style(asks, 'dim')}")
    if not body:
        body = [style("No skill that's on offers actions.", "dim")]
    return panel("▶️  Actions", body, style, footer="Run one with /do <skill> <action>.")


def skills_screen(found: Any, on: Any, style: Style) -> str:
    """/skills: every skill, whether it's on, and where it comes from."""
    if not found:
        return panel("🎓 Skills", [style("No skills. Add one in ~/.orwick/skills/<name>/SKILL.md.", "dim")], style)
    names_on = {s.name for s in on}
    body = []
    for skill in found:
        mark = style("on", "green") if skill.name in names_on else style("off", "gray")
        body.append(f"{style(skill.name, 'bold')}  {mark}  {style(skill.origin, 'dim')}")
        body.append(style("   " + " ".join(skill.description.split())[:120], "dim"))
        for action in skill.actions:
            body.append(style(f"   ▶ {action.command(skill.name)}  ({action.label})", "cyan"))
    return panel(f"🎓 Skills ({len(names_on)} on)", body, style,
                 footer='The AI sees only names and descriptions until it loads one. Setting: "skills".')


def own_tools_screen(backend: str, names: list[str], style: Style) -> str:
    """Part of /tools: the answering AI's own tools, which don't go through Orwick."""
    from agent_core.llm.own_tools import CATALOG, DANGEROUS
    catalog = CATALOG.get(backend)
    if catalog is None:
        return ""
    title = f"🌐 {backend.capitalize()}'s own tools ({len(names)})"
    if not names:
        return panel(title, [style("All off: it can only use Orwick's tools.", "dim")], style)
    body = []
    for name in names:
        tool = catalog[name]
        risky = tool.risk == DANGEROUS
        mark = style("⚠ dangerous · works without asking you", "yellow") if risky else style("safe", "green")
        body.append(f"{style(name, 'bold')}  {mark}")
        body.append(style(f"   {tool.what}", "dim"))
    return panel(title, body, style,
                 footer=f"They run inside {backend.capitalize()}, not through Orwick. Setting: {backend}_tools.")


def policy_screen(policy: PermissionPolicy, style: Style) -> str:
    """/policy: what each risk level may do."""
    rows = [
        (RiskLevel.READ, policy.allow_read, False, "list, read, search"),
        (RiskLevel.WRITE, policy.allow_write, True, "create and change files"),
        (RiskLevel.DANGEROUS, policy.allow_dangerous, True, "run commands (in the sandbox)"),
    ]
    body = []
    for level, allowed, asks, examples in rows:
        icon, color = RISK_LOOK[level]
        if not allowed:
            icon, verdict = "⛔", style("blocked", "red")
        else:
            verdict = "asks you every time" if asks else "runs without asking"
        body.append(f"{icon} {style(pad(str(level), 11), 'bold', color)}{pad(verdict, 21)}"
                    f"{style(examples, 'dim')}")
    return panel("🔒 Permission policy", body, style,
                 footer="Only y approves, and each yes covers exactly one action.")


def _first_sentence(text: str) -> str:
    """Up to the first ". " that starts a new sentence (so "e.g. pytest" isn't cut)."""
    match = re.search(r"\.\s+(?=[A-Z])", text)
    return text if match is None else text[: match.start() + 1]


def _home_short(path: Path) -> str:
    home = Path.home()
    if path == home:
        return "~"
    return f"~/{path.relative_to(home)}" if path.is_relative_to(home) else str(path)


# --- /status ------------------------------------------------------------------------

BACKEND_LOOK = {  # icon, display name, how it is paid
    "claude": ("🟠", "Claude", "subscription"),
    "codex": ("🟢", "Codex", "subscription"),
    "openai": ("🔵", "OpenAI API", "pay per token"),
}
BAR_WIDTH = 20


def status_screen(agent: Any, backend: str, model: str,
                  codex_limits: tuple[list[LimitWindow], float | None],
                  style: Style, now: float | None = None,
                  budget: int | None = None, warn_percent: int = 50) -> str:
    """/status: this session's activity, and usage of the backend in use."""
    now = time.time() if now is None else now
    session = agent.session
    parts = [_session_panel(agent, session.stats, now, style, budget, warn_percent), "",
             _backend_panel(backend, model, session.for_backend(backend), codex_limits, now, style)]
    others = [(name, usage) for name, usage in session.backends.items()
              if name != backend and usage.requests]
    if others:
        body = [f"{BACKEND_LOOK.get(name, ('⚪', name, ''))[0]} {style(pad(name, 8), 'bold')}"
                f"{usage.requests} {_plural(usage.requests, 'request')} · new in {_tokens(usage.new_input_tokens)} · "
                f"out {_tokens(usage.output_tokens)}" for name, usage in others]
        parts += ["", panel("🔀 Also used this session", body, style)]
    return "\n".join(parts)


def _session_panel(agent: Any, stats: SessionStats, now: float, style: Style,
                   budget: int | None = None, warn_percent: int = 50) -> str:
    since = time.strftime("%H:%M", time.localtime(stats.started))
    body = [
        f"⏱  {style(_duration(now - stats.started), 'bold')} running · since {since}",
        f"💬 {style(str(stats.messages), 'bold')} {_plural(stats.messages, 'message')}   "
        f"🔁 {style(str(stats.steps), 'bold')} AI {_plural(stats.steps, 'step')}   "
        f"🧰 {style(str(stats.tool_calls), 'bold')} tool {_plural(stats.tool_calls, 'call')}",
    ]
    if stats.tool_calls:
        body.append(f"   {style(f'✓ {stats.tools_ok} ok', 'green')}   "
                    f"{style(f'✗ {stats.tools_failed} failed or refused', 'red' if stats.tools_failed else 'dim')}")
        body.append("   " + style(" · ".join(f"{name} {count}" for name, count in stats.tools.most_common()), "dim"))
    if stats.approvals_asked:
        body.append(f"✋ approvals  {stats.approvals_asked} asked · "
                    f"{style(f'{stats.approvals_yes} yes', 'green')} · {stats.approvals_no} no")
    chars = sum(len(str(m.get("content") or "")) for m in agent.messages)
    body.append(f"🧠 history  {len(agent.messages)} {_plural(len(agent.messages), 'message')} · {chars:,} characters "
                + style(f"(~{_tokens(chars // 4)} tokens)", "dim"))
    if budget:
        from agent_core.compaction import fill
        level = fill(agent.messages, budget)
        color = fill_color(level.percent, warn_percent)
        body.append(f"📏 conversation {_bar(min(level.percent, 100), style, color)} "
                    f"{style(f'{level.percent}%', 'bold', color)} of its budget "
                    + style(f"(~{_tokens(level.tokens)} of {_tokens(budget)} tokens)", "dim"))
    return panel("📊 Session", body, style)


def fill_color(percent: int, warn_percent: int) -> str:
    """Green until the warning point, yellow until summarizing starts, red after."""
    from agent_core.compaction import SUMMARIZE_AT
    return "green" if percent < warn_percent else "yellow" if percent < SUMMARIZE_AT * 100 else "red"


def fill_warning(percent: int, tokens: int, budget: int, style: Style) -> str:
    """The line the chat prints when the conversation crosses the warning percent."""
    from agent_core.compaction import SUMMARIZE_AT, TRIM_AT
    color = "red" if percent >= SUMMARIZE_AT * 100 else "yellow"
    return style(f"📏 This conversation is at {percent}% of its budget (~{_tokens(tokens)} of "
                 f"{_tokens(budget)} tokens).", "bold", color) + "\n" + style(
        f"   From {TRIM_AT:.0%} Orwick trims old tool output, from {SUMMARIZE_AT:.0%} it summarizes the "
        f"older part. /compact does it now, /clear starts a new session.", "dim")


def _backend_panel(backend: str, model: str, usage: UsageStats,
                   codex_limits: tuple[list[LimitWindow], float | None],
                   now: float, style: Style) -> str:
    icon, name, paid = BACKEND_LOOK.get(backend, ("⚪", backend, ""))
    limits, note = usage.limits, usage.limits_note
    if backend == "codex" and codex_limits[0]:
        limits = codex_limits[0]
        note = f"last known · from a Codex session {_duration(now - codex_limits[1])} ago" \
            if codex_limits[1] else "last known"

    body = [style("Subscription limits", "bold") + (f"   {style(note, 'dim')}" if note else "")]
    if limits:
        for window in limits:
            color = _level_color(window.used_percent)
            mark = "⛔ " if window.status == "rejected" else ""
            reset = f"resets {_when(window.resets_at, now)}" if window.resets_at else ""
            body.append(f"{pad(window.name, 13)}{_bar(window.used_percent, style, color)} "
                        f"{style(f'{window.used_percent:>3.0f}%', 'bold', color)}  "
                        f"{mark}{style(reset, 'dim')}")
    elif backend == "claude":
        body.append(style("Shown after your first message: Claude reports them with each reply.", "dim"))
    elif backend == "codex":
        body.append(style("None recorded yet. They come from Codex sessions you run yourself.", "dim"))
    else:
        body.append(style("None: the API is billed per token.", "dim"))

    body.append("")
    body.append(style("This session", "bold"))
    if not usage.requests:
        body.append(style("No requests yet.", "dim"))
    else:
        body.append(f"🔁 {usage.requests} {_plural(usage.requests, 'request')} · "
                    f"{usage.seconds / usage.requests:.1f}s average")
        body.append(f"🔢 new in {style(_tokens(usage.new_input_tokens), 'bold')}"
                    + (style(f" ({_tokens(usage.cache_write_tokens)} cache write)", "dim")
                       if usage.cache_write_tokens else "")
                    + f" · out {style(_tokens(usage.output_tokens), 'bold')}")
        if usage.cached_tokens:
            body.append(style(f"   + {_tokens(usage.cached_tokens)} re-read from the cache "
                              "(the conversation, sent again with each request; cheap)", "dim"))
        body.append(f"   = {style(_tokens(usage.input_tokens + usage.output_tokens), 'bold')} total "
                    + style("(everything sent and received, re-reads included: the figure Claude Code shows)", "dim"))
        if usage.context_tokens and usage.context_window:
            percent = 100 * usage.context_tokens / usage.context_window
            body.append(f"🧠 context {_tokens(usage.context_tokens)} of {_tokens(usage.context_window)}  "
                        f"{_bar(percent, style, _level_color(percent), width=10)} "
                        f"{'<1' if 0 < percent < 1 else f'{percent:.0f}'}%")
        elif usage.context_tokens:
            body.append(f"🧠 last request {_tokens(usage.context_tokens)} tokens")
        if usage.cost_usd is not None:
            body.append(f"💲 ${usage.cost_usd:.2f} at API prices "
                        + style("· covered by your subscription, not charged", "dim"))
    if backend == "codex" and usage.requests:
        body.append(style("Codex adds ~25k tokens of its own instructions to every request "
                          "(mostly cached).", "dim"))
    if backend == "openai" and usage.requests:
        body.append(style("Billed per token on your OpenAI account.", "dim"))

    title = f"{icon} {name} · {usage.model or model}" + (f"  ({paid})" if paid else "")
    return panel(title, body, style, footer="Limits are for your whole account, not just Orwick.")


def _plural(count: int, word: str) -> str:
    return word if count == 1 else word + "s"


def _bar(percent: float, style: Style, color: str, width: int = BAR_WIDTH) -> str:
    filled = max(0, min(width, round(percent / 100 * width)))
    if percent > 0 and filled == 0:
        filled = 1  # show that something is used
    return style("█" * filled, color) + style("░" * (width - filled), "gray")


def _level_color(percent: float) -> str:
    return "green" if percent < 50 else "yellow" if percent < 80 else "red"


def _tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M".replace(".0M", "M")
    if count >= 10_000:
        return f"{count / 1000:.0f}k"
    if count >= 1000:
        return f"{count / 1000:.1f}k"
    return str(count)


def _duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _when(timestamp: float, now: float) -> str:
    """'17:30 (in 2h 10m)' today, 'Sun 12:00 (in 4d 3h)' later."""
    moment = time.localtime(timestamp)
    same_day = time.strftime("%Y%m%d", moment) == time.strftime("%Y%m%d", time.localtime(now))
    clock = time.strftime("%H:%M" if same_day else "%a %H:%M", moment)
    return f"{clock} (in {_duration(timestamp - now)})" if timestamp > now else clock
