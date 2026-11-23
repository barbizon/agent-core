"""MCP servers as Orwick tools, tested against a tiny real server process (fake_mcp_server.py)."""

import sys
from pathlib import Path

import pytest

from agent_core import mcp
from agent_core.errors import ConfigError, ToolExecutionError
from agent_core.permissions import RiskLevel

SERVER = str(Path(__file__).parent / "fake_mcp_server.py")


def fake_spec(env=None):
    return mcp.ServerSpec("fake", sys.executable, (SERVER,), env or {})


def test_the_usual_server_format_is_checked():
    mcp.check_servers({"jira": {"command": "uvx", "args": ["mcp-atlassian"], "env": {"A": "b"}}})
    for bad in ({"jira": {"args": []}}, {"jira": {"command": "x", "args": "y"}},
                {"jira": {"command": "x", "type": "http"}}, {"bad name!": {"command": "x"}},
                {"jira": {"command": "x", "headers": {}}}):
        with pytest.raises(ConfigError):
            mcp.check_servers(bad)


def test_variables_are_filled_from_the_environment():
    environ = {"JIRA_TOKEN": "secret"}
    assert mcp.expand("${JIRA_TOKEN}", environ) == "secret"
    assert mcp.expand("${MISSING:-fallback}/x", environ) == "fallback/x"
    spec = mcp.specs({"j": {"command": "uvx", "env": {"T": "${JIRA_TOKEN}"}}}, environ)["j"]
    assert spec.env == {"T": "secret"} and "secret" not in repr(spec)   # tokens never shown


def test_parameters_become_plain_types():
    params, required = mcp.simple_parameters({
        "properties": {"a": {"type": ["string", "null"]}, "b": {"anyOf": [{"type": "null"}, {"type": "integer"}]},
                       "c": {"description": "x" * 500}, "d": {"type": "string", "enum": ["x", "y"]}},
        "required": ["a", "zzz"]})
    assert params["a"]["type"] == "string" and params["b"]["type"] == "integer"
    assert params["c"]["type"] == "string" and len(params["c"]["description"]) == 300
    assert params["d"]["enum"] == ["x", "y"] and required == ["a"]


def test_risk_comes_from_the_servers_hints():
    assert mcp.risk_of({"annotations": {"readOnlyHint": True}}) == RiskLevel.READ
    assert mcp.risk_of({}) == RiskLevel.WRITE
    assert mcp.risk_of({"annotations": {"destructiveHint": True}}) == RiskLevel.DANGEROUS


def test_talking_to_a_real_server_process():
    client = mcp.MCPClient(fake_spec({"FAKE_TOKEN": "abc"}))
    try:
        assert [t["name"] for t in client.list_tools()] == ["get_thing", "make_thing", "nuke"]
        assert client.call_tool("get_thing", {"key": "K-1"}) == {"text": "thing K-1 token-len=3"}
        with pytest.raises(ToolExecutionError, match="no such thing"):
            client.call_tool("get_thing", {"key": "bad"})
    finally:
        client.close()


def test_only_switched_on_tools_are_offered_and_idle_servers_never_start():
    started = []

    def client(spec):
        started.append(spec.name)
        return mcp.MCPClient(spec)

    pool = mcp.Pool(client=client)
    try:
        tools, origins = pool.tools({"fake": fake_spec(), "idle": mcp.ServerSpec("idle", "/nonexistent")},
                                    {"mcp__fake__get_thing": True, "mcp__fake__nuke": False,
                                     "mcp__fake__missing": True, "read_file": False})
        assert [t.name for t in tools] == ["mcp__fake__get_thing"]
        assert tools[0].risk_level == RiskLevel.READ and tools[0].required == ["key"]
        assert origins == {"mcp__fake__get_thing": "mcp:fake"}
        assert started == ["fake"]                                  # "idle" had nothing switched on
        assert pool.notes == ["MCP server fake has no tool 'missing'"]
        assert tools[0].function(key="K-2") == {"text": "thing K-2 token-len=0"}
        pool.tools({"fake": fake_spec()}, {"mcp__fake__make_thing": True})
        assert started == ["fake"]                                  # started once per run
    finally:
        pool.close()


def test_you_are_told_when_a_server_is_being_started():
    told = []
    pool = mcp.Pool()
    try:
        pool.tools({"fake": fake_spec()}, {"mcp__fake__get_thing": True}, told.append)
        pool.tools({"fake": fake_spec()}, {"mcp__fake__get_thing": True}, told.append)
    finally:
        pool.close()
    assert told == ["fake"]                       # only when it really starts, not when reused


def test_a_server_that_wont_start_is_a_note_not_a_crash():
    pool = mcp.Pool()
    tools, _ = pool.tools({"gone": mcp.ServerSpec("gone", "/nonexistent/server")}, {"mcp__gone__x": True})
    assert tools == [] and "MCP server gone isn't available" in pool.notes[0]



def test_empty_placeholders_are_reported():
    servers = {"j": {"command": "uvx", "env": {"T": "${TOKEN}", "U": "${URL:-https://x}", "E": "${EMAIL}"}}}
    assert mcp.empty_variables(servers, {"EMAIL": "me"}) == {"j": ["TOKEN"]}
    assert mcp.empty_variables(servers, {"EMAIL": "me", "TOKEN": "t"}) == {}
