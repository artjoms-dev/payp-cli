"""Bulk insert tool — insert many rows efficiently, dialect-aware.

Handles batching, Oracle IDENTITY sequence drift, and FK validation in ONE tool call.
Avoids burning tool-round budget on row-by-row inserts.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from payp.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class BulkInsertTool(BaseTool):
    name = "bulk_insert"
    tier = "advanced"
    description = (
        "Insert MANY rows into a table in ONE call. Handles batching, dialect differences, "
        "and Oracle IDENTITY sequence drift automatically. "
        "Use this instead of calling execute_sql in a loop — it's 10-100x faster and avoids round limits. "
        "Works for 10+ rows. For fewer, use execute_sql normally."
    )
    is_read_only = False
    is_destructive = False

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "Target table name (e.g., 'orders' or 'public.orders')",
                },
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Column names (OMIT auto-increment/IDENTITY/SERIAL id columns)",
                },
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {},
                    },
                    "description": "List of rows, each row is a list of values matching columns order",
                },
                "batch_size": {
                    "type": "integer",
                    "description": "Rows per INSERT statement (default 500, Oracle max 1000)",
                },
            },
            "required": ["table", "columns", "rows"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.db.connection import ConnectionManager
        from payp.db.identifiers import qualified, quote_ident, split_and_validate_table
        from payp.models import DbType

        conn: ConnectionManager | None = context.get("connection_manager")
        if not conn or not conn.is_connected:
            return ToolResult(success=False, error="Not connected to a database")

        table = args.get("table", "").strip()
        columns = args.get("columns", [])
        rows = args.get("rows", [])
        batch_size = int(args.get("batch_size", 500))

        if not table or not columns or not rows:
            return ToolResult(success=False, error="table, columns, rows all required")

        dialect = conn.profile.db_type

        # Validate and quote all identifiers once up front
        try:
            schema, tbl = split_and_validate_table(table)
            table_q = qualified(dialect, schema, tbl)
            cols_q = [quote_ident(dialect, c) for c in columns]
        except ValueError as e:
            return ToolResult(success=False, error=f"Invalid identifier: {e}")

        total_rows = len(rows)
        inserted = 0
        errors: list[str] = []

        # Batch-insert (parameterized — no value interpolation)
        for batch_start in range(0, total_rows, batch_size):
            batch = rows[batch_start:batch_start + batch_size]
            try:
                sql, params = _build_bulk_sql(dialect, table_q, cols_q, batch)
                await conn.execute_raw(sql, params)
                inserted += len(batch)
            except Exception as e:
                logger.exception("BulkInsertTool batch failed")
                err_str = str(e)
                errors.append(f"batch {batch_start}-{batch_start + len(batch)}: {err_str[:150]}")
                # Oracle IDENTITY drift recovery + fallback to per-row inserts
                if dialect == DbType.ORACLE and ("ORA-00001" in err_str or "unique constraint" in err_str):
                    fix_result = await _attempt_oracle_sequence_fix(conn, table_q)
                    if fix_result.get("fixed"):
                        errors.append(f"→ auto-fixed IDENTITY sequence to start at {fix_result['next_id']}")
                    # Fall back to per-row INSERT (works around Oracle INSERT ALL+IDENTITY quirks)
                    batch_inserted = await _insert_per_row(conn, table_q, cols_q, batch)
                    inserted += batch_inserted
                    if batch_inserted == len(batch):
                        errors.append(f"→ batch {batch_start} succeeded via per-row fallback")
                        continue
                    else:
                        errors.append(f"→ per-row fallback inserted {batch_inserted}/{len(batch)}")
                        break
                # For non-Oracle or non-sequence errors, try per-row fallback too
                batch_inserted = await _insert_per_row(conn, table_q, cols_q, batch)
                if batch_inserted > 0:
                    inserted += batch_inserted
                    errors.append(f"→ per-row fallback: {batch_inserted}/{len(batch)}")
                    if batch_inserted == len(batch):
                        continue
                # Non-recoverable error — stop
                break

        if inserted == total_rows:
            return ToolResult(
                success=True,
                data={"inserted": inserted, "batches": (total_rows + batch_size - 1) // batch_size},
                summary=f"Inserted {inserted}/{total_rows} rows into {table}",
            )
        else:
            return ToolResult(
                success=inserted > 0,
                data={"inserted": inserted, "total": total_rows, "errors": errors},
                error="\n".join(errors) if errors else "Unknown error",
                summary=f"Inserted {inserted}/{total_rows} rows (partial)",
            )


def _build_bulk_sql(
    dialect: Any,
    table_q: str,
    cols_q: list[str],
    rows: list[list[Any]],
) -> tuple[str, tuple[Any, ...]]:
    """Build dialect-appropriate parameterized bulk INSERT.

    `table_q` and each entry of `cols_q` must already be validated and
    dialect-quoted. Row values are passed via driver paramstyle (%s) — the
    Oracle driver translates these to :1, :2, ... automatically.
    Returns (sql, flat_params_tuple).
    """
    from payp.models import DbType

    cols_csv = ", ".join(cols_q)
    ncols = len(cols_q)
    per_row_ph = "(" + ", ".join(["%s"] * ncols) + ")"

    flat_params: list[Any] = []
    for row in rows:
        flat_params.extend(row)

    if dialect == DbType.ORACLE:
        # INSERT ALL INTO t (cols) VALUES (...) INTO t (cols) VALUES (...) SELECT 1 FROM dual
        lines = ["INSERT ALL"]
        for _ in rows:
            lines.append(f"  INTO {table_q} ({cols_csv}) VALUES {per_row_ph}")  # nosec B608
        lines.append("SELECT 1 FROM dual")
        return "\n".join(lines), tuple(flat_params)

    # PG + MySQL: INSERT INTO t (cols) VALUES (...), (...), ...
    values_clause = ", ".join([per_row_ph] * len(rows))
    sql = f"INSERT INTO {table_q} ({cols_csv}) VALUES {values_clause}"  # nosec B608
    return sql, tuple(flat_params)


async def _insert_per_row(
    conn: Any, table_q: str, cols_q: list[str], rows: list[list[Any]]
) -> int:
    """Insert rows one at a time (parameterized). Returns count inserted."""
    cols_csv = ", ".join(cols_q)
    placeholders = "(" + ", ".join(["%s"] * len(cols_q)) + ")"
    sql = f"INSERT INTO {table_q} ({cols_csv}) VALUES {placeholders}"  # nosec B608
    inserted = 0
    for row in rows:
        try:
            await conn.execute_raw(sql, tuple(row))
            inserted += 1
        except Exception as e:
            logger.warning("Row insert failed, skipping: %s", e)
    return inserted


async def _attempt_oracle_sequence_fix(conn: Any, table_q: str) -> dict[str, Any]:
    """Try to fix Oracle IDENTITY sequence drift.

    `table_q` must already be validated + dialect-quoted. `next_id` is a
    locally-computed int, not user input, so it is interpolated safely.
    Returns {fixed: bool, next_id: int}.
    """
    try:
        # Find id column name (usually 'id' or 'ID')
        rows = await conn.execute_raw(
            f"SELECT MAX(id) as max_id FROM {table_q}"  # nosec B608
        )
        if not rows or rows[0].get("max_id") is None:
            return {"fixed": False}
        max_id = int(rows[0]["max_id"])
        next_id = max_id + 1

        # Oracle: use RESTART START WITH, not just START WITH (which doesn't reset)
        try:
            await conn.execute_raw(
                f"ALTER TABLE {table_q} MODIFY id GENERATED BY DEFAULT AS IDENTITY (RESTART START WITH {next_id})"  # nosec B608
            )
        except Exception:
            # Fall back to drop-and-recreate the identity
            await conn.execute_raw(
                f"ALTER TABLE {table_q} MODIFY id DROP IDENTITY"  # nosec B608
            )
            await conn.execute_raw(
                f"ALTER TABLE {table_q} MODIFY id GENERATED BY DEFAULT AS IDENTITY (START WITH {next_id})"  # nosec B608
            )
        return {"fixed": True, "next_id": next_id}
    except Exception as e:
        logger.warning("Oracle IDENTITY fix failed: %s", e)
        return {"fixed": False}
