from __future__ import annotations

import json
import re
from typing import Any

from payp.core.llm import ToolCall


def wrap_tool_output(tool_name: str, payload: Any) -> str:
    """Wrap tool payloads in an untrusted XML envelope for LLM context."""
    body = json.dumps(payload, default=str)
    # Neutralise escape attempts — if a row value contains our closing tag
    # verbatim, swap angle brackets for look-alikes.
    body = re.sub(
        r"</?\s*tool_output\b[^>]*>",
        lambda m: m.group(0).replace("<", "⟨").replace(">", "⟩"),
        body,
        flags=re.IGNORECASE,
    )
    # Sanitise the tool_name attribute to prevent attribute injection.
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", tool_name)[:64]
    return (
        f'<tool_output tool="{safe_name}" untrusted="true">\n'
        f"{body}\n"
        f"</tool_output>"
    )


def summarize_tool_response(tool_name: str, response: Any) -> str:
    """Build a compact one-line summary of a tool response for session logs."""
    if not isinstance(response, dict):
        return f"{tool_name}: ok"
    d = response
    # execute_sql-style results
    if "row_count" in d or "rows_affected" in d or "execution_ms" in d:
        rows = d.get("row_count")
        if rows is None:
            rows = d.get("rows_affected")
        ms = d.get("execution_ms")
        bits = []
        if rows is not None:
            bits.append(f"{rows} row{'s' if rows != 1 else ''}")
        if ms is not None:
            bits.append(f"{ms}ms")
        if bits:
            return ", ".join(bits)
    # propose_knowledge + chart tools often return a small descriptor
    if "table" in d and "section" in d:
        return f"knowledge proposal for {d.get('table')}"
    if "chart_type" in d or "points" in d:
        pts = d.get("points") or d.get("point_count")
        t = d.get("chart_type", "chart")
        return f"{t} rendered" + (f" ({pts} points)" if pts else "")
    if "saved" in d and d.get("saved"):
        return f"saved → {d.get('file') or d.get('id') or 'memory'}"
    if "count" in d:
        return f"{d['count']} item(s)"
    return f"{tool_name}: ok"


def parse_tool_calls(raw_calls: list[dict]) -> list[ToolCall]:
    """Parse raw tool call dicts from streaming into ToolCall objects."""
    result = []
    for raw in raw_calls:
        try:
            arguments = json.loads(raw.get("arguments", "{}"))
        except json.JSONDecodeError:
            arguments = {}

        result.append(
            ToolCall(
                id=raw.get("id", ""),
                name=raw.get("name", ""),
                arguments=arguments,
            )
        )
    return result
