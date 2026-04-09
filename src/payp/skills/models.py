"""Pydantic models for payp Skills.

A Skill is a markdown file with YAML frontmatter that defines a high-level
workflow composed of multiple tool calls. See docs/skills-architecture.md.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class SkillFrontmatter(BaseModel):
    """Parsed YAML frontmatter from a skill file.

    Required: name, description, when_to_use
    Optional: allowed_tools, db_types, author, version
    """

    name: str = Field(..., description="Unique skill identifier (kebab-case)")
    description: str = Field(..., description="One-line summary of what the skill does")
    when_to_use: str = Field(..., description="When the LLM should invoke this skill")
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="Tool names this skill is allowed to use. Empty = all tools.",
    )
    db_types: list[str] = Field(
        default_factory=list,
        description="DB dialects this skill supports. Empty = all dialects.",
    )
    author: str | None = None
    version: str | None = None


class Skill(BaseModel):
    """A full skill: frontmatter + body text + source path."""

    frontmatter: SkillFrontmatter
    body: str = Field(..., description="Markdown body — the workflow instructions")
    source_path: Path = Field(..., description="Path to the skill .md file")
    source_scope: str = Field(
        default="builtin",
        description="One of: builtin, user, project",
    )

    model_config = {"arbitrary_types_allowed": True}

    @property
    def name(self) -> str:
        return self.frontmatter.name

    @property
    def description(self) -> str:
        return self.frontmatter.description

    @property
    def when_to_use(self) -> str:
        return self.frontmatter.when_to_use

    def supports_dialect(self, db_type: str) -> bool:
        """Check if this skill supports the given database dialect."""
        if not self.frontmatter.db_types:
            return True  # empty = all dialects
        return db_type.lower() in {d.lower() for d in self.frontmatter.db_types}
