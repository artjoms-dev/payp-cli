"""Multi-dialect smoke test: Postgres, MySQL, Oracle.

Run directly:

    python tests/test_multi_dialect.py

Containers should be running (`docker compose up -d`).
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from dataclasses import dataclass

from payp.db.connection import ConnectionManager
from payp.db.introspection import (
    discover_t0,
    discover_t1,
    discover_t2,
    format_t0_for_context,
    format_t1_for_context,
)
from payp.models import ConnectionCredential, ConnectionProfile, DbType


@dataclass
class TargetDB:
    label: str
    profile: ConnectionProfile
    credential: ConnectionCredential
    t2_schema: str
    t2_table: str


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

        result = await conn.execute("SELECT * FROM customers", limit=3)
        print("\n  SELECT * FROM customers LIMIT 3:")
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
