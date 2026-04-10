"""Saved SQL queries library.

Queries stored in ./payp/queries/*.sql with comment header metadata:
  -- payp:tags: finance, monthly, table:invoices
  -- payp:desc: Monthly revenue by payment method
  SELECT ...
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

QUERIES_DIR = Path("./payp/queries")

TAGS_PATTERN = re.compile(r"^--\s*payp:tags:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
DESC_PATTERN = re.compile(r"^--\s*payp:desc:\s*(.+)$", re.MULTILINE | re.IGNORECASE)


def get_queries_dir() -> Path:
    return QUERIES_DIR


def ensure_queries_dir() -> Path:
    QUERIES_DIR.mkdir(parents=True, exist_ok=True)
    return QUERIES_DIR


def _sanitize_name(name: str) -> str:
    """Turn a free-form name into a safe filename."""
    safe = re.sub(r"[^\w\-]+", "-", name.strip().lower()).strip("-")
    return safe or "query"


def _parse_query_file(path: Path) -> dict[str, Any]:
    """Parse a saved query file, extract metadata."""
    content = path.read_text()

    tags_match = TAGS_PATTERN.search(content)
    desc_match = DESC_PATTERN.search(content)

    tags: list[str] = []
    if tags_match:
        tags = [t.strip() for t in tags_match.group(1).split(",") if t.strip()]

    desc = desc_match.group(1).strip() if desc_match else ""

    # Body = everything that isn't a payp: comment
    body_lines = []
    for line in content.split("\n"):
        if re.match(r"^\s*--\s*payp:", line, re.IGNORECASE):
            continue
        body_lines.append(line)
    body = "\n".join(body_lines).strip()

    return {
        "file": str(path),
        "filename": path.name,
        "name": path.stem,
        "tags": tags,
        "description": desc,
        "sql": body,
        "full_content": content,
        "size": path.stat().st_size,
    }


def list_queries(filter_tag: str = "") -> list[dict[str, Any]]:
    """List all saved queries. Optionally filter by tag (substring match)."""
    if not QUERIES_DIR.exists():
        return []
    queries = []
    for f in sorted(QUERIES_DIR.glob("*.sql")):
        try:
            q = _parse_query_file(f)
            if filter_tag:
                pattern = filter_tag.lower()
                matches_tag = any(pattern in t.lower() for t in q["tags"])
                matches_name = pattern in q["name"].lower()
                matches_desc = pattern in q["description"].lower()
                if not (matches_tag or matches_name or matches_desc):
                    continue
            queries.append(q)
        except Exception:
            pass
    return queries


def get_query(name: str) -> dict[str, Any] | None:
    """Load a saved query by name."""
    path = QUERIES_DIR / f"{name}.sql"
    if not path.exists():
        return None
    return _parse_query_file(path)


def save_query(
    name: str,
    sql: str,
    description: str = "",
    tags: list[str] | None = None,
) -> Path:
    """Save a query with metadata headers."""
    ensure_queries_dir()
    safe_name = _sanitize_name(name)
    path = QUERIES_DIR / f"{safe_name}.sql"

    lines = []
    if tags:
        lines.append(f"-- payp:tags: {', '.join(tags)}")
    if description:
        lines.append(f"-- payp:desc: {description}")
    if lines:
        lines.append("")  # blank line after headers
    lines.append(sql.strip())
    lines.append("")  # trailing newline

    path.write_text("\n".join(lines))
    return path


def delete_query(name: str) -> bool:
    """Delete a saved query by name."""
    path = QUERIES_DIR / f"{name}.sql"
    if path.exists():
        path.unlink()
        return True
    return False


def count_queries() -> int:
    """Quick count of saved queries."""
    if not QUERIES_DIR.exists():
        return 0
    return len(list(QUERIES_DIR.glob("*.sql")))
