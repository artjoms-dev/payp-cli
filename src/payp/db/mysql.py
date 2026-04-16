"""MySQL async driver adapter for payp.

Uses aiomysql. Presents a uniform cursor/execute surface matching the
psycopg3-flavoured API used by the rest of payp.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import aiomysql

from payp.models import ConnectionCredential, ConnectionProfile, QueryResult

_MYSQL_CONN_MARKERS = (
    "lost connection",
    "mysql server has gone away",
    "can't connect",
    "broken pipe",
    "connection was killed",
    "connection closed",
    "not connected",
    "server has closed the connection",
)


class MySQLDriver:
    """Async MySQL driver wrapping aiomysql."""

    def __init__(self, profile: ConnectionProfile, credential: ConnectionCredential) -> None:
        self.profile = profile
        self.credential = credential
        self._conn: aiomysql.Connection | None = None
        self._db_version: str | None = None

    @property
    def is_connected(self) -> bool:
        return self._conn is not None and not self._conn.closed

    @property
    def db_version(self) -> str | None:
        return self._db_version

    @staticmethod
    def _is_connection_error(exc: BaseException) -> bool:
        # aiomysql raises pymysql errors; OperationalError + InterfaceError
        # are the canonical connection-lost flags. Fall back to string match
        # so we also catch wrapped/unwrapped variants and "Broken pipe" OSError.
        try:
            import pymysql  # aiomysql ships pymysql as a dep
            if isinstance(exc, (pymysql.err.OperationalError, pymysql.err.InterfaceError)):
                return True
        except Exception:
            pass
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, EOFError)):
            return True
        msg = str(exc).lower()
        return any(m in msg for m in _MYSQL_CONN_MARKERS)

    async def connect(self) -> str:
        try:
            self._conn = await aiomysql.connect(
                host=self.profile.host,
                port=self.profile.port,
                user=self.profile.username,
                password=self.credential.password or "",
                db=self.profile.database,
                connect_timeout=self.profile.timeout,
                autocommit=True,
                charset="utf8mb4",
            )
        except Exception as e:
            import logging
            logging.getLogger("payp.db.mysql").exception("MySQL connect failed")
            raise ConnectionError(f"Failed to connect to {self.profile.name}: {e}") from e

        rows = await self.execute_raw("SELECT VERSION() AS version")
        version_full = rows[0]["version"] if rows else "unknown"
        self._db_version = f"MySQL {version_full}"
        return self._db_version

    async def disconnect(self) -> None:
        if self._conn is not None and not self._conn.closed:
            try:
                self._conn.close()
            except RuntimeError:
                # Event loop already closed — connection socket will be GC'd
                pass
        self._conn = None

    async def execute_raw(
        self, sql: str, params: tuple | None = None
    ) -> list[dict[str, Any]]:
        if not self.is_connected:
            raise ConnectionError("Not connected")
        assert self._conn is not None
        # aiomysql uses %s paramstyle — same as psycopg, so the same query works
        async with self._conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, params)
            if cur.description:
                rows = await cur.fetchall()
                # Normalise column keys to lowercase for dialect-agnostic
                # downstream code (MySQL preserves case from SELECT clauses,
                # but system catalog queries return uppercase).
                return [{k.lower(): v for k, v in r.items()} for r in rows]
            return []

    async def stream_raw(
        self,
        sql: str,
        params: tuple | None = None,
        batch_size: int = 10_000,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Stream rows via aiomysql server-side cursor (SSDictCursor).

        NOTE: SSDictCursor is bound to the underlying socket — a mid-stream
        connection drop kills the cursor and the caller sees the error. This
        path deliberately bypasses the auto-reconnect wrapper in
        ConnectionManager because a retry would re-run the query from scratch
        and could produce inconsistent results.
        """
        if not self.is_connected:
            raise ConnectionError("Not connected")
        assert self._conn is not None
        async with self._conn.cursor(aiomysql.SSDictCursor) as cur:
            await cur.execute(sql, params)
            if not cur.description:
                return
            while True:
                rows = await cur.fetchmany(batch_size)
                if not rows:
                    break
                yield [{k.lower(): v for k, v in r.items()} for r in rows]

    async def execute(self, sql: str, limit: int = 20) -> QueryResult:
        if not self.is_connected:
            raise ConnectionError("Not connected")
        assert self._conn is not None

        start = time.monotonic()
        async with self._conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql)

            if not cur.description:
                elapsed = int((time.monotonic() - start) * 1000)
                return QueryResult(
                    columns=[],
                    rows=[],
                    row_count=cur.rowcount if cur.rowcount >= 0 else 0,
                    execution_ms=elapsed,
                )

            columns = [desc[0] for desc in cur.description]
            rows_raw = await cur.fetchmany(limit + 1)
            truncated = len(rows_raw) > limit
            if truncated:
                rows_raw = rows_raw[:limit]

            rows = [[row[col] for col in columns] for row in rows_raw]
            elapsed = int((time.monotonic() - start) * 1000)

            return QueryResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                execution_ms=elapsed,
                truncated=truncated,
                total_rows=None,
            )
