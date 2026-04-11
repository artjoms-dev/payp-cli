"""Slash command registry — single source of truth for payp CLI commands.

Both _handle_command (routing) and _slash_completer (autocomplete) read from
the same SLASH_COMMANDS dict so adding a new command requires touching exactly
one place.

Import order is carefully arranged to avoid circular imports:
  dispatch -> commands/* -> state / runtime   (one direction)
  loop     -> dispatch                        (lazy, inside _interactive_loop)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from payp.cli.state import CommandCancelled, console


@dataclass(frozen=True)
class SlashCommand:
    """Registry entry for a single slash command."""

    handler: Callable[[str], None]
    help: str
    takes_args: bool


def _wrap(fn: Callable[[], None]) -> Callable[[str], None]:
    """Wrap a no-arg function so it fits the uniform (args: str) -> None signature."""
    def _wrapped(args: str) -> None:  # noqa: ARG001
        fn()
    return _wrapped


def _build_registry() -> dict[str, SlashCommand]:
    """Build SLASH_COMMANDS lazily to avoid circular imports at module load time."""
    # Import command modules here — they all import from state/runtime, never from dispatch
    from payp.cli.commands import (
        context,
        db,
        diff,
        export,
        history,
        knowledge,
        memory,
        mode,
        models,
        queries,
        resume,
        schema,
        skills,
        snapshots,
    )
    from payp.cli.commands import help as help_cmd

    return {
        "/db": SlashCommand(db._cmd_db, "manage database connections", True),
        "/credentials": SlashCommand(db._cmd_credentials, "edit saved credentials", True),
        "/models": SlashCommand(models._cmd_models, "manage AI providers", True),
        "/mode": SlashCommand(mode._cmd_mode, "show or set security mode", True),
        "/schema": SlashCommand(schema._cmd_schema, "explore schema", True),
        "/stats": SlashCommand(schema._cmd_stats, "column statistics & data profile", True),
        "/knowledge": SlashCommand(knowledge._cmd_knowledge, "business context & notes", True),
        "/memory": SlashCommand(memory._cmd_memory, "manage knowledge backend", True),
        "/queries": SlashCommand(queries._cmd_queries, "saved SQL library", True),
        "/snapshots": SlashCommand(_wrap(snapshots._cmd_snapshots), "manage backups", False),
        "/rollback": SlashCommand(_wrap(snapshots._cmd_rollback), "restore from snapshot", False),
        "/diff": SlashCommand(diff._cmd_diff, "compare schema between connections", True),
        "/history": SlashCommand(history._cmd_history, "SQL audit log", True),
        "/resume": SlashCommand(resume._cmd_resume, "continue previous session", True),
        "/compact": SlashCommand(_wrap(context._cmd_compact), "compress older messages", False),
        "/context": SlashCommand(_wrap(context._cmd_context), "show context window usage", False),
        "/more": SlashCommand(_wrap(context._cmd_more), "next 20 rows of last SELECT", False),
        "/cost": SlashCommand(_wrap(context._cmd_cost), "token usage and costs", False),
        "/export": SlashCommand(export._cmd_export, "export session to markdown", True),
        "/skills": SlashCommand(_wrap(skills._cmd_skills), "browse available workflows", False),
        "/help": SlashCommand(_wrap(help_cmd._cmd_help), "show available commands", False),
        "/quit": SlashCommand(_wrap(help_cmd._cmd_quit), "exit payp", False),
        "/exit": SlashCommand(_wrap(help_cmd._cmd_quit), "exit payp", False),
    }


# Module-level cache - built once on first call to _handle_command or _slash_completer
_REGISTRY: dict[str, SlashCommand] | None = None


def _get_registry() -> dict[str, SlashCommand]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def _handle_command(cmd: str) -> None:
    """Route slash commands from interactive mode."""
    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    entry = _get_registry().get(command)
    if entry:
        try:
            entry.handler(args)
        except CommandCancelled:
            console.print("[dim]Cancelled.[/dim]")
        except KeyboardInterrupt:
            console.print("[dim]Cancelled.[/dim]")
    else:
        console.print(
            f"[red]Unknown command: {command}[/red]. Type /help for available commands."
        )


def _slash_completer() -> Any:
    """Build a prompt_toolkit Completer from the SLASH_COMMANDS registry."""
    from prompt_toolkit.completion import Completer, Completion

    registry = _get_registry()

    class SlashCompleter(Completer):
        def get_completions(self, document: Any, complete_event: Any) -> Any:
            text = document.text_before_cursor
            if not text.startswith("/"):
                return
            for cmd, entry in registry.items():
                if cmd.startswith(text):
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display=cmd,
                        display_meta=entry.help,
                    )

    return SlashCompleter()
