"""Skill registry — discovers, stores, and queries skills.

Skills are loaded from three locations, in increasing precedence:
  1. builtin_skills/      (shipped with payp)
  2. ~/.payp/skills/      (user-level)
  3. ./payp/skills/       (project-level, team-shared, in CWD)

If two skills share the same `name`, the higher-precedence one wins.
"""

from __future__ import annotations

from pathlib import Path

from payp.skills.loader import load_skills_from_dir
from payp.skills.models import Skill


def _builtin_skills_dir() -> Path:
    """Locate the builtin_skills/ directory shipped with payp.

    src/payp/skills/registry.py → project root is 4 parents up.
    """
    return Path(__file__).resolve().parents[3] / "builtin_skills"


def _user_skills_dir() -> Path:
    return Path.home() / ".payp" / "skills"


def _project_skills_dir() -> Path:
    return Path.cwd() / "payp" / "skills"


class SkillRegistry:
    """Holds the set of skills available for the current session."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """Register a skill. Overrides any existing skill with the same name."""
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def active_names(self) -> set[str]:
        """Return names of currently-active skills from persistent config."""
        from payp.storage.active_skills import load_active_skills
        active = load_active_skills()
        if active is None:
            # First run — all discovered skills are active by default
            return set(self._skills.keys())
        return active

    def active(self) -> list[Skill]:
        """Return only skills currently marked active by the user."""
        names = self.active_names()
        return [s for s in self._skills.values() if s.name in names]

    def is_active(self, name: str) -> bool:
        return name in self.active_names()

    def for_dialect(self, db_type: str | None, active_only: bool = True) -> list[Skill]:
        """Return skills that support the given db_type.

        If db_type is None, returns only skills that declare NO db_types
        restriction (i.e. universal skills).
        If active_only is True (default), only skills the user has activated are returned.
        """
        source = self.active() if active_only else list(self._skills.values())
        if db_type is None:
            return [s for s in source if not s.frontmatter.db_types]
        return [s for s in source if s.supports_dialect(db_type)]

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: str) -> bool:
        return name in self._skills


def discover_skills(
    builtin_dir: Path | None = None,
    user_dir: Path | None = None,
    project_dir: Path | None = None,
) -> SkillRegistry:
    """Scan all three skill locations and build a registry.

    Precedence (later wins): builtin → user → project.
    """
    registry = SkillRegistry()

    builtin_dir = builtin_dir or _builtin_skills_dir()
    user_dir = user_dir or _user_skills_dir()
    project_dir = project_dir or _project_skills_dir()

    for skill in load_skills_from_dir(builtin_dir, scope="builtin"):
        registry.register(skill)
    for skill in load_skills_from_dir(user_dir, scope="user"):
        registry.register(skill)
    for skill in load_skills_from_dir(project_dir, scope="project"):
        registry.register(skill)

    return registry
