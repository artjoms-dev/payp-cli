"""Oracle async driver adapter for payp.

Uses python-oracledb in thin-mode async (no Oracle client install required).
Translates psycopg-style %s placeholders to Oracle :n bind variables so
the rest of payp can share query strings across dialects.
"""

from __future__ import annotations

import re
import time
from typing import Any

import oracledb

from payp.models import ConnectionCredential, ConnectionProfile, QueryResult


_PLACEHOLDER_RE = re.compile(r"%s")

_ORACLE_CONN_MARKERS = (
    "dpy-4011",  # connection to the database was lost
    "dpy-1001",
    "dpy-1002",
    "ora-03113",  # end-of-file on communication channel
    "ora-03114",  # not connected to ORACLE
    "ora-12571",  # TNS: packet writer failure
    "ora-12537",  # TNS: connection closed
    "ora-12547",  # TNS: lost contact
    "ora-01012",  # not logged on
    "ora-02396",  # exceeded maximum idle time
    "connection closed",
    "not connected",
)


def _translate_params(sql: str, params: tuple | list | None) -> tuple[str, list[Any]]:
    """Convert %s placeholders to Oracle :1, :2, ... binds."""
    if not params:
        return sql, []
    counter = {"i": 0}

    def repl(_m: re.Match) -> str:
        counter["i"] += 1
        return f":{counter['i']}"

    return _PLACEHOLDER_RE.sub(repl, sql), list(params)


class OracleDriver:
    """Async Oracle driver wrapping python-oracledb thin-mode."""

    def __init__(self, profile: ConnectionProfile, credential: ConnectionCredential) -> None:
        self.profile = profile
        self.credential = credential
        self._conn: oracledb.AsyncConnection | None = None
        self._db_version: str | None = None

    @property
    def is_connected(self) -> bool:
        return self._conn is not None

    @property
    def db_version(self) -> str | None:
        return self._db_version

    @staticmethod
    def _is_connection_error(exc: BaseException) -> bool:
        if isinstance(exc, (oracledb.OperationalError, oracledb.DatabaseError, oracledb.InterfaceError)):
            # All Oracle driver errors expose a readable message; string
            # match the ORA-/DPY- codes to confirm connection loss.
            pass
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, EOFError)):
            return True
        msg = str(exc).lower()
        return any(m in msg for m in _ORACLE_CONN_MARKERS)

    def _build_dsn(self) -> str:
        # gvenzl/oracle-free uses FREEPDB1 as the default pluggable-database service name
        service = self.profile.database or "FREEPDB1"
        return f"{self.profile.host}:{self.profile.port}/{service}"

    async def connect(self) -> str:
        try:
            self._conn = await oracledb.connect_async(
                user=self.profile.username,
                password=self.credential.password or "",
                dsn=self._build_dsn(),
                tcp_connect_timeout=self.profile.timeout,
            )
        except Exception as e:
            raise ConnectionError(f"Failed to connect to {self.profile.name}: {e}") from e

        rows = await self.execute_raw(
            "SELECT banner_full FROM v$version WHERE ROWNUM = 1"
        )
        if rows:
            banner = rows[0].get("BANNER_FULL") or rows[0].get("banner_full") or "Oracle"
            # banner looks like: "Oracle Database 23ai Free Release 23.0.0.0.0 - ..."
            parts = str(banner).split()
            self._db_version = " ".join(parts[:3]) if len(parts) >= 3 else str(banner)
        else:
            self._db_version = "Oracle"
        return self._db_version

    async def disconnect(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
        self._conn = None

    async def execute_raw(
        self, sql: str, params: tuple | list | None = None
    ) -> list[dict[str, Any]]:
        if not self.is_connected:
            raise ConnectionError("Not connected")
        assert self._conn is not None

        sql_t, binds = _translate_params(sql, params)
        # Strip trailing semicolon — Oracle doesn't accept it in statement execution
        sql_t = sql_t.rstrip().rstrip(";")

        cur = self._conn.cursor()
        try:
            await cur.execute(sql_t, binds)
            if cur.description:
                col_names = [d[0].lower() for d in cur.description]
                rows = await cur.fetchall()
                return [dict(zip(col_names, r)) for r in rows]
            return []
        finally:
            cur.close()

    async def execute(self, sql: str, limit: int = 20) -> QueryResult:
        if not self.is_connected:
            raise ConnectionError("Not connected")
        assert self._conn is not None

        start = time.monotonic()
        sql_stripped = sql.rstrip().rstrip(";")
        cur = self._conn.cursor()
        try:
            await cur.execute(sql_stripped)

            if not cur.description:
                elapsed = int((time.monotonic() - start) * 1000)
                return QueryResult(
                    columns=[],
                    rows=[],
                    row_count=cur.rowcount if cur.rowcount and cur.rowcount >= 0 else 0,
                    execution_ms=elapsed,
                )

            columns = [d[0].lower() for d in cur.description]
            rows_raw = await cur.fetchmany(limit + 1)
            truncated = len(rows_raw) > limit
            if truncated:
                rows_raw = rows_raw[:limit]

            rows = [list(r) for r in rows_raw]
            elapsed = int((time.monotonic() - start) * 1000)

            return QueryResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                execution_ms=elapsed,
                truncated=truncated,
                total_rows=None,
            )
        finally:
            cur.close()
