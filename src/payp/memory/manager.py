"""Memory backend manager — factory, singleton, and hot-switching.

Provides a single access point for the active memory backend, with support
for switching backends mid-session and migrating data between them.
"""

from __future__ import annotations

import asyncio
from typing import Any

from payp.memory.interface import MemoryBackend
from payp.models import MemoryConfig

_current_backend: MemoryBackend | None = None

VALID_BACKENDS = ("native", "mempalace")


def get_memory_backend() -> MemoryBackend:
    """Return the current memory backend (lazy init from config)."""
    global _current_backend
    if _current_backend is None:
        from payp.config import load_config

        config = load_config()
        _current_backend = _create_backend(config.memory)
    return _current_backend


def reset_backend() -> None:
    """Clear the cached backend so the next call to get_memory_backend() re-reads config."""
    global _current_backend
    _current_backend = None


def switch_backend(
    new_backend_name: str,
    migrate: bool = False,
    connection: str | None = None,
) -> dict[str, Any]:
    """Hot-switch to a different memory backend.

    Args:
        new_backend_name: "native" or "mempalace".
        migrate: If True, migrate data from the old backend to the new one.
        connection: If given, migration is scoped to that connection (plus
            "shared"). If None, migration copies every entry across every
            connection. Ignored when migrate=False.

    Returns:
        {"switched": True, "from": <old>, "to": <new>, "migration": <stats_or_None>}

    Raises:
        ValueError: If the backend name is invalid.
        RuntimeError: If the target backend cannot be created (e.g. mempalace not installed).
    """
    global _current_backend

    if new_backend_name not in VALID_BACKENDS:
        raise ValueError(f"Unknown backend '{new_backend_name}'. Valid: {', '.join(VALID_BACKENDS)}")

    old_backend = get_memory_backend()
    old_name = old_backend.name

    if old_name == new_backend_name:
        return {"switched": False, "reason": f"Already using '{new_backend_name}'"}

    # Build config for the new backend
    from payp.config import load_config, save_config

    config = load_config()
    new_config = MemoryConfig(backend=new_backend_name, mempalace_dir=config.memory.mempalace_dir)
    new_backend = _create_backend(new_config, raise_on_failure=True)

    migration_stats: dict[str, Any] | None = None
    if migrate:
        # Run migration: new_backend.migrate_from(old_backend, connection=...)
        loop = asyncio.new_event_loop()
        try:
            migration_stats = loop.run_until_complete(
                new_backend.migrate_from(old_backend, connection=connection)
            )
        finally:
            loop.close()

    # Update config and persist
    config.memory.backend = new_backend_name
    save_config(config)

    # Hot-swap the global singleton
    _current_backend = new_backend

    return {
        "switched": True,
        "from": old_name,
        "to": new_backend_name,
        "migration": migration_stats,
    }


def backend_status(connection: str | None = None) -> dict[str, Any]:
    """Return status dict from the current backend, optionally scoped."""
    return get_memory_backend().status(connection=connection)


def _create_backend(config: MemoryConfig, *, raise_on_failure: bool = False) -> MemoryBackend:
    """Instantiate a memory backend from config.

    By default, falls back to native if mempalace is unavailable.
    Set raise_on_failure=True (used by switch_backend) to raise instead.
    """
    import logging

    logger = logging.getLogger(__name__)

    if config.backend == "mempalace":
        try:
            from payp.memory.mempalace_bridge import MemPalaceBackend
        except ImportError:
            if raise_on_failure:
                raise RuntimeError(
                    "mempalace is not installed.\n"
                    "Install it with:  pip install mempalace\n"
                    "Then retry:       /memory switch mempalace"
                )
            logger.warning(
                "mempalace configured but not installed — falling back to native. "
                "Install with: pip install mempalace"
            )
        except Exception as exc:
            if raise_on_failure:
                raise
            logger.exception("mempalace init failed — falling back to native.")
        else:
            return MemPalaceBackend(palace_dir=config.mempalace_dir)

    # Default: native markdown files
    from payp.memory.native import NativeMemoryBackend

    return NativeMemoryBackend()


# ---------------------------------------------------------------------------
# Export / Import — backend-agnostic sharing via plain markdown files
# ---------------------------------------------------------------------------


async def export_knowledge(
    target_dir: str,
    connection: str | None = None,
) -> dict[str, Any]:
    """Dump the active backend's knowledge to markdown files at target_dir.

    Layout: {target_dir}/{connection}/tables/{table}.md

    Works with any backend because it only uses the MemoryBackend protocol
    (list_all + read). For mempalace, drawers are concatenated into a single
    markdown document per table.

    Args:
        target_dir: Where to write files (e.g. "./payp/knowledge").
        connection: Limit export to one connection, or None for all.

    Returns:
        {"exported": <n>, "target": <path>, "files": [<paths>]}
    """
    from pathlib import Path

    backend = get_memory_backend()
    entries = await backend.list_all(connection=connection)

    root = Path(target_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for entry in entries:
        conn = entry.get("connection", "")
        table = entry.get("name", "")
        if not conn or not table or table.startswith("_"):
            continue
        content = await backend.read(conn, table)
        if not content:
            continue

        target_path = root / conn / "tables" / f"{table}.md"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")
        written.append(str(target_path))

    return {
        "exported": len(written),
        "target": str(root),
        "files": written,
        "backend": backend.name,
    }


async def import_knowledge(
    source_dir: str,
    connection: str | None = None,
    replace: bool = True,
) -> dict[str, Any]:
    """Read markdown files from source_dir and save them to the active backend.

    Expected layout: {source_dir}/{connection}/tables/{table}.md

    Works with any backend because it only uses backend.save(). Connection
    name is taken from the directory name under source_dir.

    Args:
        source_dir: Directory containing exported knowledge.
        connection: Limit import to one connection, or None for all.
        replace: If True (default), wipe existing knowledge for each imported
            (conn, table) pair before writing. Makes import idempotent —
            re-importing the same folder won't duplicate content. Set to
            False to merge/append instead.

    Returns:
        {"imported": <n>, "replaced": <n>, "skipped": <n>, "errors": [<str>]}
    """
    from pathlib import Path

    backend = get_memory_backend()
    root = Path(source_dir).expanduser().resolve()

    if not root.exists():
        return {
            "imported": 0,
            "replaced": 0,
            "skipped": 0,
            "errors": [f"source not found: {root}"],
            "backend": backend.name,
        }

    imported = 0
    replaced = 0
    skipped = 0
    errors: list[str] = []

    for conn_dir in sorted(root.iterdir()):
        if not conn_dir.is_dir() or conn_dir.name.startswith("."):
            continue
        conn_name = conn_dir.name
        if connection and conn_name != connection:
            skipped += 1
            continue

        tables_dir = conn_dir / "tables"
        if not tables_dir.is_dir():
            continue

        for md in sorted(tables_dir.glob("*.md")):
            table_name = md.stem
            try:
                content = md.read_text(encoding="utf-8")
                if replace:
                    # Wipe any existing knowledge for this (conn, table) so
                    # the import is idempotent. Safe no-op if nothing exists.
                    try:
                        existed = await backend.delete(conn_name, table_name)
                        if existed:
                            replaced += 1
                    except Exception:
                        pass
                await backend.save(conn_name, table_name, content, section="business_logic")
                imported += 1
            except Exception as exc:
                errors.append(f"{conn_name}/{table_name}: {exc}")

    return {
        "imported": imported,
        "replaced": replaced,
        "skipped": skipped,
        "errors": errors,
        "backend": backend.name,
    }
