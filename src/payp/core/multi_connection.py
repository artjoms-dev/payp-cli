"""Multi-connection manager for cross-DB operations.

Wraps multiple ConnectionManager instances, tracks which is active,
and parses @name prefixes to route queries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from payp.db.connection import ConnectionError, ConnectionManager
from payp.models import ConnectionCredential, ConnectionProfile, SchemaCatalog, SchemaIndex


# Matches @connection-name at start of text
PREFIX_PATTERN = re.compile(r"^@(\S+)\s+", re.MULTILINE)


@dataclass
class ConnectionState:
    """State for a single connection within the multi-connection manager."""

    name: str
    manager: ConnectionManager
    t0: SchemaIndex | None = None
    t1: SchemaCatalog | None = None


class MultiConnectionManager:
    """Manages multiple database connections with an active default."""

    def __init__(self) -> None:
        self._connections: dict[str, ConnectionState] = {}
        self._active: str | None = None

    @property
    def active(self) -> str | None:
        return self._active

    @property
    def active_manager(self) -> ConnectionManager | None:
        if not self._active:
            return None
        state = self._connections.get(self._active)
        return state.manager if state else None

    @property
    def active_state(self) -> ConnectionState | None:
        if not self._active:
            return None
        return self._connections.get(self._active)

    def names(self) -> list[str]:
        return list(self._connections.keys())

    def has(self, name: str) -> bool:
        return name in self._connections

    def get(self, name: str) -> ConnectionState | None:
        return self._connections.get(name)

    async def connect(
        self,
        name: str,
        profile: ConnectionProfile,
        credential: ConnectionCredential,
        set_active: bool = True,
    ) -> ConnectionManager:
        """Open a new connection, add to pool, optionally set active."""
        if name in self._connections:
            # Already connected — just set active
            if set_active:
                self._active = name
            return self._connections[name].manager

        mgr = ConnectionManager(profile, credential)
        await mgr.connect()
        self._connections[name] = ConnectionState(name=name, manager=mgr)
        if set_active or self._active is None:
            self._active = name
        return mgr

    async def disconnect(self, name: str) -> bool:
        """Close one connection. Returns True if existed."""
        state = self._connections.get(name)
        if not state:
            return False
        try:
            await state.manager.disconnect()
        except Exception:
            pass
        del self._connections[name]
        if self._active == name:
            # Fall back to any other connection, or None
            self._active = next(iter(self._connections), None)
        return True

    async def disconnect_all(self) -> int:
        """Close all connections. Returns count closed."""
        count = len(self._connections)
        for name in list(self._connections.keys()):
            await self.disconnect(name)
        return count

    def set_active(self, name: str) -> bool:
        """Switch the active connection. Returns True if successful."""
        if name not in self._connections:
            return False
        self._active = name
        return True

    def set_schemas(self, name: str, t0: SchemaIndex, t1: SchemaCatalog) -> None:
        """Store cached schema for a connection."""
        state = self._connections.get(name)
        if state:
            state.t0 = t0
            state.t1 = t1

    def list_info(self) -> list[dict[str, Any]]:
        """Return info about all active connections for display."""
        result = []
        for name, state in self._connections.items():
            mgr = state.manager
            result.append({
                "name": name,
                "db_type": mgr.profile.db_type.value,
                "version": mgr.db_version,
                "host": mgr.profile.host,
                "database": mgr.profile.database,
                "is_active": name == self._active,
                "is_connected": mgr.is_connected,
                "table_count": state.t0.total_tables if state.t0 else None,
            })
        return result

    def resolve_manager(self, connection_hint: str | None = None) -> ConnectionManager | None:
        """Resolve which ConnectionManager to use for a tool call.

        Args:
            connection_hint: explicit connection name from @prefix or tool arg.
                             None → use active.
        """
        if connection_hint:
            state = self._connections.get(connection_hint)
            if state:
                return state.manager
            return None
        return self.active_manager


def parse_at_prefix(text: str) -> tuple[str | None, str]:
    """Extract @connection-name prefix from user text.

    Returns (connection_name_or_None, remaining_text).
    """
    match = PREFIX_PATTERN.match(text.strip())
    if match:
        conn_name = match.group(1)
        remaining = text[match.end():].strip()
        return conn_name, remaining
    return None, text
