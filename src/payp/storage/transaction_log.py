"""Transaction log — SQLite audit trail of all SQL operations.

Stored in ./payp/log/transactions.db (project-level).
Every operation logged: who, what, when, which mode, result, model used.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


LOG_DIR = Path("./payp/log")
DB_FILE = LOG_DIR / "transactions.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    connection_name TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    sql_executed TEXT NOT NULL,
    reverse_sql TEXT,
    execution_mode TEXT NOT NULL,
    approved_by TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    rows_affected INTEGER,
    execution_ms INTEGER,
    model_used TEXT,
    user_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_transactions_session ON transactions(session_id);
CREATE INDEX IF NOT EXISTS idx_transactions_connection ON transactions(connection_name);
CREATE INDEX IF NOT EXISTS idx_transactions_timestamp ON transactions(timestamp);
"""


def ensure_log() -> None:
    """Create the log directory and DB if they don't exist."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


class TransactionLog:
    """Writes SQL operations to the transaction log."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        ensure_log()

    def log(
        self,
        connection_name: str,
        operation_type: str,
        sql_executed: str,
        execution_mode: str,
        status: str,
        approved_by: str | None = None,
        reverse_sql: str | None = None,
        error_message: str | None = None,
        rows_affected: int | None = None,
        execution_ms: int | None = None,
        model_used: str | None = None,
        user_id: str | None = None,
    ) -> int:
        """Log a transaction and return the row id."""
        timestamp = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(DB_FILE)
        try:
            cur = conn.execute("""
                INSERT INTO transactions (
                    session_id, timestamp, connection_name, operation_type,
                    sql_executed, reverse_sql, execution_mode, approved_by,
                    status, error_message, rows_affected, execution_ms,
                    model_used, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.session_id, timestamp, connection_name, operation_type,
                sql_executed, reverse_sql, execution_mode, approved_by,
                status, error_message, rows_affected, execution_ms,
                model_used, user_id,
            ))
            conn.commit()
            return cur.lastrowid or 0
        finally:
            conn.close()


def query_history(
    connection_name: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Query the transaction log for recent operations."""
    ensure_log()
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        if connection_name:
            rows = conn.execute("""
                SELECT * FROM transactions
                WHERE connection_name = ?
                ORDER BY id DESC LIMIT ?
            """, (connection_name, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM transactions
                ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_transactions(connection_name: str | None = None) -> int:
    """Return the total transaction count."""
    ensure_log()
    conn = sqlite3.connect(DB_FILE)
    try:
        if connection_name:
            row = conn.execute(
                "SELECT COUNT(*) FROM transactions WHERE connection_name = ?",
                (connection_name,),
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()
        return row[0] if row else 0
    finally:
        conn.close()
