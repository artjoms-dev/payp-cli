"""Cross-database operations — compare schemas, compare data, switch connections."""

from __future__ import annotations

from typing import Any

from payp.tools.base import BaseTool, ToolResult


class ListConnectionsTool(BaseTool):
    name = "list_active_connections"
    description = (
        "List all currently active database connections in this session. "
        "Returns name, type, version, and which is currently active. "
        "Use when user asks what databases are connected, or when routing cross-DB queries."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.core.multi_connection import MultiConnectionManager

        mc: MultiConnectionManager | None = context.get("multi_conn")
        if not mc:
            return ToolResult(
                success=False,
                error="Multi-connection manager not initialized",
            )

        info = mc.list_info()
        if not info:
            return ToolResult(
                success=True,
                data={"connections": []},
                summary="No active connections",
            )

        return ToolResult(
            success=True,
            data={"connections": info, "active": mc.active},
            summary=f"{len(info)} active connection(s); default: {mc.active}",
        )


class SwitchConnectionTool(BaseTool):
    name = "switch_connection"
    description = (
        "Switch the default active database connection. "
        "Use when the user wants to make a specific connection the primary target for subsequent queries."
    )
    is_read_only = False

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the connection to make active",
                },
            },
            "required": ["name"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.core.multi_connection import MultiConnectionManager

        mc: MultiConnectionManager | None = context.get("multi_conn")
        if not mc:
            return ToolResult(success=False, error="Multi-connection manager not initialized")

        name = args.get("name", "").strip()
        if not name:
            return ToolResult(success=False, error="name required")

        if not mc.set_active(name):
            available = ", ".join(mc.names()) or "none"
            return ToolResult(
                success=False,
                error=f"Connection '{name}' is not active. Available: {available}",
            )

        # Propagate the switch to the in-flight tool-call context AND the
        # parent ChatSession so any tools that still read
        # context["connection_manager"] or self.conn (dialect rules,
        # schema lookups, etc.) pick up the new connection immediately
        # instead of using the stale one captured at round start.
        new_mgr = mc.active_manager
        new_state = mc.active_state
        if new_mgr is not None:
            context["connection_manager"] = new_mgr
            if new_state is not None:
                context["t0"] = new_state.t0
                context["t1"] = new_state.t1
            chat = context.get("chat_session")
            if chat is not None:
                chat.conn = new_mgr
                if new_state is not None:
                    chat.t0 = new_state.t0
                    chat.t1 = new_state.t1

        return ToolResult(
            success=True,
            data={"active": name},
            summary=f"Active connection → {name}",
        )


class CompareSchemasTool(BaseTool):
    name = "compare_schemas"
    description = (
        "Compare a table's schema (columns, types) between two active connections. "
        "Returns columns only in A, only in B, type differences, and migration SQL to sync. "
        "Use when user asks to compare, diff, or sync schema across environments (e.g., prod vs staging)."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "Table name to compare (e.g., 'orders' or 'public.orders')",
                },
                "connection_a": {
                    "type": "string",
                    "description": "First connection name (source/reference)",
                },
                "connection_b": {
                    "type": "string",
                    "description": "Second connection name (target for potential sync)",
                },
            },
            "required": ["table", "connection_a", "connection_b"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.core.multi_connection import MultiConnectionManager

        mc: MultiConnectionManager | None = context.get("multi_conn")
        if not mc:
            return ToolResult(success=False, error="Multi-connection manager not initialized")

        table = args.get("table", "").strip()
        conn_a = args.get("connection_a", "").strip()
        conn_b = args.get("connection_b", "").strip()
        if not table or not conn_a or not conn_b:
            return ToolResult(success=False, error="table, connection_a, connection_b required")

        mgr_a = mc.resolve_manager(conn_a)
        mgr_b = mc.resolve_manager(conn_b)
        if not mgr_a or not mgr_b:
            return ToolResult(success=False, error=f"Connections not found: {conn_a}, {conn_b}")

        # Parse schema.table — if no schema given, use per-dialect default
        if "." in table:
            schema_a, table_name = table.split(".", 1)
            schema_b = schema_a
        else:
            table_name = table
            schema_a = _default_schema(mgr_a)
            schema_b = _default_schema(mgr_b)

        cols_a = await _fetch_columns(mgr_a, schema_a, table_name)
        cols_b = await _fetch_columns(mgr_b, schema_b, table_name)

        if not cols_a and not cols_b:
            return ToolResult(
                success=False,
                error=f"Table '{table}' not found in either connection",
            )

        # Normalize column names to lowercase for cross-dialect comparison
        a_map = {c["name"].lower(): c for c in cols_a}
        b_map = {c["name"].lower(): c for c in cols_b}
        a_names = set(a_map.keys())
        b_names = set(b_map.keys())

        only_in_a = [a_map[n] for n in sorted(a_names - b_names)]
        only_in_b = [b_map[n] for n in sorted(b_names - a_names)]

        type_diffs = []
        for name in sorted(a_names & b_names):
            ta = a_map[name]["type"]
            tb = b_map[name]["type"]
            if _normalize_type(ta) != _normalize_type(tb):
                type_diffs.append({"column": name, "type_a": ta, "type_b": tb})

        # Generate migration SQL to sync B → A
        migration_lines = []
        for col in only_in_a:
            migration_lines.append(
                f"ALTER TABLE {table} ADD COLUMN {col['name']} {col['type']};"
            )

        return ToolResult(
            success=True,
            data={
                "table": table,
                "connection_a": conn_a,
                "connection_b": conn_b,
                "columns_in_both": len(a_names & b_names),
                "only_in_a": only_in_a,
                "only_in_b": only_in_b,
                "type_differences": type_diffs,
                "migration_sql_to_sync_b_from_a": "\n".join(migration_lines) if migration_lines else None,
            },
            summary=(
                f"{len(a_names & b_names)} shared, "
                f"{len(only_in_a)} only in {conn_a}, "
                f"{len(only_in_b)} only in {conn_b}, "
                f"{len(type_diffs)} type differences"
            ),
        )


class CompareDataTool(BaseTool):
    name = "compare_data"
    description = (
        "Compare query results between two connections by a key column. "
        "Runs the same (or similar) query on both, then diffs rows by the key. "
        "Returns rows only in A, only in B, and rows that differ. "
        "Use for data-level comparison: find missing customers in staging, etc."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SELECT query to run on BOTH connections (must include the key column)",
                },
                "connection_a": {"type": "string"},
                "connection_b": {"type": "string"},
                "key_column": {
                    "type": "string",
                    "description": "Column name to match rows between result sets (e.g., 'id', 'email')",
                },
            },
            "required": ["sql", "connection_a", "connection_b", "key_column"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.core.multi_connection import MultiConnectionManager

        mc: MultiConnectionManager | None = context.get("multi_conn")
        if not mc:
            return ToolResult(success=False, error="Multi-connection manager not initialized")

        sql = args.get("sql", "").strip()
        conn_a = args.get("connection_a", "").strip()
        conn_b = args.get("connection_b", "").strip()
        key = args.get("key_column", "").strip()

        if not all([sql, conn_a, conn_b, key]):
            return ToolResult(
                success=False,
                error="sql, connection_a, connection_b, key_column all required",
            )

        mgr_a = mc.resolve_manager(conn_a)
        mgr_b = mc.resolve_manager(conn_b)
        if not mgr_a or not mgr_b:
            return ToolResult(success=False, error=f"Connections not found: {conn_a}, {conn_b}")

        try:
            rows_a = await mgr_a.execute_raw(sql)
            rows_b = await mgr_b.execute_raw(sql)
        except Exception as e:
            return ToolResult(success=False, error=f"Query failed: {e}")

        if rows_a and key not in rows_a[0]:
            return ToolResult(
                success=False,
                error=f"Key column '{key}' not in result. Columns: {list(rows_a[0].keys())}",
            )

        a_by_key = {r[key]: r for r in rows_a}
        b_by_key = {r[key]: r for r in rows_b}
        a_keys = set(a_by_key.keys())
        b_keys = set(b_by_key.keys())

        only_in_a_keys = sorted(a_keys - b_keys)
        only_in_b_keys = sorted(b_keys - a_keys)

        # Find rows that exist in both but differ
        differences = []
        for k in a_keys & b_keys:
            ra = a_by_key[k]
            rb = b_by_key[k]
            diff_cols = {col: (ra.get(col), rb.get(col)) for col in ra if ra.get(col) != rb.get(col)}
            if diff_cols:
                differences.append({"key": k, "differences": diff_cols})

        return ToolResult(
            success=True,
            data={
                "total_a": len(rows_a),
                "total_b": len(rows_b),
                "in_both": len(a_keys & b_keys),
                "only_in_a_count": len(only_in_a_keys),
                "only_in_b_count": len(only_in_b_keys),
                "only_in_a": [a_by_key[k] for k in only_in_a_keys[:20]],
                "only_in_b": [b_by_key[k] for k in only_in_b_keys[:20]],
                "differences_count": len(differences),
                "differences": differences[:20],
            },
            summary=(
                f"{conn_a}: {len(rows_a)}, {conn_b}: {len(rows_b)} | "
                f"only in {conn_a}: {len(only_in_a_keys)} | "
                f"only in {conn_b}: {len(only_in_b_keys)} | "
                f"different: {len(differences)}"
            ),
        )


async def _fetch_columns(mgr: Any, schema: str, table: str) -> list[dict[str, str]]:
    """Fetch column list (name + type) for a table. Uses introspection T2 DDL.

    Parses the CREATE TABLE DDL string returned by discover_t2.
    """
    from payp.db.introspection import discover_t2

    try:
        ddl = await discover_t2(mgr, schema, table)
    except Exception:
        return []

    if "not found" in ddl.lower():
        return []

    # Parse columns from CREATE TABLE DDL
    import re
    result = []
    # Match lines like "    col_name TYPE ..."
    for line in ddl.splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped or stripped.upper().startswith(("CREATE", ")", "(")):
            continue
        # First token = column name, second = type (possibly with parens)
        match = re.match(r"^(\w+)\s+([\w()\s,]+?)(\s+PRIMARY KEY|\s+NOT NULL|\s+DEFAULT|\s+REFERENCES|$)", stripped)
        if match:
            name = match.group(1)
            type_str = match.group(2).strip()
            result.append({
                "name": name,
                "type": type_str.upper(),
                "nullable": "NOT NULL" not in stripped.upper(),
            })
    return result


def _default_schema(mgr: Any) -> str:
    """Return the default schema name for a connection based on dialect."""
    from payp.models import DbType
    db_type = mgr.profile.db_type
    if db_type == DbType.POSTGRESQL:
        return "public"
    if db_type == DbType.MYSQL:
        return mgr.profile.database  # MySQL uses database as schema
    if db_type == DbType.ORACLE:
        return mgr.profile.username.upper()  # Oracle schema = owner = username
    return "public"


def _normalize_type(t: str) -> str:
    """Normalize type name for cross-dialect comparison."""
    t = t.upper().strip()
    # Common equivalents
    equivalents = {
        "NUMERIC": "DECIMAL",
        "INT4": "INTEGER",
        "INT": "INTEGER",
        "INT8": "BIGINT",
        "INT2": "SMALLINT",
        "VARCHAR2": "VARCHAR",
        "NUMBER": "NUMERIC",
        "TIMESTAMP(6)": "TIMESTAMP",
        "TIMESTAMPTZ": "TIMESTAMP WITH TIME ZONE",
        "TIMESTAMP WITHOUT TIME ZONE": "TIMESTAMP",
        "BOOL": "BOOLEAN",
    }
    # Strip size info for comparison if present
    base = t.split("(")[0].strip()
    return equivalents.get(base, base)
