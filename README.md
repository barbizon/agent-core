# Orvik

An AI agent for your terminal. It chats with you, reads and writes files in
the folder you start it in, and **asks you before it changes anything**.

> **The name.** "Orvik" is an invented word. The ending *-vik* means "bay" in
> Scandinavian languages (as in Reykjavík): a sheltered harbour where every
> ship is checked before it sails out. In the same way, every action Orvik
> takes is checked first.

## What it does

- **Chats with an AI** through your ChatGPT subscription (the Codex CLI). No API key needed.
- **Looks at files:** lists folders and reads text files.
- **Changes files only with your OK:** before every write, it shows you the change and waits for `y`.
- **Stays in its folder:** it can only reach the folder you start it in. Its own `.orvik` folder (settings, secrets, tools) is invisible to it.

## Requirements

- Python 3.11 or newer
- The [Codex CLI](https://github.com/openai/codex), signed in with your ChatGPT account: `codex login`

## Install

```bash
cd ~/Documents/orvik
uv venv .venv && uv pip install -e '.[dev]'
# without uv: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

To run `orvik` from any folder, also install it as a command:

```bash
uv tool install --editable ~/Documents/orvik
```

## Start

```bash
cd ~/Documents/orvik
.venv/bin/orvik            # or just `orvik` after `uv tool install`
```

```
=== orvik · backend=codex · model=codex default · tools=3 ===
Working directory: /Users/you/Documents/orvik
Config: .orvik/config.json (loaded). It is reloaded automatically when it changes.
Type /help for commands.

You: what files are here?
```

Orvik works on **the folder you start it in**, and reads its settings and
tools from **`.orvik/` inside that folder**. In a folder without `.orvik/` it
can only chat. To use it somewhere else, copy the `.orvik` folder there.

| Option | What it does |
|---|---|
| `-d`, `--debug` | Show every step of each request (see [Debug mode](#debug-mode)) |
| `--model NAME` | Use a specific AI model |
| `--max-steps N` | Max AI calls per message (default 10) |
| `--backend codex\|openai` | `openai` = the paid OpenAI API instead of your subscription |
| `--config PATH` | Use another settings file |
| `--env-file PATH` | Use another secrets file |

## Chat commands

| Command | What it does |
|---|---|
| `/help` | List the commands |
| `/tools` | List the tools Orvik can use |
| `/config` | Show every setting, where it comes from, and what it means |
| `/debug on` · `/debug off` | Switch debug mode for this session (`/debug` alone shows the status) |
| `/policy` | Show what kinds of actions are allowed |
| `/reset` | Start a fresh conversation |
| `/exit` | Quit (Ctrl-D also works) |

Ctrl-C stops the current answer; the chat stays open.

## Tools

| Tool | What it does | Asks you first? |
|---|---|---|
| `list_folder` | Lists files and folders | No |
| `read_file` | Reads a text file (up to 200 KB) | No |
| `write_file` | Creates or replaces a text file | **Yes, every time** |

When Orvik wants to change something, it stops and asks:

```
=== APPROVAL REQUIRED ===
Tool:       write_file
Risk level: write
Arguments:  { "path": "notes.txt", "content": "..." }
Approve this action? [y/N]:
```

Only `y` approves. Anything else (Enter, `n`, Ctrl-C) means no, and Orvik
tells you it didn't do it.

## Settings

Everything Orvik keeps is in the `.orvik` folder:

```
.orvik/
├── config.json      settings (applied live when you edit them)
├── .env             secrets: only needed for the openai backend
├── .env.example     template for .env
├── logs/            the audit log of everything Orvik did
└── tools/           one folder per tool, each with a tool.json
```

`config.json`:

```json
{
  "backend": "codex",
  "model": null,
  "max_steps": 10,
  "request_timeout_seconds": 60,
  "max_tool_result_chars": 8000,
  "audit_log": ".orvik/logs/audit.jsonl",
  "workspace": null,
  "debug": false
}
```

| Setting | Meaning |
|---|---|
| `backend` | `codex` (your ChatGPT subscription) or `openai` (paid API) |
| `model` | AI model; `null` = the backend's default |
| `max_steps` | Max AI calls per message, so it can't loop forever |
| `request_timeout_seconds` | How long to wait for one AI reply |
| `max_tool_result_chars` | Tool output longer than this is cut before it reaches the AI |
| `audit_log` | Where the log of every action is written |
| `workspace` | The folder Orvik may use; `null` = the folder you started in |
| `debug` | Same as `-d` |

- **Edit `config.json` while Orvik runs:** the change applies before your next message, and the conversation is kept.
- **Order of precedence:** command-line option > `config.json` > `.env` > default. `/config` shows which one won for each setting.
- **The API key** (only for `backend: openai`) goes in `.orvik/.env`, never in `config.json`.

## Debug mode

Start with `-d`, or type `/debug on` in the chat, to watch each request travel
through Orvik step by step: your message, the exact prompt sent to the AI,
its raw reply, the safety checks for each tool, and the final answer. Type
`/debug off` to stop. [ARCHITECTURE.md](ARCHITECTURE.md#debug-tracer)
explains each step.

## Add your own tool

1. Write a Python function in `src/agent_core/tools/` that returns JSON-friendly data.
2. Describe it in `.orvik/tools/<tool_name>/tool.json`: description, parameters,
   risk level, and which function to run.
3. Restart Orvik and check with `/tools`.

The `tool.json` format is in [ARCHITECTURE.md](ARCHITECTURE.md#tools).
Tools written in any language (not just Python) are planned.

## Troubleshooting

| Problem | Fix |
|---|---|
| `[model error] codex exec failed` or `'codex' not found` | Install the Codex CLI and run `codex login`. Check with `codex login status`. |
| `401 Incorrect API key` | You're on the `openai` backend. A ChatGPT subscription doesn't include API access, so switch back to `"backend": "codex"`. |
| `[model error] ... timed out` | Raise `request_timeout_seconds` in `config.json`. |
| `[stopped] Agent reached the step limit` | Rephrase the request, or raise `max_steps`. |
| `tools=0` at startup | There's no `.orvik/tools` in this folder. Start Orvik in a folder that has one. |
| `Tool error: ... tool.json: ...` at startup | That tool's `tool.json` has a mistake; the message says which file and what's wrong. |
| `[config] ... Keeping the previous settings.` | `config.json` has a mistake (bad JSON or an unknown setting); fix it and send your next message. |
| `command not found: orvik` | Use `.venv/bin/orvik`, or install the command with `uv tool install`. |
| Answers get slow in a long chat | `/reset`. The whole conversation is resent on every step. |

## More

- **[ARCHITECTURE.md](ARCHITECTURE.md):** how Orvik works inside (agent loop, safety pipeline, AI backends, tool format, tests)
- **[ROADMAP.md](ROADMAP.md):** what's built and what's next
