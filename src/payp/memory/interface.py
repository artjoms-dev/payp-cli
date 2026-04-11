"""MemoryBackend protocol — pluggable knowledge storage for payp.

Any backend (native markdown files, mempalace, vector DB, etc.)
implements this protocol to be swappable at runtime.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MemoryBackend(Protocol):
    """Pluggable memory backend for payp knowledge storage."""

    name: str  # "native" or "mempalace"

    async def read(self, connection: str, table: str) -> str | None:
        """Read knowledge for a specific table. Returns markdown or None."""
        ...

    async def save(
        self,
        connection: str,
        table: str,
        content: str,
        section: str = "business_logic",
    ) -> dict[str, Any]:
        """Save knowledge after user approval.

        Returns {"file": <path_or_id>, "saved": True}.
        """
        ...

    async def search(
        self,
        query: str,
        connection: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Semantic or keyword search across all knowledge.

        Returns [{"table": ..., "connection": ..., "content": ..., "score": ...}].
        """
        ...

    async def list_all(
        self, connection: str | None = None
    ) -> list[dict[str, Any]]:
        """List all knowledge entries.

        Returns [{"connection": ..., "table": ..., "type": ..., "size": ...}].
        """
        ...

    async def load_for_context(
        self, connection: str, table_names: list[str]
    ) -> str:
        """Load knowledge for specific tables, concatenated for LLM context injection."""
        ...

    async def delete(self, connection: str, table: str) -> bool:
        """Delete knowledge for a table. Returns True if deleted."""
        ...

    async def migrate_from(
        self, other: MemoryBackend, connection: str | None = None
    ) -> dict[str, Any]:
        """Import all knowledge from another backend.

        Returns {"migrated": <int>, "errors": [<str>, ...]}.
        """
        ...

    def status(self, connection: str | None = None) -> dict[str, Any]:
        """Return backend status: {name, entries, size, healthy}.

        If connection is given, entries/size are scoped to that connection
        (plus any "shared" connection). Otherwise totals across all entries.
        """
        ...
