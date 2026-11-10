"""OpenAI API backend: Chat Completions with native function calling.

Requires a platform.openai.com API key (billed separately from ChatGPT).
"""

import json
import time
from typing import Any

from openai import OpenAI, OpenAIError

from agent_core.debug import Tracer
from agent_core.errors import ModelError
from agent_core.llm import ModelResponse, ToolCall


class OpenAIChatModel:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,  # SDK retries 429/5xx/connection errors with backoff
        tracer: Tracer | None = None,
    ) -> None:
        """Create the OpenAI client with a timeout and automatic retries."""
        self.model = model
        self.tracer = tracer or Tracer()  # disabled unless debug mode is on
        self._client = OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=max_retries)

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelResponse:
        """Ask the OpenAI API for the next step and return it as a ModelResponse."""
        request: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:  # the API rejects an empty tools list
            request["tools"] = tools

        self.tracer.frame(
            "MODEL INPUT · OpenAI request",
            json.dumps(request, indent=2, ensure_ascii=False, default=str),
            "input",
        )
        start = time.monotonic()
        try:
            completion = self._client.chat.completions.create(**request)
        except OpenAIError as exc:
            raise ModelError(f"{type(exc).__name__}: {exc}") from exc

        message = completion.choices[0].message
        self.tracer.frame(
            f"MODEL OUTPUT · OpenAI ({time.monotonic() - start:.1f}s)",
            json.dumps(message.model_dump(exclude_none=True), indent=2, ensure_ascii=False),
            "output",
        )
        tool_calls = [
            ToolCall(id=call.id, name=call.function.name, arguments_json=call.function.arguments)
            for call in (message.tool_calls or [])
            if call.type == "function"
        ]
        return ModelResponse(text=message.content, tool_calls=tool_calls)
