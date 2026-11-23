"""MCP servers as Orwick tools (Model Context Protocol, local stdio servers).

An MCP server is a separate program that offers tools, e.g. ``mcp-atlassian``
for Jira and Confluence. Orwick connects them the way other agents do, and
offers their tools to the AI through the same gateway as its own tools: risk
level, your approval, the audit log, the on/off switches.

* **Setting, in the usual format** (as in Claude Code and other agents)::

      "mcpServers": {
        "mcp-atlassian": {"command": "uvx", "args": ["mcp-atlassian"],
                          "env": {"JIRA_URL": "${JIRA_BASE_URL}", "JIRA_API_TOKEN": "${JIRA_TOKEN}"}}
      }

  ``${NAME}`` (or ``${NAME:-default}``) is filled from your environment when the
  server starts, so tokens don't have to be written into the file. The values go
  only to that server's process, never to the AI or the sandbox.
* **Tool names** follow the usual ``mcp__<server>__<tool>``. MCP tools are **off
  until you switch them on** in ``tools`` (``"mcp__mcp-atlassian__jira_get_issue": true``),
  so a server with ~100 tools doesn't fill every request. A server is only
  started when at least one of its tools is on, and only once per Orwick run.
* **Risk:** a tool the server marks read-only (``readOnlyHint``) is a "read" tool
  and runs without asking; any other asks you every time ("dangerous" if the
  server marks it destructive).
* **Limits:** a server runs outside Orwick's sandbox, with network access. Orwick
  decides whether a tool is called; what the server then does is up to it.
  Results are treated as untrusted data, like every tool result.
"""

import json
import os
import queue
import re
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any
from typing import Callable

from agent_core.errors import ConfigError, ToolExecutionError
from agent_core.permissions import RiskLevel
from agent_core.tools.registry import ExecutionMode, ToolDefinition

PROTOCOL_VERSION = "2025-06-18"
START_TIMEOUT = 90.0     # the first `uvx` run may download the server
CALL_TIMEOUT = 120.0
# Only these come from Orwick's own environment; the server's settings come from its definition.
PASSED_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SHELL", "SSL_CERT_FILE")
PREFIX = "mcp__"
SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$")
_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


@dataclass(frozen=True)
class ServerSpec:
    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict, compare=False, repr=False)  # may hold tokens


def tool_name(server: str, tool: str) -> str:
    return f"{PREFIX}{server}__{tool}"


def check_servers(raw: Any) -> dict[str, dict[str, Any]]:
    """The ``mcpServers`` setting, checked (stdio servers: command, args, env)."""
    if not isinstance(raw, dict):
        raise ConfigError('mcpServers must look like {"name": {"command": "...", "args": [...], "env": {...}}}')
    for name, spec in raw.items():
        where = f"mcpServers.{name}"
        if not isinstance(name, str) or not SERVER_NAME.match(name):
            raise ConfigError(f"{where}: a server name may use letters, digits, - and _")
        if not isinstance(spec, dict) or not isinstance(spec.get("command"), str) or not spec["command"]:
            raise ConfigError(f'{where}: needs a "command"')
        if spec.get("type", "stdio") != "stdio":
            raise ConfigError(f"{where}: only stdio servers (a local command) are supported")
        args = spec.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ConfigError(f"{where}: args must be a list of strings")
        env = spec.get("env", {})
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise ConfigError(f"{where}: env must map names to strings")
        unknown = sorted(set(spec) - {"command", "args", "env", "type"})
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {unknown}")
    return raw


def expand(text: str, environ: dict[str, str]) -> str:
    """``${NAME}`` (or ``${NAME:-default}``) from the environment, as other agents do."""
    return _VARIABLE.sub(lambda m: environ.get(m.group(1), m.group(2) or ""), text)


def empty_variables(servers: dict[str, dict[str, Any]], environ: dict[str, str] | None = None) -> dict[str, list[str]]:
    """The ``${NAME}`` placeholders (without a default) that are empty here, per server."""
    environ = dict(os.environ) if environ is None else environ
    found = {}
    for name, spec in servers.items():
        texts = [spec["command"], *spec.get("args", []), *spec.get("env", {}).values()]
        empty = sorted({m.group(1) for t in texts for m in _VARIABLE.finditer(t)
                        if m.group(2) is None and not environ.get(m.group(1))})
        if empty:
            found[name] = empty
    return found


def specs(servers: dict[str, dict[str, Any]], environ: dict[str, str] | None = None) -> dict[str, ServerSpec]:
    environ = dict(os.environ) if environ is None else environ
    return {name: ServerSpec(name, expand(spec["command"], environ),
                             tuple(expand(a, environ) for a in spec.get("args", [])),
                             {k: expand(v, environ) for k, v in spec.get("env", {}).items()})
            for name, spec in servers.items()}


class MCPClient:
    """One running MCP server, spoken to with JSON-RPC over its stdin/stdout."""

    def __init__(self, spec: ServerSpec, popen=subprocess.Popen) -> None:
        self.spec = spec
        env = {k: os.environ[k] for k in PASSED_ENV if k in os.environ} | spec.env
        self._process = popen([spec.command, *spec.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env,
                              start_new_session=True)
        self._lines: queue.Queue = queue.Queue()
        self._next_id = 0
        self._lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()
        self._request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                     "clientInfo": {"name": "orwick", "version": "1"}}, START_TIMEOUT)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _pump(self) -> None:
        for line in self._process.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _send(self, message: dict[str, Any]) -> None:
        self._process.stdin.write(json.dumps(message) + "\n")
        self._process.stdin.flush()

    def _request(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            while True:
                try:
                    line = self._lines.get(timeout=timeout)
                except queue.Empty:
                    raise ToolExecutionError(f"MCP server {self.spec.name} didn't answer {method} in "
                                             f"{timeout:.0f}s", error_type="mcp_timeout") from None
                if line is None:
                    raise ToolExecutionError(f"MCP server {self.spec.name} stopped", error_type="mcp_stopped")
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue  # not a protocol message
                if message.get("id") != request_id:
                    continue  # notifications, logs
                if "error" in message:
                    error = message["error"] or {}
                    raise ToolExecutionError(f"MCP {self.spec.name}: {error.get('message', error)}",
                                             error_type="mcp_error")
                return message.get("result") or {}

    def list_tools(self) -> list[dict[str, Any]]:
        tools, cursor = [], None
        while True:
            result = self._request("tools/list", {"cursor": cursor} if cursor else {}, START_TIMEOUT)
            tools += result.get("tools") or []
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._request("tools/call", {"name": name, "arguments": arguments}, CALL_TIMEOUT)
        text = "\n".join(str(part.get("text", "")) for part in result.get("content") or []
                         if isinstance(part, dict) and part.get("type") == "text")
        if result.get("isError"):
            raise ToolExecutionError(f"{name}: {text or 'the server reported an error'}", error_type="mcp_error")
        return {"text": compact_json_text(text)}

    def close(self) -> None:
        try:
            self._process.stdin.close()
            self._process.terminate()
            self._process.wait(timeout=5)
        except Exception:  # noqa: BLE001 - shutting down
            self._process.kill()


def risk_of(tool: dict[str, Any]) -> RiskLevel:
    """Read-only tools run freely; the rest ask (dangerous when marked destructive)."""
    hints = tool.get("annotations") or {}
    if hints.get("readOnlyHint") is True:
        return RiskLevel.READ
    return RiskLevel.DANGEROUS if hints.get("destructiveHint") else RiskLevel.WRITE


def simple_parameters(schema: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """MCP's JSON Schema -> Orwick's parameters (one plain JSON type each) and the required names."""
    params = {}
    for name, prop in (schema.get("properties") or {}).items():
        prop = dict(prop) if isinstance(prop, dict) else {}
        kind = prop.get("type")
        if isinstance(kind, list):  # e.g. ["string", "null"]
            kind = next((k for k in kind if k != "null"), "string")
        if kind is None:  # anyOf / oneOf: the first plain type
            options = prop.get("anyOf") or prop.get("oneOf") or []
            kind = next((o.get("type") for o in options
                         if isinstance(o, dict) and o.get("type") not in (None, "null")), "string")
        clean: dict[str, Any] = {"type": kind if kind in ("string", "integer", "number", "boolean",
                                                          "array", "object") else "string"}
        if prop.get("description"):
            clean["description"] = str(prop["description"])[:300]
        if isinstance(prop.get("enum"), list):
            clean["enum"] = prop["enum"]
        params[name] = clean
    required = [r for r in schema.get("required") or [] if r in params]
    return params, required


def to_tool(client: MCPClient, tool: dict[str, Any], full_name: str) -> ToolDefinition:
    params, required = simple_parameters(tool.get("inputSchema") or {})
    name = tool["name"]

    def call(**arguments: Any) -> dict[str, Any]:
        return client.call_tool(name, arguments)

    call.__name__ = name
    return ToolDefinition(
        name=full_name, description=(tool.get("description") or name).strip()[:1000], function=call,
        risk_level=risk_of(tool), parameters=params, optional=frozenset(set(params) - set(required)),
        execution_mode=ExecutionMode.THREAD, timeout_seconds=CALL_TIMEOUT + 5,
    )


class Pool:
    """The MCP servers of one Orwick run: started once, on first use, closed at exit."""

    def __init__(self, client=MCPClient) -> None:
        self._client = client
        self._clients: dict[str, MCPClient] = {}
        self._catalog: dict[str, list[dict[str, Any]]] = {}
        self.notes: list[str] = []

    def catalog(self, spec: ServerSpec,
                starting: Callable[[str], None] | None = None) -> list[dict[str, Any]]:
        """Every tool the server offers (starting it if needed), as the server describes them.
        ``starting(server)`` is called just before a server is started (it can take a while)."""
        if spec.name not in self._catalog:
            if starting:
                starting(spec.name)
            self._clients[spec.name] = self._client(spec)
            self._catalog[spec.name] = self._clients[spec.name].list_tools()
        return self._catalog[spec.name]

    def tools(self, servers: dict[str, ServerSpec], switched: dict[str, bool],
              starting: Callable[[str], None] | None = None,
              ) -> tuple[list[ToolDefinition], dict[str, str]]:
        """The switched-on tools of each server (as ``mcp__server__tool``) and their origin.

        A server is started only if at least one of its tools is switched on.
        """
        result, origins = [], {}
        for server, spec in servers.items():
            prefix = tool_name(server, "")
            wanted = {name for name, on in switched.items() if on and name.startswith(prefix)}
            if not wanted:
                continue
            try:
                offered = self.catalog(spec, starting)
            except (ToolExecutionError, OSError) as exc:
                self._note(f"MCP server {server} isn't available: {exc}")
                continue
            for tool in offered:
                full = tool_name(server, tool["name"])
                if full in wanted:
                    result.append(to_tool(self._clients[server], tool, full))
                    origins[full] = f"mcp:{server}"
                    wanted.discard(full)
            for name in sorted(wanted):
                self._note(f"MCP server {server} has no tool {name.removeprefix(prefix)!r}")
        return result, origins

    def _note(self, note: str) -> None:
        if note not in self.notes:
            self.notes.append(note)

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()
        self._catalog.clear()


def compact_json_text(text: str) -> str:
    """A JSON reply without indentation and without empty fields (null, "", [], {});
    anything that isn't JSON is returned as it is. Servers like mcp-atlassian
    pretty-print large objects full of empty fields, and every space and empty
    field costs tokens on each later request."""
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text
    return json.dumps(_drop_empty(value), ensure_ascii=False, separators=(",", ":"))


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        kept = {k: _drop_empty(v) for k, v in value.items()}
        return {k: v for k, v in kept.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [_drop_empty(v) for v in value]
    return value
