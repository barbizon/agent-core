# Orvik architecture

How Orvik works inside. For installing and using it, see [README.md](README.md).

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
| **Tools** (`tools/` + `.orvik/tools/`) | The functions, described by `tool.json` files |
| **Executors** (`execution/`) | Run a tool in a thread or a killable subprocess, with a timeout |
| **Audit / tracer** (`audit.py`, `debug.py`) | Record every step to a file; show it on screen in debug mode |

Everything is injected: `cli.build_model()` and `cli.build_agent()` are the
only places the pieces are connected. Tests swap in their own pieces.

---

## Project layout

```
orvik/                               ← the project (also a working directory)
├── .orvik/                          ← the agent's own folder (invisible to its tools)
│   ├── config.json                  settings, reloaded live
│   ├── .env / .env.example          secrets and fallback values
│   ├── logs/audit.jsonl             audit log
│   └── tools/<name>/tool.json       one folder per tool
├── pyproject.toml                   deps + the `orvik` command
├── src/agent_core/
│   ├── cli.py                       chat loop, /commands, live config reload, wiring
│   ├── agent.py                     Agent: conversation + bounded tool loop
│   ├── llm/
│   │   ├── __init__.py              ChatModel protocol, ModelResponse, ToolCall
│   │   ├── codex_model.py           CodexCLIModel (ChatGPT subscription)
│   │   └── openai_model.py          OpenAIChatModel (API key)
│   ├── gateway.py                   ToolGateway: the safety pipeline
│   ├── config.py                    Settings: flags > config.json > .env > defaults
│   ├── config_view.py               the /config report
│   ├── debug.py                     Tracer: numbered pipeline trace
│   ├── permissions.py               RiskLevel, PermissionPolicy
│   ├── approvals.py                 terminal / deny-all approval providers
│   ├── authorization.py             single-use action tokens
│   ├── retry.py                     RetryPolicy, execute_with_retry
│   ├── audit.py                     JSONL / in-memory audit loggers, redaction
│   ├── results.py                   ToolResult, how results are sent to the model
│   ├── errors.py                    exception types
│   ├── execution/
│   │   ├── thread_executor.py       run_in_thread (deadline)
│   │   ├── process_executor.py      killable subprocess (process group)
│   │   └── process_worker.py        subprocess entry point
│   └── tools/
│       ├── __init__.py              build_registry(): loads .orvik/tools
│       ├── manifest.py              tool.json loader and checks
│       ├── registry.py              ToolDefinition, ToolRegistry, argument validation
│       └── files.py                 list_folder, read_file, write_file + workspace rules
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

Both backends implement the `ChatModel` protocol
(`complete(messages, tools) -> ModelResponse`), so the loop doesn't care
which one runs.

| Backend | Pays with | Needs | Tool calling |
|---|---|---|---|
| `codex` (default) | Your ChatGPT subscription | `codex login` | One JSON "decision" per step (final answer **or** one tool call), forced with `--output-schema` |
| `openai` | API credits (billed separately) | `OPENAI_API_KEY` in `.orvik/.env` | Native function calling, several tools per step |

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
  through Orvik's own tools and gateway.
- Cost: about 6 s per step, and about 25,000 input tokens of Codex's own
  instructions per call (mostly cached).

---

## Tools

### `tool.json`

Each tool is a folder `.orvik/tools/<name>/` with a `tool.json`. The folder
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
  `workspace` setting, or else the folder Orvik was started in. A path that
  resolves outside it (`..`, an absolute path, a symlink pointing out) is
  refused.
- **Protected paths:** the CLI passes the tools a list of paths they must
  never see or touch: the whole `.orvik/` folder, plus a custom `--config` /
  `--env-file` if given. They're hidden from `list_folder` and refused by
  `read_file` and `write_file`. Without this, one read could send the API key
  to the AI, and one approved write could change the config (applied live)
  or plant a tool marked `read`.
- **Letter case:** macOS ignores letter case in file names (`.ORVIK/.env` is
  `.orvik/.env`), so protection compares **file identity** (device + inode)
  of the target and its parent folders, not just the spelling.
- These settings reach the tools through the environment (`AGENT_WORKSPACE`,
  `AGENT_PROTECTED_PATHS`), so they also work in the process worker.

### Built-in tools

| Tool | Risk | Mode | Limits |
|---|---|---|---|
| `list_folder(path?)` | read | process | Folders first, with file sizes; at most 500 entries |
| `read_file(path)` | read | process | UTF-8 text only; at most 200 KB |
| `write_file(path, content)` | write | thread | Creates missing folders; asks for approval |

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
| 4 | **Human approval** (write/dangerous only). A crashing approver counts as "no". | `approval_denied`, `approval_required` |
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
`.orvik/config.json` > `.orvik/.env` / environment > built-in default.
`null` in the JSON means "not set here". Secrets (`OPENAI_API_KEY`) are read
only from `.env` / the environment.

| JSON key | Env variable | Default |
|---|---|---|
| `backend` | `AGENT_BACKEND` | `codex` |
| `model` | `AGENT_MODEL` | backend default (`gpt-4.1-mini` for `openai`) |
| `max_steps` | `AGENT_MAX_STEPS` | `10` |
| `request_timeout_seconds` | `AGENT_REQUEST_TIMEOUT_SECONDS` | `120` |
| `max_tool_result_chars` | `AGENT_MAX_TOOL_RESULT_CHARS` | `8000` |
| `audit_log` | `AGENT_AUDIT_LOG` | `.orvik/logs/audit.jsonl` |
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

| Point | Hop | Shows |
|---|---|---|
| ① | CLI → Agent | Your message |
| ② | Agent | The history sent to the model, one line per message |
| ③ | Agent → adapter | Step number (of max) and the tools offered |
| ④ | adapter → model | The exact request: `codex exec` command + full prompt (or OpenAI request JSON) |
| ⑤ | model → adapter | The raw reply: Codex's event stream, token usage, time, exit code |
| ⑥ | adapter → Agent | The parsed decision: `final_answer` or `tool_call` |
| ⑦ | Agent → CLI | The final answer, steps and total time |

For a tool call, three boxes come between ⑥ and the next model call:

```
Agent → Gateway · tool call            read_file {"path": "notes.txt"}
Gateway · safety checks for 'read_file' → requested … ✓ permission … ✓ done (12 ms)
Gateway → Agent · tool result           exactly what is added to the history
```

Border colors: cyan = your input, blue = to the model, green = from the
model, yellow = tools, gray = internal and safety checks, magenta = final
answer, red = failures. Secret-looking values are hidden. Colors are off
when the output isn't a terminal, or when `NO_COLOR` is set.

---

## Audit log

Every step is one JSON line in `.orvik/logs/audit.jsonl`:

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
| Gateway | `tool.requested`, `tool.rejected`, `tool.permission_checked`, `tool.approval_requested`, `tool.approval_granted`, `tool.approval_denied`, `tool.approval_failed`, `tool.authorization_issued`, `tool.authorization_failed`, `tool.execution_started`, `tool.execution_succeeded`, `tool.execution_failed` |
| Retries | `tool.attempt_started`, `tool.attempt_succeeded`, `tool.attempt_failed`, `tool.attempt_retry_scheduled` |

```bash
# follow live
tail -f .orvik/logs/audit.jsonl | jq -c '{t: .timestamp[11:19], e: .event_type, d: .details}'
# replay one request
jq -c 'select(.run_id == "RUN_ID")' .orvik/logs/audit.jsonl
# every failure
jq -c 'select(.event_type | test("failed|denied|rejected"))' .orvik/logs/audit.jsonl
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
| `test_safety_primitives.py` | Policy, single-use tokens, retry rules, redaction, truncation, approvers |
| `test_codex_model.py` | Codex flags (sandbox/isolation), prompt building, decision parsing, failures |
| `test_debug.py` | Frames, numbering, collected safety checks, redaction, full pipeline trace |
| `test_config.py` | Env loading, backend selection, validation |
| `test_config_file.py` | `config.json` priority, `null`, bad files, change detection; `.orvik/` hidden and unreadable/unwritable, including other letter case |
| `test_config_view.py` | `/config`: every setting has value, source and meaning; API key never shown |
| `test_cli_commands.py` | The real chat loop with typed commands: `/debug`, config-file edits, usage errors |

---

## Known limitations

- **Only file tools**: no search, edit-in-place, delete or shell tools yet.
- **Tools are Python only** (`run.python`); tools in any language are planned.
- **History is never trimmed**, so a long session will eventually exceed the
  model's context window.
- **Thread-mode timeouts don't stop the work**; use process mode for anything that can hang.
- **Secret files inside the workspace** (a project's own `.env`, `.git/config`) aren't blocked yet; only `.orvik/` is.
- **Approval is terminal-only**, though `ApprovalProvider` is a protocol, so others can be plugged in.

See [ROADMAP.md](ROADMAP.md) for what's next.
