# Orwick architecture

How Orwick works inside. For installing and using it, see [README.md](README.md).

## Contents

1. [Overview](#overview)
2. [Project layout](#project-layout)
3. [The agent loop](#the-agent-loop)
4. [AI backends](#ai-backends)
5. [Tools](#tools)
6. [The safety pipeline](#the-safety-pipeline)
7. [Configuration](#configuration)
8. [Debug tracer](#debug-tracer)
9. [Audit log](#audit-log)
10. [Error types](#error-types)
11. [Testing](#testing)
12. [Known limitations](#known-limitations)

---

## Overview

```
 you ──► cli.py ──► Agent.send(message)
                       │
                       ▼
            ┌──── agent loop (max_steps) ─────────────────────┐
            │  model.complete(history, tool schemas)           │
            │    (CodexCLIModel or OpenAIChatModel)            │
            │        │                                         │
            │        ├─ final answer? ──► return it            │
            │        │                                         │
            │        └─ tool calls? ──► for each call:         │
            │              parse JSON arguments                │
            │              ToolGateway.execute(...)  ◄── safety│
            │              append the result (size-capped)     │
            │              to the history, loop again          │
            └──────────────────────────────────────────────────┘
```

Each piece has one job:

| Piece | Job |
|---|---|
| **CLI** (`cli.py`) | Reads your input, handles `/commands`, loads config (and reloads it live), wires everything together |
| **Agent** (`agent.py`) | Keeps the conversation and runs the loop: ask the model, run tools, repeat |
| **Model adapter** (`llm/`) | Talks to the AI service and translates replies into a common format |
| **Gateway** (`gateway.py`) | The only path from a tool request to running it: validation, permission, approval, one-time token, execution |
| **Tools** (`tools/`, `tools/builtin/`, `~/.orwick/tools/`, `.orwick/tools/`) | The functions, described by `tool.json` files |
| **Executors** (`execution/`) | Run a tool in a thread or a killable subprocess, with a timeout |
| **Audit / tracer** (`audit.py`, `debug.py`) | Record every step to a file; show it on screen in debug mode |

Everything is injected: `cli.build_model()` and `cli.build_agent()` are the
only places the pieces are connected. Tests swap in their own pieces.

---

## Project layout

```
orwick/                               ← the project (also a working directory)
├── .orwick/                          ← the agent's own folder (invisible to its tools)
│   ├── config.json                  settings, reloaded live
│   ├── .env / .env.example          secrets and fallback values
│   ├── sessions/<id>/               one per conversation: conversation, meta, status, audit log, lock
│   ├── logs/audit.jsonl             shared audit log (only if audit_log points here)
│   └── tools/<name>/tool.json       a project's own extra tools (optional)
├── pyproject.toml                   deps + the `orwick` command
├── src/agent_core/
│   ├── cli.py                       chat loop, /commands, live config reload, wiring
│   ├── agent.py                     Agent: conversation + bounded tool loop
│   ├── llm/
│   │   ├── __init__.py              ChatModel protocol, ModelResponse, ToolCall
│   │   ├── codex_model.py           CodexCLIModel (ChatGPT subscription)
│   │   ├── claude_model.py          ClaudeCLIModel (Claude subscription, long-running)
│   │   └── openai_model.py          OpenAIChatModel (API key)
│   ├── gateway.py                   ToolGateway: the safety pipeline
│   ├── config.py                    Settings: flags > config.json > .env > ~/.orwick/config.json > defaults
│   ├── config_view.py               the /config screen (grouped settings)
│   ├── screens.py                   startup banner, /help, /tools, /policy, /backend, /status
│   ├── usage.py                     /status numbers: session counters, per-backend usage, limits
│   ├── sessions.py                  saved sessions: .orwick/sessions/<id>/, resume, lock, cleanup
│   ├── compaction.py                long conversations: trim old tool output, summarize, /compact
│   ├── mcp.py                       MCP servers: usual mcpServers format, stdio client, tools as mcp__server__tool
│   ├── skills.py                    skills: discovery, the small index, on/off
│   ├── skills_builtin/<name>/SKILL.md built-in skills (feature-docs: five-tab feature docs)
│   ├── llm/own_tools.py             the AI's own tools (Claude's, Codex's): catalog, defaults, rules
│   ├── status_file.py               status.json: the /status numbers + tools as JSON, for apps
│   ├── ui.py                        colors, icons, left-bordered panels (no dependencies)
│   ├── debug.py                     Tracer: numbered pipeline trace
│   ├── permissions.py               RiskLevel, PermissionPolicy
│   ├── approvals.py                 terminal / deny-all approval providers
│   ├── authorization.py             single-use action tokens
│   ├── retry.py                     RetryPolicy, execute_with_retry
│   ├── audit.py                     JSONL / in-memory audit loggers, redaction
│   ├── results.py                   ToolResult, how results are sent to the model
│   ├── errors.py                    exception types
│   ├── sandbox.py                   macOS Seatbelt profile for run_command
│   ├── execution/
│   │   ├── thread_executor.py       run_in_thread (deadline)
│   │   ├── process_executor.py      killable subprocess (process group)
│   │   └── process_worker.py        subprocess entry point
│   └── tools/
│       ├── __init__.py              load_all_tools(): built-in + ~/.orwick/tools + .orwick/tools
│       ├── builtin/<name>/tool.json the 6 standard tools, shipped with Orwick
│       ├── manifest.py              tool.json loader and checks
│       ├── registry.py              ToolDefinition, ToolRegistry, argument validation
│       ├── files.py                 list_folder, read_file, grep, write/edit_file + workspace rules
│       └── commands.py              run_command: sandbox, clean env, timeout, output cap
└── tests/
```

---

## The agent loop

- **The conversation is a list of OpenAI-style messages:** `system`, `user`,
  `assistant` (possibly with `tool_calls`), and `tool`. `Agent.messages`
  holds it, and `/reset` clears it.
- **The model has no memory.** The whole conversation is sent again on every
  step. Long chats get slower and cost more tokens.
- **One user message is one run.** It gets a `run_id`, and every audit event
  in that run carries it. A run may take several steps (model calls), at most
  `max_steps`.
- **Every tool call gets a reply.** The API requires a `tool` message for each
  `tool_call_id`, so errors come back as tool results, not exceptions. If a
  run is interrupted (Ctrl-C), the agent fills in `interrupted` results so the
  next message still works.
- **Tool results are untrusted data.** The system prompt says so, and each
  result is cut at `max_tool_result_chars`.

---

## AI backends

All backends implement the `ChatModel` protocol
(`complete(messages, tools) -> ModelResponse`), so the loop doesn't care
which one runs.

| Backend | Pays with | Needs | Tool calling |
|---|---|---|---|
| `codex` (default) | Your ChatGPT subscription | `codex login` | One JSON "decision" per step (final answer **or** one tool call), forced with `--output-schema` |
| `claude` | Your Claude subscription | Claude Code, logged in | Same JSON decision as codex, forced with `--json-schema`; one long-running process |
| `openai` | API credits (billed separately) | `OPENAI_API_KEY` in `.orwick/.env` | Native function calling, several tools per step |

Switching during a session: `/backend <name> [model]` replaces the model
client and keeps `agent.messages` (the history is backend-neutral, in OpenAI
message format). The choice beats flags and `config.json` for the rest of the
session; `/config` shows its source as `/backend command`. A rebuild that
doesn't change backend, model, timeout or key (e.g. `/debug on`, or another
setting reloaded) keeps the same client, so a running claude process and its
conversation survive. On exit the client is closed (`close_model`).

### How the Codex backend works (`llm/codex_model.py`)

- `codex exec` keeps no state, so each call sends the **whole conversation**
  on stdin: the system prompt, the reply rules, the tool list (JSON schemas),
  and the transcript. Tool results are wrapped in `<tool_result>` and marked
  as untrusted.
- `--output-schema` forces the reply into
  `{"type", "answer", "tool", "arguments_json"}`, which becomes a normal
  `ModelResponse`.
- **Codex is a full agent with its own shell, so it's locked down.** Each call
  runs with `--sandbox read-only` in a fresh empty temporary folder, with
  `--ephemeral` (no saved session), `--ignore-user-config` (no MCP servers or
  plugins from `~/.codex`) and `--ignore-rules`. Real actions happen only
  through Orwick's own tools and gateway.
- Cost: about 6 s per step, and about 25,000 input tokens of Codex's own
  instructions per call (mostly cached).

### Usage numbers for `/status` (`usage.py`)

- **Session counters** come from `CountingAuditLogger`, which wraps the JSONL
  audit logger and counts every event on its way (`agent.started` = messages,
  `model.requested` = AI steps, `tool.requested` per tool, approvals,
  failures). Nothing else has to remember to count.
- **Per-backend usage** (`UsageStats`) is filled by each adapter:

  | Backend | Tokens | Limits | Cost |
  |---|---|---|---|
  | claude | `result.usage` (input + cache read + cache write, output); context window from `modelUsage` | live, from `rate_limit_event` (`unifiedWindows`: 5-hour, 7-day) | `total_cost_usd`, an API-price estimate; not charged on a subscription |
  | codex | `turn.completed.usage` | not reported by `codex exec`; `/status` reads the newest `rate_limits` snapshot from `~/.codex/sessions/**/rollout-*.jsonl` (only that object, from the end of the 5 newest files) and labels it "last known" | none |
  | openai | `completion.usage` | none | billed per token |

- `SessionUsage` (session counters + one `UsageStats` per backend) belongs to
  the CLI and is passed to every rebuilt agent, so `/backend` switches and
  config reloads keep the numbers. None of it is ever sent to the AI.
- **Sessions** (`sessions.py`): a session is one conversation, in
  `.orwick/sessions/<id>/` with ids like `2026-11-12_114105_a3f9`. Before each
  prompt (and on exit) the CLI saves `conversation.json` (the history without
  the system prompt, which is rebuilt on resume), `meta.json` (title = first
  user message, started/updated times, message count) and `status.json`.
  `--resume <id>` / `/resume <id>` load the history into a fresh agent and
  print it with `screens.conversation_screen` (like the live chat: messages,
  answers, a `→`/`←` line per tool call and result; display only), as does
  `/history`; the
  Claude adapter sees a history it doesn't know and replays it once, Codex
  resends history anyway. The `lock` file is held with an OS lock (`flock`) for
  as long as the session is open: another Orwick can't open it (`SessionBusy`,
  after waiting up to 3 seconds for one that is just closing). The system frees
  the lock however the holder ends (exit, crash, kill, or a zombie the parent
  never collected), so a stale lock can't block anyone; the process id written
  in the file is only for the message. SIGHUP (closed terminal window or tab)
  and SIGTERM end the CLI like `/exit`: the session is saved and released. On exit the lock is released and a session with no user message
  is deleted. Cleanup keeps the 50 most recently used (by `updated_at`, then
  name, then folder birth time): only real folders with session-id names, never
  symlinks, the current one, or one open elsewhere. `--session-id` names a new
  session (the Mac app does); ids not shaped like an id are refused, so they
  can't point outside the folder. A `.gitignore` (`*`) keeps sessions out of
  git; the folder is inside `.orwick/`, so tools can't see it.
- **`status.json`** (`status_file.py`): before each prompt, the CLI writes the
  /status numbers plus the tool list (name, risk, description, uses) and the
  session id as JSON (`format: 1`; `skills: null` until skills exist). Counts
  and usage only, never message text. Written atomically with mode 0600.
  Codex's last-known limits are re-read at most every 5 minutes. A write
  failure prints `[status] ...` and never stops the chat.
- **`/clear` and `/resume <id>`** open the next session (new, or saved), writes the old session's last
  `status.json` with `continued_in: <new id>` (so an app watching it can
  follow), closes the model client (the Claude process ends), drops `/backend`
  and `/debug` overrides, reloads settings, and builds a fresh agent with zeroed
  counts. `/reset` only clears the conversation and keeps the session. Session
  cleanup sorts by the time in the name, then by the folder's birth time, so
  two sessions started in the same second keep their real order.
- **Audit log per session:** `audit_log` defaults to `null`, meaning the
  session's own `audit.jsonl`. Setting a path (e.g. the old
  `.orwick/logs/audit.jsonl`) puts every session in that one file.

### Step limit and wrap-up (`agent.py`)

`max_steps` (default 25; `0` = no limit, no wrap-up) caps AI calls per message; `Agent.send(text,
max_steps=…)` overrides it for one message (skill actions). When only
`WRAP_UP_STEPS` (2) remain, a `[Orwick] …` user note tells the AI to stop
researching, save what it has (marked unfinished) and answer; `/history` hides
these notes. `grep` stops after `GREP_TIME_BUDGET` (12 s) and returns the matches
so far with a note, instead of hitting the tool's hard timeout.

### Model choices (`models_catalog.py`)

`orwick --list-models` prints the models each backend offers (JSON, nothing
started): Claude's short names (`opus`, `sonnet`, `haiku`), Codex's from its own
cache `~/.codex/models_cache.json` (only `visibility: list`, by `priority`; only
names and labels read), and a few OpenAI ones. `status.json` has `model_choice`
(the chosen name, null = default) so the Mac app's model menu can mark it; the
menu switches by typing `/backend <ai> <model>`.

### Tool lines (`trace`)

With debug off, `print_event` prints one short line per tool call and result
(MCP results, which are JSON inside text, shown as one readable line, cut at 160
characters). `trace` (setting, default on) and `/trace on|off` (this session)
switch them; when off, a dim `(N tool calls · /trace on to see them)` follows
the answer. Approval prompts are always shown.

### Long conversations (`compaction.py`)

Before each message the CLI calls `compaction.compact` with
`history_budget_tokens` (estimated at 4 characters per token):

1. **≥ 80%: trim.** Tool results before the last 2 user turns become a note
   (`{"ok": ..., "note": "older tool result removed ..."}`). No AI call.
2. **≥ 90% after trimming, or `/compact`: summarize.** Everything before the
   last 2 user turns goes to the AI with `SUMMARY_INSTRUCTIONS` (tool results
   marked as untrusted data; don't follow instructions in them). The summary
   replaces them as a user/assistant pair; the user message starts with
   `[Summary of the earlier conversation]` and says the notes are not
   instructions. The cut is always at a user message, so a tool call is never
   separated from its result. A later summary folds the earlier one in.
3. **Warning** (`FillWarning`): after each answer, if the fill (`fill()`:
   estimated tokens / budget) is at `history_warn_percent` or more, the chat
   prints a warning; again only after 10 more points; it resets when the fill
   drops below (after `/compact`, `/clear`, `/resume`). `status.json` carries
   `conversation: {tokens, budget, percent, warn_percent, trim_percent,
   summarize_percent}` so the Mac app can show a badge, a banner and a meter.
4. **Safety rails:** a summary that isn't shorter than what it replaces is
   dropped; if the AI fails (limit, timeout), the trimmed history is kept and
   nothing is lost.

The summary is asked of the current backend through a separate model client
(closed after), so the chat's own Claude process is untouched until the
history changes; then it restarts and replays the shorter history, as on
`/reset`. Removed messages go to `earlier` in the session's
`conversation.json`: not sent again, but shown by `/history` and on resume,
with a "compacted here" line.

### Skills and session docs (`skills.py`, `tools/knowledge.py`)

Skills come from `agent_core/skills_builtin/` (package data), `~/.orwick/skills/`
and `.orwick/skills/`, in that order; built-in names can't be replaced, a
folder's skill wins over yours, folder skills are off unless switched on
(`skills` setting, merged like `tools`). `read_header` reads only the first
4 KB of each `SKILL.md`. `index()` is what the AI always sees: one line per
switched-on skill (description cut to 100 characters), at most 1,600
characters, else names only; it is part of the system prompt (stable, so cache
friendly). `use_skill` (read) returns the instructions without the header (cut
at 12,000 characters) and lists the skill's other files; the switched-on skills
reach it through `ORWICK_SKILLS`. It isn't offered when no skill is on.

**Skill actions** (`ui.json`, `skills.read_actions`): up to 6 per skill, each
with an id, a label, an SF Symbol icon, inputs (name, label, optional regex
`pattern`, `uppercase`), an optional `max_steps` (1–100, for actions that
research a lot), an optional `feature` template (links the session to
that ticket key first) and a `prompt` (plain text, never a `/command`; may use only
declared inputs). A broken `ui.json` is a note; the skill still loads. `/do
<skill> <action>` asks for each input in the terminal (three tries, Enter
cancels), then sends the prompt as if typed. `status.json` lists the actions of
switched-on skills and a `state`: `working` is written just before the AI is
asked, `waiting` before each prompt, so the Mac app enables its buttons only
while Orwick waits for you.

`/feature KEY` stores a ticket key in the session's `meta.json` and adds one line
to the system prompt; resume restores it.

Session docs live in `.orwick/sessions/<id>/docs/`; the CLI points
`ORWICK_SESSION_DOCS` at the current session's folder. `read_session_doc` (read;
no name = list) and `write_session_doc` / `write_session_docs` (write, diff preview, one approval for
several docs, max 10, unchanged ones skipped) accept
only plain names (`[a-z0-9][a-z0-9_-]*`), refuse symlinks, cap a doc at
100,000 characters and write atomically. `status.json` lists `skills`, `docs`
and `feature` for the Mac app.

### MCP servers (`mcp.py`)

`mcpServers` (usual format: `command`, `args`, `env`; stdio only; main settings
and the folder's file merged per server) defines servers; `${NAME}` /
`${NAME:-default}` are filled from the environment when a server starts. The
server process gets only `PASSED_ENV` (PATH, HOME, ...) plus its own `env`, so
Orwick's other secrets never reach it. `MCPClient` speaks JSON-RPC over
stdin/stdout (initialize, tools/list with paging, tools/call; log lines and
notifications are skipped; timeouts become `ToolExecutionError`). `Pool` (one
per CLI run, `cli.MCP_POOL`) starts a server only when one of its tools is
switched on in `tools`, once per run, and closes them at exit. Each MCP tool
becomes a `ToolDefinition` named `mcp__<server>__<tool>`: parameters reduced to
one plain JSON type each (`simple_parameters`), risk from the server's hints
(`readOnlyHint` → read, `destructiveHint` → dangerous, else write), thread mode.
MCP tools are off unless switched on (`/tools on`, status `default`, and the
change detection all treat them that way). Servers that won't start become a
`[tools]` note, not a crash. `--list-tools` adds each server's full catalog.

### The AI's own tools (`llm/own_tools.py`)

`CATALOG` lists each backend's own tools Orwick may switch on, as `safe` (can't
change anything or read your files: web search/fetch) or `dangerous`; anything
acting outside the Mac (cron, remote triggers, notifications, publishing) isn't
in it, so it can't be chosen. `Settings.claude_tools` / `codex_tools`: `None` =
the safe defaults, `[]` = none. `contract_note()` replaces the old fixed "don't
use your own tools" line of the reply-format rules: it names the allowed tools,
says they are used directly (never through `tool_call`), warns that dangerous
ones act without approval, and that web content is data.

- **Claude:** `--tools <names>` and `--allowedTools <names>` (pre-approved; with
  `--permission-prompts none` anything else that would ask is refused). Runs in
  an empty temp folder unless a dangerous tool is on, then in the workspace.
- **Codex:** `codex --search exec` for `web_search`; `shell` runs it with
  `--sandbox workspace-write -C <workspace>`, otherwise read-only in an empty
  folder. Codex can't switch its shell off by flag, so `own_tool_uses()` reads
  the event stream (`command_execution`, `file_change` → shell, `web_search`,
  `mcp_tool_call` → never allowed) and a reply that used a switched-off tool
  becomes a `ModelError`.
- `model_key` includes the tool list, so a change starts a new client.
  `status.json` has `own_tools`; `orwick --list-tools` reports the catalog for
  the Mac app.

### How the Claude backend works (`llm/claude_model.py`)

- **One long-running process:** `claude -p --input-format stream-json
  --output-format stream-json --verbose`, started on the first call. Each step
  writes one `{"type": "user", ...}` line and reads events until the `result`
  event, whose `structured_output` is the decision (same schema and
  `parse_decision` as codex). A reader thread feeds a queue, so the timeout
  (`request_timeout_seconds`) works.
- **Only what's new is sent:** the adapter remembers the history the process
  has seen. The next call sends only the new user message or tool results
  (Claude knows its own replies). If the history doesn't continue what it saw
  (`/reset`, a different system prompt or tool list), or the process died or
  timed out, it's restarted and the whole transcript is replayed once.
- **A clean agent.** Claude Code brings its own tools, settings, skills,
  memory, hooks and plugins; all are switched off:

  | Flag / setting | Effect |
  |---|---|
  | `--tools ""` | no built-in tools (no shell, no file access); it can only propose Orwick's tools |
  | `--safe-mode` | no CLAUDE.md, skills, memory, hooks, plugins, custom commands |
  | `--setting-sources ""` | no user/project/local settings files (so installed plugins don't load) |
  | `--strict-mcp-config` | no MCP servers |
  | `--disable-slash-commands`, `--no-session-persistence`, `--permission-prompts none` | nothing else runs, nothing is saved |
  | `--system-prompt` | Orwick's prompt + decision rules + tool list, replacing Claude Code's own |
  | empty `orwick-claude-*` temp folder as cwd | no project is picked up |
  | `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` removed from its environment | always the subscription login, never pay-per-token |

- Cost: about 5 s for the first step, then 1–2 s per step (the conversation is cached).

---

## Tools

### `tool.json`

**Where tools come from** (`tools/__init__.py`, `load_all_tools`), in order:
built-in (`agent_core/tools/builtin/`, shipped as package data), yours
(`~/.orwick/tools/`), the folder's (`.orwick/tools/`). A tool with a built-in name
in a later layer is skipped with a note; the folder's tool replaces one of yours
with the same name (also noted). The notes are printed at startup as `[tools]`.
`orwick --list-tools` prints the merged list as JSON (`name`, `risk`,
`description`, `origin`) without starting a session; the Mac app uses it.

**The code sets the risk** (`manifest.TOOL_FUNCTIONS`): a `tool.json` that runs
something under `agent_core.tools.` may only name one of Orwick's tool functions,
at a risk level at least the one the code gives it, and may only use Orwick's
previews (`PREVIEW_FUNCTIONS`). Before this, a downloaded project's
`.orwick/tools` could run `write_file` declared as `read`, skipping approval.

Each tool is a folder `.orwick/tools/<name>/` with a `tool.json`. The folder
name must match the tool name.

```json
{
  "name": "read_file",
  "description": "Read a UTF-8 text file from the workspace.",
  "risk_level": "read",
  "mode": "process",
  "timeout_seconds": 5,
  "retry_attempts": 1,
  "parameters": {
    "path": {"type": "string", "description": "Path relative to the workspace folder"}
  },
  "required": ["path"],
  "run": {"python": "agent_core.tools.files:read_file"}
}
```

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | Tool name; must equal the folder name |
| `description` | yes | What the AI reads to decide when to use the tool. Say when to use it, and state defaults and limits |
| `risk_level` | yes | `read` (runs without asking), `write` or `dangerous` (ask every time) |
| `parameters` | yes | Each: `type` (`string`, `integer`, `number`, `boolean`, `array`, `object`), optional `description` and `enum` |
| `run` | yes | What to run. Today only `{"python": "agent_core.tools.<module>:<function>"}` |
| `preview` | no | A function with the same parameters that returns a text preview of the change (e.g. a diff). Shown in the approval prompt; see [The safety pipeline](#the-safety-pipeline) |
| `mode` | no | `thread` (default) or `process` (killable; read tools only) |
| `timeout_seconds` | no | Default 10 |
| `retry_attempts` | no | Default 1; above 1 only for read tools |
| `required` | no | Parameters the AI must give; the rest are optional. Default: none |

**Loading is strict** (`tools/manifest.py`). An unknown key, a wrong type, a
name that doesn't match its folder, a `required` entry that isn't a
parameter, a function outside `agent_core.tools.`, or a broken safety rule
stops startup with `Tool error: <file>: <what's wrong>`. A tool is never
half-loaded. Folders starting with `.` or `_` are skipped.

Tools are loaded once, at startup. After changing a `tool.json`, restart.

### Workspace and protected paths (`tools/files.py`)

- **Workspace:** every path the AI gives is relative to the workspace: the
  `workspace` setting, or else the folder Orwick was started in. A path that
  resolves outside it (`..`, an absolute path, a symlink pointing out) is
  refused.
- **Protected paths:** the CLI passes the tools a list of paths they must
  never see or touch: the whole `.orwick/` folder, plus a custom `--config` /
  `--env-file` if given. They're hidden from `list_folder` and refused by
  `read_file` and `write_file`. Without this, one read could send the API key
  to the AI, and one approved write could change the config (applied live)
  or plant a tool marked `read`.
- **Letter case:** macOS ignores letter case in file names (`.ORWICK/.env` is
  `.orwick/.env`), so protection compares **file identity** (device + inode)
  of the target and its parent folders, not just the spelling.
- **Secret files** (`SECRET_*` in `files.py`) are treated like protected paths:
  hidden from `list_folder` and `grep`, refused by every tool. Matched by name
  and folder, ignoring case, after symlinks are resolved:
  `.ssh/`, `.aws/`, `.gnupg/`, `.config/gh/`, `.docker/`; `.env`, `.env.*`,
  `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.kdbx`, `id_rsa*`-style keys, `.netrc`,
  `.npmrc`, `.pypirc`, `.pgpass`; and `.git/config`. Exceptions:
  `.env.example`, `.env.sample`, `.env.template`, `*.pub`.
- **No-write files** (`NO_WRITE_*`) can be read but not written, because they
  run code later, outside any approval. Folders: `.git/hooks/`, `.vscode/`,
  `.github/workflows/`. Names: `.zshrc`, `.zshenv`, `.zprofile`, `.zlogin`,
  `.bashrc`, `.bash_profile`, `.profile`, `.envrc`. Each rule carries the
  reason shown to the AI. Files that run only when you call them on purpose
  (`Makefile`, `package.json`, `conftest.py`) stay writable: editing them is
  normal work, so the diff approval is the check.
- **Read before write (versions):** `read_file` returns `version`, the first
  16 hex characters of the file's SHA-256. `write_file` (for an existing file)
  and `edit_file` require it, and refuse if it's missing or no longer matches
  the file on disk. This needs no shared state, which matters because
  `read_file` runs in a separate process. Files are read and written as raw
  UTF-8 bytes, so line endings are kept exactly.
- These settings reach the tools through the environment (`AGENT_WORKSPACE`,
  `AGENT_PROTECTED_PATHS`), so they also work in the process worker.

### Built-in tools

| Tool | Risk | Mode | Limits |
|---|---|---|---|
| `list_folder(path?)` | read | process | Folders first, with file sizes; at most 500 entries |
| `read_file(path)` | read | process | UTF-8 text only; at most 200 KB; returns `version` |
| `grep(pattern, path?, glob?, regex?, ignore_case?, max_matches?)` | read | process | Plain text unless `regex`; case-insensitive by default; stops after `max_matches` (default 50, max 500) with a note; skips binary files, files over 1 MB, secrets, `.git`, `.venv`, `node_modules`, caches |
| `edit_file(path, old_text, new_text, version, replace_all?)` | write | thread | `old_text` must appear exactly once (or `replace_all`); preview = diff |
| `write_file(path, content, version?)` | write | thread | `version` required to replace an existing file; preview = diff |
| `run_command(command, cwd?, timeout_seconds?)` | dangerous | thread | `/bin/sh -c` in the sandbox; timeout default 60 s, max 300 (it kills its own process group, so thread mode is safe here); output keeps the first 1,500 and last 5,000 bytes; preview = command + sandbox summary |

### Command sandbox (`sandbox.py`, `tools/commands.py`)

Approval can't make a command safe: `pytest` runs whatever code the project
contains, and that code isn't in the prompt. So `run_command` has four layers:

1. **Approval**, every time, with the full command (never shortened) and
   hidden characters made visible (`approvals.visible`).
2. **OS sandbox:** `sandbox-exec -p <profile> /bin/sh -c <command>`. The profile
   is generated per command and starts with `(deny default)`. Later rules
   win, which is how exceptions are layered:

   | Rule | Effect |
   |---|---|
   | process-exec/fork, signals and process info within the sandbox, sysctl-read, user lookup | programs run; children inherit the sandbox |
   | `file-read*` everywhere, then denied for `/Users`, `/Volumes`, `/private/tmp`, `/private/var/folders`, `$HOME` | personal data unreadable |
   | `file-read-metadata` on the parents of the workspace | programs can walk down to it |
   | `file-read*` on the workspace, the temp folder, `HOME_TOOLCHAIN_DIRS` (uv/pyenv Python, Rust, Node, Go modules) | builds and tests work |
   | `file-write*` on the workspace and the temp folder, plus `/dev/null`, `/dev/fd/*` | the only writable places |
   | deny content (`file-read-data`, `file-read-xattr`, `file-write*`) of protected paths and every `SECRET_*` rule, then re-allow `NOT_SECRET_NAMES` in the workspace, then re-deny protected paths | same secret list as the file tools; names and sizes stay visible so tools like pytest can skip them |
   | deny `file-write*` for `NO_WRITE_*` rules | hooks, editor tasks, CI, `.envrc`, shell files |
   | allow content in the temp folder | it holds only what the command created; symlinks to real secrets still fail (the target is checked) |
   | nothing for network or other Mach services | no internet, can't ask macOS to open apps or URLs |

   Names are matched ignoring case (`[eE][nN][vV]`), as on macOS.
   `.git/config` is a secret, but git can't run without reading it: if the
   workspace's `.git/config` holds no credentials (no `user:token@` URL, no
   `extraHeader`, no known token formats), it's added as a read-only exception.
3. **Clean environment:** only `PATH`, `LANG`, `LC_*`, `USER`, `LOGNAME`, `SHELL`
   are copied; `HOME` and `TMPDIR` point to a fresh `orwick-cmd-*` folder that is
   deleted afterwards. The Xcode developer `bin` is put first on `PATH`, so git
   doesn't go through the `/usr/bin/git` wrapper (which writes a cache outside
   the sandbox).
4. **Limits:** stdin is `/dev/null`; on timeout the process group gets SIGTERM,
   then SIGKILL; output is read in the background keeping only head and tail.

`sandbox.check_available()` really applies a trivial profile once. If that
fails (not macOS, no `sandbox-exec`, already inside another sandbox), both the
preview and the tool refuse, so you aren't even asked.

### Execution modes

| Mode | How it runs | On timeout |
|---|---|---|
| `thread` | Background thread in the same process | The agent stops waiting, but Python **can't kill a thread**, so the work may keep running. For fast, trusted code. |
| `process` | Separate Python subprocess in its own process group | The whole group gets SIGTERM, then SIGKILL, including child processes the tool started. For anything that might hang. |

In process mode, the worker only imports functions from allowed packages
(`agent_core.tools.`), anything the tool prints goes to stderr so it can't
fake the JSON result, and a result that can't be turned into JSON is an error.

---

## The safety pipeline

`ToolGateway.execute()` (`gateway.py`) is the **only** code path from a tool
request to running a function:

| # | Step | Fails with |
|---|---|---|
| 1 | **Lookup:** does the tool exist? | `unknown_tool` |
| 2 | **Validate** arguments against the schema (required, no extras, types, enums; `true` isn't an integer) | `missing_argument`, `unexpected_argument`, `invalid_argument_type`, `invalid_argument_value` |
| 3 | **Permission policy:** is this risk level allowed? | `permission_denied` |
| 4a | **Preview** (write/dangerous tools that declare one): the tool shows what it would change, e.g. a diff. If the preview fails (text not found, version mismatch), the call stops here, so you're never asked to approve something that can't work | the preview's own error, e.g. `permanent_failure` |
| 4b | **Human approval** (write/dangerous only), with the preview in the prompt. A crashing approver counts as "no". | `approval_denied`, `approval_required` |
| 5 | **Authorize:** issue a single-use token for the exact `(tool, arguments)`, **only after 3 and 4 pass** | `authorization_failed` |
| 6 | **Execute:** use up the token, then run with timeout and retry policy | `timeout`, `worker_crashed`, `execution_error`, … |

**Approval comes first.** No token exists until the policy and a human have
both said yes, so a refused call leaves nothing behind. The token guarantees
that what runs is exactly what was approved, and only once.

**Rules checked when a tool is defined** (`ToolDefinition`, also applied to
every `tool.json`):

- The name matches `^[a-zA-Z0-9_-]{1,64}$`, and the description isn't empty.
- Every parameter type is a supported JSON type.
- **Write and dangerous tools never retry automatically** (a retried write could happen twice).
- **Process mode is for read tools only**, and needs a module-level function.

**Retries:** only read tools, and only on `temporary_unavailable` or
`rate_limited` errors. The wait doubles each time. OpenAI API calls are
retried by the SDK (twice); Codex calls aren't retried.

---

## Configuration

**Priority** (`config.py`), highest first: command-line flag >
`.orwick/config.json` > `.orwick/.env` / environment > `~/.orwick/config.json`
(main settings, every folder) > built-in default. The main file is checked like
the folder file and may not set `workspace` (`FOLDER_ONLY_KEYS`): one shared
workspace would let every session work in the same place. The CLI reloads both
files when either changes (`--main-config` points elsewhere, for tests), and
both are on the protected list, so tools can't read or change them.
`null` in the JSON means "not set here". Every limit takes `0` for no limit
(steps, request timeout, tool result size, conversation budget, warning). `tools` (`{"name": true/false}`) is
the one setting that is merged rather than replaced: main settings first, then
the folder's file, the folder winning per tool. `build_agent` drops switched-off
tools from the registry, so they are never offered to the AI and the gateway
can't run them; `agent.disabled_tools` keeps them for `/tools`, `/config` and
`status.json` (`"enabled": false`). A change is applied by the live reload,
reported as `tools: run_command off`. On top of that, a session's own
`tools.json` (`/tools on|off <name>`, or the Mac app's Info panel) wins per tool
for that session only; it is part of the live-reload fingerprint, so a change
applies before the next message, and a broken file is ignored with a note.
`status.json` gives each tool `enabled` (what the session uses), `default` (the
settings' value) and `session` (its own switch, or null). Secrets (`OPENAI_API_KEY`) are read
only from `.env` / the environment.

| JSON key | Env variable | Default |
|---|---|---|
| `backend` | `AGENT_BACKEND` | `codex` |
| `model` | `AGENT_MODEL` | backend default (`gpt-4.1-mini` for `openai`) |
| `max_steps` | `AGENT_MAX_STEPS` | `10` |
| `request_timeout_seconds` | `AGENT_REQUEST_TIMEOUT_SECONDS` | `120` |
| `history_budget_tokens` | `AGENT_HISTORY_BUDGET_TOKENS` | `100000` |
| `history_warn_percent` | `AGENT_HISTORY_WARN_PERCENT` | `50` |
| `tools` | (none) | `{}`: every tool on |
| `max_tool_result_chars` | `AGENT_MAX_TOOL_RESULT_CHARS` | `8000` |
| `audit_log` | `AGENT_AUDIT_LOG` | the session's `.orwick/sessions/<id>/audit.jsonl` |
| `workspace` | `AGENT_WORKSPACE` | the folder you started in |
| `debug` | `AGENT_DEBUG` | `false` |
| (none) | `OPENAI_API_KEY` | empty; only for `openai` |

**Live reload** (`cli.py`):
- **When it checks:** before handling each input line, the CLI compares the config file's modified time and size with the last check.
- **On a change:** it reloads, rebuilds the agent with the new settings, copies the conversation over, and prints what changed.
- **On a broken file** (bad JSON, unknown key, wrong type): it prints the error and keeps the old settings.
- **`.env` is read once,** at startup, and copied. Reloads use that copy, so the values the CLI itself exports for the tools are never read back as your settings.

**`/debug on|off`** is a session override. It beats both `-d` and the
file's `debug` value until you quit. `/config` shows `[/debug command]` as
its source.

---

## Debug tracer

`debug.py`, enabled with `-d` or `/debug on`. Each message starts a
`REQUEST #n` banner, and each hop is a numbered box:

| Point | Who → whom | Shows |
|---|---|---|
| ① | You → Orwick | Your message |
| ② | Orwick | Step number (of max), the size of the conversation, the tools offered |
| ③ | Orwick → Claude | Exactly what is sent: the new message (Codex: `codex exec` command + full prompt; OpenAI: request JSON). When the claude process starts, also its command and the whole system prompt |
| ④ | Claude → Orwick | The raw reply: the event stream, token usage, time |
| ⑤ | Orwick | What the AI decided: final answer or tool call |
| ⑥ | Orwick → You | The final answer, steps and total time |

The parties are always **You**, **Orwick**, the AI by name (**Claude**, **Codex**,
**OpenAI**) and a **tool** by name. A title with an arrow is a message from one to
the other and is shown whole, never shortened; a title with only "Orwick" is what
Orwick does itself.

For a tool call, three boxes come between ⑤ and the next step:

```
Orwick → tool read_file · call            {"path": "notes.txt"}
Orwick · safety checks for tool read_file → requested … ✓ permission … ✓ done (12 ms)
tool read_file → Orwick · result           exactly what the AI gets in the next step
```

Border colors: cyan = your input, blue = to the model, green = from the
model, yellow = tools, gray = internal and safety checks, magenta = final
answer, red = failures. Secret-looking values are hidden. Colors are off
when the output isn't a terminal, or when `NO_COLOR` is set.

---

## Audit log

Every step is one JSON line in the session's `audit.jsonl` (`.orwick/sessions/<id>/`):

```json
{"timestamp": "...", "run_id": "…", "event_type": "tool.approval_granted",
 "details": {"tool": "write_file"}, "call_id": "…"}
```

`run_id` is one user message; `call_id` is one tool call. Values under keys
containing `password`, `secret`, `token`, `api_key`, `apikey` or
`authorization` are replaced with `***REDACTED***`.

| Area | Events |
|---|---|
| Agent | `agent.started`, `model.requested`, `model.responded`, `agent.finished`, `agent.failed`, `tool.invalid_arguments` |
| Gateway | `tool.requested`, `tool.rejected`, `tool.permission_checked`, `tool.preview_ready`, `tool.preview_failed`, `tool.approval_requested`, `tool.approval_granted`, `tool.approval_denied`, `tool.approval_failed`, `tool.authorization_issued`, `tool.authorization_failed`, `tool.execution_started`, `tool.execution_succeeded`, `tool.execution_failed` |
| Retries | `tool.attempt_started`, `tool.attempt_succeeded`, `tool.attempt_failed`, `tool.attempt_retry_scheduled` |

```bash
# follow live
tail -f .orwick/sessions/*/audit.jsonl | jq -c '{t: .timestamp[11:19], e: .event_type, d: .details}'
# replay one request
jq -c 'select(.run_id == "RUN_ID")' .orwick/sessions/*/audit.jsonl
# every failure
jq -c 'select(.event_type | test("failed|denied|rejected"))' .orwick/sessions/*/audit.jsonl
```

---

## Error types

The model gets every failure as a tool result:
`{"ok": false, "error": {"type": "...", "message": "..."}}`.

| Type | Source |
|---|---|
| `unknown_tool` | No tool by that name, or the worker couldn't import it |
| `invalid_arguments` | The arguments weren't valid JSON or not an object |
| `missing_argument` / `unexpected_argument` / `invalid_argument_type` / `invalid_argument_value` | Schema check failed |
| `permission_denied` | Blocked by the policy, or the worker's allowed-package rule |
| `approval_required` | The approver crashed or was unavailable |
| `approval_denied` | You answered anything other than `y` |
| `authorization_failed` | Token missing, used or mismatched (shouldn't happen) |
| `timeout` | The tool ran past `timeout_seconds` |
| `worker_crashed` / `worker_start_failed` / `invalid_worker_response` / `termination_unconfirmed` / `invalid_request` | Process-mode problems |
| `executor_unavailable` | Process tool, but no process executor configured |
| `temporary_unavailable` / `rate_limited` | Raised by the tool; retryable |
| `permanent_failure` | Raised by the tool (e.g. path outside the workspace, protected path); not retried |
| `execution_error` | Any other exception from the tool |
| `interrupted` | Ctrl-C before this call finished |

---

## Testing

```bash
.venv/bin/pytest              # everything (~2–3 s)
.venv/bin/pytest -k gateway   # one area
```

The tests make **no network calls** and never start `codex`. Test-only
doubles live in `tests/` (`tool_fixtures.py`, and `ScriptedModel` /
`RecordingApprover` in `conftest.py`) and are never part of the app.

| File | Covers |
|---|---|
| `test_gateway.py` | Pipeline order, approval-first tokens, approver failing closed, retries, audit IDs |
| `test_agent.py` | History, tool round trip, malformed arguments, step limit, interrupt repair, reset |
| `test_executors.py` | Thread/process timeouts, process-group kill, stdout isolation, allowed packages, crashes |
| `test_registry.py` | Definition rules, schema export, argument validation, the repo's tools |
| `test_manifest.py` | `tool.json` loading and every kind of mistake (unknown keys, wrong types, name mismatch, disallowed modules, safety rules) |
| `test_files.py` | File tools: workspace limits, size and UTF-8 checks, through the gateway |
| `test_file_safety.py` | Secret files hidden and refused (and look-alikes allowed), symlinks to secrets, no-write files |
| `test_grep.py` | Matches with line numbers, case/glob/path/regex options, `max_matches` cut-off, skipped secrets/binaries/noise folders |
| `test_edit_and_versions.py` | `edit_file` exact/ambiguous/missing text, `replace_all`, versions required and checked, line endings kept, diff previews |
| `test_preview_approval.py` | Preview passed to the approver; a failing preview means nobody is asked; prompt shows the diff, shortens long text, shows hidden characters |
| `test_commands.py` | `run_command`: argument checks, refusal without a sandbox, the profile, and real sandboxed runs (secrets, `.orwick`, home folder, writes outside, no-write files, network, environment, timeout kill, output cap, git with and without a token, through the gateway). Sandbox tests skip where it's unavailable |
| `test_safety_primitives.py` | Policy, single-use tokens, retry rules, redaction, truncation, approvers |
| `test_status_file.py` | `status.json`: atomic 0600 writes, counts/usage/limits in the JSON, never the conversation, Codex limits re-read at most every 5 minutes |
| `test_compaction.py` | Token estimate, trimming only older tool results, summarizing (system prompt and last turns kept, calls never split from results, tool results marked untrusted), the 60%/75% thresholds, failures and too-long summaries keep everything |
| `test_tool_layers.py` | Built-in tools everywhere, yours and the folder's added, built-in names can't be replaced, the folder wins over yours, a `tool.json` can't lower the risk or run anything but Orwick's tool functions and previews |
| `test_own_tools.py` | The AI's own tools: safe defaults, `[]` = none, only known names, what the AI is told, Claude's `--tools`/`--allowedTools` and folder, Codex's `--search`/sandbox/folder, a switched-off Codex tool refused |
| `test_mcp.py` | MCP against a tiny real server (`fake_mcp_server.py`): the usual format checked, `${VAR}` filled, plain parameter types, risk from hints, list and call, errors, only switched-on tools offered, idle servers never started, a missing server is a note |
| `test_skills.py` | UI actions (`ui.json` checks, broken files), headers only at start, discovery order and protection, project skills off by default, the index's size limit, `use_skill`, session docs (names, links, size, preview) |
| `test_models_catalog.py` | Codex's models from its cache (shown ones, in order), missing or broken cache, Claude's names |
| `test_sessions.py` | Session ids, bad ids refused, `.gitignore`, cleanup by last use (never symlinks, other folders, the current one, or one open elsewhere), listing, titles |
| `test_status.py` | `/status`: audit events counted, Claude limits/tokens/cost from the stream, Codex tokens and last-known limits from its session files, the screen with and without usage |
| `test_claude_model.py` | Claude backend with a fake process: isolation flags and environment, one process per conversation, only new input per step, restart + replay after `/reset`, errors, timeout, missing CLI |
| `test_codex_model.py` | Codex flags (sandbox/isolation), prompt building, decision parsing, failures |
| `test_debug.py` | Frames, numbering, collected safety checks, redaction, full pipeline trace |
| `test_config.py` | Env loading, backend selection, validation, main settings priority and the workspace rule |
| `test_config_file.py` | `config.json` priority, `null`, bad files, change detection; `.orwick/` hidden and unreadable/unwritable, including other letter case; main settings protected |
| `test_config_view.py` | `/config`: every setting has value, source and meaning; API key never shown |
| `test_screens.py` | `/help`, `/tools`, `/policy` content; colors only when asked; emoji/color-aware widths |
| `test_cli_commands.py` | The real chat loop with typed commands and a fake AI: `/debug`, config-file edits (folder and main), saving, `--resume`, `/sessions`, `/resume`, `/clear`, usage errors |

---

## Known limitations

- **Commands are macOS only** (Seatbelt). Linux would need Landlock or bubblewrap.
- **Commands can still read most of the system** (`/usr`, `/opt/homebrew`, `/Library`…), just not personal folders.
- **A command that starts its own session** (`setsid`) can escape the timeout kill; it stays inside the sandbox.
- **Virtualenvs and caches outside the project** can't be read by commands, and there's no network for installs.
- **Orwick's own tests partly fail when run through Orwick** in this repo, because the sandbox hides parts of the repo (such as `.orwick/`) on purpose.
- **Tools are Python only** (`run.python`); tools in any language are planned.
- **History is never trimmed**, so a long session will eventually exceed the
  model's context window.
- **Thread-mode timeouts don't stop the work**; use process mode for anything that can hang.
- **Secret files are recognized by name and folder.** A secret saved under an
  innocent name (e.g. a token in `notes.txt`, or a hard link) isn't detected.
- **Approval is terminal-only**, though `ApprovalProvider` is a protocol, so others can be plugged in.

See [ROADMAP.md](ROADMAP.md) for what's next.
