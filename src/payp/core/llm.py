"""LLM integration for payp via litellm.

Supports OpenRouter (primary), direct providers (Anthropic, OpenAI, Gemini), and Ollama.
Handles streaming, tool calling, and cost tracking.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import litellm

from payp.config import load_model_roles, load_models_config
from payp.models import CostTracker

# Suppress litellm's verbose logging
litellm.suppress_debug_info = True
litellm.set_verbose = False
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("LiteLLM Router").setLevel(logging.WARNING)
logging.getLogger("LiteLLM Proxy").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def _supports_prompt_caching(model: str) -> bool:
    """Anthropic-capable routes where litellm forwards ``cache_control``."""
    m = model.lower()
    if m.startswith("anthropic/") or m.startswith("openrouter/anthropic/"):
        return True
    if m.startswith("azure_ai/") and "anthropic" in m:
        return True
    return "claude" in m


def _prepare_messages(
    messages: list[dict[str, Any]], model: str
) -> list[dict[str, Any]]:
    """Collapse our two adjacent system messages into the right shape.

    ``_facade.py`` passes the system prompt as two back-to-back system
    messages (static, dynamic). For Anthropic-capable models we rewrap
    them as a single system message whose content is a list of two text
    blocks, with ``cache_control`` on the static block. For every other
    provider we concatenate them back into a plain string so we don't
    trip OpenAI/Gemini schema validation.
    """
    if (
        len(messages) >= 2
        and messages[0].get("role") == "system"
        and messages[1].get("role") == "system"
        and isinstance(messages[0].get("content"), str)
        and isinstance(messages[1].get("content"), str)
    ):
        static = messages[0]["content"]
        dynamic = messages[1]["content"]
        rest = messages[2:]

        if _supports_prompt_caching(model) and static:
            blocks: list[dict[str, Any]] = [
                {
                    "type": "text",
                    "text": static,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
            if dynamic:
                blocks.append({"type": "text", "text": dynamic})
            return [{"role": "system", "content": blocks}, *rest]

        merged = f"{static}\n\n{dynamic}" if dynamic else static
        return [{"role": "system", "content": merged}, *rest]

    return messages


def _log_cache_usage(usage: Any, model: str) -> None:
    """DEBUG-log Anthropic cache counters if litellm surfaced them."""
    if usage is None:
        return
    cache_read = getattr(usage, "cache_read_input_tokens", None)
    cache_creation = getattr(usage, "cache_creation_input_tokens", None)
    if cache_read is None and cache_creation is None:
        as_dict = getattr(usage, "model_dump", None)
        if callable(as_dict):
            try:
                d = as_dict()
            except Exception:
                d = {}
            cache_read = d.get("cache_read_input_tokens")
            cache_creation = d.get("cache_creation_input_tokens")
    if cache_read or cache_creation:
        logger.debug(
            "prompt-cache model=%s cache_read=%s cache_creation=%s",
            model,
            cache_read,
            cache_creation,
        )


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
        # Per-request usage (reset on every _track_cost call)
        self.last_input_tokens: int = 0
        self.last_output_tokens: int = 0
        self.last_cost: float = 0.0
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

    def _resolve_model(self, model: str) -> tuple[str, dict[str, Any]]:
        """Parse provider/model and return litellm-ready model + extra kwargs."""
        providers = load_models_config()
        extra_kwargs: dict[str, Any] = {}

        if "/" in model:
            provider_name, actual_model = model.split("/", 1)
        else:
            provider_name, actual_model = "", model

        provider = providers.get(provider_name) if provider_name else None

        if provider_name.startswith("azure") and provider:
            # Detect Anthropic-on-Azure (Azure AI Foundry) vs Azure OpenAI.
            # litellm uses azure_ai/ for non-OpenAI models (Claude, Mistral, etc.)
            # and azure/ for native Azure OpenAI deployments.
            is_azure_ai = provider.base_url and "/anthropic" in provider.base_url
            prefix = "azure_ai" if is_azure_ai else "azure"
            model = f"{prefix}/{actual_model}"
            extra_kwargs["api_key"] = provider.api_key
            if provider.base_url:
                extra_kwargs["api_base"] = provider.base_url
            if provider.api_version:
                extra_kwargs["api_version"] = provider.api_version
        elif provider_name == "ollama" and provider:
            model = f"ollama/{actual_model}"
            if provider.base_url:
                extra_kwargs["api_base"] = provider.base_url
        elif provider_name == "anthropic" and provider:
            model = f"anthropic/{actual_model}"
            extra_kwargs["api_key"] = provider.api_key
            if provider.base_url:
                extra_kwargs["api_base"] = provider.base_url

        return model, extra_kwargs

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
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Send a chat completion request. Non-streaming version.

        `response_format` is passed through verbatim to litellm — used by
        the reviewer to get structured JSON output via a JSON schema.
        """
        model = model or self.get_executor_model()
        model, extra_kwargs = self._resolve_model(model)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": _prepare_messages(messages, model),
            "stream": False,
            **extra_kwargs,
        }

        if tools:
            kwargs["tools"] = [_tool_to_dict(t) for t in tools]

        if response_format is not None:
            kwargs["response_format"] = response_format

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
        _log_cache_usage(usage, model)

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
        model, extra_kwargs = self._resolve_model(model)

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": _prepare_messages(messages, model),
            "stream": True,
            "stream_options": {"include_usage": True},
            **extra_kwargs,
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
                _log_cache_usage(chunk.usage, model)

            yield StreamChunk(
                content=content,
                finish_reason=finish_reason,
            )

        # After stream ends, yield tool calls if any
        if collected_tool_calls:
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
        """Accumulate cost tracking and store per-request snapshot."""
        self.last_input_tokens = input_tokens
        self.last_output_tokens = output_tokens
        self.last_cost = cost
        self.cost_tracker.total_input_tokens += input_tokens
        self.cost_tracker.total_output_tokens += output_tokens
        self.cost_tracker.total_cost_usd += cost
        self.cost_tracker.query_count += 1
        self.cost_tracker.last_input_tokens = input_tokens

    def check_cost_limit(self) -> tuple[bool, str]:
        """Check if session cost limit is exceeded. Returns (exceeded, message)."""
        from payp.config import load_config
        config = load_config()
        if config.max_session_cost_usd is None:
            return False, ""
        current = self.cost_tracker.total_cost_usd
        limit = config.max_session_cost_usd
        if current >= limit:
            return True, f"Session cost ${current:.4f} has reached the limit of ${limit:.2f}. Run /cost to see details."
        if current >= limit * 0.8:
            return False, f"\u26a0 Session cost ${current:.4f} is at {current/limit*100:.0f}% of ${limit:.2f} limit."
        return False, ""

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
