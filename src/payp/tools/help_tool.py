"""Help tool — LLM reads payp's internal help docs to answer 'how do I' questions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from payp.tools.base import BaseTool, ToolResult


HELP_DIR = Path(__file__).parent.parent.parent.parent / "docs" / "help"


def list_help_topics() -> list[str]:
    """Return available help topic names."""
    if not HELP_DIR.exists():
        return []
    return sorted(f.stem for f in HELP_DIR.glob("*.md"))


class PaypHelpTool(BaseTool):
    name = "payp_help"
    description = (
        "Read payp's internal help documentation. "
        "Use this when the user asks HOW to use payp itself (not their database) — "
        "e.g., 'how do I change mode', 'how do snapshots work', 'what commands are available'. "
        "Topics: getting-started, connections, models, security-modes, schema, snapshots, export, commands, troubleshooting."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": list_help_topics(),
                    "description": "Help topic to read",
                },
            },
            "required": ["topic"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        topic = args.get("topic", "").strip()
        if not topic:
            return ToolResult(success=False, error="No topic specified")

        path = HELP_DIR / f"{topic}.md"
        if not path.exists():
            available = ", ".join(list_help_topics())
            return ToolResult(
                success=False,
                error=f"Topic '{topic}' not found. Available: {available}",
            )

        content = path.read_text()
        return ToolResult(
            success=True,
            data={"topic": topic, "content": content},
            summary=f"Loaded help for {topic}",
        )
