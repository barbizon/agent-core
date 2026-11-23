"""Model adapters.

The agent talks to any backend through the ``ChatModel`` protocol, using
OpenAI-style message dicts as the history format:

* ``CodexCLIModel``  — uses the local ``codex`` CLI (ChatGPT subscription login).
* ``ClaudeCLIModel`` — uses the local ``claude`` CLI (Claude subscription login).
* ``OpenAIChatModel`` — uses the OpenAI API (needs a paid API key).
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_CALLS_PER_REPLY = 8   # tool calls run from one reply; the rest get an error and are not run


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str  # raw string from the model; parsed by the agent


@dataclass(frozen=True)
class ModelResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_message(self) -> dict[str, Any]:
        """The assistant message to append to the conversation history."""
        message: dict[str, Any] = {"role": "assistant", "content": self.text}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments_json},
                }
                for call in self.tool_calls
            ]
        return message


class ChatModel(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelResponse:
        """Return the next assistant turn. Raise ModelError on failure."""
        ...


__all__ = ["MAX_CALLS_PER_REPLY", "ChatModel", "ModelResponse", "ToolCall"]
