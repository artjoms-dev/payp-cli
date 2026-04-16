"""Query tool — execute SQL against the connected database."""

from __future__ import annotations

from typing import Any

from payp.tools.base import BaseTool, ToolResult


class QueryTool(BaseTool):
    name = "execute_sql"
    description = (
        "Execute a SQL query against the connected database. "
        "Returns rows, columns, row count, and execution time. "
        "SELECT queries return data. DDL/DML returns affected row count."
    )
    is_read_only = False
    is_destructive = False  # Depends on the actual SQL

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SQL query to execute",
                },
            },
            "required": ["sql"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.db.connection import ConnectionManager

        # Prefer the multi-connection manager's *current* active connection
        # so mid-round /switch_connection calls take effect immediately.
        # Falls back to the chat session's connection if multi_conn is absent.
        conn: ConnectionManager | None = None
        mc = context.get("multi_conn")
        if mc is not None:
            conn = mc.active_manager
        if conn is None:
            conn = context.get("connection_manager")
        if not conn or not conn.is_connected:
            return ToolResult(success=False, error="Not connected to a database")

        sql = args.get("sql", "").strip()
        if not sql:
            return ToolResult(success=False, error="No SQL provided")

        try:
            result = await conn.execute(sql, limit=20)

            if result.columns:
                # SELECT — return data + remember SQL for /more pagination
                data = {
                    "columns": result.columns,
                    "rows": result.rows,
                    "row_count": result.row_count,
                    "execution_ms": result.execution_ms,
                    "truncated": result.truncated,
                }
                summary = f"{result.row_count} rows in {result.execution_ms}ms"
                if result.truncated:
                    summary += " (truncated to 20 — use /more to paginate)"
            else:
                # DDL/DML — return affected count
                data = {
                    "rows_affected": result.row_count,
                    "execution_ms": result.execution_ms,
                }
                summary = f"{result.row_count} rows affected in {result.execution_ms}ms"

            return ToolResult(success=True, data=data, summary=summary)

        except Exception as e:
            import logging
            logging.getLogger("payp.tools.query").exception("QueryTool failed")
            return ToolResult(success=False, error=str(e), summary=f"Query failed: {e}")
