"""Skill discovery and loading.

Skills are markdown files with YAML frontmatter that define higher-level
workflows composed of multiple tool calls. See docs/skills-architecture.md.

We use a simple, dependency-free frontmatter parser since we only need to
handle a small known set of fields (name, description, when_to_use,
allowed_tools, db_types, author, version).
"""

from __future__ import annotations

import re
from pathlib import Path

from payp.skills.models import Skill, SkillFrontmatter

# Fields that are parsed as lists (YAML flow-style: [a, b, c])
_LIST_FIELDS = {"allowed_tools", "db_types"}

# Required frontmatter fields
_REQUIRED_FIELDS = {"name", "description", "when_to_use"}

_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)$",
    re.DOTALL,
)


def _parse_list_value(raw: str) -> list[str]:
    """Parse a YAML flow-style list: [item1, item2, "item 3"]."""
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        parts = [p.strip() for p in inner.split(",")]
        # Strip surrounding quotes
        cleaned = []
        for p in parts:
            if (p.startswith('"') and p.endswith('"')) or (
                p.startswith("'") and p.endswith("'")
            ):
                p = p[1:-1]
            if p:
                cleaned.append(p)
        return cleaned
    # Single value fallback
    return [raw]


def _parse_scalar_value(raw: str) -> str:
    """Parse a YAML scalar, stripping surrounding quotes."""
    raw = raw.strip()
    if (raw.startswith('"') and raw.endswith('"')) or (
        raw.startswith("'") and raw.endswith("'")
    ):
        return raw[1:-1]
    return raw


def _parse_frontmatter_block(block: str) -> dict[str, object]:
    """Parse a YAML frontmatter block into a flat dict.

    Supports: key: value, key: "value", key: [a, b, c].
    Ignores comments (# ...) and blank lines.
    Does NOT support nested keys or multi-line values.
    """
    result: dict[str, object] = {}
    for line in block.splitlines():
        line = line.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        # Simple key: value split on first colon
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if not key:
            continue
        if key in _LIST_FIELDS:
            result[key] = _parse_list_value(value)
        else:
            result[key] = _parse_scalar_value(value)
    return result


def parse_skill_file(path: Path, scope: str = "builtin") -> Skill | None:
    """Parse a skill .md file into a Skill object.

    Returns None (and logs a warning) if the file is malformed or missing
    required fields. Never raises — bad skills should be skipped silently.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        _warn(f"Skipped skill {path}: could not read file ({e})")
        return None

    match = _FRONTMATTER_RE.match(text)
    if not match:
        _warn(f"Skipped skill {path}: missing YAML frontmatter (---)")
        return None

    fm_block, body = match.group(1), match.group(2).strip()
    try:
        fm_dict = _parse_frontmatter_block(fm_block)
    except Exception as e:
        import logging
        logging.getLogger("payp.skills.loader").exception("skill frontmatter parse failed")
        _warn(f"Skipped skill {path}: could not parse frontmatter ({e})")
        return None

    # Check required fields
    missing = _REQUIRED_FIELDS - set(fm_dict.keys())
    if missing:
        _warn(
            f"Skipped skill {path}: missing required field(s): "
            f"{', '.join(sorted(missing))}"
        )
        return None

    # Build pydantic frontmatter model
    try:
        frontmatter = SkillFrontmatter(**fm_dict)  # type: ignore[arg-type]
    except Exception as e:
        import logging
        logging.getLogger("payp.skills.loader").exception("skill frontmatter validation failed")
        _warn(f"Skipped skill {path}: invalid frontmatter ({e})")
        return None

    if not body:
        _warn(f"Skipped skill {path}: empty body")
        return None

    return Skill(
        frontmatter=frontmatter,
        body=body,
        source_path=path,
        source_scope=scope,
    )


def _warn(msg: str) -> None:
    """Print a warning without crashing payp."""
    # Use a lazy import to avoid circular imports or slow startup
    try:
        import sys
        print(f"[skills] warning: {msg}", file=sys.stderr)
    except Exception:
        pass


def load_skills_from_dir(directory: Path, scope: str) -> list[Skill]:
    """Load all valid .md skill files from a directory.

    Ignores files starting with _ or . and README.md.
    Returns an empty list if the directory does not exist.
    """
    if not directory.is_dir():
        return []

    skills: list[Skill] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith((".", "_")) or path.name.lower() == "readme.md":
            continue
        skill = parse_skill_file(path, scope=scope)
        if skill is not None:
            skills.append(skill)
    return skills
