"""Snapshot management — list, inspect, and clean up snapshot files."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

SNAPSHOTS_DIR = Path("./payp/snapshots")


def get_snapshots_dir() -> Path:
    """Return the snapshots directory."""
    return SNAPSHOTS_DIR


def list_snapshots() -> list[dict[str, Any]]:
    """List all snapshot files with metadata, sorted newest first."""
    d = get_snapshots_dir()
    if not d.exists():
        return []

    snapshots = []
    for f in sorted(d.glob("*.jsonl"), reverse=True):
        try:
            # Read just the first line (metadata)
            with open(f) as fp:
                first_line = fp.readline().strip()
            meta = json.loads(first_line).get("_payp_meta", {})

            file_size = os.path.getsize(f)
            size_str = f"{file_size / 1024:.1f} KB" if file_size < 1024 * 1024 else f"{file_size / 1024 / 1024:.1f} MB"

            snapshots.append({
                "file": str(f),
                "filename": f.name,
                "table": meta.get("table", "unknown"),
                "operation": meta.get("operation", "unknown"),
                "row_count": meta.get("row_count", 0),
                "timestamp": meta.get("timestamp", ""),
                "connection": meta.get("connection", ""),
                "where": meta.get("where", ""),
                "size": size_str,
            })
        except Exception:
            # Corrupted snapshot — include with minimal info
            snapshots.append({
                "file": str(f),
                "filename": f.name,
                "table": "?",
                "operation": "?",
                "row_count": 0,
                "timestamp": "",
                "connection": "",
                "where": "",
                "size": "?",
            })

    return snapshots


def delete_snapshot(filepath: str) -> bool:
    """Delete a snapshot file. Returns True if deleted."""
    p = Path(filepath)
    if p.exists() and p.suffix == ".jsonl":
        p.unlink()
        return True
    return False


def delete_all_snapshots() -> int:
    """Delete all snapshots. Returns count deleted."""
    d = get_snapshots_dir()
    if not d.exists():
        return 0
    count = 0
    for f in d.glob("*.jsonl"):
        f.unlink()
        count += 1
    return count


def snapshot_count() -> int:
    """Quick count of snapshot files."""
    d = get_snapshots_dir()
    if not d.exists():
        return 0
    return len(list(d.glob("*.jsonl")))


def format_snapshot_age(timestamp_str: str) -> str:
    """Format a timestamp as relative age (e.g., '2h ago', '3d ago')."""
    if not timestamp_str:
        return "unknown"
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = datetime.now(ts.tzinfo)
        delta = now - ts
        seconds = int(delta.total_seconds())

        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            return f"{seconds // 60}m ago"
        elif seconds < 86400:
            return f"{seconds // 3600}h ago"
        else:
            return f"{seconds // 86400}d ago"
    except Exception:
        return "unknown"
