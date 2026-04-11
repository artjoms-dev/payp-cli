"""Adapter: convert payp tools to MCP tool format and back.

The tool CATALOG lives in `payp.tools.registry` — this module is only
responsible for type conversion between payp.ToolResult and MCP's
TextContent/Tool types.

Bridges:
- payp.tools.base.BaseTool  <->  mcp.types.Tool
- payp.tools.base.ToolResult <->  list[mcp.types.TextContent]
"""

from __future__ import annotations

import json
from typing import Any

import mcp.types as mcp_types

from payp.tools.base import BaseTool, ToolRegistry, ToolResult
from payp.tools.registry import build_mcp_registry


def build_payp_registry(
    *,
    mode: str = "manual",
    allow_code_exec: bool = False,
) -> ToolRegistry:
    """Build the MCP-facing registry. Delegates to the shared catalog.

    Args:
        mode: "manual" (default) or "yolo" — used by server.py to choose
            which destructive tools to block at call time.
        allow_code_exec: Expose execute_python/r/shell over MCP. Gated
            further inside each tool by PAYP_DANGEROUS_UNGATED_CODE_EXEC.
    """
    return build_mcp_registry(mode=mode, allow_code_exec=allow_code_exec)


def payp_tool_to_mcp(tool: BaseTool) -> mcp_types.Tool:
    """Convert a payp BaseTool to an MCP Tool definition."""
    schema = tool.get_parameters_schema()
    # Ensure schema is a valid JSON schema object
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
    if "type" not in schema:
        schema = {"type": "object", **schema}

    # Annotate description with safety metadata so the client LLM sees
    # the tool's risk level at a glance.
    desc = tool.description
    if getattr(tool, "is_destructive", False):
        desc = f"[DESTRUCTIVE] {desc}"
    elif not getattr(tool, "is_read_only", True):
        desc = f"[WRITE] {desc}"

    return mcp_types.Tool(
        name=tool.name,
        description=desc,
        inputSchema=schema,
    )


def tool_result_to_mcp(result: ToolResult) -> list[mcp_types.TextContent]:
    """Convert a payp ToolResult to MCP content blocks.

    Success: JSON-serialized data + summary.
    Failure: error message text.
    """
    if not result.success:
        return [
            mcp_types.TextContent(
                type="text",
                text=f"ERROR: {result.error or 'Unknown error'}",
            )
        ]

    text_parts: list[str] = []
    if result.summary:
        text_parts.append(result.summary)

    if result.data is not None:
        try:
            body = json.dumps(result.data, default=str, indent=2, ensure_ascii=False)
        except Exception as e:  # pragma: no cover
            body = f"<unserializable result: {e}>"
        text_parts.append(body)

    text = "\n\n".join(text_parts) if text_parts else "OK"
    return [mcp_types.TextContent(type="text", text=text)]


def error_content(message: str) -> list[mcp_types.TextContent]:
    """Build a single TextContent error block."""
    return [mcp_types.TextContent(type="text", text=f"ERROR: {message}")]


def info_content(message: str, data: Any = None) -> list[mcp_types.TextContent]:
    """Build an info content block with optional structured data."""
    parts = [message]
    if data is not None:
        try:
            parts.append(json.dumps(data, default=str, indent=2, ensure_ascii=False))
        except Exception:
            pass
    return [mcp_types.TextContent(type="text", text="\n\n".join(parts))]
