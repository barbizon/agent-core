"""Subprocess entry point: run exactly one tool function and print a JSON result.

Protocol: one JSON request on stdin -> one JSON response on stdout.

Defenses:
* only imports functions from modules under an allowed prefix (given on argv
  by the trusted parent, never by the request);
* tool output written to stdout is redirected to stderr so it cannot forge
  the response.
"""

import argparse
import contextlib
import importlib
import json
import sys
from typing import Any

from agent_core.errors import ToolExecutionError, ToolOperationError


def error_response(error_type: str, message: str) -> dict[str, Any]:
    """Build a failure reply in the worker's JSON format."""
    return {"ok": False, "error": {"type": error_type, "message": message}}


def resolve_function(function_ref: str, allowed_prefixes: list[str]) -> Any:
    """Import the tool function, but only from allowed modules."""
    module_name, sep, qualname = function_ref.partition(":")
    if not sep or not module_name or not qualname:
        raise ToolExecutionError("Malformed function reference", "invalid_request")
    if not any(module_name.startswith(prefix) for prefix in allowed_prefixes):
        raise ToolExecutionError(
            f"Module {module_name!r} is not an allowed tool module",
            "permission_denied",
        )

    target: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        target = getattr(target, part)
    if not callable(target):
        raise ToolExecutionError("Function reference is not callable", "invalid_request")
    return target


def execute_request(request: Any, allowed_prefixes: list[str]) -> dict[str, Any]:
    """Run one tool request and return the reply (never raises)."""
    if not isinstance(request, dict):
        return error_response("invalid_request", "Request must be a JSON object")

    function_ref = request.get("function")
    arguments = request.get("arguments")
    if not isinstance(function_ref, str):
        return error_response("invalid_request", "function must be a string")
    if not isinstance(arguments, dict):
        return error_response("invalid_request", "arguments must be an object")

    try:
        function = resolve_function(function_ref, allowed_prefixes)
        with contextlib.redirect_stdout(sys.stderr):
            data = function(**arguments)
        json.dumps(data)  # fail here, not in the parent, if not serializable
        return {"ok": True, "data": data}
    except (ToolExecutionError, ToolOperationError) as exc:
        return error_response(exc.error_type, str(exc))
    except (ImportError, AttributeError):
        return error_response("unknown_tool", "Tool function could not be resolved")
    except (TypeError, ValueError) as exc:
        return error_response("execution_error", f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        return error_response("execution_error", f"Tool failed: {type(exc).__name__}")


def main() -> None:
    """Read one request from stdin, run it, and print the reply to stdout."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-prefix", action="append", default=[], dest="prefixes")
    args = parser.parse_args()

    try:
        request = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        response = error_response("invalid_request", "Invalid JSON request")
    else:
        response = execute_request(request, args.prefixes)

    sys.stdout.write(json.dumps(response))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
