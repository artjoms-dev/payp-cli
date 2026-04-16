"""MemPalaceBackend — bridges payp knowledge storage to the mempalace library.

Maps payp concepts to mempalace concepts:
  - payp connection  → mempalace wing
  - payp table       → mempalace room
  - payp section     → mempalace hall
  - payp content     → mempalace drawer (verbatim text in ChromaDB)

Requires: pip install mempalace (optional dependency).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Section → hall mapping
# ---------------------------------------------------------------------------

SECTION_TO_HALL = {
    "business_logic": "hall_facts",
    "columns": "hall_facts",
    "relationships": "hall_facts",
    "usage_examples": "hall_advice",
    "data_quality": "hall_discoveries",
    "edge_cases": "hall_discoveries",
    "history": "hall_events",
}

DEFAULT_HALL = "hall_facts"

# ---------------------------------------------------------------------------
# Lazy import guard
# ---------------------------------------------------------------------------

_HAS_MEMPALACE = None


def _check_mempalace() -> bool:
    global _HAS_MEMPALACE
    if _HAS_MEMPALACE is None:
        try:
            import chromadb  # noqa: F401

            _HAS_MEMPALACE = True
        except ImportError:
            _HAS_MEMPALACE = False
    return _HAS_MEMPALACE


# ---------------------------------------------------------------------------
# Collection name — isolated from mempalace's default collection so payp
# data never collides with personal mempalace data.
# ---------------------------------------------------------------------------

COLLECTION_NAME = "payp_drawers"


class MemPalaceBackend:
    """ChromaDB + SQLite memory backend powered by mempalace concepts."""

    name: str = "mempalace"

    def __init__(self, palace_dir: str | None = None):
        if not _check_mempalace():
            raise RuntimeError(
                "mempalace is not installed. Run: pip install mempalace"
            )

        import chromadb

        self._palace_dir = os.path.expanduser(palace_dir or "~/.payp/mempalace")
        Path(self._palace_dir).mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=self._palace_dir)
        self._col = self._client.get_or_create_collection(COLLECTION_NAME)

        # Optional: knowledge graph for temporal entity tracking
        self._kg = None
        try:
            from mempalace.knowledge_graph import KnowledgeGraph

            kg_path = os.path.join(self._palace_dir, "knowledge_graph.sqlite3")
            self._kg = KnowledgeGraph(db_path=kg_path)
        except Exception:
            pass  # KG is a bonus, not required

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _drawer_id(self, connection: str, table: str, section: str, content: str) -> str:
        """Deterministic drawer ID for upsert idempotency."""
        digest = hashlib.md5(content.encode()).hexdigest()[:12]
        return f"payp_{connection}_{table}_{section}_{digest}"

    def _hall(self, section: str) -> str:
        return SECTION_TO_HALL.get(section, DEFAULT_HALL)

    # ------------------------------------------------------------------
    # MemoryBackend protocol
    # ------------------------------------------------------------------

    async def read(self, connection: str, table: str) -> str | None:
        """Read all drawers for a connection/table pair, concatenated."""

        def _read() -> str | None:
            where = {"$and": [{"wing": connection}, {"room": table}]}
            try:
                results = self._col.get(
                    where=where,
                    include=["documents", "metadatas"],
                )
            except Exception:
                import logging
                logging.getLogger("payp.memory.mempalace").exception("MemPalace read failed")
                return None

            docs = results.get("documents", [])
            if not docs:
                return None

            # Sort by filed_at if available, then concatenate
            pairs = list(zip(docs, results.get("metadatas", [{}] * len(docs))))
            pairs.sort(key=lambda p: p[1].get("filed_at", ""))

            parts: list[str] = []
            for doc, meta in pairs:
                hall = meta.get("hall", "")
                section = meta.get("section", "")
                header = section or hall or ""
                if header:
                    parts.append(f"## {header}\n{doc}")
                else:
                    parts.append(doc)

            return "\n\n".join(parts)

        return await asyncio.to_thread(_read)

    async def save(
        self,
        connection: str,
        table: str,
        content: str,
        section: str = "business_logic",
    ) -> dict[str, Any]:
        """Add a drawer to the palace."""

        def _save() -> dict[str, Any]:
            hall = self._hall(section)
            drawer_id = self._drawer_id(connection, table, section, content)

            try:
                self._col.add(
                    documents=[content],
                    ids=[drawer_id],
                    metadatas=[
                        {
                            "wing": connection,
                            "room": table,
                            "hall": hall,
                            "section": section,
                            "source": "payp",
                            "filed_at": datetime.now().isoformat(),
                        }
                    ],
                )
            except Exception as exc:
                # ChromaDB raises if ID already exists — upsert instead
                if "already exists" in str(exc).lower():
                    self._col.upsert(
                        documents=[content],
                        ids=[drawer_id],
                        metadatas=[
                            {
                                "wing": connection,
                                "room": table,
                                "hall": hall,
                                "section": section,
                                "source": "payp",
                                "filed_at": datetime.now().isoformat(),
                            }
                        ],
                    )
                else:
                    import logging
                    logging.getLogger("payp.memory.mempalace").exception("MemPalace save failed")
                    raise

            # Optionally record in knowledge graph
            if self._kg:
                try:
                    self._kg.add_triple(
                        connection,
                        f"has_knowledge_{section}",
                        table,
                        valid_from=datetime.now().strftime("%Y-%m-%d"),
                        source_closet=drawer_id,
                    )
                except Exception:
                    pass  # KG errors are non-fatal

            return {"id": drawer_id, "saved": True}

        return await asyncio.to_thread(_save)

    async def search(
        self,
        query: str,
        connection: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Semantic search via ChromaDB embeddings."""

        def _search() -> list[dict[str, Any]]:
            where: dict[str, Any] = {}
            if connection:
                where = {"wing": connection}

            kwargs: dict[str, Any] = {
                "query_texts": [query],
                "n_results": limit,
                "include": ["documents", "metadatas", "distances"],
            }
            if where:
                kwargs["where"] = where

            try:
                results = self._col.query(**kwargs)
            except Exception:
                import logging
                logging.getLogger("payp.memory.mempalace").exception("MemPalace search failed")
                return []

            docs = results["documents"][0]
            metas = results["metadatas"][0]
            dists = results["distances"][0]

            hits: list[dict[str, Any]] = []
            for doc, meta, dist in zip(docs, metas, dists):
                hits.append(
                    {
                        "table": meta.get("room", ""),
                        "connection": meta.get("wing", ""),
                        "content": doc,
                        "score": round(1 - dist, 3),
                    }
                )
            return hits

        return await asyncio.to_thread(_search)

    async def load_for_context(
        self, connection: str, table_names: list[str]
    ) -> str:
        """Load knowledge for multiple tables, concatenated for LLM context."""
        parts: list[str] = []
        for table in table_names:
            content = await self.read(connection, table)
            if content:
                parts.append(f"# {table}\n{content}")
        return "\n\n---\n\n".join(parts)

    async def list_all(
        self, connection: str | None = None
    ) -> list[dict[str, Any]]:
        """List all wing/room pairs in the palace."""

        def _list() -> list[dict[str, Any]]:
            kwargs: dict[str, Any] = {"include": ["metadatas"]}
            if connection:
                # Scope to the active wing plus the "shared" pseudo-wing
                # so cross-database notes remain visible.
                if connection == "shared":
                    kwargs["where"] = {"wing": "shared"}
                else:
                    kwargs["where"] = {"wing": {"$in": [connection, "shared"]}}

            try:
                results = self._col.get(**kwargs)
            except Exception:
                import logging
                logging.getLogger("payp.memory.mempalace").exception("MemPalace list_all failed")
                return []

            # Deduplicate by (wing, room)
            seen: dict[tuple[str, str], int] = {}
            for meta in results.get("metadatas", []):
                wing = meta.get("wing", "")
                room = meta.get("room", "")
                key = (wing, room)
                seen[key] = seen.get(key, 0) + 1

            entries: list[dict[str, Any]] = []
            for (wing, room), count in sorted(seen.items()):
                entries.append(
                    {
                        "connection": wing,
                        "name": room,
                        "type": "mempalace_drawer",
                        "drawers": count,
                    }
                )
            return entries

        return await asyncio.to_thread(_list)

    async def delete(self, connection: str, table: str) -> bool:
        """Delete all drawers for a connection/table pair."""

        def _delete() -> bool:
            where = {"$and": [{"wing": connection}, {"room": table}]}
            try:
                results = self._col.get(where=where)
                ids = results.get("ids", [])
                if ids:
                    self._col.delete(ids=ids)
                    return True
            except Exception:
                import logging
                logging.getLogger("payp.memory.mempalace").exception("MemPalace delete failed")
            return False

        return await asyncio.to_thread(_delete)

    async def migrate_from(
        self, other: Any, connection: str | None = None
    ) -> dict[str, Any]:
        """Import all knowledge from another MemoryBackend."""
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
                    await self.save(conn_name, table_name, content, "business_logic")
                    migrated += 1
            except Exception as exc:
                import logging
                logging.getLogger("payp.memory.mempalace").exception("MemPalace migrate_from entry failed")
                errors.append(f"{conn_name}/{table_name}: {exc}")

        return {"migrated": migrated, "errors": errors}

    def status(self, connection: str | None = None) -> dict[str, Any]:
        """Return backend health and statistics, optionally scoped."""
        try:
            if connection:
                where: dict[str, Any]
                if connection == "shared":
                    where = {"wing": "shared"}
                else:
                    where = {"wing": {"$in": [connection, "shared"]}}
                results = self._col.get(where=where, include=["metadatas"])
                drawer_count = len(results.get("metadatas", []) or [])
            else:
                drawer_count = self._col.count()
        except Exception:
            import logging
            logging.getLogger("payp.memory.mempalace").exception("MemPalace status count failed")
            drawer_count = 0

        kg_stats: dict[str, Any] = {}
        if self._kg:
            try:
                kg_stats = self._kg.stats()
            except Exception:
                kg_stats = {"error": "unable to read KG"}

        return {
            "name": self.name,
            "palace_dir": self._palace_dir,
            "entries": drawer_count,
            "collection": COLLECTION_NAME,
            "knowledge_graph": kg_stats or "not available",
            "healthy": drawer_count >= 0,
            "scope": connection or "all",
        }
