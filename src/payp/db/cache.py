"""Schema cache — save/load T0, T1, and metadata as JSON files.

Cache location: ~/.payp/cache/
Files:
  {connection}_t0.json   — T0 schema index
  {connection}_t1.json   — T1 table name catalog
  {connection}_meta.json — DB version, size, user, last check timestamp
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from payp.config import global_dir
from payp.models import SchemaCatalog, SchemaIndex


def cache_dir() -> Path:
    """Return the cache directory, creating if needed."""
    d = global_dir() / "cache"
    d.mkdir(exist_ok=True)
    return d


def save_t0(connection_name: str, index: SchemaIndex) -> None:
    """Save T0 schema index to cache."""
    path = cache_dir() / f"{connection_name}_t0.json"
    path.write_text(index.model_dump_json(indent=2))


def load_t0(connection_name: str) -> SchemaIndex | None:
    """Load cached T0 schema index."""
    path = cache_dir() / f"{connection_name}_t0.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return SchemaIndex(**data)


def save_t1(connection_name: str, catalog: SchemaCatalog) -> None:
    """Save T1 table catalog to cache."""
    path = cache_dir() / f"{connection_name}_t1.json"
    path.write_text(catalog.model_dump_json(indent=2))


def load_t1(connection_name: str) -> SchemaCatalog | None:
    """Load cached T1 table catalog."""
    path = cache_dir() / f"{connection_name}_t1.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return SchemaCatalog(**data)


def save_metadata(connection_name: str, meta: dict[str, Any]) -> None:
    """Save connection metadata to cache."""
    path = cache_dir() / f"{connection_name}_meta.json"
    meta["last_check"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(meta, indent=2, default=str))


def load_metadata(connection_name: str) -> dict[str, Any] | None:
    """Load cached connection metadata."""
    path = cache_dir() / f"{connection_name}_meta.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def get_cached_table_count(connection_name: str) -> int | None:
    """Get total table count from cached T0 for freshness check."""
    t0 = load_t0(connection_name)
    if t0 is None:
        return None
    return t0.total_tables


def has_cache(connection_name: str) -> bool:
    """Check if cache exists for a connection."""
    return (cache_dir() / f"{connection_name}_t0.json").exists()
