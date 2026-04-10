"""Context window management — token counting + auto-compaction.

Tracks conversation token usage, auto-summarizes older messages when approaching
the model's context limit. Inspired by Claude Code's /compact behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import litellm

# Start compacting when we hit this fraction of the model's context window
COMPACT_THRESHOLD = 0.75

# After compaction, target this fraction of remaining space filled
COMPACT_TARGET = 0.40

# How many of the most-recent messages to preserve verbatim (never summarize)
KEEP_RECENT_MESSAGES = 8

# Fallback context size if model info unavailable
DEFAULT_CONTEXT_SIZE = 200_000


@dataclass
class ContextStats:
    used_tokens: int
    max_tokens: int
    usage_ratio: float
    message_count: int
    should_compact: bool


def get_model_context_size(model: str) -> int:
    """Return the max input tokens for a model, with fallback."""
    try:
        info = litellm.get_model_info(model)
        max_input = info.get("max_input_tokens") or info.get("max_tokens")
        if max_input:
            return int(max_input)
    except Exception:
        pass
    return DEFAULT_CONTEXT_SIZE


def count_tokens(messages: list[dict[str, Any]], model: str) -> int:
    """Count tokens in a message list for the given model."""
    try:
        return int(litellm.token_counter(model=model, messages=messages))
    except Exception:
        # Fallback: rough estimate at 4 chars per token
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        return total_chars // 4


def get_context_stats(
    messages: list[dict[str, Any]],
    model: str,
    system_prompt_tokens: int = 0,
) -> ContextStats:
    """Compute current context usage stats."""
    used = count_tokens(messages, model) + system_prompt_tokens
    max_tokens = get_model_context_size(model)
    ratio = used / max_tokens if max_tokens else 0.0
    return ContextStats(
        used_tokens=used,
        max_tokens=max_tokens,
        usage_ratio=ratio,
        message_count=len(messages),
        should_compact=ratio >= COMPACT_THRESHOLD,
    )


SUMMARIZE_PROMPT = """Summarize the conversation below into a concise assistant \
summary that preserves:

- What the user wanted / their original intent
- Key facts discovered about the database (table names, relationships, counts)
- SQL queries that were run and their outcomes (successes and failures with reasons)
- Business context the user mentioned
- Current state of any ongoing task

DO NOT summarize the last {keep} messages — leave those intact.

Format your output as ONE compact summary block (200-400 words max), starting with \
"## Conversation Summary" — no preamble, no meta-commentary.

Conversation to summarize:
{conversation}"""


async def compact_messages(
    messages: list[dict[str, Any]],
    model: str,
    llm_client: Any,
    keep_recent: int = KEEP_RECENT_MESSAGES,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Summarize older messages, keep recent ones.

    Returns (new_messages, stats) where stats = {before_tokens, after_tokens, saved_tokens}.
    """
    if len(messages) <= keep_recent + 2:
        # Not enough to compact
        return messages, {"before_tokens": 0, "after_tokens": 0, "saved_tokens": 0}

    before_tokens = count_tokens(messages, model)

    # Split: older messages to summarize vs recent messages to preserve
    older = messages[:-keep_recent]
    recent = messages[-keep_recent:]

    # Build a text representation of the older messages for the summarizer
    conv_lines = []
    for msg in older:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str) and content.strip():
            conv_lines.append(f"[{role}] {content}")
        # Skip tool_calls details — too verbose for summary

    conversation = "\n\n".join(conv_lines)
    if not conversation.strip():
        return messages, {"before_tokens": before_tokens, "after_tokens": before_tokens, "saved_tokens": 0}

    # Ask the LLM to summarize
    summary_prompt = SUMMARIZE_PROMPT.format(conversation=conversation, keep=keep_recent)
    summary_messages = [
        {"role": "system", "content": "You are a precise conversation summarizer."},
        {"role": "user", "content": summary_prompt},
    ]

    try:
        response = await llm_client.chat(summary_messages, model=model, stream=False)
        summary_text = response.content or "## Conversation Summary\n(summary failed)"
    except Exception as e:
        summary_text = f"## Conversation Summary\n(summary failed: {e})"

    # Replace old messages with a single summary assistant message + keep recent
    new_messages = [
        {"role": "assistant", "content": summary_text},
    ] + recent

    after_tokens = count_tokens(new_messages, model)
    return new_messages, {
        "before_tokens": before_tokens,
        "after_tokens": after_tokens,
        "saved_tokens": max(0, before_tokens - after_tokens),
    }
