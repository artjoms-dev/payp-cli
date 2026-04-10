"""Query library tools — save, list, load, delete saved queries."""

from __future__ import annotations

from typing import Any

from payp.storage.queries import (
    delete_query,
    get_query,
    list_queries,
    save_query,
)
from payp.tools.base import BaseTool, ToolResult


class SaveQueryTool(BaseTool):
    name = "save_query"
    description = (
        "Save a SQL query to the library for later reuse. "
        "Use when user says 'save this', 'save as X', or creates a query worth keeping. "
        "Requires name, SQL, and optionally tags (for search) and description."
    )
    is_read_only = False

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short name for the query (will be sanitized to filename)",
                },
                "sql": {
                    "type": "string",
                    "description": "The SQL query to save",
                },
                "description": {
                    "type": "string",
                    "description": "Human-readable 1-sentence description",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for search. Include table refs as 'table:name'. Examples: finance, monthly, table:orders",
                },
            },
            "required": ["name", "sql"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        name = args.get("name", "").strip()
        sql = args.get("sql", "").strip()
        if not name or not sql:
            return ToolResult(success=False, error="name and sql required")

        path = save_query(
            name=name,
            sql=sql,
            description=args.get("description", ""),
            tags=args.get("tags", []),
        )
        return ToolResult(
            success=True,
            data={"name": path.stem, "file": str(path)},
            summary=f"Saved query: {path.stem}",
        )


class ListQueriesTool(BaseTool):
    name = "list_queries"
    description = (
        "List saved queries from the library, optionally filtered. "
        "Returns names, descriptions, and tags. "
        "Use when user asks what saved queries they have or looks for something to reuse."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "Optional search term — matches against tags, name, or description",
                },
            },
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        filter_tag = args.get("filter", "").strip()
        queries = list_queries(filter_tag=filter_tag)
        if not queries:
            msg = f"No queries matching '{filter_tag}'" if filter_tag else "No saved queries"
            return ToolResult(success=True, data={"queries": []}, summary=msg)

        # Strip SQL body for listing (keep metadata only)
        summary_list = [
            {
                "name": q["name"],
                "description": q["description"],
                "tags": q["tags"],
            }
            for q in queries
        ]
        return ToolResult(
            success=True,
            data={"queries": summary_list, "count": len(summary_list)},
            summary=f"{len(summary_list)} saved queries",
        )


class LoadQueryTool(BaseTool):
    name = "load_query"
    description = (
        "Load the SQL for a saved query by name. "
        "Use when user says 'run the X query' or 'show me the saved Y query'. "
        "Returns the full SQL text — use execute_sql to run it."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the saved query",
                },
            },
            "required": ["name"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        name = args.get("name", "").strip()
        if not name:
            return ToolResult(success=False, error="name required")

        q = get_query(name)
        if not q:
            return ToolResult(success=False, error=f"Query '{name}' not found")

        return ToolResult(
            success=True,
            data={
                "name": q["name"],
                "sql": q["sql"],
                "description": q["description"],
                "tags": q["tags"],
            },
            summary=f"Loaded query: {q['name']}",
        )


class DeleteQueryTool(BaseTool):
    name = "delete_query"
    description = "Delete a saved query by name."
    is_read_only = False
    is_destructive = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the saved query"},
            },
            "required": ["name"],
        }

    async def preview(
        self, args: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any] | None:
        name = (args.get("name") or "").strip()
        if not name:
            return {"summary": "No query name provided.", "items": [], "total": 0}
        q = get_query(name)
        if q is None:
            return {
                "summary": f"Query '{name}' not found — nothing to delete.",
                "items": [],
                "total": 0,
            }
        return {
            "summary": f"Would delete 1 saved query: {name}",
            "items": [
                f"{name} — {q.get('description', '')}".strip(" —"),
            ],
            "total": 1,
            "warning": "Deleted queries cannot be recovered.",
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        name = args.get("name", "").strip()
        if not name:
            return ToolResult(success=False, error="name required")
        if delete_query(name):
            return ToolResult(
                success=True, data={"name": name}, summary=f"Deleted query: {name}"
            )
        return ToolResult(success=False, error=f"Query '{name}' not found")
