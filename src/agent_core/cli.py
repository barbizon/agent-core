"""Interactive chat CLI."""

import argparse
import json
import os
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from agent_core.agent import Agent, AgentEvent
from agent_core.approvals import TerminalApprovalProvider
from agent_core.audit import JsonlAuditLogger
from agent_core.config import (
    AGENT_DIR,
    AGENT_NAME,
    BACKENDS,
    DEFAULT_CONFIG_FILE,
    DEFAULT_ENV_FILE,
    DEFAULT_OPENAI_MODEL,
    Settings,
    load_settings,
    read_config_file,
)
from agent_core.config_view import describe_config
from agent_core.debug import Tracer
from agent_core.errors import ConfigError, ModelError, StepLimitExceeded, ToolManifestError
from agent_core.execution.process_executor import ProcessExecutor
from agent_core.gateway import ToolGateway
from agent_core.llm import ChatModel
from agent_core.permissions import PermissionPolicy
from agent_core.tools import DEFAULT_TOOLS_DIR, TOOL_MODULE_PREFIX, build_registry
from agent_core.tools.files import PROTECTED_PATHS_ENV, WORKSPACE_ENV

HELP = """\
Commands:
  /tools    list available tools
  /policy   show the permission policy
  /config   show every setting, where it comes from, and what it means
  /debug    show debug status; "/debug on" or "/debug off" switches it for this session
  /reset    clear the conversation
  /help     show this help
  /exit     quit (Ctrl-D also works)

Settings in .orvik/config.json are reloaded automatically when the file changes."""

DEBUG_INTRO = (
    "Debug mode: each message is traced through the pipeline as numbered points.\n"
    "Colors: cyan = your input, blue = to the model, green = from the model, "
    "yellow = tools, gray = internal, magenta = answer, red = error."
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Read the command-line flags."""
    parser = argparse.ArgumentParser(prog=AGENT_NAME, description="Chat with Orvik, your AI agent.")
    parser.add_argument("--backend", choices=BACKENDS, help="Model backend (overrides the config file and AGENT_BACKEND)")
    parser.add_argument("--model", help="Model name (overrides the config file and AGENT_MODEL)")
    parser.add_argument("--max-steps", type=int, help="Model calls per message")
    parser.add_argument("-d", "--debug", action="store_true",
                        help="Print every step of the flow (model input/output, safety checks) in frames")
    parser.add_argument("--config", default=DEFAULT_CONFIG_FILE,
                        help=f"JSON settings file, reloaded when it changes (default {DEFAULT_CONFIG_FILE})")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE,
                        help=f"File with secrets and fallback values (default {DEFAULT_ENV_FILE})")
    return parser.parse_args(argv)


def build_model(settings: Settings, tracer: Tracer) -> ChatModel:
    """Create the model client for the chosen backend (codex or openai)."""
    if settings.backend == "openai":
        from agent_core.llm.openai_model import OpenAIChatModel

        return OpenAIChatModel(
            api_key=settings.openai_api_key,
            model=settings.model or DEFAULT_OPENAI_MODEL,
            timeout_seconds=settings.request_timeout_seconds,
            tracer=tracer,
        )

    from agent_core.llm.codex_model import CodexCLIModel

    return CodexCLIModel(
        model=settings.model, timeout_seconds=settings.request_timeout_seconds, tracer=tracer
    )


def build_agent(settings: Settings, policy: PermissionPolicy, tracer: Tracer) -> Agent:
    """Wire everything together: tools, safety gateway, audit log and model."""
    registry = build_registry(DEFAULT_TOOLS_DIR)
    audit_logger = JsonlAuditLogger(settings.audit_log_path)
    gateway = ToolGateway(
        registry=registry,
        permission_policy=policy,
        approval_provider=TerminalApprovalProvider(),
        audit_logger=audit_logger,
        process_executor=ProcessExecutor(allowed_module_prefixes=(TOOL_MODULE_PREFIX,)),
        tracer=tracer,
    )
    return Agent(
        model=build_model(settings, tracer),
        registry=registry,
        gateway=gateway,
        audit_logger=audit_logger,
        max_steps=settings.max_steps,
        max_tool_result_chars=settings.max_tool_result_chars,
        # In debug mode the frames already show tool calls, so skip the short lines.
        on_event=None if tracer.enabled else print_event,
        tracer=tracer,
    )


def print_event(event: AgentEvent) -> None:
    """Print a short line for each tool call and its result."""
    data = event.data
    if event.kind == "tool_call":
        print(f"  → {data['name']}({json.dumps(data['arguments'], default=str)})")
    elif event.kind == "tool_result":
        if data["ok"]:
            preview = json.dumps(data["data"], default=str)
            print(f"  ← ok: {preview[:200]}{'…' if len(preview) > 200 else ''}")
        else:
            print(f"  ← error [{data['error']['type']}]: {data['error']['message']}")


def file_stamp(path: str | Path) -> tuple[int, int] | None:
    """A cheap fingerprint (modified time, size) to notice when a file changes."""
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def export_for_tools(
    settings: Settings, config_path: str | Path, env_path: str | Path | None = None
) -> None:
    """Pass settings the tools read from the environment (also seen by the process worker)."""
    if settings.workspace is not None:
        os.environ[WORKSPACE_ENV] = str(settings.workspace.resolve())
    else:
        os.environ.pop(WORKSPACE_ENV, None)
    # The agent's own folder, config and secrets are invisible to its tools.
    protected = [AGENT_DIR, config_path] + ([env_path] if env_path else [])
    os.environ[PROTECTED_PATHS_ENV] = os.pathsep.join(str(Path(p).resolve()) for p in protected)


def settings_changes(old: Settings, new: Settings) -> list[str]:
    """Human-readable list of settings that differ, e.g. 'max_steps: 10 → 5'."""
    changes = []
    for field in fields(Settings):
        if field.name == "openai_api_key":
            if old.openai_api_key != new.openai_api_key:
                changes.append("openai_api_key: (changed)")  # never print the key
            continue
        before, after = getattr(old, field.name), getattr(new, field.name)
        if before != after:
            changes.append(f"{field.name}: {before} → {after}")
    return changes


def rebuild_agent(
    agent: Agent, settings: Settings, policy: PermissionPolicy, debug: bool
) -> tuple[Agent, Tracer]:
    """Build a fresh agent (new settings and/or debug state) that continues the same chat."""
    tracer = Tracer(enabled=debug, request_count=agent.tracer.request_count)
    new_agent = build_agent(settings, policy, tracer)
    new_agent.messages = agent.messages
    return new_agent, tracer


def main(argv: list[str] | None = None) -> int:
    """Run the interactive chat and return the exit code."""
    args = parse_args(argv)

    # .env is read once, at startup. The snapshot keeps reloads independent of the
    # values the CLI itself exports for the tools (see export_for_tools).
    if args.env_file:
        load_dotenv(args.env_file)
    base_env = dict(os.environ)

    def load() -> tuple[Settings, dict[str, Any]]:
        """Read the settings and the raw JSON file values."""
        settings = load_settings(
            env_file=None, backend=args.backend, model=args.model, max_steps=args.max_steps,
            config_file=args.config, debug=args.debug, environ=base_env,
        )
        return settings, read_config_file(args.config)

    try:
        settings, file_values = load()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    config_stamp = file_stamp(args.config)
    export_for_tools(settings, args.config, args.env_file)

    # As in Claude Code: read tools run, write and dangerous tools ask you on every call.
    policy = PermissionPolicy(allow_write=True, allow_dangerous=True)
    tracer = Tracer(enabled=settings.debug)
    try:
        agent = build_agent(settings, policy, tracer)
    except ToolManifestError as exc:
        print(f"Tool error: {exc}", file=sys.stderr)
        return 2
    # Set by /debug on|off. While set, it beats the -d flag and the config file.
    debug_override: bool | None = None

    model_label = settings.model or ("codex default" if settings.backend == "codex" else DEFAULT_OPENAI_MODEL)
    print(f"=== {AGENT_NAME} · backend={settings.backend} · model={model_label} · tools={len(agent.registry)} ===")
    print(f"Working directory: {Path.cwd()}")
    if not Path(AGENT_DIR).is_dir():
        print(f"No {AGENT_DIR}/ folder here: no tools, default settings.")
    else:
        config_note = "loaded" if config_stamp else "not found, using .env and defaults"
        print(f"Config: {args.config} ({config_note}). It is reloaded automatically when it changes.")
    if tracer.enabled:
        print(DEBUG_INTRO)
    print("Type /help for commands.")

    while True:
        try:
            line = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        # Live reload: if the config file changed, rebuild the agent but keep the chat.
        stamp = file_stamp(args.config)
        if stamp != config_stamp:
            config_stamp = stamp
            try:
                new_settings, new_file_values = load()
            except ConfigError as exc:
                print(f"[config] {exc}\n[config] Keeping the previous settings.")
            else:
                changes = settings_changes(settings, new_settings)
                file_values = new_file_values
                if changes:
                    was_debug = tracer.enabled
                    settings = new_settings
                    export_for_tools(settings, args.config, args.env_file)
                    debug = settings.debug if debug_override is None else debug_override
                    agent, tracer = rebuild_agent(agent, settings, policy, debug)
                    print("[config] Reloaded: " + "; ".join(changes))
                    if debug_override is not None and any(c.startswith("debug:") for c in changes):
                        print("[config] Note: debug is set by /debug in this session, "
                              "so the file's debug value is ignored.")
                    if tracer.enabled and not was_debug:
                        print(DEBUG_INTRO)

        if not line:
            continue
        if line in ("/exit", "/quit"):
            return 0
        if line == "/help":
            print(HELP)
            continue
        if line == "/tools":
            print(agent.registry.describe())
            continue
        if line == "/policy":
            print(policy)
            continue
        if line == "/config":
            debug_source = "/debug command" if debug_override is not None else None
            print(describe_config(settings, args, policy, tracer, file_values, base_env,
                                  debug_source=debug_source))
            continue
        if line == "/debug" or line.startswith("/debug "):
            choice = line.split()[1:]
            if not choice:
                print(f"Debug is {'on' if tracer.enabled else 'off'}. "
                      "Use /debug on or /debug off to switch it.")
            elif choice[0] in ("on", "off") and len(choice) == 1:
                debug_override = choice[0] == "on"
                if debug_override == tracer.enabled:
                    print(f"Debug is already {choice[0]}.")
                else:
                    agent, tracer = rebuild_agent(agent, settings, policy, debug_override)
                    print(f"Debug is now {choice[0]}. The conversation is kept.")
                    if tracer.enabled:
                        print(DEBUG_INTRO)
            else:
                print("Usage: /debug, /debug on, /debug off")
            continue
        if line == "/reset":
            agent.reset()
            print("Conversation cleared.")
            continue

        try:
            answer = agent.send(line)
        except KeyboardInterrupt:
            print("\n[interrupted]")
            continue
        except StepLimitExceeded as exc:
            print(f"\n[stopped] {exc}")
            continue
        except ModelError as exc:
            print(f"\n[model error] {exc}")
            continue

        print(f"\nAgent: {answer}")


if __name__ == "__main__":
    sys.exit(main())
