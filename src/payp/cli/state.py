"""Shared runtime state for the payp CLI.

Single source of truth for mutable session state, the Rich console instance,
and the CommandCancelled sentinel. All other CLI modules import from here so
that there is exactly one _state dict regardless of how many modules share it.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console

from payp.config import load_config
from payp.models import AppConfig

_state: dict[str, Any] = {
    "config": None,
    "active_connection": None,
    "connection_manager": None,  # ConnectionManager instance
    "mode": None,
    "t0": None,  # SchemaIndex
    "t1": None,  # SchemaCatalog
    "llm_client": None,  # LLMClient instance
    "last_select": None,  # {sql, offset, page_size, connection} for /more pagination
}

console = Console()


class CommandCancelled(Exception):  # noqa: N818
    """Raised when user presses Esc during a slash command."""


def get_config() -> AppConfig:
    if _state["config"] is None:
        _state["config"] = load_config()
    return _state["config"]
