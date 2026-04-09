"""Knowledge base tools — per-table knowledge with user confirmation.

Key principle: LLM proposes knowledge, user confirms before saving.
All reads/writes go through the pluggable MemoryBackend (native or mempalace).
"""

from __future__ import annotations

from typing import Any

from payp.memory.manager import get_memory_backend
from payp.tools.base import BaseTool, ToolResult


class ReadKnowledgeTool(BaseTool):
    name = "read_knowledge"
    description = (
        "Read knowledge about a specific table from the knowledge base. "
        "Returns business logic, column meanings, enum values, usage examples. "
        "ALWAYS call this before working with a table you haven't queried yet this session."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "Table name (e.g., 'customers', 'orders')",
                },
                "connection": {
                    "type": "string",
                    "description": "Connection name. If omitted, uses the active connection.",
                },
            },
            "required": ["table"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        table = args.get("table", "").strip().lower()
        conn_name = args.get("connection", "")
        if not conn_name:
            mgr = context.get("connection_manager")
            conn_name = mgr.profile.name if mgr else ""
        if not table or not conn_name:
            return ToolResult(success=False, error="table and connection required")

        backend = get_memory_backend()
        content = await backend.read(conn_name, table)
        if content is None:
            # Also try with schema prefix variations
            for variant in [table, table.upper(), table.lower()]:
                content = await backend.read(conn_name, variant)
                if content:
                    break

        if content is None:
            return ToolResult(
                success=True,
                data={"table": table, "connection": conn_name, "content": None},
                summary=f"No knowledge file for {table} in {conn_name}",
            )

        return ToolResult(
            success=True,
            data={"table": table, "connection": conn_name, "content": content},
            summary=f"Loaded knowledge for {table} ({len(content)} chars)",
        )


class ProposeKnowledgeTool(BaseTool):
    name = "propose_knowledge"
    description = (
        "Propose new knowledge about a table for the user to review and approve. "
        "DO NOT write knowledge directly — ALWAYS use this tool to propose, "
        "then the user confirms or edits before it's saved. "
        "Use this when you discover: column meanings, enum values, NULL semantics, "
        "business rules, data quality quirks, valid filter patterns, or relationships."
    )
    is_read_only = True  # Doesn't write yet — user must approve

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "Table name",
                },
                "connection": {
                    "type": "string",
                    "description": "Connection name (omit for active)",
                },
                "discovery": {
                    "type": "string",
                    "description": "What you discovered — business logic, column meanings, enum values, quirks. Markdown format.",
                },
                "section": {
                    "type": "string",
                    "enum": ["business_logic", "columns", "relationships", "usage_examples", "data_quality"],
                    "description": "Which section of the knowledge file this belongs to",
                },
            },
            "required": ["table", "discovery"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        table = args.get("table", "").strip()
        discovery = args.get("discovery", "").strip()
        section = args.get("section", "business_logic")
        conn_name = args.get("connection", "")
        if not conn_name:
            mgr = context.get("connection_manager")
            conn_name = mgr.profile.name if mgr else ""

        if not table or not discovery:
            return ToolResult(success=False, error="table and discovery required")

        # Return the proposal for the chat loop to present to the user
        return ToolResult(
            success=True,
            data={
                "table": table,
                "connection": conn_name,
                "discovery": discovery,
                "section": section,
                "action": "propose_knowledge",  # Signal for chat loop to show approval
            },
            summary=f"Proposing knowledge update for {table}",
        )


class WriteKnowledgeTool(BaseTool):
    name = "write_knowledge"
    description = (
        "Write complete knowledge file for a table (replaces existing). "
        "Only use AFTER user has approved via propose_knowledge. "
        "Or when creating initial documentation from scratch."
    )
    is_read_only = False

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "connection": {"type": "string", "description": "Connection name (omit for active)"},
                "content": {"type": "string", "description": "Full markdown content"},
            },
            "required": ["table", "content"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        table = args.get("table", "").strip().lower()
        content = args.get("content", "")
        conn_name = args.get("connection", "")
        if not conn_name:
            mgr = context.get("connection_manager")
            conn_name = mgr.profile.name if mgr else ""
        if not table or not content:
            return ToolResult(success=False, error="table and content required")

        backend = get_memory_backend()
        result = await backend.save(conn_name, table, content, section="full_replace")
        return ToolResult(
            success=True,
            data={"table": table, "file": result.get("file", "")},
            summary=f"Saved knowledge for {table} -> {result.get('file', '')}",
        )


class AppendKnowledgeTool(BaseTool):
    name = "append_knowledge"
    description = (
        "Append new information to an existing table's knowledge file. "
        "Use after user approves a propose_knowledge discovery."
    )
    is_read_only = False

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "connection": {"type": "string"},
                "content": {"type": "string", "description": "Markdown to append"},
            },
            "required": ["table", "content"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        table = args.get("table", "").strip().lower()
        content = args.get("content", "")
        conn_name = args.get("connection", "")
        if not conn_name:
            mgr = context.get("connection_manager")
            conn_name = mgr.profile.name if mgr else ""
        if not table or not content:
            return ToolResult(success=False, error="table and content required")

        backend = get_memory_backend()
        result = await backend.save(conn_name, table, content)
        return ToolResult(
            success=True,
            data={"table": table, "file": result.get("file", "")},
            summary=f"Appended to {table} knowledge -> {result.get('file', '')}",
        )


class ListKnowledgeTool(BaseTool):
    name = "list_knowledge"
    description = "List all knowledge files, optionally filtered by connection."
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "connection": {"type": "string", "description": "Filter by connection (omit for all)"},
            },
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        conn_name = args.get("connection", "")
        backend = get_memory_backend()
        files = await backend.list_all(conn_name if conn_name else None)

        return ToolResult(
            success=True,
            data={"files": files, "count": len(files)},
            summary=f"{len(files)} knowledge file(s)",
        )


class SearchKnowledgeTool(BaseTool):
    name = "search_knowledge"
    description = (
        "Search across all knowledge for a term or concept. "
        "Works with both native (keyword) and mempalace (semantic) backends. "
        "Use to find knowledge across all tables — e.g., 'timezone handling', "
        "'soft delete pattern', 'status enum values'."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term or concept to find across knowledge base",
                },
                "connection": {
                    "type": "string",
                    "description": "Limit search to a specific connection (omit for all)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 5)",
                },
            },
            "required": ["query"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        query = args.get("query", "").strip()
        if not query:
            return ToolResult(success=False, error="query is required")

        conn_name = args.get("connection", "") or None
        limit = args.get("limit", 5)

        backend = get_memory_backend()
        matches = await backend.search(query, connection=conn_name, limit=limit)

        if not matches:
            return ToolResult(
                success=True,
                data={"matches": [], "count": 0, "query": query},
                summary=f"No knowledge matches for '{query}'",
            )

        return ToolResult(
            success=True,
            data={"matches": matches, "count": len(matches), "query": query},
            summary=f"{len(matches)} match(es) for '{query}'",
        )
