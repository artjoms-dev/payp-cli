"""Tests for the Skills system."""

from __future__ import annotations

import asyncio
from pathlib import Path

from payp.skills.loader import load_skills_from_dir, parse_skill_file
from payp.skills.models import Skill, SkillFrontmatter
from payp.skills.registry import SkillRegistry, discover_skills
from payp.tools.skills_tool import InvokeSkillTool, ListSkillsTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_SKILL = """\
---
name: test-skill
description: A skill for testing
when_to_use: When running tests
allowed_tools: [query, schema_lookup]
db_types: [postgresql, mysql]
version: 1.0
---

## Test Workflow

1. Do thing
2. Do other thing
"""

MISSING_REQUIRED = """\
---
name: broken
description: Missing when_to_use
---

## Body
"""

NO_FRONTMATTER = """\
## Just a markdown file

No YAML up top.
"""

EMPTY_BODY = """\
---
name: empty
description: No body
when_to_use: never
---
"""


# ---------------------------------------------------------------------------
# Loader tests
# ---------------------------------------------------------------------------

def test_parse_valid_skill(tmp_path: Path) -> None:
    p = tmp_path / "test.md"
    p.write_text(VALID_SKILL)
    skill = parse_skill_file(p, scope="builtin")
    assert skill is not None
    assert skill.name == "test-skill"
    assert skill.description == "A skill for testing"
    assert skill.when_to_use == "When running tests"
    assert skill.frontmatter.allowed_tools == ["query", "schema_lookup"]
    assert skill.frontmatter.db_types == ["postgresql", "mysql"]
    assert skill.frontmatter.version == "1.0"
    assert "Do thing" in skill.body
    assert skill.source_scope == "builtin"


def test_parse_missing_required_fields(tmp_path: Path) -> None:
    p = tmp_path / "broken.md"
    p.write_text(MISSING_REQUIRED)
    assert parse_skill_file(p) is None


def test_parse_no_frontmatter(tmp_path: Path) -> None:
    p = tmp_path / "plain.md"
    p.write_text(NO_FRONTMATTER)
    assert parse_skill_file(p) is None


def test_parse_empty_body(tmp_path: Path) -> None:
    p = tmp_path / "empty.md"
    p.write_text(EMPTY_BODY)
    assert parse_skill_file(p) is None


def test_parse_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "nope.md"
    assert parse_skill_file(p) is None


def test_load_skills_from_dir_ignores_readme_and_hidden(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(VALID_SKILL)
    (tmp_path / "_draft.md").write_text(VALID_SKILL)
    (tmp_path / "good.md").write_text(VALID_SKILL)
    skills = load_skills_from_dir(tmp_path, scope="user")
    assert len(skills) == 1
    assert skills[0].name == "test-skill"


def test_load_from_missing_dir() -> None:
    assert load_skills_from_dir(Path("/nonexistent/xyz"), scope="user") == []


def test_list_fields_with_quoted_items(tmp_path: Path) -> None:
    body = """\
---
name: q
description: d
when_to_use: w
allowed_tools: ["query", 'schema_lookup', export]
---

body
"""
    p = tmp_path / "q.md"
    p.write_text(body)
    skill = parse_skill_file(p)
    assert skill is not None
    assert skill.frontmatter.allowed_tools == ["query", "schema_lookup", "export"]


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

def _make_skill(name: str, db_types: list[str] | None = None) -> Skill:
    return Skill(
        frontmatter=SkillFrontmatter(
            name=name,
            description="d",
            when_to_use="w",
            db_types=db_types or [],
        ),
        body="body",
        source_path=Path(f"/tmp/{name}.md"),
    )


def test_registry_register_and_get() -> None:
    reg = SkillRegistry()
    s = _make_skill("foo")
    reg.register(s)
    assert reg.get("foo") is s
    assert reg.get("bar") is None
    assert "foo" in reg
    assert len(reg) == 1


def test_registry_for_dialect_filters() -> None:
    reg = SkillRegistry()
    reg.register(_make_skill("pg-only", db_types=["postgresql"]))
    reg.register(_make_skill("universal", db_types=[]))
    reg.register(_make_skill("oracle-only", db_types=["oracle"]))

    pg_skills = {s.name for s in reg.for_dialect("postgresql")}
    assert pg_skills == {"pg-only", "universal"}

    oracle_skills = {s.name for s in reg.for_dialect("oracle")}
    assert oracle_skills == {"oracle-only", "universal"}

    mysql_skills = {s.name for s in reg.for_dialect("mysql")}
    assert mysql_skills == {"universal"}


def test_discover_skills_precedence(tmp_path: Path) -> None:
    """Project overrides user overrides builtin."""
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    project = tmp_path / "project"
    for d in (builtin, user, project):
        d.mkdir()

    # Same-name skill in each scope with different body
    template = """\
---
name: dup
description: {scope} version
when_to_use: test
---

{scope} body
"""
    (builtin / "dup.md").write_text(template.format(scope="builtin"))
    (user / "dup.md").write_text(template.format(scope="user"))
    (project / "dup.md").write_text(template.format(scope="project"))

    reg = discover_skills(builtin_dir=builtin, user_dir=user, project_dir=project)
    assert len(reg) == 1
    winner = reg.get("dup")
    assert winner is not None
    assert winner.source_scope == "project"
    assert "project body" in winner.body


def test_discover_finds_builtin_schema_explorer() -> None:
    """The shipped schema-explorer skill should be discovered."""
    # Point at the real builtin_skills directory
    builtin = Path(__file__).resolve().parents[1] / "builtin_skills"
    reg = discover_skills(
        builtin_dir=builtin,
        user_dir=Path("/nonexistent-user"),
        project_dir=Path("/nonexistent-project"),
    )
    assert "schema-explorer" in reg
    s = reg.get("schema-explorer")
    assert s is not None
    assert "postgresql" in s.frontmatter.db_types
    assert "schema_lookup" in s.frontmatter.allowed_tools


# ---------------------------------------------------------------------------
# Tool tests
# ---------------------------------------------------------------------------

def test_invoke_skill_tool_returns_body() -> None:
    reg = SkillRegistry()
    reg.register(_make_skill("foo", db_types=["postgresql"]))

    tool = InvokeSkillTool()
    result = asyncio.run(
        tool.call(
            {"skill_name": "foo", "user_context": "do the thing"},
            {"skills": reg, "connection_manager": None},
        )
    )
    assert result.success
    assert result.data["skill"] == "foo"
    assert "body" in result.data["plan"]
    assert "do the thing" in result.data["plan"]


def test_invoke_skill_tool_unknown_skill() -> None:
    reg = SkillRegistry()
    tool = InvokeSkillTool()
    result = asyncio.run(
        tool.call(
            {"skill_name": "missing", "user_context": "x"},
            {"skills": reg, "connection_manager": None},
        )
    )
    assert not result.success
    assert "not found" in (result.error or "").lower()


def test_invoke_skill_tool_missing_name() -> None:
    tool = InvokeSkillTool()
    result = asyncio.run(
        tool.call(
            {"skill_name": "", "user_context": "x"},
            {"skills": SkillRegistry()},
        )
    )
    assert not result.success


def test_list_skills_tool() -> None:
    reg = SkillRegistry()
    reg.register(_make_skill("a"))
    reg.register(_make_skill("b", db_types=["oracle"]))

    tool = ListSkillsTool()
    result = asyncio.run(
        tool.call({}, {"skills": reg, "connection_manager": None})
    )
    assert result.success
    assert result.data["count"] == 2
    names = {s["name"] for s in result.data["skills"]}
    assert names == {"a", "b"}


def test_list_skills_tool_no_registry() -> None:
    tool = ListSkillsTool()
    result = asyncio.run(tool.call({}, {}))
    assert result.success
    assert result.data["count"] == 0


# ---------------------------------------------------------------------------
# Dialect check
# ---------------------------------------------------------------------------

def test_skill_supports_dialect() -> None:
    s = _make_skill("x", db_types=["postgresql", "mysql"])
    assert s.supports_dialect("postgresql")
    assert s.supports_dialect("PostgreSQL")
    assert s.supports_dialect("mysql")
    assert not s.supports_dialect("oracle")

    universal = _make_skill("u", db_types=[])
    assert universal.supports_dialect("oracle")
    assert universal.supports_dialect("anything")
