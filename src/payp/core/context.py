"""Context management — smart T2 injection based on user input.

Scans user messages for table mentions, auto-loads FK-related tables,
caches T2 DDL per-connection on disk, and respects a context budget.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from payp.config import global_dir
from payp.db.connection import ConnectionManager
from payp.db.introspection import discover_t2
from payp.models import SchemaCatalog


# Max total T2 content to inject per query (approx chars, ~30KB)
MAX_T2_BUDGET = 30000


def _t2_cache_dir(connection_name: str) -> Path:
    d = global_dir() / "cache" / f"{connection_name}_t2"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _t2_cache_path(connection_name: str, schema: str, table: str) -> Path:
    safe = f"{schema}.{table}".replace("/", "_")
    return _t2_cache_dir(connection_name) / f"{safe}.sql"


async def discover_t2_cached(conn: ConnectionManager, schema: str, table: str) -> str:
    """Load T2 DDL from cache if present, otherwise introspect and cache."""
    conn_name = conn.profile.name
    cache_path = _t2_cache_path(conn_name, schema, table)

    if cache_path.exists():
        try:
            return cache_path.read_text()
        except Exception:
            pass

    ddl = await discover_t2(conn, schema, table)
    try:
        cache_path.write_text(ddl)
    except Exception:
        pass
    return ddl


def extract_table_candidates(text: str, catalog: SchemaCatalog) -> list[tuple[str, str]]:
    """Extract potential table references from user text (exact + plural/singular match)."""
    if not text or not catalog.tables:
        return []

    text_lower = text.lower()
    words = set(re.findall(r"\b[a-z_][a-z0-9_]*\b", text_lower))

    # Flat index: lowercase name → (schema, original_name)
    table_index: dict[str, tuple[str, str]] = {}
    for schema_name, tables in catalog.tables.items():
        for table in tables:
            table_index[table.lower()] = (schema_name, table)

    matches: list[tuple[str, str]] = []
    seen = set()

    for word in words:
        if word in table_index:
            key = table_index[word]
            if key not in seen:
                matches.append(key)
                seen.add(key)

    for word in words:
        for variant in (word.rstrip("s"), word + "s", word + "es"):
            if variant in table_index:
                key = table_index[variant]
                if key not in seen:
                    matches.append(key)
                    seen.add(key)

    return matches


async def fk_related_tables(
    conn: ConnectionManager, schema: str, table: str
) -> list[tuple[str, str]]:
    """Find tables referenced by FKs from this table (1-hop outgoing).

    Dialect-aware. Returns list of (schema, table) tuples.
    """
    from payp.models import DbType

    db_type = conn.profile.db_type
    try:
        if db_type == DbType.POSTGRESQL:
            rows = await conn.execute_raw("""
                SELECT DISTINCT ccu.table_schema AS ref_schema,
                                ccu.table_name AS ref_table
                FROM information_schema.table_constraints tc
                JOIN information_schema.constraint_column_usage ccu
                  ON tc.constraint_name = ccu.constraint_name
                  AND tc.table_schema = ccu.table_schema
                WHERE tc.table_schema = %s AND tc.table_name = %s
                  AND tc.constraint_type = 'FOREIGN KEY'
            """, (schema, table))
            return [(r["ref_schema"], r["ref_table"]) for r in rows]
        elif db_type == DbType.MYSQL:
            rows = await conn.execute_raw("""
                SELECT DISTINCT referenced_table_schema AS ref_schema,
                                referenced_table_name AS ref_table
                FROM information_schema.key_column_usage
                WHERE table_schema = DATABASE()
                  AND table_name = %s
                  AND referenced_table_name IS NOT NULL
            """, (table,))
            return [(r.get("ref_schema") or schema, r["ref_table"]) for r in rows if r.get("ref_table")]
        elif db_type == DbType.ORACLE:
            rows = await conn.execute_raw("""
                SELECT DISTINCT ac2.owner AS ref_schema,
                                ac2.table_name AS ref_table
                  FROM all_constraints ac
                  JOIN all_constraints ac2
                    ON ac.r_constraint_name = ac2.constraint_name
                   AND ac.r_owner = ac2.owner
                 WHERE ac.owner = UPPER(:1)
                   AND ac.table_name = UPPER(:2)
                   AND ac.constraint_type = 'R'
            """, (schema, table))
            return [(r["ref_schema"], r["ref_table"]) for r in rows]
    except Exception:
        pass
    return []


async def build_t2_context(
    conn: ConnectionManager,
    catalog: SchemaCatalog,
    user_text: str,
    pinned: list[tuple[str, str]] | None = None,
    budget: int = MAX_T2_BUDGET,
) -> str:
    """Build the T2 context string for the system prompt.

    - Detects tables mentioned in user_text
    - Adds FK-related tables (1 hop outgoing) for each detected table
    - Uses on-disk cache to avoid re-querying
    - Respects character budget
    """
    if not conn or not conn.is_connected:
        return ""

    tables_to_load: list[tuple[str, str]] = list(pinned or [])

    detected = extract_table_candidates(user_text, catalog)
    for entry in detected:
        if entry not in tables_to_load:
            tables_to_load.append(entry)

    # FK traversal — for each detected table, add its FK targets
    seen = set(tables_to_load)
    for schema, table in detected:
        try:
            fk_targets = await fk_related_tables(conn, schema, table)
        except Exception:
            continue
        for ref in fk_targets:
            if ref not in seen:
                tables_to_load.append(ref)
                seen.add(ref)

    if not tables_to_load:
        return ""

    parts: list[str] = []
    total_chars = 0
    loaded_count = 0

    for schema, table in tables_to_load:
        try:
            ddl = await discover_t2_cached(conn, schema, table)
        except Exception:
            continue

        if total_chars + len(ddl) > budget:
            remaining = len(tables_to_load) - loaded_count
            parts.append(f"-- ({remaining} more tables not loaded due to context budget)")
            break

        parts.append(ddl)
        total_chars += len(ddl)
        loaded_count += 1

    # Auto-load business knowledge for detected tables via memory backend
    conn_name = conn.profile.name
    table_names = [t for _, t in tables_to_load[:loaded_count]]
    try:
        from payp.memory.manager import get_memory_backend
        backend = get_memory_backend()
        knowledge = await backend.load_for_context(conn_name, table_names)
    except Exception:
        # Fallback: if backend fails, try direct storage access
        from payp.storage.knowledge import load_knowledge_for_tables
        knowledge = load_knowledge_for_tables(conn_name, table_names)
    if knowledge and total_chars + len(knowledge) <= budget:
        parts.append(knowledge)

    return "\n\n".join(parts)


def invalidate_t2_cache(connection_name: str) -> int:
    """Clear cached T2 DDL for a connection. Returns count of files removed."""
    d = _t2_cache_dir(connection_name)
    count = 0
    for f in d.glob("*.sql"):
        try:
            f.unlink()
            count += 1
        except Exception:
            pass
    return count
