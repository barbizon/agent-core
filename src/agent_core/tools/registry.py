"""Tool definitions and the registry.

The registry is the single source of truth for what the agent can do. It:

* enforces safety invariants when a tool is defined (see ToolDefinition);
* lists and describes tools (for the CLI and for humans);
* exports OpenAI function-calling schemas (for the model);
* validates model-supplied arguments against the declared schema.
"""

import re
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

from agent_core.errors import ToolExecutionError
from agent_core.permissions import RiskLevel
from agent_core.retry import RetryPolicy

ToolFunction = Callable[..., Any]

TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# JSON-schema type -> accepted Python types. bool is excluded from numbers explicitly.
_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def _resolves_to(function: ToolFunction) -> bool:
    """True if module:qualname imports back to this exact function (what the worker does)."""
    module = sys.modules.get(getattr(function, "__module__", ""))
    target: Any = module
    for part in getattr(function, "__qualname__", "<?>").split("."):
        target = getattr(target, part, None)
    return target is function


class ExecutionMode(StrEnum):
    THREAD = "thread"    # in-process; a hung tool cannot be killed, only abandoned
    PROCESS = "process"  # isolated subprocess; killed on timeout


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    function: ToolFunction
    risk_level: RiskLevel
    # JSON-schema "properties": {"arg": {"type": "string", "description": "..."}}
    parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    optional: frozenset[str] = frozenset()
    execution_mode: ExecutionMode = ExecutionMode.THREAD
    timeout_seconds: float = 10.0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    def __post_init__(self) -> None:
        """Check the tool's safety rules; raise ValueError if one is broken."""
        if not TOOL_NAME_PATTERN.match(self.name):
            raise ValueError(f"Invalid tool name: {self.name!r}")
        if not self.description.strip():
            raise ValueError(f"Tool {self.name!r} needs a description")
        if not isinstance(self.risk_level, RiskLevel):
            raise ValueError(f"Invalid risk level: {self.risk_level!r}")
        if not isinstance(self.execution_mode, ExecutionMode):
            raise ValueError(f"Invalid execution mode: {self.execution_mode!r}")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        for arg_name, schema in self.parameters.items():
            if schema.get("type") not in _JSON_TYPES:
                raise ValueError(
                    f"Tool {self.name!r}: argument {arg_name!r} has "
                    f"unsupported type {schema.get('type')!r}"
                )
        unknown_optional = self.optional - set(self.parameters)
        if unknown_optional:
            raise ValueError(
                f"Tool {self.name!r}: optional args not declared: {sorted(unknown_optional)}"
            )

        # A retried write could apply twice: non-read tools never auto-retry.
        if self.risk_level != RiskLevel.READ and self.retry_policy.max_attempts != 1:
            raise ValueError("Non-read tools cannot enable automatic retries")

        # The process worker refuses non-read tools (defense in depth).
        if self.execution_mode == ExecutionMode.PROCESS:
            if self.risk_level != RiskLevel.READ:
                raise ValueError("Process execution supports only read tools")
            if not _resolves_to(self.function):
                raise ValueError("Process tools must be importable module-level functions")

    @property
    def required(self) -> list[str]:
        """Names of the arguments the model must provide."""
        return [name for name in self.parameters if name not in self.optional]

    @property
    def function_ref(self) -> str:
        """Import path the process worker uses to locate the function."""
        return f"{self.function.__module__}:{self.function.__qualname__}"

    def openai_schema(self) -> dict[str, Any]:
        """Describe this tool in the format the model expects."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self.required,
                    "additionalProperties": False,
                },
            },
        }


class ToolRegistry:
    def __init__(self, tools: list[ToolDefinition] | None = None) -> None:
        """Create a registry, optionally with some tools."""
        self._tools: dict[str, ToolDefinition] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: ToolDefinition) -> None:
        """Add a tool; names must be unique."""
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        """Find a tool by name, or return None."""
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        """All tools, sorted by name."""
        return sorted(self._tools.values(), key=lambda tool: tool.name)

    def __len__(self) -> int:
        """Number of registered tools."""
        return len(self._tools)

    def describe(self) -> str:
        """Human-readable list of tools (shown by /tools)."""
        if not self._tools:
            return "(no tools registered)"
        lines = []
        for tool in self.list_tools():
            args = ", ".join(
                f"{name}{'?' if name in tool.optional else ''}: {schema['type']}"
                for name, schema in tool.parameters.items()
            )
            lines.append(
                f"- {tool.name}({args}) [risk={tool.risk_level}, "
                f"mode={tool.execution_mode}, timeout={tool.timeout_seconds}s]: "
                f"{tool.description}"
            )
        return "\n".join(lines)

    def openai_schemas(self) -> list[dict[str, Any]]:
        """All tool descriptions, in the format the model expects."""
        return [tool.openai_schema() for tool in self.list_tools()]

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> ToolDefinition:
        """Check the model's arguments against the tool's schema and return the tool."""
        tool = self.get(name)
        if tool is None:
            raise ToolExecutionError(f"Unknown tool: {name}", error_type="unknown_tool")

        missing = set(tool.required) - set(arguments)
        if missing:
            raise ToolExecutionError(
                f"Missing tool arguments: {sorted(missing)}",
                error_type="missing_argument",
            )

        unexpected = set(arguments) - set(tool.parameters)
        if unexpected:
            raise ToolExecutionError(
                f"Unexpected tool arguments: {sorted(unexpected)}",
                error_type="unexpected_argument",
            )

        for arg_name, value in arguments.items():
            expected = tool.parameters[arg_name]["type"]
            is_bool = isinstance(value, bool)
            ok = isinstance(value, _JSON_TYPES[expected]) and (
                expected == "boolean" or not is_bool
            )
            if not ok:
                raise ToolExecutionError(
                    f"Argument {arg_name!r} must be of type {expected}",
                    error_type="invalid_argument_type",
                )
            allowed = tool.parameters[arg_name].get("enum")
            if allowed is not None and value not in allowed:
                raise ToolExecutionError(
                    f"Argument {arg_name!r} must be one of {allowed}",
                    error_type="invalid_argument_value",
                )

        return tool
