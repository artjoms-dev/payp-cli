"""LLM-callable tools for working with Skills.

- InvokeSkillTool: returns the full body of a named skill so the LLM can follow it.
- ListSkillsTool: returns metadata for all available skills (filtered by dialect).

Skills return TEXT INSTRUCTIONS. They never bypass security modes — the LLM
still executes the plan via the normal tool registry, which enforces approval /
review as usual.
"""

from __future__ import annotations

from typing import Any

from payp.tools.base import BaseTool, ToolResult


class InvokeSkillTool(BaseTool):
    name = "invoke_skill"
    description = (
        "Load a pre-defined workflow (skill) by name. Returns the skill's full "
        "workflow text as a PLAN for you to follow. After calling this, use your "
        "normal tools (execute_sql, schema_lookup, etc.) to execute each step. "
        "Call this when the user's request matches a skill's `when_to_use` "
        "(listed in the Available Skills section of your system prompt). "
        "Skills do NOT bypass security modes — destructive SQL still requires approval."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "The name of the skill to invoke (from Available Skills list)",
                },
                "user_context": {
                    "type": "string",
                    "description": "What the user asked for — helps tailor skill execution",
                },
            },
            "required": ["skill_name", "user_context"],
        }

    async def call(
        self, args: dict[str, Any], context: dict[str, Any]
    ) -> ToolResult:
        skill_name = (args.get("skill_name") or "").strip()
        user_context = (args.get("user_context") or "").strip()

        if not skill_name:
            return ToolResult(success=False, error="skill_name is required")

        registry = context.get("skills")
        if registry is None:
            return ToolResult(
                success=False, error="Skills system is not initialized"
            )

        skill = registry.get(skill_name)
        if skill is None:
            available = ", ".join(sorted(s.name for s in registry.active())) or "(none)"
            return ToolResult(
                success=False,
                error=(
                    f"Skill '{skill_name}' not found. "
                    f"Active skills: {available}"
                ),
            )

        # Reject inactive skills
        if not registry.is_active(skill_name):
            return ToolResult(
                success=False,
                error=(
                    f"Skill '{skill_name}' is not active. "
                    f"User must activate it via /skills first."
                ),
            )

        # Check dialect compatibility against the active connection
        conn_mgr = context.get("connection_manager")
        if conn_mgr is not None and getattr(conn_mgr, "is_connected", False):
            try:
                db_type = conn_mgr.profile.db_type.value
            except Exception:
                db_type = None
            if db_type and not skill.supports_dialect(db_type):
                return ToolResult(
                    success=False,
                    error=(
                        f"Skill '{skill_name}' does not support {db_type}. "
                        f"Supported dialects: "
                        f"{', '.join(skill.frontmatter.db_types) or 'any'}"
                    ),
                )

        allowed_tools = skill.frontmatter.allowed_tools or []
        allowed_note = (
            f"Allowed tools for this workflow: {', '.join(allowed_tools)}."
            if allowed_tools
            else "This skill may use any available tool."
        )

        plan = (
            f"# Skill: {skill.name}\n\n"
            f"{skill.description}\n\n"
            f"User's request: {user_context}\n\n"
            f"{allowed_note}\n\n"
            f"---\n\n"
            f"{skill.body}\n\n"
            f"---\n\n"
            f"Now execute this plan step-by-step using your tools. "
            f"Destructive operations still require approval via the active security mode."
        )

        return ToolResult(
            success=True,
            data={
                "skill": skill.name,
                "version": skill.frontmatter.version,
                "allowed_tools": allowed_tools,
                "plan": plan,
            },
            summary=f"Loaded skill: {skill.name}",
        )


class ListSkillsTool(BaseTool):
    name = "list_skills"
    description = (
        "List all available skills (pre-defined workflows) for the current "
        "database connection. Returns name, description, and when_to_use for each. "
        "Use this to discover workflows when the user asks 'what can you do' or "
        "'what skills are available'."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def call(
        self, args: dict[str, Any], context: dict[str, Any]
    ) -> ToolResult:
        registry = context.get("skills")
        if registry is None:
            return ToolResult(
                success=True,
                data={"skills": [], "count": 0},
                summary="Skills system not initialized",
            )

        # Filter by current dialect if connected
        conn_mgr = context.get("connection_manager")
        db_type: str | None = None
        if conn_mgr is not None and getattr(conn_mgr, "is_connected", False):
            try:
                db_type = conn_mgr.profile.db_type.value
            except Exception:
                db_type = None

        # Only return skills the user has activated
        skills = (
            registry.for_dialect(db_type, active_only=True) if db_type
            else registry.active()
        )

        items = [
            {
                "name": s.name,
                "description": s.description,
                "when_to_use": s.when_to_use,
                "db_types": s.frontmatter.db_types or ["any"],
                "allowed_tools": s.frontmatter.allowed_tools or ["any"],
                "version": s.frontmatter.version,
                "scope": s.source_scope,
            }
            for s in skills
        ]

        return ToolResult(
            success=True,
            data={"skills": items, "count": len(items), "dialect": db_type},
            summary=f"{len(items)} skill(s) available",
        )
