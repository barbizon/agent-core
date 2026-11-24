# Orwick learning roadmap

Steps to turn Orwick into an agent that works like Claude Code or Codex.
Work through them one at a time.

**Legend:** ✅ done · 🟡 partly done · ⬜ not started

---

## Status at a glance

| # | Step | Status | What it means, in plain words |
|---|---|---|---|
| 1 | Stay inside one folder | ✅ | Orwick only touches the folder you start it in. Secret files (passwords, keys, tokens) are invisible to it, and files that run code automatically can't be changed. |
| 2 | Read tools | ✅ | Orwick can list folders, read files, and search text across files (`grep`) without asking you, because looking changes nothing. |
| 3 | Write tools | ✅ | Orwick can create files and change parts of them. It must read a file before changing it, and you see exactly which lines change before you say yes. |
| 4 | Run commands safely | ✅ | Orwick runs commands like `pytest` after you say yes, inside a macOS sandbox: no internet, writes only in the project, can't see your home folder or secrets. |
| 5 | Skills | ✅ | Instruction folders loaded only when needed (small index, `use_skill`); built-in `feature-docs` (five-tab feature docs); session docs; sessions linked to Jira features. |
| 6 | Project instructions | ⬜ | Orwick reads a project's `AGENTS.md` rules automatically. |
| 7 | Planning | ⬜ | For big tasks, Orwick writes a plan first and you approve it before anything changes. |
| 8 | Memory limits | ✅ | Long chats are shortened (old tool output trimmed, then summarized) to fit a budget; a warning comes first; `/compact` does it now. |
| 9 | Approval modes | ⬜ | Choose how often Orwick asks: never change anything, ask every time, or trust file edits. |
| 10 | Saved conversations | ✅ | Every session is saved; `/sessions`, `/resume <id>`, `orwick --resume`; the whole chat is shown again. |
| 11 | Live answers | ⬜ | See the answer appear word by word. |
| 12 | Nicer terminal | 🟡 | Done: icons, colors and panels for the banner, `/help`, `/tools`, `/policy`, `/config`. Left: multi-line typing, history with arrow keys. |
| 13 | Settings files | 🟡 | Done: `config.json` with live reload, `/config`, `/debug`, and main settings in `~/.orwick/config.json` for every folder. Left: designing per-folder overrides and security settings. |
| 14 | One-shot mode | ⬜ | `orwick -p "task"` does one task and exits, for scripts. |
| 15 | Undo | ⬜ | `/undo` restores a file Orwick changed. |
| 16 | Usage display | ✅ | `/status` shows this session's activity, tokens, context fill and your subscription limits (live for Claude, last known for Codex). |
| 17 | Plug-in tools | 🟡 | Done: tools described in `tool.json`; built-in tools in every folder, plus yours (`~/.orwick/tools`) and a project's; risk set by the code; on/off per settings and per session. MCP servers (usual `mcpServers` format, tools off until switched on). Left: tools in any language, `sort_by`/`limit` options. |
| 18 | Hooks | ⬜ | Your own scripts that run before or after a tool. |
| 19 | Helper agents | ⬜ | Orwick hands side tasks to a helper and gets a summary back. |
| 20 | Parallel reads | ⬜ | Read several files at once instead of one by one. |

---

## Already built (the foundation)

| | What | In plain words |
|---|---|---|
| ✅ | Chat with an AI | You type, the AI answers, through your ChatGPT subscription (Codex). |
| ✅ | Agent loop | The AI can ask to use a tool, gets the result, and continues until it has an answer. |
| ✅ | Safety gateway | Every tool request passes checks: does the tool exist, are the inputs valid, is it allowed, did you approve it. |
| ✅ | Human approval | Before anything that changes files, you're asked "Approve? y/N". |
| ✅ | One-time permission | Your "yes" works only once, and only for exactly the action you approved. |
| ✅ | Timeouts and retries | A stuck tool is stopped; a briefly failing read is retried. |
| ✅ | Audit log | Every step is recorded in the session's `.orwick/sessions/<id>/audit.jsonl`. |
| ✅ | Debug mode (`-d`, `/debug`) | Shows each message's journey through the program, step by step. |
| ✅ | Own folder `.orwick/` | Config, secrets, logs and tools in one place, invisible to the agent's own tools. |

---

## Stage 1: The agent can do things

### 1. ✅ Workspace confinement: keep the agent inside one folder

The agent can only touch files inside one folder you choose, the way a
contractor is only given the key to one room.

- ✅ Paths that try to escape the folder (`..`, absolute paths, symlinks that point outside) are refused.
- ✅ The workspace is the folder you start the agent in (no longer your whole home folder).
- ✅ The agent's own `.orwick/` folder is hidden and can't be read or written, in any letter case.
- ✅ **Secret files are invisible:** `.env`, keys (`*.pem`, `*.key`, `id_rsa`…),
  `.ssh/`, `.aws/`, `.git/config` and similar can't be listed, searched, read or
  written, so their content never reaches the AI. Templates like
  `.env.example` and public keys (`*.pub`) stay readable.
- ✅ **Files that run code automatically can't be changed:** `.git/hooks/`,
  shell startup files like `.zshrc`, `.envrc` (direnv), `.vscode/` (editor
  tasks) and `.github/workflows/` (CI). Orwick can read them, never write them.
- ✅ Upper/lower case and symlinks can't be used to get around these rules.

### 2. ✅ Read tools: the agent can look

Tools that only look and never change anything, so they run without asking you.

- ✅ `list_folder`: see which files are in a folder.
- ✅ `read_file`: read a text file. It also returns a `version`, used by the write tools.
- ✅ `grep`: search for a word or phrase across many files ("where is `send` used?").
  It returns file, line number and the matching line, **stops after 50 matches**
  (configurable up to 500) and says when there were more, so a big search
  doesn't flood the AI's memory. It skips binary files, big files, secrets,
  and folders like `.git` and `node_modules`.

### 3. ✅ Write tools: the agent can change files

Tools that change files, so they always ask you first.

- ✅ `write_file`: create a file, or replace a whole file.
- ✅ `edit_file`: change only a small part of a file (find this text, replace
  it with that text). If the text isn't found, or appears more than once,
  nothing changes and the AI is told why.
- ✅ **Read before write:** to change an existing file, the AI must pass the
  `version` it got from `read_file`. That proves it read the file, and if the
  file changed on disk since (e.g. you edited it), the change is refused and
  the AI has to read it again.
- ✅ **See the change before you approve:** the approval prompt shows exactly
  which lines are removed (`-`) and added (`+`), not the whole file. If the
  change can't work anyway, you aren't asked at all.

### 4. ✅ Run commands safely

A tool that runs terminal commands, such as `pytest` or `git status`. It's
the most powerful tool and the most dangerous one, so approval isn't enough:
`pytest` runs code you never see in the prompt.

- ✅ `run_command`: you approve **every** command; the prompt shows the full
  command, and hidden characters can't disguise it.
- ✅ **macOS sandbox** that starts from "deny everything": writes only in the
  project, no internet, no reading your home folder, secrets or `.orwick`, no
  changing hooks/CI/editor files. Everything the command starts stays inside.
- ✅ **No sandbox, no commands:** where it can't be applied, commands are refused.
- ✅ **Clean environment:** no keys or tokens are passed; a private temp folder
  is deleted afterwards.
- ✅ Timeout (default 60 s, max 300) kills the command and its children;
  long output keeps its start and end.
- ✅ `git` works when the repo's `.git/config` holds no token; otherwise it stays blocked.
- ⬜ Later: network on request, Linux sandbox, "don't ask again" for safe commands (step 9).

---

## Stage 2: The agent gets smarter

### 5. ✅ Skills

Instruction folders (`SKILL.md`) that teach the agent how to do a specific
job. The AI first sees only each skill's name and short description, and
loads the full instructions when a task needs them.

### 6. ⬜ Project instructions (`AGENTS.md` / `CLAUDE.md`)

A file in your project with rules ("use Python 3.11", "run tests with
pytest"). The agent reads it automatically at the start, so you don't
repeat yourself.

### 7. ⬜ Planning

For bigger tasks, the agent first writes a to-do list and updates it as it
works. A "plan mode" lets it only read and propose; nothing changes until
you approve the plan.

### 8. ✅ Memory limits (context management)

The AI can only read a limited amount of text at once. In a long chat, the
agent summarizes older parts so the conversation still fits, without losing
important details.

---

## Stage 3: It feels like a real CLI

### 9. ⬜ Approval modes

Choose how often you're asked: never allow changes (read-only), ask every
time (today's behavior), auto-approve file edits, or allow everything. Plus
"yes, and don't ask again this session" for safe repeats like `git status`.

### 10. ✅ Saved conversations

Close the program and continue the same chat tomorrow (`--continue`).

### 11. ⬜ Live answers (streaming)

See the answer appear word by word instead of waiting for all of it, plus a
spinner like "Reading app.py…".

### 12. 🟡 Nicer terminal

- ✅ Icons, colors and panels for the startup banner, `/help`, `/tools`,
  `/policy` and `/config` (grouped by topic). Plain text when not a terminal
  or when `NO_COLOR` is set.
- ⬜ Multi-line typing, up/down arrows for earlier messages, formatted
  answers, colored code.

### 13. 🟡 Settings files

Save your preferences (model, approval mode, workspace folder) in a file
instead of passing flags every time.

- ✅ `.orwick/config.json` with live reload, and `/config` to see every value and its source.
- ✅ `/debug on|off` to switch debug mode during a session.
- ✅ Main settings in `~/.orwick/config.json`, used in every folder (not `workspace`).
  The folder's own file still wins. Edited by the Mac app's Settings screen.
- ⬜ Per-folder overrides, designed properly: which settings a folder may change,
  and whether a folder may only tighten security rules.
- ⬜ Security settings (blocked files, approval mode) in the file.

### 14. ⬜ One-shot mode

Run `orwick -p "fix the failing test"`: it does the task and exits.
Useful in scripts.

### 15. ⬜ Undo

The agent saves a copy before each change, and `/undo` restores it if you
don't like the result.

### 16. ✅ Usage display

`/status`: session activity (messages, AI steps, tool calls, approvals),
tokens per backend, how full the AI's memory is, and your subscription
limits with reset times (live for Claude, last known for Codex).

---

## Stage 4: Advanced

### 17. 🟡 Plug-in tools

Add tools without changing the agent's code.

- ✅ Each tool is described in `.orwick/tools/<name>/tool.json` (description,
  parameters, risk level, what to run), checked strictly at startup.
- ⬜ Tools in any language: `"run": {"command": [...]}`, JSON in, JSON out.
  Open question: respect a command tool's declared risk level, or always ask?
- ⬜ Tool parameters for relevance, e.g. `sort_by` / `limit` on `list_folder`,
  so the AI asks only for what it needs (see the "smallest file" case).
- ✅ MCP: connect ready-made tool servers (tickets, databases, browsers); local servers, see stage 17.
  Their tools still go through the safety gateway.

### 18. ⬜ Hooks

Your own scripts that run automatically before or after a tool, e.g. "block
any command containing `rm -rf`", or "format code after every edit".

### 19. ⬜ Helper agents (subagents)

The main agent hands a side task (e.g. "search the whole codebase") to a
helper agent, which works separately and reports back a summary.

### 20. ⬜ Parallel reads

When the AI wants to read 5 files, read them all at once instead of one
after another.

---

## Separate decision: which AI service to use

- ✅ Codex (ChatGPT subscription): about 6 seconds per step, one tool per step.
- ✅ Claude (Claude subscription): one long-running process, about 1–2 seconds
  per step after the first; clean (no skills, memory or hooks).
- ✅ Switch during a chat with `/backend`; the conversation is kept.
- ⬜ Live answers (streaming), several tools per step, a local model.

---

## Suggested order

Stage 1 is done. Next: **6** (project instructions, `AGENTS.md`), **9**
(approval modes), or **15** (undo), since Orwick can now change and run things.
