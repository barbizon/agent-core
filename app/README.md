# Orwick for Mac

A small Mac app that runs the [`orwick`](../README.md) command-line agent in a window.

The app doesn't talk to Orwick directly. Each session is a terminal running the `orwick` command in a folder you choose, exactly like typing `cd <folder> && orwick` in iTerm. So:

- Orwick and the app are developed separately; the app only needs `orwick` on your PATH.
- Everything Orwick does is unchanged: its sandbox, approvals, `/status`, `/backend`.
- You can still use Orwick from any terminal.

## Use

- **＋ New Session** (⌘N): pick a folder. Orwick starts there, and that folder is its workspace.
- **Home** is the dashboard, shown when the app opens and from the 🏠 button above the sidebar
  (⇧⌘H): what is open now and what Orwick is doing there, every saved session with its ticket,
  docs and tokens (from your recent folders, the code root and the folders in it), the service
  maps, and totals at the top. Click a session to continue it, a doc to open it with its docs, a
  map to read it; the filter finds sessions by title, ticket, folder or doc.
- **Analytics** (the chart button above the sidebar, or on the dashboard) is your collection of
  analytics, by title. Opening it starts nothing. **Create analytics** asks for a title and what you
  want to get, in your own words, and saves it to the list at once (Orwick writes a skill from it; no
  AI runs). Pick a title to see what you asked for and its latest report; **Run** works that one out
  in a session and saves the report; **Edit…** changes its title or text; right-click to delete it.
  **Common review issues** is Orwick's own: the issues reviewers raise again and again in pull
  requests, the most widespread first; set each to **On** (Review code and Code Scoring look for it),
  **Muted**, or leave it **Suggested**, and press **Learn from PRs** to read the new review comments.
- **Top / Bottom** (⤒ ⤓ in the toolbar, between the model menu and Docs) scroll the console, or the
  messages, steps and calls screen, to its very top or bottom.
- **Sessions** in the sidebar each run their own Orwick. Switching between them doesn't stop anything.
- **Recent folders** switch to that folder's session, or start one if it has none (right-click to remove one from the list). The same goes for New Session and `--open`: one session per folder.
- **Settings** (⌘, or the ⚙︎ buttons) edits Orwick's main settings, `~/.orwick/config.json`, used by
  every session: backend, model, agent-loop limits (including the conversation budget for
  compacting long conversations), debug and audit log. The **Tools** part (General | Tools at the top) is the
  main tool list every session starts with. It asks Orwick which tools exist (`orwick --list-tools`:
  the built-in ones and yours in `~/.orwick/tools`) and adds project tools found in your recent and
  open folders. A switched-off tool is never offered to the AI.
- **Each AI's own tools** (Settings → Tools, one section per AI): Claude's and Codex's built-in
  tools. Safe ones (web search, reading web pages) are on by default; dangerous ones (files,
  commands) are off unless you switch them on, with a warning that they work without asking you.
  **All off** and **Safe defaults** buttons. The Info panel shows which ones the answering AI has.
- **Session docs and features:** a session's docs open **below its terminal**, in a split you resize by dragging the divider up or
  down (so the Info panel on the right keeps its width)
  (📄 on the session's row, the **Docs** toolbar button, or Info → Show docs; for a saved session in
  Recent sessions, 📄 resumes it with its docs open). They show as tabs: Product Requirement ·
  Technical Design · Flows · To Do · Code Review (written by Orwick's `feature-docs` skill), then any
  other docs, with colored sections, status words (Done, In progress, To Do, (guess)) and tables, and
  they refresh while Orwick writes them. A session linked to a Jira issue shows the key.
- **Action buttons:** skills can offer actions (from their `ui.json`, reported by Orwick). They show
  as buttons above the terminal, e.g. **🎫 Read ticket** and **↻ Update docs** from `feature-docs`, and
  **Review code** and **Grade code** from `code-check` (the final check before a PR; they write the
  **Review Findings** and **Grade** tabs). Each skill's buttons are grouped under its title
  (**Feature docs**, **Code review checklist**); hover a button or a title to see what it does.
  A click types the action's command (`/do feature-docs read-ticket`) into Orwick, which then asks in
  the terminal for what it needs (the ticket ID). Buttons are greyed out while Orwick is working, so
  a click never lands in the middle of an answer.
- **Model menu** (session toolbar): shows the AI and model answering in the session, e.g. `🟠 claude
  · Sonnet`, and lists the models of each AI (from `orwick --list-models`; Codex's from its own
  list). Picking one types `/backend <ai> <model>` into Orwick, so the conversation is kept; it's
  greyed out while Orwick is working. Settings → General has the same list for every session.
- **Tools for one session:** the Info panel has a switch per tool that changes only that session
  (the main list stays as it is), marked "this session", with "Use the tool list from Settings" to
  undo. It writes the session's `tools.json`, the same file Orwick's `/tools on|off <name>` writes; it
  applies with your next message and comes back when you resume the session. Empty means Orwick's
  default. Running sessions re-read the file before your next message, so there's no need to
  restart them. A folder's own `.orwick/config.json` (or `.env`) still wins; Settings tells you
  which values the selected session's folder overrides. The app checks values with the same rules
  as Orwick and never overwrites a file Orwick can't read (it offers "Open in Editor" instead). It
  never reads `.env` files, because they hold secrets.
- **Appearance** (Settings → 🖥 App): System, Light or Dark, for the whole app including the
  terminal. It's an app preference, not an Orwick setting, and applies right away; System follows
  macOS live. The terminal has its own light and dark color sets, tuned so every color Orwick
  prints stays readable (checked by a contrast test).
- **Info** (ⓘ or ⌘I) opens a side panel for the selected session. It lists every skill (Orwick's, yours, the folder's, and Claude Code's grouped as Custom, Anthropic, Atlassian and open source) above the tools, each with a checkbox: tick or untick one to switch it for this session only (the AI is told with your next message). At the top is the session's **name**: type one and press Return, and it's shown in the sidebar, the window title, the docs, Recent sessions and Orwick's `/sessions` (empty removes it; `/name` in the chat does the same). Below: tokens (new in, out, and re-read from the cache)
  share, context fill, subscription limits with reset times, what Orwick is doing now, messages, AI steps, tool calls (expand to see each call and its tool) and
  approvals, the folder's tools (with their risk and how often they were used), and skills
  (Orwick has none yet). It updates after every step.
- **Deleting a session:** right-click it (in Recent sessions, or an open one) → **Delete Session…**. A
  dialog shows what goes (messages, docs) and asks; then Orwick deletes everything in it
  (`orwick --delete-session`). An open session is closed first. In a terminal: `/sessions delete <id>`.
- **Recent sessions** in the sidebar: your saved conversations from recent folders, most recently
  used first (15 at most), with title, folder, message count and time. Click one to continue it: the
  app switches to it if it's open, otherwise opens it with `orwick --resume <id>` and the
  whole earlier conversation is shown again, so you can scroll back (10,000 lines). Orwick saves each
  session itself, in `<folder>/.orwick/sessions/<id>/` (also when you use it from a terminal), and
  the app only lists and opens them. `/clear` in a session starts a new one; the Info panel
  follows it.
- **Conversation size:** when a session's conversation reaches the warning percent of its budget
  (Settings → "Warn at", default 50%), its sidebar row gets a percent badge (orange; red from
  75%, where Orwick starts summarizing) and a banner appears above the terminal suggesting `/compact`
  or `/clear`. Close the banner and it comes back after another 10%. The Info panel shows the fill
  as a meter. The numbers come from Orwick's `status.json`; Orwick itself warns in the terminal too.
- **Restart** (⌘R) starts Orwick again and continues the same conversation (a fresh one if nothing was said yet). **Close** (⇧⌘W) ends the session.
- **Orwick → About Orwick** shows the avatar. Poke it.

From a terminal: `open -a Orwick --args --open ~/some/folder` starts a session in that folder.

Quitting asks first if any session is running, since quitting ends those conversations.

## Build

Needs Xcode (or the Swift toolchain), macOS 14+, and `orwick` installed (`uv tool install --editable ~/Documents/orwick`).

```bash
./build-app.sh            # builds build/Orwick.app
./build-app.sh --install  # also copies it to ~/Applications
```

The app is ad-hoc signed for this Mac only. It is not sandboxed by macOS on purpose: Orwick
runs its commands inside its own `sandbox-exec` sandbox, which can't start from inside an
App Store–sandboxed app.

## How it starts Orwick

`<your login shell> -l -i -c "exec orwick --session-id <id>"`, with the chosen folder as the working directory.
The login + interactive shell gives Orwick the same environment as your terminal (`.zprofile` and `.zshrc`: PATH, and variables such as the tokens your MCP servers use), so it finds `orwick`, `claude`
and `codex`; `exec` then replaces the shell with Orwick. The terminal is
[SwiftTerm](https://github.com/migueldeicaza/SwiftTerm).

## Files

| File | What it is |
|---|---|
| `Sources/OrwickApp/OrwickApp.swift` | App, menus, quit confirmation |
| `Sources/OrwickApp/Sessions.swift` | A session (terminal + `orwick` process) and the session list |
| `Sources/OrwickApp/ContentView.swift` | Sidebar, terminal view, welcome screen |
| `Sources/OrwickApp/Avatar.swift` | The robo-viking avatar, drawn in code; also the app icon |
| `Sources/OrwickApp/AboutView.swift` | About window |
| `Sources/OrwickApp/Config.swift` | Reads, checks and writes `~/.orwick/config.json` (same rules as Orwick) |
| `Sources/OrwickApp/Theme.swift` | Appearance choice and the terminal's light and dark colors |
| `Sources/OrwickApp/ToolCatalog.swift` | Lists tools from `.orwick/tools/*/tool.json` for the Tools tab |
| `Sources/OrwickApp/RecentSessions.swift` | Lists saved sessions from `.orwick/sessions/*/meta.json` |
| `Sources/OrwickApp/DocsView.swift` | Docs pane beside the terminal (tabs, small colored Markdown renderer) |
| `Sources/OrwickApp/Status.swift` | Reads the session's `status.json` |
| `Sources/OrwickApp/InfoPanel.swift` | The ⓘ side panel |
| `Sources/OrwickApp/SettingsView.swift` | The Settings sheet |
| `Tests/OrwickAppTests` | Config file, status file and theme contrast tests (`swift test`); `Fixtures/` holds status files written by real Orwick |
| `build-app.sh` | Builds and signs `Orwick.app`, renders the icon |

**Settings → Skills** (before Tools) switches skills on or off for every new session: Orwick's and
yours are on by default; a folder's and your Claude Code skills (`~/.claude/skills`) are off until you
switch them on. Each skill you switch on adds a line to every request, so switch on only what you use.
