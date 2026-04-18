"""Tool base class and registry for payp.

Tools are operations the LLM can call via function calling.
Each tool has a JSON schema, description, and async call method.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from payp.core.llm import ToolDefinition


@dataclass
class ToolResult:
    """Result from a tool execution."""

    success: bool
    data: Any = None
    error: str | None = None
    summary: str = ""  # Short description for transparent display


class BaseTool(ABC):
    """Base class for all payp tools."""

    name: str
    description: str
    is_read_only: bool = True
    is_destructive: bool = False
    tier: str = "core"  # "core", "advanced"

    @abstractmethod
    def get_parameters_schema(self) -> dict[str, Any]:
        """Return the JSON schema for tool parameters."""
        ...

    @abstractmethod
    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        """Execute the tool with given arguments."""
        ...

    async def preview(
        self, args: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Describe what this tool WOULD do without executing it.

        Return None if the tool has no meaningful preview (generic handler
        will fall back to a plain args dump). Destructive tools should
        override this to return a dict like::

            {"summary": "Would remove 12 sessions",
             "items": ["2026-04-10_no-db_f7e6.jsonl", ...],
             "warning": "This cannot be undone."}

        The chat approval wrapper uses this to show the user exactly what
        will happen before calling `.call()`.
        """
        return None

    def to_definition(self) -> ToolDefinition:
        """Convert to ToolDefinition for LLM."""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.get_parameters_schema(),
        )


class ToolRegistry:
    """Registry of available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def all_definitions(self) -> list[ToolDefinition]:
        return [t.to_definition() for t in self._tools.values()]

    def core_definitions(self) -> list[ToolDefinition]:
        """Return only core-tier tool definitions (for default LLM context)."""
        return [t.to_definition() for t in self._tools.values() if t.tier == "core"]

    def advanced_names(self) -> list[str]:
        """Return names of advanced-tier tools (for system prompt listing)."""
        return [t.name for t in self._tools.values() if t.tier == "advanced"]

    def all_tools(self) -> list[BaseTool]:
        return list(self._tools.values())
