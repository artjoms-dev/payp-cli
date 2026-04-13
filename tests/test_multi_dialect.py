"""Multi-dialect smoke test: Postgres, MySQL, Oracle, MongoDB.

Run directly:

    python tests/test_multi_dialect.py

Containers should be running (`docker compose up -d`).
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from dataclasses import dataclass

from payp.db.connection import ConnectionManager
from payp.db.introspection import (
    discover_fk_graph,
    discover_t0,
    discover_t1,
    discover_t2,
    format_schema_graph_for_context,
    format_t0_for_context,
    format_t1_for_context,
    hash_table_names,
)
from payp.models import ConnectionCredential, ConnectionProfile, DbType


@dataclass
class TargetDB:
    label: str
    profile: ConnectionProfile
    credential: ConnectionCredential
    t2_schema: str
    t2_table: str
    test_sql: str = "SELECT * FROM customers"


TARGETS: list[TargetDB] = [
    TargetDB(
        label="PostgreSQL",
        profile=ConnectionProfile(
            name="pg-local",
            db_type=DbType.POSTGRESQL,
            host="localhost",
            port=5432,
            database="payp_test",
            username="payp",
        ),
        credential=ConnectionCredential(password="payp_dev"),
        t2_schema="public",
        t2_table="customers",
    ),
    TargetDB(
        label="MySQL",
        profile=ConnectionProfile(
            name="mysql-local",
            db_type=DbType.MYSQL,
            host="localhost",
            port=3306,
            database="payp_test",
            username="payp",
        ),
        credential=ConnectionCredential(password="payp_dev"),
        t2_schema="payp_test",
        t2_table="customers",
    ),
    TargetDB(
        label="Oracle",
        profile=ConnectionProfile(
            name="oracle-local",
            db_type=DbType.ORACLE,
            host="localhost",
            port=1521,
            database="payp_test",
            username="payp",
        ),
        credential=ConnectionCredential(password="payp_dev"),
        t2_schema="PAYP",
        t2_table="CUSTOMERS",
    ),
    TargetDB(
        label="MongoDB",
        profile=ConnectionProfile(
            name="mongo-local",
            db_type=DbType.MONGODB,
            host="localhost",
            port=27017,
            database="payp_test",
            username="payp",
        ),
        credential=ConnectionCredential(password="payp_dev"),
        t2_schema="payp_test",
        t2_table="customers",
        test_sql=json.dumps({"op": "find", "collection": "customers", "filter": {}, "limit": 3}),
    ),
]


async def run_one(target: TargetDB) -> tuple[str, bool, str]:
    print(f"\n{'=' * 60}")
    print(f"  {target.label}")
    print("=" * 60)
    conn = ConnectionManager(target.profile, target.credential)
    try:
        version = await conn.connect()
        print(f"  Connected: {version}")

        t0 = await discover_t0(conn)
        print("\n  T0 SchemaIndex:")
        print("  " + format_t0_for_context(t0).replace("\n", "\n  "))

        t1 = await discover_t1(conn)
        print("\n  T1 SchemaCatalog:")
        t1_text = format_t1_for_context(t1)
        print("  " + t1_text.replace("\n", "\n  "))

        ddl = await discover_t2(conn, target.t2_schema, target.t2_table)
        print(f"\n  T2 DDL for {target.t2_schema}.{target.t2_table}:")
        print("  " + ddl.replace("\n", "\n  "))

        fk_graph = await discover_fk_graph(conn)
        fk_graph.table_names_hash = hash_table_names(t1)
        print(f"\n  FK graph: {len(fk_graph.edges)} relationships")
        if fk_graph.edges:
            graph_text = format_schema_graph_for_context(fk_graph)
            print("  " + graph_text.replace("\n", "\n  "))

        result = await conn.execute(target.test_sql, limit=3)
        print(f"\n  Query: {target.test_sql[:60]}...")
        print(f"  cols: {result.columns}")
        for row in result.rows:
            print(f"  {row}")
        print(f"  ({result.row_count} rows, {result.execution_ms} ms)")

        return target.label, True, ""
    except Exception as e:
        tb = traceback.format_exc()
        print(f"  FAIL: {e}")
        print(tb)
        return target.label, False, str(e)
    finally:
        try:
            await conn.disconnect()
        except Exception:
            pass


async def main() -> int:
    results: list[tuple[str, bool, str]] = []
    for target in TARGETS:
        results.append(await run_one(target))

    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    for label, ok, err in results:
        mark = "PASS" if ok else "FAIL"
        extra = "" if ok else f"  ({err})"
        print(f"  [{mark}] {label}{extra}")

    failed = sum(1 for _, ok, _ in results if not ok)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
