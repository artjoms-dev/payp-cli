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


def switch_backend(new_backend_name: str, migrate: bool = False) -> dict[str, Any]:
    """Hot-switch to a different memory backend.

    Args:
        new_backend_name: "native" or "mempalace".
        migrate: If True, migrate all data from the old backend to the new one.

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
        # Run migration: new_backend.migrate_from(old_backend)
        loop = asyncio.new_event_loop()
        try:
            migration_stats = loop.run_until_complete(new_backend.migrate_from(old_backend))
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


def backend_status() -> dict[str, Any]:
    """Return status dict from the current backend."""
    return get_memory_backend().status()


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
            logger.warning("mempalace init failed (%s) — falling back to native.", exc)
        else:
            return MemPalaceBackend(palace_dir=config.mempalace_dir)

    # Default: native markdown files
    from payp.memory.native import NativeMemoryBackend

    return NativeMemoryBackend()
