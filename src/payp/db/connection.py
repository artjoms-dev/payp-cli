"""Database connection manager for payp.

Dispatches async connections to a dialect-specific driver based on
ConnectionProfile.db_type. Supported: PostgreSQL (psycopg3), MySQL
(aiomysql), Oracle (python-oracledb thin-mode async).

The public surface (connect, disconnect, execute, execute_raw, reconnect,
is_connected, db_version) is identical across drivers.
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

import psycopg
from psycopg.rows import dict_row

from payp.models import ConnectionCredential, ConnectionProfile, DbType, QueryResult


class ConnectionError(Exception):
    """Raised when a database connection fails."""


# Connection-lost error substrings per dialect. Matched case-insensitively
# against str(exception) to decide whether to auto-reconnect.
_PG_CONN_MARKERS = (
    "not connected",
    "connection closed",
    "terminating connection",
    "server closed the connection unexpectedly",
    "consuming input failed",
    "connection is closed",
    "connection is bad",
    "no connection to the server",
    "ssl connection has been closed unexpectedly",
    "connection timed out",
    "could not receive data from server",
    "could not send data to server",
    "broken pipe",
)

def _is_pg_connection_error(exc: BaseException) -> bool:
    if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
        return True
    msg = str(exc).lower()
    return any(m in msg for m in _PG_CONN_MARKERS)


class _PostgresDriver:
    """Async PostgreSQL driver wrapping psycopg3 (unchanged behaviour)."""

    def __init__(self, profile: ConnectionProfile, credential: ConnectionCredential) -> None:
        self.profile = profile
        self.credential = credential
        self._conn: psycopg.AsyncConnection | None = None
        self._db_version: str | None = None

    @property
    def is_connected(self) -> bool:
        return self._conn is not None and not self._conn.closed

    @property
    def db_version(self) -> str | None:
        return self._db_version

    @staticmethod
    def _is_connection_error(exc: BaseException) -> bool:
        return _is_pg_connection_error(exc)

    def _build_conninfo(self) -> str:
        p = self.profile
        parts = [
            f"host={p.host}",
            f"port={p.port}",
            f"dbname={p.database}",
            f"user={p.username}",
            f"connect_timeout={p.timeout}",
        ]
        if self.credential.password:
            parts.append(f"password={self.credential.password}")
        if p.ssl:
            parts.append("sslmode=require")
        return " ".join(parts)

    async def connect(self) -> str:
        conninfo = self._build_conninfo()
        try:
            self._conn = await psycopg.AsyncConnection.connect(
                conninfo,
                autocommit=True,
                row_factory=dict_row,
            )
        except Exception as e:
            raise ConnectionError(f"Failed to connect to {self.profile.name}: {e}") from e

        result = await self.execute_raw("SELECT version()")
        version_full = result[0]["version"] if result else "unknown"
        self._db_version = " ".join(version_full.split()[:2])
        return self._db_version

    async def disconnect(self) -> None:
        if self._conn and not self._conn.closed:
            await self._conn.close()
        self._conn = None

    async def execute_raw(
        self, sql: str, params: tuple | None = None
    ) -> list[dict[str, Any]]:
        if not self.is_connected:
            raise ConnectionError("Not connected")
        async with self._conn.cursor(row_factory=dict_row) as cur:  # type: ignore[union-attr]
            await cur.execute(sql, params)
            if cur.description:
                return [dict(row) for row in await cur.fetchall()]
            return []

    async def execute(self, sql: str, limit: int = 20) -> QueryResult:
        if not self.is_connected:
            raise ConnectionError("Not connected")

        start = time.monotonic()
        async with self._conn.cursor(row_factory=dict_row) as cur:  # type: ignore[union-attr]
            await cur.execute(sql)

            if not cur.description:
                elapsed = int((time.monotonic() - start) * 1000)
                return QueryResult(
                    columns=[],
                    rows=[],
                    row_count=cur.rowcount if cur.rowcount >= 0 else 0,
                    execution_ms=elapsed,
                )

            columns = [desc.name for desc in cur.description]
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


def _build_driver(
    profile: ConnectionProfile, credential: ConnectionCredential
) -> Any:
    """Factory — selects dialect driver by db_type."""
    if profile.db_type == DbType.POSTGRESQL:
        return _PostgresDriver(profile, credential)
    if profile.db_type == DbType.MYSQL:
        from payp.db.mysql import MySQLDriver
        return MySQLDriver(profile, credential)
    if profile.db_type == DbType.ORACLE:
        from payp.db.oracle import OracleDriver
        return OracleDriver(profile, credential)
    raise ConnectionError(f"Unsupported db_type: {profile.db_type}")


class ConnectionManager:
    """Unified async connection manager across PostgreSQL, MySQL, Oracle."""

    def __init__(self, profile: ConnectionProfile, credential: ConnectionCredential) -> None:
        self.profile = profile
        self.credential = credential
        self._driver = _build_driver(profile, credential)

    @property
    def db_type(self) -> DbType:
        return self.profile.db_type

    @property
    def is_connected(self) -> bool:
        return self._driver.is_connected

    @property
    def db_version(self) -> str | None:
        return self._driver.db_version

    async def connect(self) -> str:
        return await self._driver.connect()

    async def disconnect(self) -> None:
        await self._driver.disconnect()

    async def execute_raw(
        self, sql: str, params: tuple | None = None
    ) -> list[dict[str, Any]]:
        try:
            return await self._driver.execute_raw(sql, params)
        except Exception as e:
            if self._should_auto_reconnect(e):
                if await self._auto_reconnect():
                    return await self._driver.execute_raw(sql, params)
            raise

    async def execute(self, sql: str, limit: int = 20) -> QueryResult:
        try:
            return await self._driver.execute(sql, limit)
        except Exception as e:
            if self._should_auto_reconnect(e):
                if await self._auto_reconnect():
                    return await self._driver.execute(sql, limit)
            raise

    def _should_auto_reconnect(self, exc: BaseException) -> bool:
        detector = getattr(self._driver, "_is_connection_error", None)
        if detector is None:
            return False
        try:
            return bool(detector(exc))
        except Exception:
            return False

    async def _auto_reconnect(self) -> bool:
        reconnected = await self.reconnect(max_attempts=3)
        if reconnected:
            print("  [payp] Connection dropped, reconnected.", file=sys.stderr)
        return reconnected

    async def reconnect(self, max_attempts: int = 3) -> bool:
        for attempt in range(1, max_attempts + 1):
            try:
                await self.disconnect()
                await self.connect()
                return True
            except ConnectionError:
                if attempt < max_attempts:
                    await asyncio.sleep(1)
        return False
