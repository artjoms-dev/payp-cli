"""Auto-reconnect tests for ConnectionManager.

Verifies that when a query fails because the underlying socket is dead,
ConnectionManager.execute/execute_raw silently reconnects and retries once.

Strategy (container-free):
  1. Connect to a live PostgreSQL via docker compose.
  2. Forcibly close the underlying socket (simulating `docker restart`).
  3. Issue another query via execute/execute_raw.
  4. Assert it still returns a result (reconnect + retry worked).

Also runs a pure-unit test of the `_is_connection_error` heuristics for
all three dialects using synthetic exceptions so the test passes without
MySQL/Oracle containers.

Run directly:
    python tests/test_auto_reconnect.py

Requires Postgres container:
    docker compose up -d postgres
"""

from __future__ import annotations

import asyncio
import sys
import traceback

from payp.db.connection import ConnectionManager, _PostgresDriver
from payp.models import ConnectionCredential, ConnectionProfile, DbType


PG_PROFILE = ConnectionProfile(
    name="pg-local",
    db_type=DbType.POSTGRESQL,
    host="localhost",
    port=5432,
    database="payp_test",
    username="payp",
)
PG_CRED = ConnectionCredential(password="payp_dev")


# ---------------------------------------------------------------------------
# Unit tests — _is_connection_error heuristics
# ---------------------------------------------------------------------------

def test_pg_connection_error_detection() -> None:
    from payp.db.connection import _is_pg_connection_error

    import psycopg

    assert _is_pg_connection_error(psycopg.OperationalError("boom"))
    assert _is_pg_connection_error(psycopg.InterfaceError("connection closed"))
    assert _is_pg_connection_error(
        Exception("server closed the connection unexpectedly")
    )
    assert _is_pg_connection_error(Exception("terminating connection due to shutdown"))
    assert _is_pg_connection_error(Exception("consuming input failed"))
    # Non-connection errors should NOT match
    assert not _is_pg_connection_error(Exception("duplicate key value violates"))
    assert not _is_pg_connection_error(Exception("syntax error at or near SELECT"))
    assert not _is_pg_connection_error(
        Exception("violates foreign key constraint")
    )
    print("  [OK] PG connection-error heuristics")


def test_mysql_connection_error_detection() -> None:
    from payp.db.mysql import MySQLDriver

    assert MySQLDriver._is_connection_error(
        Exception("Lost connection to MySQL server during query")
    )
    assert MySQLDriver._is_connection_error(
        Exception("MySQL server has gone away")
    )
    assert MySQLDriver._is_connection_error(Exception("Can't connect to MySQL"))
    assert MySQLDriver._is_connection_error(BrokenPipeError("Broken pipe"))
    assert MySQLDriver._is_connection_error(ConnectionResetError(104, "reset"))
    assert not MySQLDriver._is_connection_error(
        Exception("Duplicate entry '42' for key 'PRIMARY'")
    )
    assert not MySQLDriver._is_connection_error(
        Exception("You have an error in your SQL syntax")
    )
    print("  [OK] MySQL connection-error heuristics")


def test_oracle_connection_error_detection() -> None:
    from payp.db.oracle import OracleDriver

    assert OracleDriver._is_connection_error(
        Exception("DPY-4011: the database or network closed the connection")
    )
    assert OracleDriver._is_connection_error(
        Exception("ORA-03113: end-of-file on communication channel")
    )
    assert OracleDriver._is_connection_error(
        Exception("ORA-03114: not connected to ORACLE")
    )
    assert OracleDriver._is_connection_error(
        Exception("ORA-12571: TNS:packet writer failure")
    )
    assert OracleDriver._is_connection_error(BrokenPipeError())
    assert not OracleDriver._is_connection_error(
        Exception("ORA-00001: unique constraint violated")
    )
    assert not OracleDriver._is_connection_error(
        Exception("ORA-00942: table or view does not exist")
    )
    print("  [OK] Oracle connection-error heuristics")


# ---------------------------------------------------------------------------
# Integration test — real Postgres, simulated connection drop
# ---------------------------------------------------------------------------

async def test_postgres_auto_reconnect_execute() -> None:
    mgr = ConnectionManager(PG_PROFILE, PG_CRED)
    await mgr.connect()
    assert mgr.is_connected, "failed initial connect"

    # Baseline query
    result = await mgr.execute("SELECT 1 AS n")
    assert result.rows == [[1]], f"unexpected baseline rows: {result.rows}"

    # Simulate socket loss: close the underlying psycopg connection directly.
    # Next query against the manager should trip the connection-error detector,
    # auto-reconnect, and transparently retry.
    driver = mgr._driver
    assert isinstance(driver, _PostgresDriver)
    await driver._conn.close()  # type: ignore[union-attr]
    assert not mgr.is_connected, "expected is_connected=False after close"

    result2 = await mgr.execute("SELECT 42 AS n")
    assert mgr.is_connected, "manager should have reconnected"
    assert result2.rows == [[42]], f"unexpected retry rows: {result2.rows}"

    # Drop again and verify execute_raw path
    await driver._conn.close()  # type: ignore[union-attr]
    rows = await mgr.execute_raw("SELECT 7 AS n")
    assert rows == [{"n": 7}], f"unexpected execute_raw rows: {rows}"

    await mgr.disconnect()
    print("  [OK] Postgres auto-reconnect on dropped socket")


async def test_postgres_syntax_error_bubbles_up() -> None:
    """Non-connection errors MUST NOT trigger reconnect — they bubble up."""
    mgr = ConnectionManager(PG_PROFILE, PG_CRED)
    await mgr.connect()
    try:
        raised = False
        try:
            await mgr.execute("SELEKT oops FROM nowhere")
        except Exception as e:
            raised = True
            msg = str(e).lower()
            assert "syntax" in msg or "selekt" in msg, (
                f"expected syntax error, got: {e}"
            )
        assert raised, "syntax error should have raised"
        # Manager must still be connected — no spurious reconnect
        assert mgr.is_connected, "syntax error must not trigger reconnect"
        # Follow-up query should still work on same connection
        result = await mgr.execute("SELECT 3 AS n")
        assert result.rows == [[3]]
    finally:
        await mgr.disconnect()
    print("  [OK] Syntax errors bubble up without reconnecting")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def _run_async() -> tuple[int, int]:
    passed = failed = 0
    async_tests = [
        ("postgres auto-reconnect", test_postgres_auto_reconnect_execute),
        ("postgres syntax-error bubble-up", test_postgres_syntax_error_bubbles_up),
    ]
    for label, fn in async_tests:
        try:
            await fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {label}: {e}")
            traceback.print_exc()
    return passed, failed


def main() -> int:
    print("=== Auto-reconnect tests ===\n")
    print("Unit: connection-error detection")
    passed = failed = 0
    for fn in (
        test_pg_connection_error_detection,
        test_mysql_connection_error_detection,
        test_oracle_connection_error_detection,
    ):
        try:
            fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {fn.__name__}: {e}")
            traceback.print_exc()

    print("\nIntegration: real Postgres")
    try:
        ap, af = asyncio.run(_run_async())
        passed += ap
        failed += af
    except Exception as e:
        failed += 1
        print(f"  [FAIL] async runner: {e}")
        traceback.print_exc()

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
