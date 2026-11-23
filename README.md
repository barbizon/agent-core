# Orwick

An AI agent for your terminal. It chats with you, reads and writes files and
runs commands in the folder you start it in, and **asks you before it changes
or runs anything**.

> **The name.** "Orwick" is an invented word. The ending *-vik* means "bay" in
> Scandinavian languages (as in Reykjavík): a sheltered harbour where every
> ship is checked before it sails out. In the same way, every action Orwick
> takes is checked first.

## What's in this repository

| Folder | What it is |
|---|---|
| `src/agent_core/` | The agent and its command line (`orwick`), in Python |
| `src/agent_core/tools/builtin/` | The built-in tools, one `tool.json` each |
| `src/agent_core/skills_builtin/` | The built-in skills (`SKILL.md`, with their buttons in `ui.json`) |
| `tests/` | The agent's tests (`pytest`) |
| `app/` | Orwick for Mac: the app that runs `orwick` in a window, in Swift ([app/README.md](app/README.md)) |
| `docs/` | [ARCHITECTURE.md](docs/ARCHITECTURE.md) and [ROADMAP.md](docs/ROADMAP.md) |

## What it does

- **Chats with an AI** through your ChatGPT subscription (Codex CLI) or your Claude subscription (Claude Code CLI). No API key needed. Switch any time with `/backend`.
- **Looks at files:** lists folders, reads text files, and searches text across files.
- **Changes files only with your OK:** before every change, it shows you exactly which lines change and waits for `y`.
- **Runs commands safely:** tests, `git status` and the like run only after your OK,
  inside a macOS sandbox: no internet, writes only in the project, no access to your home folder.
- **Stays in its folder:** it can only reach the folder you start it in.
- **Never sees your secrets:** `.env` files, keys, `.ssh/`, `.git/config` and Orwick's own `.orwick` folder are invisible to it.

## Requirements

- Python 3.11 or newer
- At least one of:
  - the [Codex CLI](https://github.com/openai/codex), signed in with your ChatGPT account: `codex login`
  - [Claude Code](https://claude.com/claude-code), signed in with your Claude account (run `claude` once and log in)

## Install

```bash
cd ~/Documents/orwick
uv venv .venv && uv pip install -e '.[dev]'
# without uv: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

To run `orwick` from any folder, also install it as a command:

```bash
uv tool install --editable ~/Documents/orwick
```

## Start

```bash
cd ~/Documents/orwick
.venv/bin/orwick            # or just `orwick` after `uv tool install`
```

```
╭─ ⚓ orwick
│  🤖 codex · codex default    🐞 debug off
│  🧰 6 Orwick tools + 1 Codex tool (web_search)
│  📂 ~/Documents/orwick
│  🔧 .orwick/config.json (loaded) · reloads automatically
╰─ Type /help for commands.

👤 You › what files are here?
```

Orwick works on **the folder you start it in**. Its standard tools are built in, so they work in
any folder. Your main settings are in `~/.orwick/config.json`; a folder can add its own settings,
tools and saved sessions in **`.orwick/` inside it**.

| Option | What it does |
|---|---|
| `-d`, `--debug` | Show every step of each request (see [Debug mode](#debug-mode)) |
| `--model NAME` | Use a specific AI model |
| `--max-steps N` | Max AI calls per message (default 10) |
| `--backend codex\|claude\|openai` | Which AI answers: `codex` (ChatGPT subscription), `claude` (Claude subscription), `openai` (paid API) |
| `--config PATH` | Use another settings file |
| `--env-file PATH` | Use another secrets file |

## Chat commands

| Command | What it does |
|---|---|
| `/help` | List the commands |
| `/tools` | List the tools Orwick can use |
| `/config` | Show every setting, where it comes from, and what it means |
| `/backend` · `/backend claude [model]` | Show or switch the AI service during the chat; the conversation is kept |
| `/status` | Usage: what this session did, tokens, and your subscription limits |
| `/debug on` · `/debug off` | Switch debug mode for this session (`/debug` alone shows the status) |
| `/policy` | Show what kinds of actions are allowed |
| `/clear` | Start a new session in this folder; the current one stays saved. Counts and `/backend` / `/debug` choices start fresh (like restarting Orwick) |
| `/sessions` | Saved sessions in this folder, most recently used first |
| `/resume <id>` | Continue a saved session (also: `orwick --resume <id>`); the whole conversation is shown again |
| `/history` | Show this session's conversation again |
| `/trace [on\|off]` | Show or hide the title line per tool call, e.g. `▸ Read app.py (lines 1–120)` (off: just a count after the answer; `/debug` shows full calls and results) |
| `/feature [KEY\|off]` | Link the session to a Jira issue (also `orwick --feature PROJ-16765`) |
| `/name [text\|off]` | Show or set the session's name; `/sessions` and the app show it instead of the first message. Saved in the session's `name.txt` |
| `/skills [on\|off <name>]` | The skills Orwick can load and which are on; switch one for this session only (saved in the session's `skills.json`; the app's Info panel checkboxes do the same). Mid-conversation the AI is told with your next message, so the conversation isn't sent again |
| `/map [flow or topic] [--name <name>] [--with <folders>]` | Service map: the flows this service offers and how they connect (messages, receivers, calls); saved to `~/.orwick/maps/` |
| `/related [add\|remove <folders>\|clear]` | Other projects this session may read, never change (shown without arguments) |
| `/do <skill> <action>` | Run a skill's action, e.g. `/do feature-docs read-ticket` (asks for the ticket ID) |
| `/tools on\|off <name>` | Switch a tool for this session only (`/tools reset` goes back to your settings) |
| `/compact` | Shorten the conversation now: the AI summarizes the older part (`/history` still shows everything) |
| `/reset` | Clear the conversation but keep this session (its counts and folder) |
| `/exit` | Quit (Ctrl-D also works) |

Ctrl-C stops the current answer; the chat stays open.

**`/status`** shows two panels:

```
╭─ 📊 Session
│  ⏱  6s running · since 17:10
│  💬 1 message   🔁 3 AI steps   🧰 2 tool calls
│     ✓ 2 ok   ✗ 0 failed or refused
│  🧠 history  7 messages · 1,093 characters (~273 tokens)
╰─
╭─ 🟠 Claude · claude-opus-5-5  (subscription)
│  Subscription limits   live, from Claude
│  5-hour       ████░░░░░░░░░░░░░░░░  18%  resets 18:30 (in 1h 19m)
│  7-day        █░░░░░░░░░░░░░░░░░░░   6%  resets Tue 20:00 (in 6d 2h)
│  This session
│  🔁 3 requests · 2.2s average
│  🔢 new in 1.2k (1.1k cache write) · out 481
│     + 8.6k re-read from the cache (the conversation, sent again with each request; cheap)
│  💲 $0.04 at API prices · covered by your subscription, not charged
╰─ Limits are for your whole account, not just Orwick.
```

What it shows depends on the backend:
- **Claude:** live 5-hour and 7-day limits (Claude reports them with every reply), tokens, how full the context is, and what the same use would cost at API prices (you aren't charged).
- **Codex:** tokens per request. Codex doesn't report limits to Orwick, so the weekly limit shown is the **last known** value from a Codex session you ran yourself, with its age.
- **OpenAI API:** tokens; billed per token.

**Sessions are saved:** each conversation has a folder, `.orwick/sessions/<date_time_id>/`, shown
in the start banner. Orwick saves it before every prompt, so nothing is lost if it stops:

| File | What it holds |
|---|---|
| `conversation.json` | the conversation: your messages, answers, tool calls and their results |
| `meta.json` | title (your first message), times, number of messages |
| `status.json` | the `/status` numbers and the tool list (no message text) |
| `audit.jsonl` | what was run and approved |

- **Come back later:** `/sessions` lists them, `/resume <id>` or `orwick --resume <id>` continues one. While it loads, a line says so ("⠹ Loading session <id> · 2s") instead of a bare cursor.
  The whole earlier conversation is shown again, the way it looked (questions, answers, tool calls),
  so you can scroll back; `/history` shows it again anytime. Showing it costs no tokens.
  The earlier conversation is sent to the AI again on your next message, and files may have changed
  since, so ask Orwick to read them again if it matters.
- **One place at a time:** a session open in one Orwick can't be resumed in another. Closing the
  terminal window or tab ends Orwick properly: the session is saved and can be opened again at once.
- **Kept private:** the folder is inside `.orwick/`, which Orwick's tools can't see; files are
  readable only by you, and a `.gitignore` keeps sessions out of git.
- **Cleanup:** the 50 most recently used are kept. A session where you never sent a message is
  removed when Orwick exits.
- `--session-id <id>` names a new session (the Orwick Mac app uses it to find the folder).

**About the Claude backend:** it's a *clean* Claude: your Claude Code skills,
memory, `CLAUDE.md`, hooks, plugins and MCP servers are all off. Of Claude Code's
own tools, only the safe ones you allow are on (see below), so files and commands
go through Orwick's tools and approvals. It uses your subscription login, never an API
key, and counts against your subscription's usage limits.

The screens use icons and colors: 🟢 runs freely, 🟡 asks before changing
files, 🔴 asks before running commands. Colors turn off automatically when
the output isn't a terminal, or when `NO_COLOR` is set.

## Tools

| Tool | What it does | Asks you first? |
|---|---|---|
| `list_folder` | Lists files and folders | No |
| `read_file` | Reads a text file (up to 200 KB), or some of its lines. A whole read of a big file returns the lines that fit (about 16,000 characters) and says how to read the rest | No |
| `grep` | Searches text across files; shows file, line and text (first 50 matches) | No |
| `edit_file` | Changes part of a file: replaces one exact piece of text | **Yes, every time** |
| `write_file` | Creates a file, or replaces a whole file | **Yes, every time** |
| `run_command` | Runs a terminal command (e.g. `pytest -q`) in a sandbox | **Yes, every time** |
| `read_pull_request` | Reads a Bitbucket pull request: its branches, changed files, or one file's diff (commands have no network). Needs `BITBUCKET_EMAIL` and `BITBUCKET_TOKEN` in the environment or `~/.orwick/.env` | No |
| `save_analytics_report` | Keeps an analytics' latest report in Orwick's own store, for the app | No |
| `collect_review_comments`, `read_review_comments`, `save_review_patterns` | Review analytics: read pull request review comments into Orwick's own store and group them into common issues (suggestions you switch on in the app) | No |
| `run_subagent` | Hands a side task to a helper agent with a fresh conversation and read-only tools (more with `allow_changes`, each still asking you); only its report comes back, so its reads don't fill your conversation | No (its own writes and commands ask) |

When Orwick wants to change something, it stops and shows you the exact change:

```
=== APPROVAL REQUIRED ===
Tool:       edit_file
Risk level: write
Arguments:  { "path": "src/app.py", "old_text": "...", "new_text": "...", "version": "3b80bd5416bc651b" }
Preview:
  --- src/app.py (before)
  +++ src/app.py (after)
   def send():
  -    return 1
  +    return 2
Approve this action? [y/N]:
```

Only `y` approves. Anything else (Enter, `n`, Ctrl-C) means no, and Orwick
tells you it didn't do it.

For a command, the preview shows the full command and what the sandbox allows:

```
=== APPROVAL REQUIRED ===
Tool:       run_command
Risk level: dangerous
Preview:
  Command:
    $ pytest -q
  Folder:   .
  Timeout:  60s
  Sandbox:  writes only inside the workspace · no network
            can't read your home folder, secret files or .orwick
            can't change .git/hooks, .vscode, .github/workflows, .envrc
Approve this action? [y/N]:
```

Hidden characters (like terminal escape codes) are shown as `\x1b`, so a
command or file can't disguise what you approve.

**Safety rules the tools always follow:**

- **Read before change:** Orwick must read a file before changing it. If the file
  changed since (e.g. you edited it), the change is refused and it reads again.
- **Secrets stay hidden:** `.env`, `*.pem`, `*.key`, SSH keys, `.ssh/`, `.aws/`,
  `.git/config` and similar can't be listed, searched or opened.
  `.env.example` and `*.pub` are fine.
- **No sneaky code:** files that run code on their own can be read but never
  changed: `.git/hooks/`, shell files like `.zshrc`, `.envrc` (direnv),
  `.vscode/` (editor tasks) and `.github/workflows/` (CI).

**What commands can and can't do** (enforced by macOS itself, so it holds
even for code the command runs, like your tests):

- ✅ Read the project, the system, and installed Python/Go/Rust/Node toolchains.
- ✅ Write inside the project, and in a private temp folder that is deleted afterwards.
- ❌ No internet. `pip install` / `npm install` won't work; run those yourself.
- ❌ No reading your home folder outside the project (`~/.ssh`, `~/.zshrc`, other projects…).
- ❌ No reading secret files or `.orwick`, and no changing the "no sneaky code" files above.
- ❌ No keys or tokens from Orwick's environment: commands start with an empty one.
- ⏱ Stopped after 60 s by default (max 300), together with everything they started.
- 📄 Long output keeps the start and the end (where errors and summaries are).
- 🍎 macOS only for now. Where no sandbox can be applied, commands are refused.

**Switching tools on and off:** a switched-off tool is never offered to the AI, so it can't be used.

- **Main list:** the `tools` setting in `~/.orwick/config.json`, for example
  `"tools": {"run_command": false}`. A tool not listed is on. Every session starts from it. The
  Orwick Mac app edits it in Settings → Tools.
- **This session only:** `/tools off <name>`, `/tools on <name>`, `/tools reset`. The main list
  doesn't change. The choice is saved with the session (`tools.json` in its folder), so it comes back
  when you resume; `/clear` starts from the main list. The Mac app's Info panel has the same switches.
- A running session picks up changes with your next message. `/tools` marks what's off and what was
  changed for this session.

## Settings

Every limit below accepts **`0` for no limit**: `max_steps`, `request_timeout_seconds`,
`max_tool_result_chars`, `history_budget_tokens` (never shortened or warned) and `history_warn_percent`
(never warns).

**Main settings**, used in every folder, are in `~/.orwick/config.json`. They take the same
settings as the folder file below, except `workspace` (the workspace is always the folder Orwick
starts in). The Orwick Mac app's Settings screen edits this file.

Everything Orwick keeps for one folder is in that folder's `.orwick` folder:

```
.orwick/
├── config.json      settings (applied live when you edit them)
├── .env             secrets: only needed for the openai backend
├── .env.example     template for .env
├── sessions/        one folder per conversation, saved so you can resume it
├── logs/            the shared audit log, if you set audit_log to it
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
  "audit_log": null,
  "workspace": null,
  "debug": false
}
```

| Setting | Meaning |
|---|---|
| `backend` | `codex` (ChatGPT subscription), `claude` (Claude subscription) or `openai` (paid API) |
| `model` | AI model; `null` = the backend's default |
| `max_steps` | Max AI calls per message, so it can't loop forever (default 25, `0` = no limit; two steps before the limit the AI is told to wrap up and save what it has) |
| `request_timeout_seconds` | How long to wait for one AI reply (default 300; a timeout restarts claude and re-sends the conversation) |
| `max_tool_result_chars` | Tool output longer than this is cut before it reaches the AI (default 20,000) |
| `audit_log` | Where the log of every action is written; `null` = each session's own folder |
| `workspace` | The folder Orwick may use; `null` = the folder you started in |
| `debug` | Same as `-d` |
| `trace` | A title line per tool call in the chat (default on); `/trace on\|off` for one session |
| `history_budget_tokens` | How big a conversation may get before Orwick shortens it (default 100,000; see below) |
| `history_warn_percent` | Tell you when the conversation reaches this share of its budget (default 70) |
| `tools` | Tools switched on or off, e.g. `{"run_command": false}`; not listed = on |
| `claude_tools`, `codex_tools` | The AI's own tools to switch on (see below); `null` = safe ones, `[]` = none |

- **Edit either `config.json` while Orwick runs:** the change applies before your next message, and the conversation is kept.
- **Order of precedence:** command-line option > the folder's `.orwick/config.json` > `.env` > `~/.orwick/config.json` > default. `/config` shows which one won for each setting.
- **The API key** (only for `backend: openai`) goes in `.orwick/.env`, never in `config.json`.
- **`~/.orwick/.env`** is read too, for every folder: logins the tools need, e.g. `BITBUCKET_EMAIL` and
  `BITBUCKET_TOKEN` for `read_pull_request`. A value may point at a variable your shell already sets
  (`BITBUCKET_TOKEN=${MY_TOKEN}`). The folder's `.env` and the environment win.

## The AI's own tools

Claude and Codex come with built-in tools of their own. Those run **inside the AI, not through
Orwick**, so Orwick can't ask you or sandbox them. You choose per AI which ones are on:

| AI | Safe (on by default) | Dangerous (off unless you switch them on) |
|---|---|---|
| Claude | `WebSearch`, `WebFetch` | `Read`, `Edit`, `Write`, `Bash`, `NotebookEdit`, `Task` |
| Codex | `web_search` | `shell` (its own commands and file changes) |

- Settings: `claude_tools` and `codex_tools`, lists of names. `null` (or not set) means the safe
  ones; `[]` switches all off. The Orwick Mac app has a section per AI in Settings → Tools, with
  **All off** and **Safe defaults**.
- With a dangerous tool on, that AI works in your folder and uses it **without asking you**.
- The AI is told exactly which of its own tools it may use, and that web content is data, not
  instructions. If Codex uses one that's off anyway, Orwick ignores that reply and says so.
- Anything that acts outside your Mac on its own (scheduling, notifications, publishing) is never
  offered.

## Skills, features and session docs

**Skills** teach Orwick a job. A skill is a folder with a `SKILL.md` (a short header with `name` and
`description`, then the instructions), the same format Claude Code uses. They're built for small
context: the AI always sees only a short index (name and one line each, about 400 tokens at most),
and loads a skill's instructions with the read-only `use_skill` tool only when a task needs it.

- Built in: **`feature-docs`**: turns the session's Jira ticket into Orwick's feature docs, five tabs
  kept in the session: **Product Requirement** (plain words), **Technical Design** (the ten numbered
  sections), **Flows** (which service and endpoint handles each step, with sequence diagrams),
  **To Do** (done / left / questions / execution plan / tasks per flow) and **Code Review**. It reads
  the ticket, its parent and siblings through MCP and the code through `grep`, and saves every tab
  with **one** approval (`write_session_docs`). The Orwick Mac app shows them as tabs.
- Built in: **`service-map`**: give it a service folder and it finds **the flows that service offers**
  (HTTP routes, message consumers, scheduled workers) and **how each connects to the whole system**:
  the messages it sends and who receives them and why, what it receives from others, whom it calls,
  its tables. Ask for the whole service (`/map`) or one flow or topic (`/map the refund job`).
  It **finds the repos that take part itself**: it reads the **code root** — the `code_root` setting
  (e.g. `~/Documents/go`; also in the app's Settings), else the folder around this one when it holds
  other git repos — never changing it, and searches it for the same exchanges, routing keys and
  routes. The map stays on this service: other repos are written up in words (endpoint, handler,
  what happens there), one hop deep. A service kept
  elsewhere: `/map --with ~/other/place/wallet` (or `/related add`). Before scanning it asks what
  to name the map (`/map Refund job --name refund` names it up front).
  The map is saved **outside any session**, in the map library `~/.orwick/maps/<name>/`
  (`write_service_map`, one approval), so any session can update it and the Mac app lists every map
  under **Service maps** and draws its diagrams. The app's **New Service Map** panel picks the service,
  the flow and the name, and runs it for you.
- **Skill contracts.** A skill may ship `contract.json`: the docs it may create, which are required,
  and each doc's required table columns, section headings and Mermaid diagram types. The write tool
  checks every doc against it *before* the approval prompt, so a doc in the wrong shape is never shown
  nor saved; the AI gets the list of problems and fixes them. `service-map` has one
  (`map`, `flows` required; `messages`, `calls`, `services`, `open-questions`).
- Built in: **`code-check`**: the final check of finished code, with a general checklist; your team's
  own rules are added as a skill of yours (see **Additions** below).
  **Review code** (`/do code-check review-code`) first asks **Review this session's PR?** Yes (Enter):
  it finds the open pull request of the session's branch (`read_pull_request`) and checks the branch
  against the PR's destination plus uncommitted work; with no pull request yet, against `develop`. No:
  it asks for a **PR link** and reviews that pull request from Bitbucket. It goes through the Code Review
  Checklist (security, tests, errors, clean code, performance, correctness, design, style, API) and
  writes the **Review Findings** tab (findings with `file:line` and the fix). **Code Scoring**
  (`/do code-check grade-code`) asks the same, adds ticket fit and writes the
  **Grade** tab: 100 minus deductions, the hard caps (build/test fails 40, Critical 49,
  Missing 69), grade A–D and what must be fixed before the PR. It reports only; it never edits
  code. git works in any repo inside the workspace whose `.git/config` holds no credentials.
- **Actions:** a skill can offer buttons in an optional `ui.json` next to `SKILL.md` (label, icon,
  inputs to ask for, the prompt to send, and a `hint`: the button's tooltip in the app), plus a
  group `title` and `hint` at the top: the app shows each skill's buttons together under that title). `feature-docs` offers **Read ticket** (asks for the ticket
  ID, links the session to it, and makes the docs) and **Update docs**; both may use up to 40 steps
  (an action's `max_steps`). Run them with
  `/do feature-docs read-ticket` in any terminal; the Orwick Mac app shows them as buttons above the
  terminal. `ui.json` costs no context: it's never sent to the AI.
- Built in: **`review-analytics`**: learns from past pull request reviews. **Learn from PRs**
  (`/do review-analytics learn`) reads the review comments of pull requests changed since last time
  (it asks which repos the first time; Bitbucket), with the code each was written on and the replies,
  and groups them into the issues reviewers raise again and again, counted by pull requests. The list
  is kept in `~/.orwick/analytics/`, outside any project. Patterns start as suggestions: you switch on
  the ones that count in the Mac app's **Analytics** screen, under **Common review issues** (`decisions.json`), and those are written
  out as the skill `code-check-known-issues`, an addition to `code-check`, so **Review code** and
  **Code Scoring** look for them too. It changes no code and posts nothing.
- **Your own analytics:** `orwick --create-analytics "Slow pull requests" --wanted "Which pull requests
  wait longest for a review? Per repo."` saves an analytics: a skill of yours in
  `~/.orwick/skills/<name>/` (header `kind: analytics`) made from your words, with how to work it out
  and how to report. Nothing runs then. Run it with `/do <name> run` in a new session, or from the Mac
  app's **Analytics** screen (which also creates and edits them); it ends by saving its report
  (`save_analytics_report`) to `~/.orwick/analytics/<name>/report.md`. Analytics skills have no buttons
  over the terminal.
- **Additions:** a skill whose header says `extends: <skill>` adds to that skill. When the AI loads
  `code-check`, every addition that is on comes with it, and wins where it differs; an addition is
  never listed to the AI by itself. That is where one team's rules go, so the built-in skills stay
  general: e.g. `~/.orwick/skills/code-check-myteam/SKILL.md` with `extends: code-check` and your
  naming rules, libraries and response format. `feature-docs` and `service-map` take additions the
  same way.
- Yours: `~/.orwick/skills/<name>/SKILL.md`, for every folder. A project's: `.orwick/skills/`, **off
  unless you switch it on** (a downloaded project's skill is text that tells the AI what to do).
- **Claude Code's:** your `~/.claude/skills/` (company skills synced there included) are found too,
  **off unless you switch them on** (`"skills": {"golang-testing": true}`, or Settings → Skills in
  the app), so you decide what goes into the context. Orwick runs `claude` with its own skills off,
  so Claude never uses one on its own: only the ones you switch on reach the AI, through Orwick's
  index and `use_skill`. Their multi-line descriptions (`description: |`) are read, and they go by
  their folder name. They may mention Claude's tools (Read, Bash…); here the AI uses Orwick's. A
  skill's other files (`references/…`, a shared folder beside it like `../golang-shared/rules.md`)
  are read with `use_skill(name, file=…)`, never outside the skills folder and never secret files.
  Yours (`~/.orwick/skills`) win over Claude's with the same name.
  When a Claude skill loads, Orwick adds a note mapping Claude's tools to its own (Read → `read_file`,
  Bash → `run_command`, Agent/Task → `run_subagent`…). Commands may read and run the files of
  switched-on skills (their `scripts/`, and shared folders beside them), never write them, and never
  read secret files there. Steps that need the network or Claude-only features still won't work.
- `skills` setting switches them by name; `/skills` lists them. Skills add knowledge, not powers.

**Features:** `/feature PROJ-16765` (or `orwick --feature PROJ-16765`) links the session to a Jira issue.
The AI is told, the link is saved with the session, and `/sessions` shows it.

**Session docs:** Markdown notes kept with the session in `.orwick/sessions/<id>/docs/`, through
`read_session_doc`, `write_session_doc` and `write_session_docs` (several at once); writing shows you
the change and asks first. They
can only touch that folder, come back when you resume, and are deleted with the session.

## MCP servers (Jira and more)

Orwick connects MCP servers the way other agents do. Put them in `mcpServers` in
`~/.orwick/config.json` (or a folder's `.orwick/config.json`), in the usual format:

```json
"mcpServers": {
  "mcp-atlassian": {
    "command": "uvx", "args": ["mcp-atlassian"],
    "env": {"JIRA_URL": "${JIRA_BASE_URL}", "JIRA_USERNAME": "${JIRA_EMAIL}",
            "JIRA_API_TOKEN": "${JIRA_TOKEN}"}
  }
},
"tools": {"mcp__mcp-atlassian__jira_get_issue": true, "mcp__mcp-atlassian__jira_search": true}
```

- `${NAME}` is filled from your environment, so tokens don't have to be in the file. They go only to
  that server, never to the AI.
- Tools are named `mcp__<server>__<tool>` and are **off until you switch them on** in `tools` (or with
  `/tools on <name>` for one session, or in the Mac app's Settings → Tools). A server is started only
  when one of its tools is on.
- Tools the server marks read-only run without asking; any other asks you every time. Every call is
  in the audit log, and results are treated as data, not instructions.
- A server runs outside Orwick's sandbox, with network access: Orwick decides whether a tool is called,
  not what the server does with it. `orwick --list-tools` shows every tool of each server.

## Keeping token use low

Every request sends the whole conversation again (mostly as cheap cache re-reads), so Orwick keeps
the conversation small and the number of requests low:

- **Several tool calls per reply.** The AI asks for every call that doesn't depend on another
  one's result at once (up to 8): three file reads cost one request, not three.
- **Arguments as real JSON (Claude).** A doc the AI writes isn't escaped twice inside a string.
- **Replies only through structured output (Claude).** The AI doesn't also write its decision as
  text, which used to pay for each reply twice.
- **Long text in results is plain text.** File contents, command output and MCP text follow the
  result's JSON line unescaped (`"content": "<below: content>"`, then `--- content ---` …).
- **MCP JSON is compacted:** no indentation and no empty fields (`null`, `""`, `[]`, `{}`); every
  value stays.
- **Big files are read in parts** (see `read_file`), never cut mid-line.
- **The system prompt never changes mid-conversation.** A new system prompt would restart the
  claude process and re-send the whole conversation, so linking a feature later (`/feature`, an
  action) goes in as a note with your next message. A timeout restarts it too: the default
  `request_timeout_seconds` is 300.

`/status` and the app show **new in** (input the requests really added; on Claude, written to the
cache), **out**, and what was **re-read from the cache** separately.

## Long conversations

Every request sends the whole conversation to the AI, so a long session gets slower and uses more
of your subscription. Like Claude Code, Orwick shortens it on its own, checking before each message
against `history_budget_tokens` (default 100,000 estimated tokens), late like Claude Code:

- **From 80%:** results of tool calls from earlier turns (old file contents, command output) are
  replaced by a short note. No AI call; the AI can read a file again if it needs it.
- **From 90%:** the AI summarizes everything except your last two messages, and the summary takes
  their place. The summary is marked as notes, not instructions, so text hidden in old files can't
  turn into orders.
- **`/compact`** does it now. A summary is only used if it's actually shorter.
- **A warning first:** when the conversation reaches `history_warn_percent` of the budget (default
  50%), Orwick prints `📏 This conversation is at 52% of its budget …` after its answer, and again
  every 10% more, so you can `/compact` or `/clear` before anything is shortened. `/status` shows a
  fill bar (green, yellow from the warning point, red from 75%).

Only what's sent to the AI gets shorter. The session keeps everything: `/history` and resuming show
the whole conversation, with a line where the summary takes over.

## Debug mode

Start with `-d`, or type `/debug on` in the chat, to watch each request travel
through Orwick step by step. Every box says who sends what to whom (You → Orwick,
Orwick → Claude, Claude → Orwick, Orwick → tool read_file, tool read_file → Orwick,
Orwick → You) and shows the whole message, never shortened: your message, the
exact text sent to the AI, its raw reply, each tool call with its safety checks
and result, and the final answer. Type
`/debug off` to stop. [ARCHITECTURE.md](docs/ARCHITECTURE.md#debug-tracer)
explains each step.

## Add your own tool

Orwick's 6 standard tools are **built in** and available in every folder. You can add more:

- **For every folder:** put a `<tool_name>/tool.json` in `~/.orwick/tools/`.
- **For one project:** put it in that folder's `.orwick/tools/`. It wins over one of yours with the
  same name.

A built-in tool can't be replaced: a `tool.json` with a built-in name is skipped with a note. And
whoever wrote the `tool.json`, **Orwick's code decides how risky a function is**: a `tool.json` may
only run Orwick's own tool functions, and can't mark one as less risky than it is (so a project you
download can't make `write_file` run without asking you). `orwick --list-tools` prints every tool
available in a folder.

To add a new kind of tool:
1. Write a Python function in `src/agent_core/tools/` that returns JSON-friendly data, and list it
   with its risk in `TOOL_FUNCTIONS` (`tools/manifest.py`).
2. Describe it in a `tool.json`: description, parameters, risk level, and which function to run.
3. Restart Orwick and check with `/tools`.

The `tool.json` format is in [ARCHITECTURE.md](docs/ARCHITECTURE.md#tools).
Tools written in any language (not just Python) are planned.

## Troubleshooting

| Problem | Fix |
|---|---|
| `[model error] codex exec failed` or `'codex' not found` | Install the Codex CLI and run `codex login`. Check with `codex login status`. |
| `[model error] ... 'claude' not found` | Install Claude Code and log in once by running `claude`. |
| `[model error] claude reported an error: ...` | Usually your Claude usage limit; the message says. Switch with `/backend codex`, or wait. |
| `401 Incorrect API key` | You're on the `openai` backend. A ChatGPT subscription doesn't include API access, so switch back to `"backend": "codex"`. |
| `[model error] ... timed out` | Raise `request_timeout_seconds` in `config.json`. |
| `[stopped] Agent reached the step limit` | Rephrase the request, or raise `max_steps`. |
| `[tools] ... skipped` at startup | A `tool.json` in `~/.orwick/tools` or `.orwick/tools` uses a built-in tool's name; rename it. |
| "… is protected as a secret" | On purpose: Orwick never opens secret files. Show it a `.env.example` instead. |
| "… changed since you read it" | The file changed after Orwick read it. It reads it again by itself; just let it continue. |
| `Tool error: ... tool.json: ...` at startup | That tool's `tool.json` has a mistake; the message says which file and what's wrong. |
| `[config] ... Keeping the previous settings.` | `config.json` has a mistake (bad JSON or an unknown setting); fix it and send your next message. |
| A command says `Operation not permitted` | The sandbox blocked it on purpose (internet, a file outside the project, a secret). Run that step yourself. |
| `git` says it can't access `.git/config` | That repo's `.git/config` holds a token (e.g. in the remote URL), so it stays blocked. Remove the token from the URL and use a credential helper. |
| A command can't find your virtualenv | Virtualenvs outside the project folder can't be read. Create it inside the project (`.venv`). |
| "Commands are disabled …" | No sandbox is available (not macOS, or Orwick itself runs inside another sandbox). |
| `command not found: orwick` | Use `.venv/bin/orwick`, or install the command with `uv tool install`. |
| Answers get slow in a long chat | `/clear` (or `/reset`). The whole conversation is resent on every step. |

## More

- **[ARCHITECTURE.md](docs/ARCHITECTURE.md):** how Orwick works inside (agent loop, safety pipeline, AI backends, tool format, tests)
- **[ROADMAP.md](docs/ROADMAP.md):** what's built and what's next
- **[app/README.md](app/README.md):** Orwick for Mac

## License

MIT. See [LICENSE](LICENSE).
