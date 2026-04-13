"""Schema introspection for payp — multi-dialect.

Dispatches discovery queries by DbType:
- PostgreSQL: information_schema + pg_* catalogs
- MySQL: information_schema (column 'referenced_table_name' for FK target)
- Oracle: all_tables / all_tab_columns / all_constraints / all_indexes,
          system schemas excluded.

The output format (SchemaIndex, SchemaCatalog, T2 DDL string, T3 dict)
is uniform across dialects so the LLM prompt builder doesn't need to
know the source database.
"""

from __future__ import annotations

from typing import Any

from payp.db.connection import ConnectionManager
from payp.models import DbType, SchemaCatalog, SchemaGraph, SchemaIndex

# System schemas we never introspect
PG_SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")
MYSQL_SYSTEM_SCHEMAS = ("information_schema", "mysql", "performance_schema", "sys")
ORACLE_SYSTEM_SCHEMAS = (
    "SYS", "SYSTEM", "OUTLN", "XDB", "CTXSYS", "MDSYS", "ORDSYS", "ORDDATA",
    "ORDPLUGINS", "DBSNMP", "APPQOSSYS", "DBSFWUSER", "AUDSYS", "GSMADMIN_INTERNAL",
    "GSMCATUSER", "GSMUSER", "LBACSYS", "OJVMSYS", "OLAPSYS", "REMOTE_SCHEDULER_AGENT",
    "SI_INFORMTN_SCHEMA", "SYSBACKUP", "SYSDG", "SYSKM", "SYSRAC", "SYS$UMF",
    "WMSYS", "DIP", "ANONYMOUS", "XS$NULL", "PUBLIC", "DVSYS", "DVF",
    "GSMROOTUSER", "GGSYS", "MDDATA", "PDBADMIN",
)


def _oracle_excl_list() -> str:
    return ", ".join(f"'{s}'" for s in ORACLE_SYSTEM_SCHEMAS)


# ---------------------------------------------------------------------------
# T0 — schema summary
# ---------------------------------------------------------------------------

async def discover_t0(conn: ConnectionManager) -> SchemaIndex:
    """T0 — Schema-level summary: schema names, table counts, view counts."""
    db_version = conn.db_version or "unknown"

    if conn.db_type == DbType.POSTGRESQL:
        rows = await conn.execute_raw("""
            SELECT table_schema, table_type, COUNT(*) as cnt
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
            GROUP BY table_schema, table_type
            ORDER BY table_schema
        """)
    elif conn.db_type == DbType.MYSQL:
        rows = await conn.execute_raw("""
            SELECT table_schema, table_type, COUNT(*) as cnt
            FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
            GROUP BY table_schema, table_type
            ORDER BY table_schema
        """)
    elif conn.db_type == DbType.ORACLE:
        excl = _oracle_excl_list()
        # excl is built from a hardcoded ORACLE_SYSTEM_SCHEMAS tuple
        rows = await conn.execute_raw(f"""
            SELECT owner AS table_schema, 'BASE TABLE' AS table_type, COUNT(*) AS cnt
              FROM all_tables
             WHERE owner NOT IN ({excl})
             GROUP BY owner
            UNION ALL
            SELECT owner AS table_schema, 'VIEW' AS table_type, COUNT(*) AS cnt
              FROM all_views
             WHERE owner NOT IN ({excl})
             GROUP BY owner
        """)  # nosec B608
    elif conn.db_type == DbType.MONGODB:
        coll_rows = await conn.execute_raw('{"op": "listCollections"}')
        version_rows = await conn.execute_raw('{"op": "serverInfo"}')
        version = version_rows[0].get("version", "unknown") if version_rows else "unknown"
        db_name = conn.profile.database
        coll_count = len(coll_rows)
        return SchemaIndex(
            db_version=f"MongoDB {version}",
            schemas={db_name: coll_count},
            view_count=0,
            total_tables=coll_count,
        )
    else:
        raise ValueError(f"Unsupported db_type: {conn.db_type}")

    schemas: dict[str, int] = {}
    view_count = 0
    total_tables = 0

    for row in rows:
        schema = row["table_schema"]
        ttype = str(row["table_type"]).upper()
        cnt = int(row["cnt"])

        if ttype == "BASE TABLE":
            schemas[schema] = schemas.get(schema, 0) + cnt
            total_tables += cnt
        elif ttype == "VIEW":
            view_count += cnt

    return SchemaIndex(
        db_version=db_version,
        schemas=schemas,
        view_count=view_count,
        total_tables=total_tables,
    )


# ---------------------------------------------------------------------------
# T1 — all table names by schema
# ---------------------------------------------------------------------------

async def discover_t1(conn: ConnectionManager) -> SchemaCatalog:
    """T1 — All table names grouped by schema."""
    if conn.db_type == DbType.POSTGRESQL:
        rows = await conn.execute_raw("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
              AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name
        """)
    elif conn.db_type == DbType.MYSQL:
        rows = await conn.execute_raw("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
              AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name
        """)
    elif conn.db_type == DbType.ORACLE:
        excl = _oracle_excl_list()
        # excl is built from a hardcoded ORACLE_SYSTEM_SCHEMAS tuple
        rows = await conn.execute_raw(f"""
            SELECT owner AS table_schema, table_name
              FROM all_tables
             WHERE owner NOT IN ({excl})
             ORDER BY owner, table_name
        """)  # nosec B608
    elif conn.db_type == DbType.MONGODB:
        coll_rows = await conn.execute_raw('{"op": "listCollections"}')
        db_name = conn.profile.database
        tables_result: dict[str, list[str]] = {db_name: [row["name"] for row in coll_rows]}
        return SchemaCatalog(tables=tables_result)
    else:
        raise ValueError(f"Unsupported db_type: {conn.db_type}")

    tables: dict[str, list[str]] = {}
    for row in rows:
        schema = row["table_schema"]
        tables.setdefault(schema, []).append(row["table_name"])

    return SchemaCatalog(tables=tables)


# ---------------------------------------------------------------------------
# T2 — per-table DDL
# ---------------------------------------------------------------------------

async def discover_t2(conn: ConnectionManager, schema: str, table: str) -> str:
    """T2 — Full DDL-like description for a specific table.

    For MySQL, `schema` is the database name. Oracle uppercases unquoted
    identifiers: callers may pass lowercase; we normalise to uppercase.
    """
    if conn.db_type == DbType.POSTGRESQL:
        return await _t2_postgres(conn, schema, table)
    if conn.db_type == DbType.MYSQL:
        return await _t2_mysql(conn, schema, table)
    if conn.db_type == DbType.ORACLE:
        return await _t2_oracle(conn, schema.upper(), table.upper())
    if conn.db_type == DbType.MONGODB:
        return await _t2_mongo(conn, table)
    raise ValueError(f"Unsupported db_type: {conn.db_type}")


async def _t2_postgres(conn: ConnectionManager, schema: str, table: str) -> str:
    cols = await conn.execute_raw("""
        SELECT column_name, data_type, character_maximum_length,
               numeric_precision, numeric_scale,
               is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
    """, (schema, table))

    if not cols:
        return f"-- Table {schema}.{table} not found or has no columns"

    pk_rows = await conn.execute_raw("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
          AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = %s AND tc.table_name = %s
          AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position
    """, (schema, table))
    pk_columns = {r["column_name"] for r in pk_rows}

    fk_rows = await conn.execute_raw("""
        SELECT
            kcu.column_name,
            ccu.table_schema AS ref_schema,
            ccu.table_name AS ref_table,
            ccu.column_name AS ref_column
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
          AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name
          AND tc.table_schema = ccu.table_schema
        WHERE tc.table_schema = %s AND tc.table_name = %s
          AND tc.constraint_type = 'FOREIGN KEY'
    """, (schema, table))
    fk_map = {r["column_name"]: f"{r['ref_table']}({r['ref_column']})" for r in fk_rows}

    return _render_ddl(schema, table, cols, pk_columns, fk_map)


async def _t2_mysql(conn: ConnectionManager, schema: str, table: str) -> str:
    cols = await conn.execute_raw("""
        SELECT column_name, data_type, character_maximum_length,
               numeric_precision, numeric_scale,
               is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
    """, (schema, table))

    if not cols:
        return f"-- Table {schema}.{table} not found or has no columns"

    # MySQL: primary key via table_constraints + key_column_usage (same as PG)
    pk_rows = await conn.execute_raw("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
          AND tc.table_schema = kcu.table_schema
          AND tc.table_name = kcu.table_name
        WHERE tc.table_schema = %s AND tc.table_name = %s
          AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position
    """, (schema, table))
    pk_columns = {r["column_name"] for r in pk_rows}

    # MySQL FK: column in key_column_usage has referenced_table_name / referenced_column_name
    fk_rows = await conn.execute_raw("""
        SELECT column_name,
               referenced_table_schema AS ref_schema,
               referenced_table_name   AS ref_table,
               referenced_column_name  AS ref_column
        FROM information_schema.key_column_usage
        WHERE table_schema = %s AND table_name = %s
          AND referenced_table_name IS NOT NULL
    """, (schema, table))
    fk_map = {r["column_name"]: f"{r['ref_table']}({r['ref_column']})" for r in fk_rows}

    # Normalise keys: aiomysql may return column names in mixed case depending
    # on server config; keep as-is.
    return _render_ddl(schema, table, cols, pk_columns, fk_map)


async def _t2_oracle(conn: ConnectionManager, schema: str, table: str) -> str:
    cols = await conn.execute_raw("""
        SELECT column_name, data_type,
               char_length AS character_maximum_length,
               data_precision AS numeric_precision,
               data_scale AS numeric_scale,
               nullable, data_default AS column_default
          FROM all_tab_columns
         WHERE owner = :1 AND table_name = :2
         ORDER BY column_id
    """, (schema, table))

    if not cols:
        return f"-- Table {schema}.{table} not found or has no columns"

    # Oracle returns nullable as 'Y'/'N' — normalise to 'YES'/'NO'
    for c in cols:
        nul = c.get("nullable")
        c["is_nullable"] = "YES" if nul == "Y" else "NO"

    # Primary key columns
    pk_rows = await conn.execute_raw("""
        SELECT acc.column_name
          FROM all_constraints ac
          JOIN all_cons_columns acc
            ON ac.owner = acc.owner
           AND ac.constraint_name = acc.constraint_name
         WHERE ac.owner = :1 AND ac.table_name = :2
           AND ac.constraint_type = 'P'
         ORDER BY acc.position
    """, (schema, table))
    pk_columns = {r["column_name"] for r in pk_rows}

    # Foreign keys: join constraints -> cons_columns -> referenced table
    fk_rows = await conn.execute_raw("""
        SELECT acc.column_name,
               rcc.table_name  AS ref_table,
               rcc.column_name AS ref_column
          FROM all_constraints ac
          JOIN all_cons_columns acc
            ON ac.owner = acc.owner
           AND ac.constraint_name = acc.constraint_name
          JOIN all_cons_columns rcc
            ON ac.r_owner = rcc.owner
           AND ac.r_constraint_name = rcc.constraint_name
           AND acc.position = rcc.position
         WHERE ac.owner = :1 AND ac.table_name = :2
           AND ac.constraint_type = 'R'
    """, (schema, table))
    fk_map = {r["column_name"]: f"{r['ref_table']}({r['ref_column']})" for r in fk_rows}

    return _render_ddl(schema, table, cols, pk_columns, fk_map)


async def _t2_mongo(conn: ConnectionManager, collection: str) -> str:
    """T2 for MongoDB: sample 50 docs to infer schema + list indexes."""
    import json as _json

    # Sample documents for schema inference
    sample_rows = await conn.execute_raw(
        _json.dumps({"op": "sample", "collection": collection, "size": 50})
    )
    # Count
    count_rows = await conn.execute_raw(
        _json.dumps({"op": "countDocuments", "collection": collection, "filter": {}})
    )
    total_docs = count_rows[0].get("count", "?") if count_rows else "?"

    # Infer field types from sampled documents
    field_types: dict[str, set[str]] = {}
    for doc in sample_rows:
        for k, v in doc.items():
            if k == "_id":
                continue
            type_name = type(v).__name__
            field_types.setdefault(k, set()).add(type_name)

    # Get indexes
    idx_rows = await conn.execute_raw(
        _json.dumps({"op": "indexes", "collection": collection})
    )

    lines = [
        f"Collection: {collection}  ({total_docs:,} documents)" if isinstance(total_docs, int)
        else f"Collection: {collection}  ({total_docs} documents)",
        "",
        f"Inferred fields (from {len(sample_rows)} sampled docs):",
    ]
    for field, types in sorted(field_types.items()):
        type_str = " | ".join(sorted(types))
        lines.append(f"  {field:<24} {type_str}")

    if idx_rows:
        lines.append("")
        lines.append("Indexes:")
        for idx in idx_rows:
            name = idx.get("name", "?")
            key = idx.get("key", {})
            unique = "  [unique]" if idx.get("unique") else ""
            lines.append(f"  {name:<30} {key}{unique}")

    return "\n".join(lines)


def _render_ddl(
    schema: str,
    table: str,
    cols: list[dict[str, Any]],
    pk_columns: set[str],
    fk_map: dict[str, str],
) -> str:
    """Shared DDL renderer — compatible with all dialects."""
    lines = [f"CREATE TABLE {schema}.{table} ("]
    col_defs = []
    for c in cols:
        col_name = c["column_name"]
        col_type = _format_column_type(c)
        parts = [f"    {col_name} {col_type}"]

        if col_name in pk_columns:
            parts.append("PRIMARY KEY")
        nullable = str(c.get("is_nullable", "YES")).upper()
        if nullable == "NO" and col_name not in pk_columns:
            parts.append("NOT NULL")
        default = c.get("column_default")
        if default is not None and "nextval" not in str(default):
            parts.append(f"DEFAULT {str(default).strip()}")
        if col_name in fk_map:
            parts.append(f"REFERENCES {fk_map[col_name]}")

        col_defs.append(" ".join(parts))

    lines.append(",\n".join(col_defs))
    lines.append(");")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# T3 — deep info
# ---------------------------------------------------------------------------

async def discover_t3(conn: ConnectionManager, schema: str, table: str) -> dict[str, Any]:
    """T3 — Deep info: indexes, constraints, triggers, stats, references."""
    if conn.db_type == DbType.POSTGRESQL:
        return await _t3_postgres(conn, schema, table)
    if conn.db_type == DbType.MYSQL:
        return await _t3_mysql(conn, schema, table)
    if conn.db_type == DbType.ORACLE:
        return await _t3_oracle(conn, schema.upper(), table.upper())
    if conn.db_type == DbType.MONGODB:
        return await _t3_mongo(conn, table)
    raise ValueError(f"Unsupported db_type: {conn.db_type}")


async def _t3_postgres(conn: ConnectionManager, schema: str, table: str) -> dict[str, Any]:
    result: dict[str, Any] = {}

    idx_rows = await conn.execute_raw("""
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = %s AND tablename = %s
        ORDER BY indexname
    """, (schema, table))
    result["indexes"] = [(r["indexname"], r["indexdef"]) for r in idx_rows]

    chk_rows = await conn.execute_raw("""
        SELECT tc.constraint_name, cc.check_clause
        FROM information_schema.table_constraints tc
        JOIN information_schema.check_constraints cc
          ON tc.constraint_name = cc.constraint_name
          AND tc.constraint_schema = cc.constraint_schema
        WHERE tc.table_schema = %s AND tc.table_name = %s
          AND tc.constraint_type = 'CHECK'
    """, (schema, table))
    result["checks"] = [(r["constraint_name"], r["check_clause"]) for r in chk_rows]

    trg_rows = await conn.execute_raw("""
        SELECT trigger_name, event_manipulation, action_timing
        FROM information_schema.triggers
        WHERE event_object_schema = %s AND event_object_table = %s
    """, (schema, table))
    result["triggers"] = [
        (r["trigger_name"], f"{r['action_timing']} {r['event_manipulation']}")
        for r in trg_rows
    ]

    stat_rows = await conn.execute_raw("""
        SELECT
            n_live_tup AS row_estimate,
            pg_size_pretty(pg_total_relation_size(quote_ident(%s) || '.' || quote_ident(%s))) AS total_size,
            last_vacuum,
            last_autovacuum
        FROM pg_stat_user_tables
        WHERE schemaname = %s AND relname = %s
    """, (schema, table, schema, table))
    if stat_rows:
        s = stat_rows[0]
        result["stats"] = {
            "row_estimate": s["row_estimate"],
            "total_size": s["total_size"],
            "last_vacuum": str(s["last_vacuum"]) if s["last_vacuum"] else None,
            "last_autovacuum": str(s["last_autovacuum"]) if s["last_autovacuum"] else None,
        }

    ref_rows = await conn.execute_raw("""
        SELECT
            kcu.table_schema || '.' || kcu.table_name AS referencing_table,
            kcu.column_name AS referencing_column
        FROM information_schema.referential_constraints rc
        JOIN information_schema.key_column_usage kcu
          ON rc.constraint_name = kcu.constraint_name
          AND rc.constraint_schema = kcu.constraint_schema
        JOIN information_schema.constraint_column_usage ccu
          ON rc.unique_constraint_name = ccu.constraint_name
          AND rc.unique_constraint_schema = ccu.constraint_schema
        WHERE ccu.table_schema = %s AND ccu.table_name = %s
    """, (schema, table))
    result["referenced_by"] = [
        (r["referencing_table"], r["referencing_column"]) for r in ref_rows
    ]

    return result


async def _t3_mysql(conn: ConnectionManager, schema: str, table: str) -> dict[str, Any]:
    result: dict[str, Any] = {}

    # Indexes — aggregated per index_name
    idx_rows = await conn.execute_raw("""
        SELECT index_name, GROUP_CONCAT(column_name ORDER BY seq_in_index) AS cols,
               non_unique, index_type
          FROM information_schema.statistics
         WHERE table_schema = %s AND table_name = %s
         GROUP BY index_name, non_unique, index_type
         ORDER BY index_name
    """, (schema, table))
    result["indexes"] = [
        (
            r["index_name"],
            f"{'' if r['non_unique'] else 'UNIQUE '}INDEX ({r['cols']}) USING {r['index_type']}",
        )
        for r in idx_rows
    ]

    result["checks"] = []  # MySQL 8 has check_constraints but skip for parity

    trg_rows = await conn.execute_raw("""
        SELECT trigger_name, event_manipulation, action_timing
          FROM information_schema.triggers
         WHERE event_object_schema = %s AND event_object_table = %s
    """, (schema, table))
    result["triggers"] = [
        (r["trigger_name"], f"{r['action_timing']} {r['event_manipulation']}")
        for r in trg_rows
    ]

    stat_rows = await conn.execute_raw("""
        SELECT table_rows AS row_estimate,
               data_length + index_length AS total_size
          FROM information_schema.tables
         WHERE table_schema = %s AND table_name = %s
    """, (schema, table))
    if stat_rows:
        s = stat_rows[0]
        result["stats"] = {
            "row_estimate": s["row_estimate"],
            "total_size": f"{s['total_size']} bytes" if s["total_size"] else None,
            "last_vacuum": None,
            "last_autovacuum": None,
        }

    ref_rows = await conn.execute_raw("""
        SELECT CONCAT(table_schema, '.', table_name) AS referencing_table,
               column_name AS referencing_column
          FROM information_schema.key_column_usage
         WHERE referenced_table_schema = %s AND referenced_table_name = %s
    """, (schema, table))
    result["referenced_by"] = [
        (r["referencing_table"], r["referencing_column"]) for r in ref_rows
    ]

    return result


async def _t3_oracle(conn: ConnectionManager, schema: str, table: str) -> dict[str, Any]:
    result: dict[str, Any] = {}

    idx_rows = await conn.execute_raw("""
        SELECT ai.index_name,
               LISTAGG(aic.column_name, ',') WITHIN GROUP (ORDER BY aic.column_position) AS cols,
               ai.uniqueness, ai.index_type
          FROM all_indexes ai
          JOIN all_ind_columns aic
            ON ai.owner = aic.index_owner
           AND ai.index_name = aic.index_name
         WHERE ai.table_owner = :1 AND ai.table_name = :2
         GROUP BY ai.index_name, ai.uniqueness, ai.index_type
         ORDER BY ai.index_name
    """, (schema, table))
    result["indexes"] = [
        (
            r["index_name"],
            f"{r['uniqueness']} INDEX ({r['cols']}) TYPE {r['index_type']}",
        )
        for r in idx_rows
    ]

    chk_rows = await conn.execute_raw("""
        SELECT constraint_name, search_condition_vc AS check_clause
          FROM all_constraints
         WHERE owner = :1 AND table_name = :2 AND constraint_type = 'C'
           AND generated = 'USER NAME'
    """, (schema, table))
    result["checks"] = [(r["constraint_name"], r["check_clause"]) for r in chk_rows]

    trg_rows = await conn.execute_raw("""
        SELECT trigger_name, triggering_event, trigger_type
          FROM all_triggers
         WHERE table_owner = :1 AND table_name = :2
    """, (schema, table))
    result["triggers"] = [
        (r["trigger_name"], f"{r['trigger_type']} {r['triggering_event']}")
        for r in trg_rows
    ]

    stat_rows = await conn.execute_raw("""
        SELECT num_rows AS row_estimate, blocks
          FROM all_tables
         WHERE owner = :1 AND table_name = :2
    """, (schema, table))
    if stat_rows:
        s = stat_rows[0]
        result["stats"] = {
            "row_estimate": s["row_estimate"],
            "total_size": f"{s['blocks']} blocks" if s["blocks"] else None,
            "last_vacuum": None,
            "last_autovacuum": None,
        }

    ref_rows = await conn.execute_raw("""
        SELECT ac.owner || '.' || ac.table_name AS referencing_table,
               acc.column_name                   AS referencing_column
          FROM all_constraints ac
          JOIN all_cons_columns acc
            ON ac.owner = acc.owner
           AND ac.constraint_name = acc.constraint_name
          JOIN all_constraints ref
            ON ac.r_owner = ref.owner
           AND ac.r_constraint_name = ref.constraint_name
         WHERE ac.constraint_type = 'R'
           AND ref.owner = :1 AND ref.table_name = :2
    """, (schema, table))
    result["referenced_by"] = [
        (r["referencing_table"], r["referencing_column"]) for r in ref_rows
    ]

    return result


async def _t3_mongo(conn: ConnectionManager, collection: str) -> dict[str, Any]:
    """T3 for MongoDB: collection stats and index details."""
    import json as _json

    stats_rows = await conn.execute_raw(
        _json.dumps({"op": "collStats", "collection": collection})
    )
    idx_rows = await conn.execute_raw(
        _json.dumps({"op": "indexes", "collection": collection})
    )
    stats = stats_rows[0] if stats_rows else {}
    return {
        "count": stats.get("count", 0),
        "size": stats.get("size", 0),
        "avgObjSize": stats.get("avgObjSize", 0),
        "storageSize": stats.get("storageSize", 0),
        "indexes": idx_rows,
    }


# ---------------------------------------------------------------------------
# Column type formatting + metadata + context formatters
# ---------------------------------------------------------------------------

async def get_db_metadata(conn: ConnectionManager) -> dict[str, Any]:
    """Basic database metadata for initial discovery display."""
    meta: dict[str, Any] = {"version": conn.db_version}

    if conn.db_type == DbType.POSTGRESQL:
        rows = await conn.execute_raw(
            "SELECT pg_size_pretty(pg_database_size(current_database())) AS db_size"
        )
        meta["db_size"] = rows[0]["db_size"] if rows else "unknown"
        rows = await conn.execute_raw("SELECT NOW() - pg_postmaster_start_time() AS uptime")
        if rows:
            uptime = rows[0]["uptime"]
            meta["uptime"] = str(uptime).split(".")[0] if uptime else "unknown"
        rows = await conn.execute_raw(
            "SELECT current_user AS username, current_setting('is_superuser') AS is_superuser"
        )
        if rows:
            meta["username"] = rows[0]["username"]
            meta["is_superuser"] = rows[0]["is_superuser"] == "on"
    elif conn.db_type == DbType.MYSQL:
        rows = await conn.execute_raw("""
            SELECT SUM(data_length + index_length) AS size_bytes
              FROM information_schema.tables
             WHERE table_schema = DATABASE()
        """)
        size = rows[0]["size_bytes"] if rows and rows[0]["size_bytes"] else 0
        meta["db_size"] = f"{int(size) // 1024 // 1024} MB"
        rows = await conn.execute_raw("SELECT CURRENT_USER() AS username")
        if rows:
            meta["username"] = rows[0]["username"]
        meta["is_superuser"] = False
        meta["uptime"] = "unknown"
    elif conn.db_type == DbType.ORACLE:
        rows = await conn.execute_raw("SELECT USER AS username FROM dual")
        if rows:
            meta["username"] = rows[0]["username"]
        meta["db_size"] = "unknown"
        meta["uptime"] = "unknown"
        meta["is_superuser"] = False
    elif conn.db_type == DbType.MONGODB:
        stats_rows = await conn.execute_raw('{"op": "dbStats"}')
        stats = stats_rows[0] if stats_rows else {}
        version_rows = await conn.execute_raw('{"op": "serverInfo"}')
        version = version_rows[0].get("version", "unknown") if version_rows else "unknown"
        meta["db_version"] = f"MongoDB {version}"
        meta["db_size"] = f"{stats.get('dataSize', 0) // 1024} KB"
        meta["username"] = conn.profile.username or ""
        meta["uptime"] = "see server logs"
        meta["is_superuser"] = False

    return meta


def _format_column_type(col: dict[str, Any]) -> str:
    """Format a column type from introspection row — cross-dialect."""
    dtype = str(col["data_type"]).upper()

    if dtype in ("CHARACTER VARYING", "VARCHAR", "VARCHAR2", "NVARCHAR2", "NVARCHAR"):
        max_len = col.get("character_maximum_length")
        return f"VARCHAR({max_len})" if max_len else "VARCHAR"
    if dtype in ("CHARACTER", "CHAR", "NCHAR"):
        max_len = col.get("character_maximum_length")
        return f"CHAR({max_len})" if max_len else "CHAR"
    if dtype in ("NUMERIC", "DECIMAL", "NUMBER"):
        prec = col.get("numeric_precision")
        scale = col.get("numeric_scale")
        if prec and scale:
            return f"NUMERIC({prec},{scale})"
        if prec:
            return f"NUMERIC({prec})"
        return "NUMERIC"
    if dtype == "TIMESTAMP WITH TIME ZONE":
        return "TIMESTAMPTZ"
    if dtype in ("TIMESTAMP WITHOUT TIME ZONE", "TIMESTAMP", "DATETIME"):
        return "TIMESTAMP"
    if dtype in ("INTEGER", "INT"):
        return "INT"
    if dtype == "BIGINT":
        default = col.get("column_default", "") or ""
        if "nextval" in str(default):
            return "BIGSERIAL"
        return "BIGINT"
    if dtype == "SMALLINT":
        return "SMALLINT"
    if dtype in ("BOOLEAN", "BOOL"):
        return "BOOLEAN"
    if dtype in ("TEXT", "LONGTEXT", "MEDIUMTEXT", "TINYTEXT", "CLOB"):
        return "TEXT"
    if dtype in ("TINYINT", "MEDIUMINT"):
        return "INT"
    if dtype == "FLOAT":
        return "FLOAT"
    if dtype == "DOUBLE":
        return "DOUBLE"
    if dtype == "DATE":
        return "DATE"
    return dtype


def format_t0_for_context(index: SchemaIndex) -> str:
    """Format T0 index as text for LLM system prompt."""
    lines = [f"Database: {index.db_version}"]
    lines.append("Schemas:")
    for schema, count in sorted(index.schemas.items()):
        lines.append(f"  - {schema}: {count} tables")
    lines.append(f"  Views: {index.view_count}")
    lines.append(f"Total: {index.total_tables} tables, {index.view_count} views")
    return "\n".join(lines)


def format_t1_for_context(catalog: SchemaCatalog) -> str:
    """Format T1 catalog as text for LLM system prompt."""
    lines = []
    for schema, tables in sorted(catalog.tables.items()):
        lines.append(f"Schema: {schema} ({len(tables)} tables)")
        lines.append(f"  {', '.join(tables)}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FK graph — whole-DB foreign key adjacency
# ---------------------------------------------------------------------------

def hash_table_names(catalog: SchemaCatalog) -> str:
    """SHA-1 of sorted 'schema.table' names — used as drift detector for the FK graph cache."""
    import hashlib
    names = sorted(
        f"{schema}.{table}"
        for schema, tables in catalog.tables.items()
        for table in tables
    )
    return hashlib.sha1("|".join(names).encode()).hexdigest()


async def discover_fk_graph(conn: ConnectionManager) -> SchemaGraph:
    """Whole-DB FK adjacency graph — one query per database, all dialects."""
    if conn.db_type == DbType.POSTGRESQL:
        return await _fk_graph_postgres(conn)
    if conn.db_type == DbType.MYSQL:
        return await _fk_graph_mysql(conn)
    if conn.db_type == DbType.ORACLE:
        return await _fk_graph_oracle(conn)
    if conn.db_type == DbType.MONGODB:
        # MongoDB has no declared FK constraints
        return SchemaGraph(edges=[], table_names_hash="")
    raise ValueError(f"Unsupported db_type: {conn.db_type}")


async def _fk_graph_postgres(conn: ConnectionManager) -> SchemaGraph:
    rows = await conn.execute_raw("""
        SELECT
            kcu.table_schema || '.' || kcu.table_name  AS from_table,
            kcu.column_name                             AS from_col,
            ccu.table_schema || '.' || ccu.table_name  AS to_table,
            ccu.column_name                             AS to_col
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema    = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name
         AND tc.table_schema    = ccu.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
        ORDER BY from_table, from_col
    """)
    edges = [
        (r["from_table"], r["from_col"], r["to_table"], r["to_col"])
        for r in rows
    ]
    return SchemaGraph(edges=edges)


async def _fk_graph_mysql(conn: ConnectionManager) -> SchemaGraph:
    rows = await conn.execute_raw("""
        SELECT
            CONCAT(table_schema, '.', table_name)             AS from_table,
            column_name                                        AS from_col,
            CONCAT(referenced_table_schema, '.',
                   referenced_table_name)                      AS to_table,
            referenced_column_name                             AS to_col
        FROM information_schema.key_column_usage
        WHERE referenced_table_name IS NOT NULL
          AND table_schema NOT IN
              ('information_schema', 'mysql', 'performance_schema', 'sys')
        ORDER BY from_table, from_col
    """)
    edges = [
        (r["from_table"], r["from_col"], r["to_table"], r["to_col"])
        for r in rows
    ]
    return SchemaGraph(edges=edges)


async def _fk_graph_oracle(conn: ConnectionManager) -> SchemaGraph:
    excl = _oracle_excl_list()
    # excl is built from a hardcoded ORACLE_SYSTEM_SCHEMAS tuple
    rows = await conn.execute_raw(f"""
        SELECT
            ac.owner  || '.' || ac.table_name   AS from_table,
            acc.column_name                       AS from_col,
            rc.owner  || '.' || rc.table_name    AS to_table,
            rcc.column_name                       AS to_col
          FROM all_constraints ac
          JOIN all_cons_columns acc
            ON ac.owner = acc.owner
           AND ac.constraint_name = acc.constraint_name
          JOIN all_constraints rc
            ON ac.r_owner = rc.owner
           AND ac.r_constraint_name = rc.constraint_name
          JOIN all_cons_columns rcc
            ON rc.owner = rcc.owner
           AND rc.constraint_name = rcc.constraint_name
           AND acc.position = rcc.position
         WHERE ac.constraint_type = 'R'
           AND ac.owner NOT IN ({excl})
         ORDER BY from_table, from_col
    """)  # nosec B608
    edges = [
        (r["from_table"], r["from_col"], r["to_table"], r["to_col"])
        for r in rows
    ]
    return SchemaGraph(edges=edges)


def format_schema_graph_for_context(graph: SchemaGraph) -> str:
    """Format FK graph as compact text for LLM system prompt.

    Output: one line per FK, ~35 chars each.  200 FKs ≈ 7 KB — cheap.
    """
    if not graph.edges:
        return ""
    lines = [f"### Foreign Key Graph ({len(graph.edges)} relationships)"]
    for from_table, from_col, to_table, to_col in sorted(graph.edges):
        lines.append(f"{from_table}.{from_col} -> {to_table}.{to_col}")
    return "\n".join(lines)
