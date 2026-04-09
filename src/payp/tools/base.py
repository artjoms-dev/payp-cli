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

    @abstractmethod
    def get_parameters_schema(self) -> dict[str, Any]:
        """Return the JSON schema for tool parameters."""
        ...

    @abstractmethod
    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        """Execute the tool with given arguments."""
        ...

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

    def all_tools(self) -> list[BaseTool]:
        return list(self._tools.values())
