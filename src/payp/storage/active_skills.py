"""Track which skills are active (user-enabled).

Storage: ~/.payp/active_skills.json — a simple list of skill names.
Default behavior: if the file doesn't exist yet, all discovered skills are active
(so first-time users see them working). Once the file exists, only listed names are active.
"""

from __future__ import annotations

import json
from pathlib import Path

from payp.config import global_dir


def _active_skills_path() -> Path:
    return global_dir() / "active_skills.json"


def load_active_skills() -> set[str] | None:
    """Return the set of active skill names.

    Returns None if no config exists yet (signals "all skills active" default).
    """
    path = _active_skills_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return set(data.get("active", []))
    except Exception:
        return None


def save_active_skills(names: set[str]) -> None:
    """Persist the active skill names."""
    path = _active_skills_path()
    path.write_text(json.dumps({"active": sorted(names)}, indent=2))


def toggle_skill(name: str, all_skill_names: set[str]) -> tuple[bool, set[str]]:
    """Toggle a skill's active state. Returns (now_active, full_active_set).

    If this is the first toggle (no config file yet), initialize with all
    skills active, THEN apply the toggle.
    """
    current = load_active_skills()
    if current is None:
        # First time: start with all active, then apply toggle
        current = set(all_skill_names)

    if name in current:
        current.discard(name)
        now_active = False
    else:
        current.add(name)
        now_active = True

    save_active_skills(current)
    return now_active, current


def is_active(name: str, all_skill_names: set[str]) -> bool:
    """Check if a specific skill is currently active."""
    current = load_active_skills()
    if current is None:
        return True  # Default: all active
    return name in current
