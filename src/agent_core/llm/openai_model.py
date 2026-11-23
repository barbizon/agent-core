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
from agent_core.usage import UsageStats


class OpenAIChatModel:
    label = "OpenAI adapter"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,  # SDK retries 429/5xx/connection errors with backoff
        tracer: Tracer | None = None,
        usage: UsageStats | None = None,
    ) -> None:
        """Create the OpenAI client with a timeout and automatic retries."""
        self.usage = usage or UsageStats()
        self.model = model
        self.tracer = tracer or Tracer()  # disabled unless debug mode is on
        self._client = OpenAI(api_key=api_key, timeout=timeout_seconds or None, max_retries=max_retries)  # 0 = no limit

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelResponse:
        """Ask the OpenAI API for the next step and return it as a ModelResponse."""
        request: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:  # the API rejects an empty tools list
            request["tools"] = tools

        self.tracer.point(
            "Orwick → OpenAI · request",
            json.dumps(request, indent=2, ensure_ascii=False, default=str),
            "input",
        )
        start = time.monotonic()
        try:
            completion = self._client.chat.completions.create(**request)
        except OpenAIError as exc:
            raise ModelError(f"{type(exc).__name__}: {exc}") from exc

        message = completion.choices[0].message
        usage = completion.usage.model_dump() if completion.usage else {}
        cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        self.usage.add_request(time.monotonic() - start, usage.get("prompt_tokens") or 0,
                               cached, usage.get("completion_tokens") or 0)
        self.usage.model = completion.model or self.usage.model
        self.tracer.point(
            f"OpenAI → Orwick · raw reply ({time.monotonic() - start:.1f}s)",
            json.dumps(
                {"message": message.model_dump(exclude_none=True), "usage": usage},
                indent=2, ensure_ascii=False, default=str,
            ),
            "output",
        )
        tool_calls = [
            ToolCall(id=call.id, name=call.function.name, arguments_json=call.function.arguments)
            for call in (message.tool_calls or [])
            if call.type == "function"
        ]
        return ModelResponse(text=message.content, tool_calls=tool_calls)
