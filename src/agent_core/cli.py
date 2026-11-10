"""Interactive chat CLI."""

import argparse
import json
import sys

from agent_core.agent import Agent, AgentEvent
from agent_core.approvals import TerminalApprovalProvider
from agent_core.audit import JsonlAuditLogger
from agent_core.config import BACKENDS, DEFAULT_OPENAI_MODEL, Settings, load_settings
from agent_core.debug import Tracer
from agent_core.errors import ConfigError, ModelError, StepLimitExceeded
from agent_core.execution.process_executor import ProcessExecutor
from agent_core.gateway import ToolGateway
from agent_core.llm import ChatModel
from agent_core.permissions import PermissionPolicy
from agent_core.tools import TOOL_MODULE_PREFIX, build_registry

HELP = """\
Commands:
  /tools    list available tools
  /policy   show the permission policy
  /reset    clear the conversation
  /help     show this help
  /exit     quit (Ctrl-D also works)"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Read the command-line flags."""
    parser = argparse.ArgumentParser(prog="agent-core", description="Chat with the agent.")
    parser.add_argument("--backend", choices=BACKENDS, help="Model backend (overrides AGENT_BACKEND; default codex)")
    parser.add_argument("--model", help="Model name (overrides AGENT_MODEL; default = backend default)")
    parser.add_argument("--max-steps", type=int, help="Model calls per message")
    parser.add_argument("--allow-write", action="store_true", help="Permit write tools (still need approval)")
    parser.add_argument("--allow-dangerous", action="store_true", help="Permit dangerous tools (still need approval)")
    parser.add_argument("-d", "--debug", action="store_true",
                        help="Print every step of the flow (model input/output, safety checks) in frames")
    parser.add_argument("--env-file", default=".env")
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
    registry = build_registry()
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


def main(argv: list[str] | None = None) -> int:
    """Run the interactive chat and return the exit code."""
    args = parse_args(argv)

    try:
        settings = load_settings(
            args.env_file, backend=args.backend, model=args.model, max_steps=args.max_steps
        )
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    policy = PermissionPolicy(allow_write=args.allow_write, allow_dangerous=args.allow_dangerous)
    tracer = Tracer(enabled=args.debug)
    agent = build_agent(settings, policy, tracer)

    model_label = settings.model or ("codex default" if settings.backend == "codex" else DEFAULT_OPENAI_MODEL)
    print(f"=== agent-core · backend={settings.backend} · model={model_label} · tools={len(agent.registry)} ===")
    if tracer.enabled:
        print("Debug mode: each message is traced through the pipeline as numbered points.\n"
              "Colors: cyan = your input, blue = to the model, green = from the model, "
              "yellow = tools, gray = internal, magenta = answer, red = error.")
    print("Type /help for commands.")

    while True:
        try:
            line = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

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
