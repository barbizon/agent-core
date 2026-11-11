"""Load tool definitions from ``tool.json`` files, so describing a tool needs no code.

Layout (one folder per tool; the folder name must equal the tool name)::

    .orvik/tools/
    └── read_file/
        └── tool.json

A ``tool.json``::

    {
      "name": "read_file",
      "description": "Read a UTF-8 text file from the workspace.",
      "risk_level": "read",                 # read | write | dangerous
      "mode": "process",                    # process | thread   (default thread)
      "timeout_seconds": 5,                 # optional, default 10
      "retry_attempts": 1,                  # optional, read tools only
      "parameters": {
        "path": {"type": "string", "description": "Path relative to the workspace"}
      },
      "required": ["path"],                 # optional, default: none required
      "run": {"python": "agent_core.tools.files:read_file"}
    }

Loading is strict: an unknown key, a wrong type, a name that doesn't match its
folder, or a function outside the allowed modules stops startup with a clear
error. A tool is never half-loaded.
"""

import importlib
import json
from pathlib import Path
from typing import Any, Callable

from agent_core.errors import ToolManifestError
from agent_core.permissions import RiskLevel
from agent_core.retry import RetryPolicy
from agent_core.tools.registry import ExecutionMode, ToolDefinition

MANIFEST_NAME = "tool.json"
REQUIRED_KEYS = {"name", "description", "risk_level", "parameters", "run"}
OPTIONAL_KEYS = {"mode", "timeout_seconds", "retry_attempts", "required"}
PARAMETER_KEYS = {"type", "description", "enum"}
RUN_KINDS = {"python"}  # "command" (any language) comes next


def load_tools(tools_dir: str | Path, allowed_modules: tuple[str, ...]) -> list[ToolDefinition]:
    """Load every ``<tools_dir>/<name>/tool.json``; a missing folder means no tools."""
    tools_dir = Path(tools_dir)
    if not tools_dir.is_dir():
        return []
    tools = []
    for folder in sorted(p for p in tools_dir.iterdir() if p.is_dir()):
        if folder.name.startswith((".", "_")):
            continue  # e.g. __pycache__, hidden folders
        manifest = folder / MANIFEST_NAME
        if not manifest.is_file():
            raise ToolManifestError(f"{folder}: missing {MANIFEST_NAME}")
        tools.append(load_manifest(manifest, allowed_modules))
    return tools


def load_manifest(path: str | Path, allowed_modules: tuple[str, ...]) -> ToolDefinition:
    """Read and check one tool.json, and turn it into a ToolDefinition."""
    path = Path(path)

    def fail(message: str) -> ToolManifestError:
        """An error that names the file, so you know what to fix."""
        return ToolManifestError(f"{path}: {message}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise fail(f"invalid JSON ({exc.msg}, line {exc.lineno})") from exc
    if not isinstance(data, dict):
        raise fail("must contain a JSON object {...}")

    missing = sorted(REQUIRED_KEYS - set(data))
    if missing:
        raise fail(f"missing key(s) {missing}")
    unknown = sorted(set(data) - REQUIRED_KEYS - OPTIONAL_KEYS)
    if unknown:
        raise fail(f"unknown key(s) {unknown}")

    name = data["name"]
    if name != path.parent.name:
        raise fail(f"name {name!r} must match its folder name {path.parent.name!r}")
    if not isinstance(data["description"], str):
        raise fail("description must be a string")

    try:
        risk = RiskLevel(data["risk_level"])
    except ValueError:
        raise fail(f"risk_level must be one of {[r.value for r in RiskLevel]}") from None
    try:
        mode = ExecutionMode(data.get("mode", ExecutionMode.THREAD.value))
    except ValueError:
        raise fail(f"mode must be one of {[m.value for m in ExecutionMode]}") from None

    parameters = _parameters(data["parameters"], fail)
    required = data.get("required", [])
    if not isinstance(required, list) or not all(isinstance(r, str) for r in required):
        raise fail("required must be a list of parameter names")
    not_declared = sorted(set(required) - set(parameters))
    if not_declared:
        raise fail(f"required lists unknown parameter(s) {not_declared}")

    timeout = data.get("timeout_seconds", 10.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise fail("timeout_seconds must be a number")
    attempts = data.get("retry_attempts", 1)
    if isinstance(attempts, bool) or not isinstance(attempts, int):
        raise fail("retry_attempts must be a whole number")

    function = _resolve_run(data["run"], allowed_modules, fail)

    try:
        return ToolDefinition(
            name=name,
            description=data["description"],
            function=function,
            risk_level=risk,
            parameters=parameters,
            optional=frozenset(set(parameters) - set(required)),
            execution_mode=mode,
            timeout_seconds=float(timeout),
            retry_policy=RetryPolicy(max_attempts=attempts),
        )
    except ValueError as exc:  # the safety rules in ToolDefinition
        raise fail(str(exc)) from exc


def _parameters(raw: Any, fail: Callable[[str], ToolManifestError]) -> dict[str, dict[str, Any]]:
    """Check the parameters block: each entry needs a type and only known keys."""
    if not isinstance(raw, dict):
        raise fail("parameters must be an object {name: {type, description}}")
    for param, schema in raw.items():
        if not isinstance(schema, dict) or "type" not in schema:
            raise fail(f"parameter {param!r} needs an object with at least a 'type'")
        unknown = sorted(set(schema) - PARAMETER_KEYS)
        if unknown:
            raise fail(f"parameter {param!r} has unknown key(s) {unknown}")
        if "enum" in schema and not isinstance(schema["enum"], list):
            raise fail(f"parameter {param!r}: enum must be a list")
    return raw


def _resolve_run(
    run: Any, allowed_modules: tuple[str, ...], fail: Callable[[str], ToolManifestError]
) -> Callable[..., Any]:
    """Find the function a tool runs; only modules under an allowed prefix are imported."""
    if not isinstance(run, dict) or len(run) != 1:
        raise fail(f"run must have exactly one of {sorted(RUN_KINDS)}")
    kind, target = next(iter(run.items()))
    if kind not in RUN_KINDS:
        raise fail(f"run kind {kind!r} is not supported yet (supported: {sorted(RUN_KINDS)})")

    module_name, _, qualname = str(target).partition(":")
    if not module_name or not qualname:
        raise fail("run.python must look like 'package.module:function'")
    if not any(module_name.startswith(prefix) for prefix in allowed_modules):
        raise fail(f"module {module_name!r} is not allowed (allowed: {list(allowed_modules)})")
    try:
        function: Any = importlib.import_module(module_name)
        for part in qualname.split("."):
            function = getattr(function, part)
    except (ImportError, AttributeError) as exc:
        raise fail(f"cannot find {target!r} ({exc})") from exc
    if not callable(function):
        raise fail(f"{target!r} is not a function")
    return function
