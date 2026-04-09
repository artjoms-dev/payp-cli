"""LLM integration for payp via litellm.

Supports OpenRouter (primary), direct providers (Anthropic, OpenAI, Gemini), and Ollama.
Handles streaming, tool calling, and cost tracking.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import litellm

from payp.config import load_model_roles, load_models_config
from payp.models import CostTracker


# Suppress litellm's verbose logging
litellm.suppress_debug_info = True
litellm.set_verbose = False


@dataclass
class ToolDefinition:
    """A tool the LLM can call."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    """A tool call requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Response from an LLM call."""

    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    model: str = ""


@dataclass
class StreamChunk:
    """A chunk from a streaming LLM response."""

    content: str | None = None
    tool_calls: list[dict] | None = None
    finish_reason: str | None = None


class LLMClient:
    """Wrapper around litellm for multi-provider LLM access."""

    def __init__(self) -> None:
        self.cost_tracker = CostTracker()
        self._configure_providers()

    def _configure_providers(self) -> None:
        """Set up API keys from models.toml as environment variables for litellm."""
        providers = load_models_config()

        for name, provider in providers.items():
            if name == "openrouter":
                os.environ["OPENROUTER_API_KEY"] = provider.api_key
            elif name == "anthropic":
                os.environ["ANTHROPIC_API_KEY"] = provider.api_key
            elif name == "openai":
                os.environ["OPENAI_API_KEY"] = provider.api_key
            elif name == "gemini":
                os.environ["GEMINI_API_KEY"] = provider.api_key

    def get_executor_model(self) -> str:
        """Get the configured executor model name."""
        roles = load_model_roles()
        return roles.executor

    def get_reviewer_model(self) -> str | None:
        """Get the configured reviewer model name."""
        roles = load_model_roles()
        return roles.reviewer

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[ToolDefinition] | None = None,
        stream: bool = True,
    ) -> LLMResponse:
        """Send a chat completion request. Non-streaming version."""
        model = model or self.get_executor_model()

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }

        if tools:
            kwargs["tools"] = [_tool_to_dict(t) for t in tools]

        response = await litellm.acompletion(**kwargs)

        # Extract response
        choice = response.choices[0]
        content = choice.message.content
        tool_calls = []

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                import json
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                ))

        # Track usage
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0

        # Cost from litellm
        cost = 0.0
        try:
            cost = litellm.completion_cost(completion_response=response)
        except Exception:
            pass

        self._track_cost(input_tokens, output_tokens, cost)

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            model=model,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Send a streaming chat completion request. Yields chunks."""
        model = model or self.get_executor_model()

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        if tools:
            kwargs["tools"] = [_tool_to_dict(t) for t in tools]

        response = await litellm.acompletion(**kwargs)

        collected_tool_calls: dict[int, dict] = {}
        input_tokens = 0
        output_tokens = 0

        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            finish_reason = chunk.choices[0].finish_reason if chunk.choices else None

            content = None
            if delta and delta.content:
                content = delta.content

            # Collect tool call deltas
            if delta and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in collected_tool_calls:
                        collected_tool_calls[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }
                    if tc_delta.id:
                        collected_tool_calls[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            collected_tool_calls[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            collected_tool_calls[idx]["arguments"] += tc_delta.function.arguments

            # Track usage from final chunk
            if hasattr(chunk, "usage") and chunk.usage:
                input_tokens = chunk.usage.prompt_tokens or 0
                output_tokens = chunk.usage.completion_tokens or 0

            yield StreamChunk(
                content=content,
                finish_reason=finish_reason,
            )

        # After stream ends, yield tool calls if any
        if collected_tool_calls:
            import json
            tool_calls_list = []
            for _idx, tc_data in sorted(collected_tool_calls.items()):
                tool_calls_list.append(tc_data)
            yield StreamChunk(
                tool_calls=tool_calls_list,
                finish_reason="tool_calls",
            )

        # Track cost
        cost = 0.0
        # litellm doesn't give cost for streaming easily, estimate from tokens
        try:
            cost = litellm.cost_per_token(
                model=model,
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
            )
            if isinstance(cost, tuple):
                cost = sum(cost)
        except Exception:
            pass

        self._track_cost(input_tokens, output_tokens, cost)

    def _track_cost(self, input_tokens: int, output_tokens: int, cost: float) -> None:
        """Accumulate cost tracking."""
        self.cost_tracker.total_input_tokens += input_tokens
        self.cost_tracker.total_output_tokens += output_tokens
        self.cost_tracker.total_cost_usd += cost
        self.cost_tracker.query_count += 1

    def get_cost_summary(self) -> dict[str, Any]:
        """Return current cost tracking summary."""
        ct = self.cost_tracker
        return {
            "input_tokens": ct.total_input_tokens,
            "output_tokens": ct.total_output_tokens,
            "total_tokens": ct.total_input_tokens + ct.total_output_tokens,
            "total_cost_usd": ct.total_cost_usd,
            "query_count": ct.query_count,
        }


def _tool_to_dict(tool: ToolDefinition) -> dict[str, Any]:
    """Convert a ToolDefinition to the OpenAI tool format for litellm."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }
