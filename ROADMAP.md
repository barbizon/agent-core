# Orvik learning roadmap

Steps to turn Orvik into an agent that works like Claude Code or Codex.
Work through them one at a time.

**Legend:** ✅ done · 🟡 partly done · ⬜ not started

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
| ✅ | Audit log | Every step is recorded in `.orvik/logs/audit.jsonl`. |
| ✅ | Debug mode (`-d`, `/debug`) | Shows each message's journey through the program, step by step. |
| ✅ | Own folder `.orvik/` | Config, secrets, logs and tools in one place, invisible to the agent's own tools. |

---

## Stage 1: The agent can do things

### 1. 🟡 Workspace confinement: keep the agent inside one folder

The agent can only touch files inside one folder you choose, the way a
contractor is only given the key to one room.

- ✅ Paths that try to escape the folder (`..`, absolute paths, symlinks that point outside) are refused.
- ✅ The workspace is the folder you start the agent in (no longer your whole home folder).
- ✅ The agent's own `.orvik/` folder is hidden and can't be read or written, in any letter case.
- ⬜ Other secret files inside the workspace (a project's `.env`, `*.pem`,
  `.git/config`, `.git/hooks`) aren't blocked yet.

### 2. 🟡 Read tools: the agent can look

Tools that only look and never change anything, so they run without asking you.

- ✅ `list_folder`: see which files are in a folder.
- ✅ `read_file`: read a text file.
- ⬜ `grep`: search for a word or phrase across many files ("where is `send` used?").

### 3. 🟡 Write tools: the agent can change files

Tools that change files, so they always ask you first.

- ✅ `write_file`: create a file, or replace a whole file.
- ⬜ `edit_file`: change only a small part of a file (find this text, replace
  it with that text). It's safer and clearer than rewriting the whole file.
- ⬜ Read-before-write rule: the agent must read a file before changing it,
  so it doesn't overwrite things it never saw.
- ⬜ Show the diff when asking for approval ("these 3 lines change") instead
  of dumping the whole file.

### 4. ⬜ Run commands safely

A tool that runs terminal commands, such as `pytest` or `git status`. It's
the most powerful tool and the most dangerous one, so it needs an
operating-system sandbox: the command can only write inside the workspace
and has no internet access unless allowed.

---

## Stage 2: The agent gets smarter

### 5. ⬜ Skills

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

### 8. ⬜ Memory limits (context management)

The AI can only read a limited amount of text at once. In a long chat, the
agent summarizes older parts so the conversation still fits, without losing
important details.

---

## Stage 3: It feels like a real CLI

### 9. ⬜ Approval modes

Choose how often you're asked: never allow changes (read-only), ask every
time (today's behavior), auto-approve file edits, or allow everything. Plus
"yes, and don't ask again this session" for safe repeats like `git status`.

### 10. ⬜ Saved conversations

Close the program and continue the same chat tomorrow (`--continue`).

### 11. ⬜ Live answers (streaming)

See the answer appear word by word instead of waiting for all of it, plus a
spinner like "Reading app.py…".

### 12. ⬜ Nicer terminal

Multi-line typing, up/down arrows for earlier messages, formatted text,
colored code and changes.

### 13. 🟡 Settings files

Save your preferences (model, approval mode, workspace folder) in a file
instead of passing flags every time.

- ✅ `.orvik/config.json` with live reload, and `/config` to see every value and its source.
- ✅ `/debug on|off` to switch debug mode during a session.
- ⬜ Two levels: a user folder (`~/.orvik/`, can loosen rules) and the
  project folder (can only tighten them).
- ⬜ Security settings (blocked files, approval mode) in the file.

### 14. ⬜ One-shot mode

Run `orvik -p "fix the failing test"`: it does the task and exits.
Useful in scripts.

### 15. ⬜ Undo

The agent saves a copy before each change, and `/undo` restores it if you
don't like the result.

### 16. ⬜ Usage display

See how many tokens were used and how much room is left in the AI's memory.

---

## Stage 4: Advanced

### 17. 🟡 Plug-in tools

Add tools without changing the agent's code.

- ✅ Each tool is described in `.orvik/tools/<name>/tool.json` (description,
  parameters, risk level, what to run), checked strictly at startup.
- ⬜ Tools in any language: `"run": {"command": [...]}`, JSON in, JSON out.
  Open question: respect a command tool's declared risk level, or always ask?
- ⬜ Tool parameters for relevance, e.g. `sort_by` / `limit` on `list_folder`,
  so the AI asks only for what it needs (see the "smallest file" case).
- ⬜ MCP: connect ready-made tool servers (Jira, databases, browsers).
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

Codex (your subscription) costs about 6 seconds per step, shows answers only
when finished, and allows one tool per step. A paid API, a long-running
Codex process, or a local model would change speed, cost and abilities.

---

## Suggested order

Finish **1** first: block the remaining secret files inside the workspace.
Then **2** (`grep`) and **3** (`edit_file`, diffs).
