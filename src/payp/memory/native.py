"""NativeMemoryBackend — wraps storage/knowledge.py markdown files.

This is the default backend. Knowledge is stored as per-table .md files
under ~/.payp/knowledge/{connection}/tables/{table}.md (GLOBAL).
"""

from __future__ import annotations

from typing import Any

from payp.storage.knowledge import (
    append_table_knowledge,
    list_knowledge_files,
    list_table_knowledge,
    load_knowledge_for_tables,
    read_table_knowledge,
    table_knowledge_path,
    write_table_knowledge,
)


class NativeMemoryBackend:
    """Markdown-file-based memory backend (the payp default)."""

    name: str = "native"

    async def read(self, connection: str, table: str) -> str | None:
        """Read knowledge for a specific table."""
        return read_table_knowledge(connection, table)

    async def save(
        self,
        connection: str,
        table: str,
        content: str,
        section: str = "business_logic",
    ) -> dict[str, Any]:
        """Save knowledge — appends if file exists, writes if new."""
        existing = read_table_knowledge(connection, table)
        if existing:
            path = append_table_knowledge(connection, table, content)
        else:
            path = write_table_knowledge(connection, table, content)
        return {"file": str(path), "saved": True}

    async def search(
        self,
        query: str,
        connection: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Case-insensitive substring search across all knowledge .md files."""
        if not query:
            return []

        query_lower = query.lower()
        results: list[dict[str, Any]] = []

        all_files = (
            list_table_knowledge(connection)
            if connection
            else list_knowledge_files()
        )

        for entry in all_files:
            try:
                from pathlib import Path

                path = Path(entry["file"])
                if not path.exists():
                    continue
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue

            text_lower = text.lower()
            if query_lower not in text_lower:
                continue

            # Find the first matching line for context
            matching_line = ""
            for line in text.splitlines():
                if query_lower in line.lower():
                    matching_line = line.strip()
                    break

            results.append({
                "table": entry.get("name", ""),
                "connection": entry.get("connection", ""),
                "content": matching_line,
                "score": 1.0,  # Binary match for native backend
            })

            if len(results) >= limit:
                break

        return results

    async def list_all(
        self, connection: str | None = None
    ) -> list[dict[str, Any]]:
        """List all knowledge entries."""
        if connection:
            return list_table_knowledge(connection)
        return list_knowledge_files()

    async def load_for_context(
        self, connection: str, table_names: list[str]
    ) -> str:
        """Load knowledge for specific tables, concatenated for LLM context."""
        return load_knowledge_for_tables(connection, table_names)

    async def delete(self, connection: str, table: str) -> bool:
        """Delete knowledge file for a table."""
        path = table_knowledge_path(connection, table)
        if path.exists():
            path.unlink()
            return True
        return False

    async def migrate_from(
        self, other: NativeMemoryBackend, connection: str | None = None
    ) -> dict[str, Any]:
        """Import all knowledge from another backend."""
        migrated = 0
        errors: list[str] = []

        entries = await other.list_all(connection)
        for entry in entries:
            conn_name = entry.get("connection", "")
            table_name = entry.get("name", "")
            if not conn_name or not table_name:
                continue
            try:
                content = await other.read(conn_name, table_name)
                if content:
                    write_table_knowledge(conn_name, table_name, content)
                    migrated += 1
            except Exception as exc:
                errors.append(f"{conn_name}/{table_name}: {exc}")

        return {"migrated": migrated, "errors": errors}

    def status(self) -> dict[str, Any]:
        """Return backend status."""
        all_files = list_knowledge_files()
        total_size = sum(f.get("size", 0) for f in all_files)
        return {
            "name": self.name,
            "entries": len(all_files),
            "size": total_size,
            "healthy": True,
        }
