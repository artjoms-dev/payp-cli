"""Schema tools — lookup table DDL and search the catalog."""

from __future__ import annotations

from typing import Any

from payp.tools.base import BaseTool, ToolResult


class SchemaLookupTool(BaseTool):
    name = "schema_lookup"
    description = (
        "Load full DDL (CREATE TABLE) for one or more tables. "
        "Returns column names, types, primary keys, foreign keys, and defaults. "
        "Use this to understand table structure before writing queries."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tables": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Table names to look up (e.g., ['orders', 'customers']). Prefix with schema if not public (e.g., 'staging.orders').",
                },
            },
            "required": ["tables"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.db.connection import ConnectionManager
        from payp.db.introspection import discover_t2

        conn: ConnectionManager | None = context.get("connection_manager")
        if not conn or not conn.is_connected:
            return ToolResult(success=False, error="Not connected to a database")

        tables = args.get("tables", [])
        if not tables:
            return ToolResult(success=False, error="No tables specified")

        ddl_parts = []
        for table_ref in tables:
            if "." in table_ref:
                schema, table = table_ref.split(".", 1)
            else:
                schema, table = "public", table_ref

            ddl = await discover_t2(conn, schema, table)
            ddl_parts.append(ddl)

        full_ddl = "\n\n".join(ddl_parts)
        return ToolResult(
            success=True,
            data={"ddl": full_ddl, "tables": tables},
            summary=f"DDL for {len(tables)} table(s)",
        )


class SchemaSearchTool(BaseTool):
    name = "schema_search"
    description = (
        "Search the table catalog for tables matching a name pattern. "
        "Returns matching table names from the T1 catalog. "
        "Use this when you're not sure of the exact table name."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Search pattern (substring match, case-insensitive). E.g., 'order' matches 'orders', 'order_items'.",
                },
            },
            "required": ["pattern"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.models import SchemaCatalog

        t1: SchemaCatalog | None = context.get("t1")
        if not t1:
            return ToolResult(success=False, error="No schema catalog loaded. Connect to a database first.")

        pattern = args.get("pattern", "").lower().strip()

        matches = []
        for schema_name, tables in t1.tables.items():
            for table in tables:
                if not pattern or pattern in table.lower():
                    matches.append(f"{schema_name}.{table}")

        if not matches:
            return ToolResult(
                success=True,
                data={"matches": [], "pattern": pattern},
                summary=f"No tables matching '{pattern}'",
            )

        return ToolResult(
            success=True,
            data={"matches": matches, "pattern": pattern},
            summary=f"{len(matches)} table(s) matching '{pattern}': {', '.join(matches[:10])}",
        )


class CheckCascadeTool(BaseTool):
    name = "check_cascade"
    description = (
        "REQUIRED before any DELETE operation. Check which tables have foreign keys "
        "referencing a given table and whether they use ON DELETE CASCADE. "
        "Returns all tables that would be affected by a DELETE, with row count estimates."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "Table name to check for incoming FK references (e.g., 'customers')",
                },
                "schema": {
                    "type": "string",
                    "description": "Schema name (default: 'public')",
                },
            },
            "required": ["table"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.db.connection import ConnectionManager

        conn: ConnectionManager | None = context.get("connection_manager")
        if not conn or not conn.is_connected:
            return ToolResult(success=False, error="Not connected to a database")

        table = args.get("table", "").strip()
        schema = args.get("schema", "public").strip()

        if not table:
            return ToolResult(success=False, error="No table specified")

        try:
            # Find all FK references to this table with cascade info
            rows = await conn.execute_raw("""
                SELECT
                    tc.table_schema || '.' || tc.table_name AS referencing_table,
                    kcu.column_name AS referencing_column,
                    rc.delete_rule
                FROM information_schema.referential_constraints rc
                JOIN information_schema.table_constraints tc
                    ON rc.constraint_name = tc.constraint_name
                    AND rc.constraint_schema = tc.constraint_schema
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                    ON rc.unique_constraint_name = ccu.constraint_name
                WHERE ccu.table_schema = %s AND ccu.table_name = %s
            """, (schema, table))

            if not rows:
                return ToolResult(
                    success=True,
                    data={"references": [], "cascade_tables": [], "table": table},
                    summary=f"No tables reference {table} — safe to delete without cascade effects",
                )

            # Get row counts for referencing tables
            references = []
            cascade_tables = []
            for row in rows:
                ref_table = row["referencing_table"]
                ref_col = row["referencing_column"]
                delete_rule = row["delete_rule"]

                # Get estimated row count
                ref_parts = ref_table.split(".")
                count_rows = await conn.execute_raw(f"""
                    SELECT COUNT(*) as cnt FROM {ref_table}
                """)
                row_count = count_rows[0]["cnt"] if count_rows else 0

                ref_info = {
                    "table": ref_table,
                    "column": ref_col,
                    "delete_rule": delete_rule,
                    "row_count": row_count,
                }
                references.append(ref_info)

                if delete_rule == "CASCADE":
                    cascade_tables.append(ref_info)

            summary_parts = [f"{len(references)} table(s) reference {table}"]
            if cascade_tables:
                cascade_detail = ", ".join(
                    f"{ct['table']} ({ct['row_count']} rows)" for ct in cascade_tables
                )
                summary_parts.append(f"CASCADE DELETE will affect: {cascade_detail}")

            return ToolResult(
                success=True,
                data={
                    "references": references,
                    "cascade_tables": cascade_tables,
                    "table": table,
                },
                summary=" | ".join(summary_parts),
            )

        except Exception as e:
            return ToolResult(success=False, error=str(e), summary=f"Cascade check failed: {e}")
