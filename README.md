# agent-core

A small, clean AI agent runtime in Python. You chat with a real LLM in the
terminal (through your **ChatGPT subscription** via the Codex CLI by default,
or through the OpenAI API), and the LLM can call **tools** (Python functions) to do
work. Every tool call passes through a safety pipeline first: argument
checks, a permission policy, human approval, a single-use authorization,
timeouts, retries and an audit log.

**Current state:** the runtime, the safety layer and the CLI are complete and
tested. **No tools are registered yet**, so right now the agent is a plain
chat assistant. Tools are the next step (see [Adding a tool](#adding-a-tool)).

---

## Contents

1. [Quick start](#quick-start)
2. [Configuration](#configuration)
3. [Using the CLI](#using-the-cli)
4. [How it works](#how-it-works)
5. [The safety pipeline](#the-safety-pipeline)
6. [Audit log](#audit-log)
7. [Adding a tool](#adding-a-tool)
8. [Error types](#error-types)
9. [Testing](#testing)
10. [Project layout](#project-layout)
11. [Troubleshooting](#troubleshooting)
12. [Known limitations / next steps](#known-limitations--next-steps)

---

## Quick start

Requires Python 3.11+ and the [Codex CLI](https://github.com/openai/codex)
signed in with your ChatGPT account. No API key is needed.

```bash
cd ~/Documents/agent-core

# 1. Sign in to Codex once (check with: codex login status)
codex login

# 2. Create a virtualenv and install (pick one)
uv venv .venv && uv pip install -e '.[dev]'
# or: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

# 3. Optional: copy the config (the defaults already work)
cp .env.example .env

# 4. Chat
.venv/bin/agent-core
```

```
=== agent-core · backend=codex · model=codex default · tools=0 ===
Type /help for commands.

You: What is an agent loop?

Agent: An agent loop is ...
```

Run it from the project root: `.env` and the `logs/` folder are resolved
relative to the current directory. With the Codex backend each reply takes
about 5–15 seconds, because every model call starts a `codex exec` process.

### Model backends

| Backend | Pays with | Needs | Tool calling |
|---|---|---|---|
| `codex` (default) | Your ChatGPT subscription | `codex login` | Codex replies with one JSON "decision" (final answer **or** one tool call), enforced with `--output-schema` |
| `openai` | API credits (billed separately from ChatGPT) | `OPENAI_API_KEY` from platform.openai.com | Native function calling, several tools per step possible |

A ChatGPT Plus/Pro subscription does **not** include API access. That is
why `codex` is the default.

How the Codex backend works (`src/agent_core/llm/codex_model.py`):

- `codex exec` keeps no state between calls, so each call sends the **whole
  conversation** on stdin: the system prompt, the rules for replying, the
  tool list (with JSON schemas) and the transcript so far.
- `--output-schema` forces the reply into
  `{"type", "answer", "tool", "arguments_json"}`, which is turned into a
  normal `ModelResponse`. The agent loop and the gateway behave the same
  for both backends.
- **Codex is a full agent with its own shell**, so it is locked down. Each
  call runs with `--sandbox read-only` inside a fresh empty temporary folder,
  with `--ephemeral` (no saved session), `--ignore-user-config` (no MCP
  servers or plugins from `~/.codex`) and `--ignore-rules`. Real actions
  happen only through **our** tools and gateway.

---

## Configuration

Settings come from environment variables, which can be put in `.env`.
Command-line flags override them.

| Env variable | Default | Meaning |
|---|---|---|
| `AGENT_BACKEND` | `codex` | `codex` (ChatGPT subscription) or `openai` (API key). |
| `AGENT_MODEL` | backend default | Model name. Empty means Codex's default, or `gpt-4.1-mini` for `openai`. |
| `OPENAI_API_KEY` | (empty) | Needed **only** for `AGENT_BACKEND=openai`. The CLI exits with code 2 if it's missing then. |
| `AGENT_MAX_STEPS` | `10` | Maximum model calls per user message. Stops runaway tool loops. |
| `AGENT_AUDIT_LOG` | `logs/audit.jsonl` | Where audit events are appended. |
| `AGENT_REQUEST_TIMEOUT_SECONDS` | `120` | Timeout for each model call (`codex exec` run or API request). |
| `AGENT_MAX_TOOL_RESULT_CHARS` | `8000` | Tool output sent to the model is cut off at this size. |

| CLI flag | Effect |
|---|---|
| `--backend codex\|openai` | Overrides `AGENT_BACKEND`. |
| `--model NAME` | Overrides `AGENT_MODEL`. |
| `--max-steps N` | Overrides `AGENT_MAX_STEPS`. |
| `--allow-write` | Lets the agent *request* **write** tools. Each call still needs your approval. |
| `--allow-dangerous` | Lets the agent *request* **dangerous** tools. Each call still needs your approval. |
| `-d`, `--debug` | Prints every step of the flow in frames. See [Debug mode](#debug-mode). |
| `--env-file PATH` | Loads a different env file (default `.env`). |

Without flags, a session is **read-only**: write and dangerous tools are
refused before you are even asked.

---

## Using the CLI

| Input | What it does |
|---|---|
| any text | Sends a message to the agent. The conversation is kept between messages. |
| `/tools` | Lists registered tools with their arguments, risk level, execution mode and timeout. |
| `/policy` | Shows which risk levels this session allows. |
| `/reset` | Clears the conversation history (the system prompt is kept). |
| `/help` | Shows the commands. |
| `/exit`, `/quit`, Ctrl-D | Quits. |
| Ctrl-C during a reply | Stops the current run. The chat stays open and the history stays valid. |

### What you'll see once tools exist

Tool activity is printed as it happens:

```
You: what's in notes.txt?
  → read_file({"path": "notes.txt"})
  ← ok: {"content": "buy milk"}

Agent: The file says "buy milk".
```

A **write** or **dangerous** tool stops and asks you first (only if you
started with `--allow-write` / `--allow-dangerous`):

```
=== APPROVAL REQUIRED ===
Tool:       write_file
Risk level: write
Arguments:  {
  "path": "notes.txt",
  "content": "..."
}
Approve this action? [y/N]:
```

Only `y` approves. Anything else, including Enter, Ctrl-C or Ctrl-D, is a
"no". When a call is refused, the model receives a structured error
(`approval_denied`), and the system prompt tells it to explain the refusal
rather than work around it.

### Debug mode

Run `.venv/bin/agent-core -d` to see what really happens on each message.
Every step is printed in a box:

```
┌─ agent.started ──────────────────────────────┐
│ message: say hi in 3 words                   │
└──────────────────────────────────────────────┘
┌─ MODEL INPUT · codex exec ───────────────────┐
│ $ codex exec --json --sandbox read-only ...  │
│ --- prompt (sent on stdin) ---               │
│ (the full prompt: rules, tools, chat so far) │
└──────────────────────────────────────────────┘
┌─ MODEL OUTPUT · codex (6.6s) ────────────────┐
│ { "type": "final_answer", "answer": ... }    │
└──────────────────────────────────────────────┘
```

| Border color | Box |
|---|---|
| Blue | Model input: the exact prompt or request sent to the model |
| Green | Model output: the raw reply and how long it took |
| Yellow | Tool result: exactly what goes back to the model |
| Gray | Steps (the same events as the audit log) |
| Red | Failed, denied, rejected or invalid steps |

Notice that the **MODEL INPUT** box holds the whole conversation every
time: the model has no memory, so the full history is resent on each call.
Values under secret-looking keys are hidden, as in the audit log.
Colors are off when the output isn't a terminal, or when `NO_COLOR` is set.

### Errors in the chat

| Message | Meaning |
|---|---|
| `[model error] codex exec failed (exit N): ...` | Codex failed; the last lines of its stderr are shown. Usually you're not signed in: run `codex login`. |
| `[model error] codex exec timed out ...` | Raise `AGENT_REQUEST_TIMEOUT_SECONDS`. |
| `[model error] codex returned ...` | Codex replied in an unexpected format. Send the message again. |
| `[model error] AuthenticationError ...` | (`openai` backend) Bad or missing API key. |
| `[model error] RateLimitError / APIConnectionError ...` | (`openai` backend) The SDK already retried twice. Try again. |
| `[stopped] Agent reached the step limit ...` | The model kept calling tools without answering. Raise `--max-steps` or rephrase. |
| `[interrupted]` | You pressed Ctrl-C. |

The chat keeps going after any of these.

---

## How it works

```
 you ──► cli.py ──► Agent.send(message)
                       │
                       ▼
            ┌──── agent loop (max_steps) ─────────────────────┐
            │  model.complete(history, tool schemas)           │
            │    (CodexCLIModel or OpenAIChatModel)            │
            │        │                                         │
            │        ├─ text only?  ──► return the answer       │
            │        │                                         │
            │        └─ tool calls? ──► for each call:          │
            │              parse JSON arguments                 │
            │              ToolGateway.execute(...)  ◄── safety │
            │              append the result (size-capped)      │
            │              to the history, loop again           │
            └──────────────────────────────────────────────────┘
```

Key ideas:

- **The conversation is a list of OpenAI messages**: `system`, `user`,
  `assistant` (possibly with `tool_calls`), and `tool`. `Agent.messages`
  holds it, and `/reset` clears it.
- **One user message is one run.** It gets a `run_id`, and every audit
  event in that run carries it.
- **Every tool call gets a reply.** The OpenAI API requires a `tool` message
  for each `tool_call_id`. Errors are returned as tool results, not raised.
  If a run is interrupted, the agent fills in `interrupted` results so the
  next message still works.
- **Tool results are untrusted data.** The system prompt says so, and each
  result is capped at `AGENT_MAX_TOOL_RESULT_CHARS`.
- **Backends are interchangeable.** Both implement the `ChatModel`
  protocol (`complete(messages, tools) -> ModelResponse`), and the history
  always uses the OpenAI message format.
- **Everything is injected.** `cli.build_model()` / `cli.build_agent()` are
  the only places the pieces are wired together: model, registry, gateway, approver, audit
  logger and process executor. Tests swap in their own pieces.

---

## The safety pipeline

`ToolGateway.execute()` in `src/agent_core/gateway.py` is the **only** code
path from a model's tool request to running a function. It runs in this order:

| # | Step | Fails with |
|---|---|---|
| 1 | **Lookup**: does the tool exist? | `unknown_tool` |
| 2 | **Validate** the arguments against the declared schema (required, no extras, types, enums; `true` is not accepted as an integer) | `missing_argument`, `unexpected_argument`, `invalid_argument_type`, `invalid_argument_value` |
| 3 | **Permission policy**: is this risk level allowed in this session? | `permission_denied` |
| 4 | **Human approval** (write/dangerous only). A crashing approver counts as "no". | `approval_denied`, `approval_required` |
| 5 | **Authorize**: issue a single-use token tied to the exact `(tool, arguments)`. This happens **only after steps 3 and 4 pass.** | `authorization_failed` |
| 6 | **Execute**: use up the token, then run with a timeout and the tool's retry policy | `timeout`, `worker_crashed`, `execution_error`, ... |

**Approval comes first.** No token exists until the policy and a human have
both said yes, so a refused call leaves nothing behind. The token guarantees
that the action that runs is exactly the one that was approved, and that an
approval cannot be reused.

### Rules checked when a tool is defined

`ToolDefinition` raises `ValueError` at startup if any of these is broken:

- The name matches `^[a-zA-Z0-9_-]{1,64}$` and the description is not empty.
- `risk_level` is a `RiskLevel` and `execution_mode` is an `ExecutionMode` (enums, not plain strings).
- Every argument type is one of `string`, `integer`, `number`, `boolean`, `array`, `object`.
- **Write and dangerous tools cannot retry automatically** (a retried write could happen twice).
- **Process mode is for read tools only**, and the function must be importable at module level.

### Execution modes

| Mode | How it runs | On timeout |
|---|---|---|
| `THREAD` (default) | Background thread in the same process | The agent stops waiting, but Python **cannot kill a thread**, so the work may keep running. Use for fast, trusted code. |
| `PROCESS` | Separate Python subprocess in its own process group | The whole group is sent SIGTERM, then SIGKILL, including any child processes the tool started. Use for anything that might hang. |

Extra protections in process mode: the worker only imports functions from
packages it is told to allow (`agent_core.tools.` by default), anything the
tool prints goes to stderr so it cannot fake the JSON result, and a result
that can't be turned into JSON is reported as an error.

### Retries

Only **read** tools can retry, and only on `temporary_unavailable` or
`rate_limited` errors. The wait doubles each time (`initial_delay_seconds ×
backoff_multiplier^(attempt-1)`). Retries of the OpenAI API calls themselves are
handled by the SDK (`max_retries=2`). Codex calls are not retried.

---

## Audit log

Every decision is written as one JSON line to `logs/audit.jsonl`:

```json
{"timestamp": "...", "run_id": "…", "event_type": "tool.approval_granted",
 "details": {"tool": "write_file"}, "call_id": "…"}
```

- `run_id` identifies one user message; `call_id` identifies one tool call within it.
- Values under keys containing `password`, `secret`, `token`, `api_key`,
  `apikey` or `authorization` are replaced with `***REDACTED***`.

**Event types**

| Area | Events |
|---|---|
| Agent | `agent.started`, `model.requested`, `model.responded`, `agent.finished`, `agent.failed`, `tool.invalid_arguments` |
| Gateway | `tool.requested`, `tool.rejected`, `tool.permission_checked`, `tool.approval_requested`, `tool.approval_granted`, `tool.approval_denied`, `tool.approval_failed`, `tool.authorization_issued`, `tool.authorization_failed`, `tool.execution_started`, `tool.execution_succeeded`, `tool.execution_failed` |
| Retries | `tool.attempt_started`, `tool.attempt_succeeded`, `tool.attempt_failed`, `tool.attempt_retry_scheduled` |

**Useful queries** (needs `jq`):

```bash
# follow the log live
tail -f logs/audit.jsonl | jq -c '{t: .timestamp[11:19], e: .event_type, d: .details}'

# replay one run
jq -c 'select(.run_id == "RUN_ID")' logs/audit.jsonl

# every failure
jq -c 'select(.event_type | test("failed|denied|rejected"))' logs/audit.jsonl
```

---

## Adding a tool

1. **Write the function** in a module under `src/agent_core/tools/`. It
   takes keyword arguments and returns JSON-friendly data. For known
   failures, raise one of the classes in `agent_core.errors`:
   `TemporaryToolError` (retryable), `RateLimitToolError` (retryable) or
   `PermanentToolError`.

   ```python
   # src/agent_core/tools/clock.py
   from datetime import datetime
   from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

   from agent_core.errors import PermanentToolError


   def current_time(timezone: str) -> dict:
       try:
           now = datetime.now(ZoneInfo(timezone))
       except ZoneInfoNotFoundError:
           raise PermanentToolError(f"Unknown timezone: {timezone}")
       return {"timezone": timezone, "iso": now.isoformat()}
   ```

2. **Register it** in `build_registry()` in `src/agent_core/tools/__init__.py`:

   ```python
   from agent_core.permissions import RiskLevel
   from agent_core.retry import RetryPolicy
   from agent_core.tools import clock

   def build_registry() -> ToolRegistry:
       return ToolRegistry([
           ToolDefinition(
               name="current_time",
               description="Get the current time in an IANA timezone.",
               function=clock.current_time,
               risk_level=RiskLevel.READ,
               parameters={
                   "timezone": {"type": "string", "description": "e.g. Europe/Berlin"},
               },
               execution_mode=ExecutionMode.PROCESS,
               timeout_seconds=5,
               retry_policy=RetryPolicy(max_attempts=1),
           ),
       ])
   ```

3. **Check it**: run `/tools` in the CLI, then ask *"what time is it in Tokyo?"*.

**Choosing a risk level**

| Level | Use for | Behavior |
|---|---|---|
| `READ` | Reads and lookups with no side effects | Runs without asking. Can retry. Can use process mode. |
| `WRITE` | Changes something (files, records) | Needs `--allow-write` **and** your `y` for each call. Never retries. Thread mode only. |
| `DANGEROUS` | Destructive or hard to undo (deleting, deploying, shell commands) | Needs `--allow-dangerous` **and** your `y` for each call. Never retries. Thread mode only. |

Optional arguments: list them in `optional=frozenset({"name"})`. They stay
in `parameters` but are left out of `required` in the schema.

---

## Error types

The model receives every failure as a structured tool result:
`{"ok": false, "error": {"type": "...", "message": "..."}}`.

| Type | Source |
|---|---|
| `unknown_tool` | No tool by that name, or the process worker could not import it |
| `invalid_arguments` | The model's arguments were not valid JSON or not an object |
| `missing_argument` / `unexpected_argument` / `invalid_argument_type` / `invalid_argument_value` | Schema check failed |
| `permission_denied` | Blocked by the session policy, or by the process worker's allowed-package rule |
| `approval_required` | The approver crashed or was unavailable |
| `approval_denied` | You answered anything other than `y` |
| `authorization_failed` | The token was missing, already used or didn't match (should never happen in normal use) |
| `timeout` | The tool ran past its `timeout_seconds` |
| `worker_crashed` / `worker_start_failed` / `invalid_worker_response` / `termination_unconfirmed` / `invalid_request` | Problems in process mode |
| `executor_unavailable` | A process-mode tool was called but no process executor is configured |
| `temporary_unavailable` / `rate_limited` | Raised by the tool; retryable |
| `permanent_failure` | Raised by the tool; not retried |
| `execution_error` | Any other exception from the tool |
| `interrupted` | You pressed Ctrl-C before this call finished |

---

## Testing

```bash
.venv/bin/pytest            # the whole suite (~2–3 s)
.venv/bin/pytest -k gateway # one area
.venv/bin/pytest -v tests/test_gateway.py::test_approval_denied_issues_no_token
```

The tests make **no network calls** and never start `codex`. They use test-only doubles from
`tests/`, which are never part of the app:

- `tests/tool_fixtures.py`: small test tools (echo, hang, crash, flaky, ...)
- `tests/conftest.py`: `ScriptedModel` (replays fixed model responses), `RecordingApprover`, and a `make_gateway` fixture

| File | Covers |
|---|---|
| `test_gateway.py` | Pipeline order, approval-first authorization, approver failing closed, retries, audit IDs |
| `test_agent.py` | Chat history, tool-call round trip, malformed arguments, step limit, interrupt repair, reset |
| `test_executors.py` | Thread/process timeouts, killing the process group, stdout isolation, allowed packages, crashes |
| `test_registry.py` | Definition rules, schema export, argument validation |
| `test_safety_primitives.py` | Policy, single-use tokens, retry rules, redaction, truncation, approvers |
| `test_codex_model.py` | Codex flags (sandbox/isolation), prompt building, decision parsing, failures (using recorded output, no real `codex` calls) |
| `test_debug.py` | Frame drawing, wrapping, redaction, and full-flow tracing for the agent and Codex |
| `test_config.py` | Env loading, backend selection and validation |

---

## Project layout

```
agent-core/
├── .env.example                 # copy to .env
├── pyproject.toml               # deps + `agent-core` console script
├── logs/audit.jsonl             # created on first run (gitignored)
├── src/agent_core/
│   ├── cli.py                   # chat loop + build_agent() wiring
│   ├── agent.py                 # Agent: conversation + bounded tool loop
│   ├── llm/
│   │   ├── __init__.py          # ChatModel protocol, ModelResponse, ToolCall
│   │   ├── codex_model.py       # CodexCLIModel (ChatGPT subscription)
│   │   └── openai_model.py      # OpenAIChatModel (API key)
│   ├── gateway.py               # ToolGateway: the safety pipeline
│   ├── debug.py                 # Tracer: framed debug output (-d)
│   ├── config.py                # Settings from env/.env
│   ├── permissions.py           # RiskLevel, PermissionPolicy
│   ├── approvals.py             # Terminal / DenyAll approval providers
│   ├── authorization.py         # single-use action tokens
│   ├── retry.py                 # RetryPolicy, execute_with_retry
│   ├── audit.py                 # JSONL / in-memory audit loggers, redaction
│   ├── results.py               # ToolResult / ToolError, model serialization
│   ├── errors.py                # exception types
│   ├── execution/
│   │   ├── thread_executor.py   # run_in_thread (deadline)
│   │   ├── process_executor.py  # ProcessExecutor (killable subprocess)
│   │   └── process_worker.py    # subprocess entry point
│   └── tools/
│       ├── __init__.py          # build_registry(): register tools here
│       └── registry.py          # ToolDefinition, ToolRegistry
└── tests/
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `[model error] codex exec failed` / `'codex' not found` | Install the Codex CLI, run `codex login`, and check with `codex login status`. |
| `401 Incorrect API key` | You're on `--backend openai` (or `AGENT_BACKEND=openai`) without an API key. A ChatGPT subscription doesn't include one, so use the default `codex` backend. |
| `Configuration error: OPENAI_API_KEY is not set` | Only for the `openai` backend. Add the key to `.env`, or switch back to `codex`. |
| `[model error] ... model` not found | That model isn't available to you. Change `AGENT_MODEL` / `--model`, or leave it empty. |
| `command not found: agent-core` | Use `.venv/bin/agent-core`, or activate the env with `source .venv/bin/activate`. |
| `ValueError` at startup about a tool | A tool definition breaks one of the [rules above](#rules-checked-when-a-tool-is-defined); the message says which. |
| The agent says it isn't permitted to do something | Start with `--allow-write` / `--allow-dangerous`. Check with `/policy`. |
| Answers drift or get slow in a long chat | Run `/reset`. History is not trimmed yet (see below). |

---

## Known limitations / next steps

- **No tools yet.** That is the next planning step.
- **History is not trimmed.** A long session will eventually go past the
  model's context window. Trimming must keep each assistant `tool_calls`
  message together with its `tool` replies.
- **Thread-mode timeouts don't stop the work**: the function may keep
  running. Use process mode for anything that can hang.
- **Approval is only in the terminal.** `ApprovalProvider` is a protocol, so
  a Slack or web approver can be plugged in without changing the gateway.
- **The authorization store is in memory** and is cleared on restart.
- **Tool calls run one after another** even when the model asks for several at once.
  The Codex backend asks for one tool per step.
- **Codex calls are slow and resend everything**: each step starts a new
  `codex exec` process and sends the full conversation. Long chats with
  many tool steps get slower; trimming the history helps here too.
