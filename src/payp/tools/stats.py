"""Stats tool — column-level data profiling for a table.

Dialect-aware: PostgreSQL, MySQL, Oracle.

For each column returns:
  - null_count, null_percent
  - distinct_count
  - numeric columns: min, max, avg, p50 (median), p95
  - text columns: avg_length, max_length
  - top 5 most common values (when cardinality < 50)

For large tables (>1M estimated rows), scans a LIMIT 100k sample for
distinct counting to keep runtime bounded.
"""

from __future__ import annotations

from typing import Any

from payp.db.connection import ConnectionManager
from payp.db.identifiers import qualified as _qualified
from payp.db.identifiers import quote_ident as _quote_ident
from payp.models import DbType
from payp.tools.base import BaseTool, ToolResult

# ---------------------------------------------------------------------------
# Type classification
# ---------------------------------------------------------------------------

_NUMERIC_TYPES = {
    # PostgreSQL
    "smallint", "integer", "bigint", "decimal", "numeric", "real",
    "double precision", "serial", "bigserial", "money",
    # MySQL
    "tinyint", "mediumint", "int", "float", "double", "dec", "fixed",
    # Oracle
    "number", "binary_float", "binary_double", "float",
}

_TEXT_TYPES = {
    # PostgreSQL
    "character varying", "varchar", "character", "char", "text", "citext",
    # MySQL
    "tinytext", "mediumtext", "longtext",
    # Oracle
    "varchar2", "nvarchar2", "nchar", "clob", "nclob",
}

_BINARY_TYPES = {
    "bytea", "blob", "tinyblob", "mediumblob", "longblob",
    "raw", "long raw", "bfile", "binary", "varbinary",
}

_DATE_TYPES = {
    "date", "timestamp", "timestamp with time zone",
    "timestamp without time zone", "datetime", "time",
    "timestamptz", "timestamp with local time zone",
}


def _classify(data_type: str) -> str:
    """Return 'numeric' | 'text' | 'date' | 'binary' | 'other'."""
    dt = data_type.lower().strip()
    if dt in _NUMERIC_TYPES:
        return "numeric"
    if dt in _TEXT_TYPES:
        return "text"
    if dt in _DATE_TYPES:
        return "date"
    if dt in _BINARY_TYPES:
        return "binary"
    # fuzzy matches
    if "int" in dt or "numeric" in dt or "decimal" in dt or "float" in dt or "double" in dt or "number" in dt:
        return "numeric"
    if "char" in dt or "text" in dt or "clob" in dt:
        return "text"
    if "blob" in dt or "binary" in dt or "bytea" in dt or "raw" in dt:
        return "binary"
    if "date" in dt or "time" in dt:
        return "date"
    return "other"


def _default_schema(db_type: DbType) -> str:
    if db_type == DbType.POSTGRESQL:
        return "public"
    if db_type == DbType.ORACLE:
        return ""  # Oracle uses current user; resolved via session
    return ""  # MySQL uses current database


# ---------------------------------------------------------------------------
# Column discovery
# ---------------------------------------------------------------------------

async def _get_columns(
    conn: ConnectionManager,
    schema: str,
    table: str,
) -> list[dict[str, Any]]:
    """Return list of {name, data_type} for table columns."""
    if conn.db_type == DbType.POSTGRESQL:
        rows = await conn.execute_raw(
            """
            SELECT column_name, data_type
              FROM information_schema.columns
             WHERE table_schema = %s AND table_name = %s
             ORDER BY ordinal_position
            """,
            (schema, table),
        )
    elif conn.db_type == DbType.MYSQL:
        sch = schema or None
        if sch:
            rows = await conn.execute_raw(
                """
                SELECT column_name, data_type
                  FROM information_schema.columns
                 WHERE table_schema = %s AND table_name = %s
                 ORDER BY ordinal_position
                """,
                (sch, table),
            )
        else:
            rows = await conn.execute_raw(
                """
                SELECT column_name, data_type
                  FROM information_schema.columns
                 WHERE table_schema = DATABASE() AND table_name = %s
                 ORDER BY ordinal_position
                """,
                (table,),
            )
    elif conn.db_type == DbType.ORACLE:
        sch = (schema or "").upper()
        tbl = table.upper()
        if sch:
            rows = await conn.execute_raw(
                """
                SELECT column_name, data_type
                  FROM all_tab_columns
                 WHERE owner = :1 AND table_name = :2
                 ORDER BY column_id
                """,
                (sch, tbl),
            )
        else:
            rows = await conn.execute_raw(
                """
                SELECT column_name, data_type
                  FROM user_tab_columns
                 WHERE table_name = :1
                 ORDER BY column_id
                """,
                (tbl,),
            )
    else:
        raise ValueError(f"Unsupported db_type: {conn.db_type}")

    return [
        {"name": r["column_name"], "data_type": r["data_type"], "kind": _classify(str(r["data_type"]))}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Percentile SQL (dialect-aware)
# ---------------------------------------------------------------------------

def _percentile_select(db_type: DbType, col_q: str) -> tuple[str, str] | None:
    """Return (p50_expr, p95_expr) or None if unsupported."""
    if db_type == DbType.POSTGRESQL:
        return (
            f"percentile_cont(0.5) WITHIN GROUP (ORDER BY {col_q})",
            f"percentile_cont(0.95) WITHIN GROUP (ORDER BY {col_q})",
        )
    if db_type == DbType.ORACLE:
        return (
            f"percentile_cont(0.5) WITHIN GROUP (ORDER BY {col_q})",
            f"percentile_cont(0.95) WITHIN GROUP (ORDER BY {col_q})",
        )
    # MySQL 8+: no percentile_cont; skip percentiles for now
    return None


def _top_n_limit(db_type: DbType, n: int) -> str:
    if db_type == DbType.ORACLE:
        return f"FETCH FIRST {n} ROWS ONLY"
    return f"LIMIT {n}"


# ---------------------------------------------------------------------------
# Per-column statistics
# ---------------------------------------------------------------------------

async def _column_stats(
    conn: ConnectionManager,
    schema: str,
    table: str,
    column: dict[str, Any],
    total_rows: int,
) -> dict[str, Any]:
    """Compute stats for a single column."""
    name = column["name"]
    kind = column["kind"]
    data_type = column["data_type"]

    result: dict[str, Any] = {
        "column": name,
        "data_type": data_type,
        "kind": kind,
        "null_count": None,
        "null_percent": None,
        "distinct_count": None,
        "min": None,
        "max": None,
        "avg": None,
        "p50": None,
        "p95": None,
        "avg_length": None,
        "max_length": None,
        "top_values": [],
        "skipped": None,
    }

    if kind == "binary":
        result["skipped"] = "binary column — stats skipped"
        return result

    qual = _qualified(conn.db_type, schema, table)
    col_q = _quote_ident(conn.db_type, name)

    # Base counts (non-null & distinct). Use sampled distinct when big.
    use_sample = total_rows > 1_000_000
    try:
        if use_sample:
            # Sample subquery for distinct
            # col_q and qual are validated + dialect-quoted via db.identifiers
            if conn.db_type == DbType.ORACLE:
                distinct_sql = (
                    f"SELECT COUNT(*) AS total, COUNT({col_q}) AS non_null, "  # nosec B608
                    f"COUNT(DISTINCT {col_q}) AS distinct_count "
                    f"FROM (SELECT {col_q} FROM {qual} FETCH FIRST 100000 ROWS ONLY)"
                )
            else:
                distinct_sql = (
                    f"SELECT COUNT(*) AS total, COUNT({col_q}) AS non_null, "  # nosec B608
                    f"COUNT(DISTINCT {col_q}) AS distinct_count "
                    f"FROM (SELECT {col_q} FROM {qual} LIMIT 100000) sub"
                )
            sample_rows = await conn.execute_raw(distinct_sql)
            if sample_rows:
                r = sample_rows[0]
                # approx null% from sample
                sample_total = int(r.get("total") or 0)
                sample_non_null = int(r.get("non_null") or 0)
                sample_nulls = sample_total - sample_non_null
                result["null_count"] = int(sample_nulls * (total_rows / sample_total)) if sample_total else 0
                result["null_percent"] = round(100.0 * sample_nulls / sample_total, 2) if sample_total else 0.0
                result["distinct_count"] = int(r.get("distinct_count") or 0)
                result["distinct_sampled"] = True
        else:
            base_sql = (
                f"SELECT COUNT({col_q}) AS non_null, "  # nosec B608
                f"COUNT(DISTINCT {col_q}) AS distinct_count "
                f"FROM {qual}"
            )
            base = await conn.execute_raw(base_sql)
            if base:
                r = base[0]
                non_null = int(r.get("non_null") or 0)
                nulls = total_rows - non_null
                result["null_count"] = nulls
                result["null_percent"] = round(100.0 * nulls / total_rows, 2) if total_rows else 0.0
                result["distinct_count"] = int(r.get("distinct_count") or 0)
    except Exception as e:
        result["error"] = f"base stats: {e}"
        return result

    # Numeric stats
    if kind == "numeric":
        try:
            pct = _percentile_select(conn.db_type, col_q)
            if pct:
                num_sql = (
                    f"SELECT MIN({col_q}) AS minv, MAX({col_q}) AS maxv, AVG({col_q}) AS avgv, "  # nosec B608
                    f"{pct[0]} AS p50, {pct[1]} AS p95 "
                    f"FROM {qual} WHERE {col_q} IS NOT NULL"
                )
            else:
                num_sql = (
                    f"SELECT MIN({col_q}) AS minv, MAX({col_q}) AS maxv, AVG({col_q}) AS avgv "  # nosec B608
                    f"FROM {qual} WHERE {col_q} IS NOT NULL"
                )
            nrows = await conn.execute_raw(num_sql)
            if nrows:
                r = nrows[0]
                result["min"] = r.get("minv")
                result["max"] = r.get("maxv")
                avg_v = r.get("avgv")
                result["avg"] = float(avg_v) if avg_v is not None else None
                p50 = r.get("p50")
                p95 = r.get("p95")
                result["p50"] = float(p50) if p50 is not None else None
                result["p95"] = float(p95) if p95 is not None else None
        except Exception as e:
            result["error"] = f"numeric: {e}"

    # Text stats
    if kind == "text":
        try:
            tsql = (
                f"SELECT AVG(LENGTH({col_q})) AS avg_len, MAX(LENGTH({col_q})) AS max_len "  # nosec B608
                f"FROM {qual} WHERE {col_q} IS NOT NULL"
            )
            trows = await conn.execute_raw(tsql)
            if trows:
                r = trows[0]
                avg_len = r.get("avg_len")
                max_len = r.get("max_len")
                result["avg_length"] = float(avg_len) if avg_len is not None else None
                result["max_length"] = int(max_len) if max_len is not None else None
        except Exception as e:
            result["error"] = f"text: {e}"

    # Date stats (min/max)
    if kind == "date":
        try:
            dsql = (
                f"SELECT MIN({col_q}) AS minv, MAX({col_q}) AS maxv "  # nosec B608
                f"FROM {qual} WHERE {col_q} IS NOT NULL"
            )
            drows = await conn.execute_raw(dsql)
            if drows:
                r = drows[0]
                result["min"] = str(r.get("minv")) if r.get("minv") is not None else None
                result["max"] = str(r.get("maxv")) if r.get("maxv") is not None else None
        except Exception as e:
            result["error"] = f"date: {e}"

    # Top 5 values when cardinality is low
    distinct = result.get("distinct_count") or 0
    if 0 < distinct < 50 and kind in ("text", "numeric", "date", "other"):
        try:
            limit_clause = _top_n_limit(conn.db_type, 5)
            topsql = (
                f"SELECT {col_q} AS v, COUNT(*) AS cnt "  # nosec B608
                f"FROM {qual} WHERE {col_q} IS NOT NULL "
                f"GROUP BY {col_q} ORDER BY COUNT(*) DESC {limit_clause}"
            )
            trows = await conn.execute_raw(topsql)
            top = []
            for r in trows:
                v = r.get("v")
                cnt = int(r.get("cnt") or 0)
                top.append({"value": v, "count": cnt})
            result["top_values"] = top
        except Exception as e:
            result["error"] = (result.get("error") or "") + f" top: {e}"

    return result


# ---------------------------------------------------------------------------
# Main profile function
# ---------------------------------------------------------------------------

async def profile_table(
    conn: ConnectionManager,
    table: str,
    schema: str | None = None,
) -> dict[str, Any]:
    """Profile all columns of a table. Returns a dict with total_rows + columns."""
    if schema is None:
        schema = _default_schema(conn.db_type)
    # Normalize Oracle
    if conn.db_type == DbType.ORACLE:
        table = table.upper()
        schema = (schema or "").upper()

    cols = await _get_columns(conn, schema, table)
    if not cols:
        return {
            "table": table,
            "schema": schema,
            "total_rows": 0,
            "columns": [],
            "error": f"Table not found or empty column list: {schema}.{table}" if schema else f"Table not found: {table}",
        }

    qual = _qualified(conn.db_type, schema, table)
    # Total row count
    cnt_rows = await conn.execute_raw(f"SELECT COUNT(*) AS total FROM {qual}")  # nosec B608
    total_rows = int(cnt_rows[0].get("total") or 0) if cnt_rows else 0

    column_stats: list[dict[str, Any]] = []
    for c in cols:
        stats = await _column_stats(conn, schema, table, c, total_rows)
        column_stats.append(stats)

    return {
        "table": table,
        "schema": schema,
        "total_rows": total_rows,
        "columns": column_stats,
    }


# ---------------------------------------------------------------------------
# LLM-callable tool
# ---------------------------------------------------------------------------

class StatsTool(BaseTool):
    name = "table_stats"
    description = (
        "Profile a table and return column-level statistics. For each column: "
        "null count/percent, distinct count (cardinality), numeric min/max/avg/median/p95, "
        "text length stats, and top 5 most common values when cardinality is low. "
        "Useful for data quality checks, EDA, and understanding a dataset before querying."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "Table name to profile",
                },
                "schema": {
                    "type": "string",
                    "description": "Schema name (optional — defaults to 'public' for PG, current DB for MySQL, current user for Oracle)",
                },
            },
            "required": ["table"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        conn: ConnectionManager | None = context.get("connection_manager")
        if not conn or not conn.is_connected:
            return ToolResult(success=False, error="Not connected to a database")

        table = (args.get("table") or "").strip()
        schema = (args.get("schema") or "").strip() or None
        if not table:
            return ToolResult(success=False, error="No table provided")

        # Allow "schema.table" shorthand
        if schema is None and "." in table:
            schema, table = table.split(".", 1)

        try:
            profile = await profile_table(conn, table, schema)
            if profile.get("error") and not profile.get("columns"):
                return ToolResult(success=False, error=profile["error"])

            total = profile["total_rows"]
            n_cols = len(profile["columns"])
            summary = f"Profiled {profile['schema']}.{profile['table']}: {total} rows, {n_cols} columns"
            return ToolResult(success=True, data=profile, summary=summary)
        except Exception as e:
            return ToolResult(success=False, error=str(e), summary=f"Stats failed: {e}")
