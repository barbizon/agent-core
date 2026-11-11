import pytest

from agent_core.errors import ToolExecutionError
from agent_core.permissions import RiskLevel
from agent_core.retry import RetryPolicy
from agent_core.tools import build_registry
from agent_core.tools.registry import ExecutionMode, ToolDefinition, ToolRegistry
from tests import tool_fixtures as fx

REPO_TOOLS = __import__("pathlib").Path(__file__).parents[1] / ".orvik" / "tools"

STR = {"type": "string", "description": "text"}


def tool(**overrides) -> ToolDefinition:
    spec = dict(name="echo", description="Echo text.", function=fx.echo,
                risk_level=RiskLevel.READ, parameters={"text": STR})
    spec.update(overrides)
    return ToolDefinition(**spec)


def test_empty_registry_describe():
    assert ToolRegistry([]).describe() == "(no tools registered)"


def test_default_registry_has_the_file_tools():
    registry = build_registry(REPO_TOOLS)
    assert [t.name for t in registry.list_tools()] == ["list_folder", "read_file", "write_file"]
    assert registry.get("list_folder").risk_level == RiskLevel.READ
    assert registry.get("read_file").risk_level == RiskLevel.READ
    assert registry.get("write_file").risk_level == RiskLevel.WRITE


def test_openai_schema_shape():
    schema = tool(parameters={"text": STR, "n": {"type": "integer"}},
                  optional=frozenset({"n"})).openai_schema()
    assert schema["type"] == "function"
    params = schema["function"]["parameters"]
    assert params["required"] == ["text"]
    assert params["additionalProperties"] is False


def test_list_and_describe_sorted():
    registry = ToolRegistry([tool(name="b"), tool(name="a")])
    assert [t.name for t in registry.list_tools()] == ["a", "b"]
    assert registry.describe().startswith("- a(text: string) [risk=read")


def test_duplicate_registration_rejected():
    with pytest.raises(ValueError):
        ToolRegistry([tool(), tool()])


@pytest.mark.parametrize("overrides", [
    {"name": "bad name!"},
    {"description": " "},
    {"risk_level": "read"},  # plain string, not RiskLevel
    {"timeout_seconds": 0},
    {"parameters": {"text": {"type": "date"}}},
    {"optional": frozenset({"missing"})},
    {"risk_level": RiskLevel.WRITE, "retry_policy": RetryPolicy(max_attempts=2)},
    {"risk_level": RiskLevel.WRITE, "execution_mode": ExecutionMode.PROCESS},
    {"execution_mode": ExecutionMode.PROCESS, "function": lambda text: text},
])
def test_definition_invariants(overrides):
    with pytest.raises(ValueError):
        tool(**overrides)


@pytest.mark.parametrize("args,error_type", [
    ({}, "missing_argument"),
    ({"text": "x", "extra": 1}, "unexpected_argument"),
    ({"text": 5}, "invalid_argument_type"),
])
def test_argument_validation(args, error_type):
    with pytest.raises(ToolExecutionError) as exc:
        ToolRegistry([tool()]).validate_arguments("echo", args)
    assert exc.value.error_type == error_type


def test_bool_is_not_an_integer_and_enum_enforced():
    registry = ToolRegistry([tool(parameters={
        "n": {"type": "integer"}, "mode": {"type": "string", "enum": ["a", "b"]}})])
    with pytest.raises(ToolExecutionError):
        registry.validate_arguments("echo", {"n": True, "mode": "a"})
    with pytest.raises(ToolExecutionError) as exc:
        registry.validate_arguments("echo", {"n": 1, "mode": "c"})
    assert exc.value.error_type == "invalid_argument_value"


def test_unknown_tool():
    with pytest.raises(ToolExecutionError) as exc:
        ToolRegistry().validate_arguments("nope", {})
    assert exc.value.error_type == "unknown_tool"
