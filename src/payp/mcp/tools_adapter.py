"""Adapter: convert payp tools to MCP tool format and back.

Bridges:
- payp.tools.base.BaseTool  <->  mcp.types.Tool
- payp.tools.base.ToolResult <->  list[mcp.types.TextContent]
"""

from __future__ import annotations

import json
from typing import Any

import mcp.types as mcp_types

from payp.tools.base import BaseTool, ToolRegistry, ToolResult
from payp.tools.chart import ChartTool
from payp.tools.explain import ExplainTool
from payp.tools.export import ExportTool
from payp.tools.help_tool import PaypHelpTool
from payp.tools.knowledge import (
    AppendKnowledgeTool,
    ListKnowledgeTool,
    ReadKnowledgeTool,
    WriteKnowledgeTool,
)
from payp.tools.queries import (
    DeleteQueryTool,
    ListQueriesTool,
    LoadQueryTool,
    SaveQueryTool,
)
from payp.tools.query import QueryTool
from payp.tools.schema import CheckCascadeTool, SchemaLookupTool, SchemaSearchTool
from payp.tools.snapshot import (
    DeleteSnapshotTool,
    ListSnapshotsTool,
    RestoreSnapshotTool,
    SnapshotBeforeDeleteTool,
)


def build_payp_registry() -> ToolRegistry:
    """Build a registry containing all payp tools (same as ChatSession)."""
    reg = ToolRegistry()
    reg.register(QueryTool())
    reg.register(ExplainTool())
    reg.register(SchemaLookupTool())
    reg.register(SchemaSearchTool())
    reg.register(CheckCascadeTool())
    reg.register(SnapshotBeforeDeleteTool())
    reg.register(RestoreSnapshotTool())
    reg.register(ListSnapshotsTool())
    reg.register(DeleteSnapshotTool())
    reg.register(ExportTool())
    reg.register(PaypHelpTool())
    reg.register(ReadKnowledgeTool())
    reg.register(WriteKnowledgeTool())
    reg.register(AppendKnowledgeTool())
    reg.register(ListKnowledgeTool())
    reg.register(SaveQueryTool())
    reg.register(ListQueriesTool())
    reg.register(LoadQueryTool())
    reg.register(DeleteQueryTool())
    reg.register(ChartTool())
    return reg


def payp_tool_to_mcp(tool: BaseTool) -> mcp_types.Tool:
    """Convert a payp BaseTool to an MCP Tool definition."""
    schema = tool.get_parameters_schema()
    # Ensure schema is a valid JSON schema object
    if not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
    if "type" not in schema:
        schema = {"type": "object", **schema}

    # Annotate description with safety metadata
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
