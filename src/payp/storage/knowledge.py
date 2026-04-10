"""Knowledge base — per-connection, per-table business context.

Storage is GLOBAL by default (~/.payp/knowledge/) because knowledge is
bound to the *database*, not the project directory. Team sharing happens
via explicit /knowledge export → git-commit workflow, not by writing into
the project folder at runtime.

Structure (global):
  ~/.payp/knowledge/
  ├── {connection-name}/
  │   ├── tables/
  │   │   ├── customers.md
  │   │   └── orders.md
  │   ├── views/
  │   ├── procedures/
  │   └── _overview.md      ← DB-level notes
  └── shared.md             ← cross-DB knowledge

Auto-loaded into LLM context before queries. User confirms before saving.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from payp.config import global_dir


def _knowledge_root() -> Path:
    """Always global: ~/.payp/knowledge/"""
    return global_dir() / "knowledge"


LEGACY_KNOWLEDGE_DIR = Path("./payp/knowledge")
KNOWLEDGE_DIR = _knowledge_root()


def has_legacy_knowledge() -> bool:
    """True if an old project-local ./payp/knowledge/ exists (pre-refactor)."""
    if not LEGACY_KNOWLEDGE_DIR.exists():
        return False
    # Ignore empty/hidden-only dirs
    for p in LEGACY_KNOWLEDGE_DIR.rglob("*.md"):
        if p.is_file():
            return True
    return False

# Object type subdirectories
OBJECT_TYPES = ("tables", "views", "procedures")

OVERVIEW_TEMPLATE = """# {connection} — Database Overview

Notes, conventions, and quirks for this database.

## Conventions
- (document naming conventions, PK strategy, timestamp patterns here)

## Known Issues
- (data quality quirks, known bad data, workarounds)
"""

TABLE_TEMPLATE = """# {schema}.{table}

(Brief description of what this table contains)

## Purpose
(What the table is used for)

## Business Logic
(Important rules, filters, NULL meanings, enum values, valid ranges)

## Columns
| Column | Type | Nullable | Description |
|--------|------|----------|-------------|

## Related Tables
(Foreign key relationships, both outgoing and incoming)

## Usage Examples
```sql
-- Example query
```
"""


def get_knowledge_dir() -> Path:
    return _knowledge_root()


def get_connection_dir(connection_name: str, root: Path | None = None) -> Path:
    """Return the knowledge dir for a specific connection."""
    base = root if root is not None else _knowledge_root()
    return base / _safe_name(connection_name)


def ensure_connection_dir(connection_name: str, root: Path | None = None) -> Path:
    """Create the connection's knowledge directory structure."""
    base = get_connection_dir(connection_name, root=root)
    for subdir in OBJECT_TYPES:
        (base / subdir).mkdir(parents=True, exist_ok=True)
    return base


def _safe_name(name: str) -> str:
    """Sanitize a connection/table name for use as filename."""
    return name.replace("/", "_").replace("\\", "_").replace(" ", "_")


# ─── Per-table knowledge ───


def table_knowledge_path(
    connection_name: str,
    table_name: str,
    obj_type: str = "tables",
    root: Path | None = None,
) -> Path:
    """Return the path for a table/view/procedure knowledge file."""
    safe_table = _safe_name(table_name.lower())
    return get_connection_dir(connection_name, root=root) / obj_type / f"{safe_table}.md"


def read_table_knowledge(connection_name: str, table_name: str, obj_type: str = "tables") -> str | None:
    """Read knowledge for a specific table. Returns None if not found."""
    path = table_knowledge_path(connection_name, table_name, obj_type)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def write_table_knowledge(
    connection_name: str,
    table_name: str,
    content: str,
    obj_type: str = "tables",
    root: Path | None = None,
) -> Path:
    """Write/overwrite knowledge for a specific table."""
    ensure_connection_dir(connection_name, root=root)
    path = table_knowledge_path(connection_name, table_name, obj_type, root=root)
    path.write_text(content, encoding="utf-8")
    return path


def append_table_knowledge(
    connection_name: str,
    table_name: str,
    content: str,
    obj_type: str = "tables",
    root: Path | None = None,
) -> Path:
    """Append new knowledge to an existing table file (or create it)."""
    ensure_connection_dir(connection_name, root=root)
    path = table_knowledge_path(connection_name, table_name, obj_type, root=root)
    if path.exists():
        existing = path.read_text(encoding="utf-8").rstrip()
        path.write_text(existing + "\n\n" + content.strip() + "\n", encoding="utf-8")
    else:
        path.write_text(content.strip() + "\n", encoding="utf-8")
    return path


def list_table_knowledge(
    connection_name: str, root: Path | None = None
) -> list[dict[str, Any]]:
    """List all knowledge files for a connection."""
    base = get_connection_dir(connection_name, root=root)
    if not base.exists():
        return []
    files = []
    for obj_type in OBJECT_TYPES:
        type_dir = base / obj_type
        if not type_dir.is_dir():
            continue
        for f in sorted(type_dir.glob("*.md")):
            files.append({
                "file": str(f),
                "name": f.stem,
                "type": obj_type,
                "size": f.stat().st_size,
                "connection": connection_name,
            })
    # Also include _overview.md
    overview = base / "_overview.md"
    if overview.exists():
        files.insert(0, {
            "file": str(overview),
            "name": "_overview",
            "type": "overview",
            "size": overview.stat().st_size,
            "connection": connection_name,
        })
    return files


# ─── Auto-load for context injection ───


def load_knowledge_for_tables(connection_name: str, table_names: list[str]) -> str:
    """Load knowledge for specific tables, concatenated as context string.

    Used by smart T2 injection — injects business logic alongside DDL.
    """
    parts = []
    for table in table_names:
        content = read_table_knowledge(connection_name, table)
        if content:
            parts.append(f"### Business Context: {table}\n{content}")
    return "\n\n".join(parts)


def load_overview(connection_name: str) -> str | None:
    """Load the DB-level overview for a connection."""
    path = get_connection_dir(connection_name) / "_overview.md"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


# ─── Backward compatibility with old flat format ───


def list_knowledge_files(root: Path | None = None) -> list[dict[str, Any]]:
    """List ALL knowledge files across ALL connections (for /knowledge command)."""
    base = root if root is not None else _knowledge_root()
    if not base.exists():
        return []
    all_files = []
    for conn_dir in sorted(base.iterdir()):
        if conn_dir.is_dir() and not conn_dir.name.startswith("."):
            conn_name = conn_dir.name
            all_files.extend(list_table_knowledge(conn_name, root=base))
    for f in sorted(base.glob("*.md")):
        all_files.append({
            "file": str(f),
            "name": f.stem,
            "type": "shared",
            "size": f.stat().st_size,
            "connection": "shared",
        })
    return all_files


def read_knowledge_file(name: str) -> str | None:
    """Backward compat: read a root-level knowledge file by name."""
    path = KNOWLEDGE_DIR / f"{name}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def write_knowledge_file(name: str, content: str) -> Path:
    """Backward compat: write a root-level knowledge file."""
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    path = KNOWLEDGE_DIR / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


def append_to_knowledge_file(name: str, content: str) -> Path:
    """Backward compat: append to a root-level knowledge file."""
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    path = KNOWLEDGE_DIR / f"{name}.md"
    if path.exists():
        existing = path.read_text(encoding="utf-8").rstrip()
        path.write_text(existing + "\n\n" + content.strip() + "\n", encoding="utf-8")
    else:
        path.write_text(content.strip() + "\n", encoding="utf-8")
    return path


def delete_knowledge_file(name: str) -> bool:
    """Backward compat: delete a root-level knowledge file."""
    path = KNOWLEDGE_DIR / f"{name}.md"
    if path.exists():
        path.unlink()
        return True
    return False


def total_knowledge_size() -> int:
    root = _knowledge_root()
    if not root.exists():
        return 0
    return sum(f.stat().st_size for f in root.rglob("*.md"))


# ─── Legacy migration ───


def migrate_legacy_to_global() -> dict[str, Any]:
    """One-shot move ./payp/knowledge/ → ~/.payp/knowledge/.

    Merges connection-by-connection; existing global files are kept (legacy
    is appended with a separator marker).
    """
    if not has_legacy_knowledge():
        return {"migrated": 0, "skipped": 0, "note": "no legacy dir"}

    src = LEGACY_KNOWLEDGE_DIR
    dst = _knowledge_root()
    dst.mkdir(parents=True, exist_ok=True)

    migrated = 0
    skipped = 0
    for md in src.rglob("*.md"):
        rel = md.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = target.read_text(encoding="utf-8").rstrip()
            legacy_content = md.read_text(encoding="utf-8").strip()
            merged = (
                existing
                + "\n\n<!-- merged from legacy ./payp/knowledge -->\n"
                + legacy_content
                + "\n"
            )
            target.write_text(merged, encoding="utf-8")
            skipped += 1
        else:
            target.write_text(md.read_text(encoding="utf-8"), encoding="utf-8")
            migrated += 1

    return {"migrated": migrated, "skipped": skipped, "src": str(src), "dst": str(dst)}
