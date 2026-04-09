"""Explain tool — run EXPLAIN on a query without executing."""

from __future__ import annotations

from typing import Any

from payp.tools.base import BaseTool, ToolResult


class ExplainTool(BaseTool):
    name = "explain_query"
    description = (
        "Run EXPLAIN on a SQL query to show the execution plan without executing it. "
        "Use this to check performance, find missing indexes, or estimate row counts "
        "before running a query."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SQL query to explain",
                },
            },
            "required": ["sql"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.db.connection import ConnectionManager

        conn: ConnectionManager | None = context.get("connection_manager")
        if not conn or not conn.is_connected:
            return ToolResult(success=False, error="Not connected to a database")

        sql = args.get("sql", "").strip()
        if not sql:
            return ToolResult(success=False, error="No SQL provided")

        try:
            rows = await conn.execute_raw(f"EXPLAIN {sql}")
            plan_lines = [row.get("QUERY PLAN", str(row)) for row in rows]
            plan = "\n".join(plan_lines)

            return ToolResult(
                success=True,
                data={"plan": plan, "sql": sql},
                summary=f"Execution plan for query ({len(plan_lines)} lines)",
            )

        except Exception as e:
            return ToolResult(success=False, error=str(e), summary=f"EXPLAIN failed: {e}")
