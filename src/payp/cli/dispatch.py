"""Slash command registry - single source of truth for payp CLI commands.

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
    """Registry entry for a single slash command.

    `subcommands` is an optional tuple of `(trigger, help_meta)` pairs that
    the completer surfaces after the user has typed the command + a space.
    Triggers may contain spaces (e.g. `"scan docker"`) - the completer
    replaces the entire arg text, so multi-token subcommands autocomplete
    in one tab.
    """

    handler: Callable[[str], None]
    help: str
    takes_args: bool
    subcommands: tuple[tuple[str, str], ...] = ()


def _wrap(fn: Callable[[], None]) -> Callable[[str], None]:
    """Wrap a no-arg function so it fits the uniform (args: str) -> None signature."""
    def _wrapped(args: str) -> None:  # noqa: ARG001
        fn()
    return _wrapped


def _build_registry() -> dict[str, SlashCommand]:
    """Build SLASH_COMMANDS lazily to avoid circular imports at module load time."""
    # Import command modules here - they all import from state/runtime, never from dispatch
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
        "/db": SlashCommand(
            db._cmd_db, "manage database connections", True,
            subcommands=(
                ("scan docker", "auto-detect running DB containers"),
            ),
        ),
        "/credentials": SlashCommand(db._cmd_credentials, "edit saved credentials", True),
        "/models": SlashCommand(
            models._cmd_models, "manage AI providers", True,
            subcommands=(
                ("set executor", "pick the main model"),
                ("set reviewer", "pick the review model"),
                ("add", "register a new provider"),
                ("check", "verify API keys and balance"),
            ),
        ),
        "/mode": SlashCommand(
            mode._cmd_mode, "show or set security mode", True,
            subcommands=(
                ("manual", "approve each write"),
                ("yolo", "auto-execute all"),
                ("secure", "reviewer + approval"),
                ("secure-auto", "reviewer decides"),
            ),
        ),
        "/schema": SlashCommand(
            schema._cmd_schema, "explore schema", True,
            subcommands=(
                ("--refresh", "rebuild the schema cache"),
                ("--graph", "show the foreign-key graph"),
            ),
        ),
        "/stats": SlashCommand(schema._cmd_stats, "column statistics & data profile", True),
        "/knowledge": SlashCommand(
            knowledge._cmd_knowledge, "business context & notes", True,
            subcommands=(
                ("all", "list every knowledge file"),
                ("export", "write knowledge to a file"),
                ("import", "load knowledge from a file"),
                ("migrate-legacy", "move ./payp/knowledge to the global dir"),
            ),
        ),
        "/memory": SlashCommand(
            memory._cmd_memory, "manage knowledge backend", True,
            subcommands=(
                ("switch", "change the active memory backend"),
                ("migrate", "move data to the current backend"),
                ("all", "list every memory entry"),
            ),
        ),
        "/queries": SlashCommand(queries._cmd_queries, "saved SQL library", True),
        "/snapshots": SlashCommand(_wrap(snapshots._cmd_snapshots), "manage backups", False),
        "/rollback": SlashCommand(_wrap(snapshots._cmd_rollback), "restore from snapshot", False),
        "/diff": SlashCommand(diff._cmd_diff, "compare schema between connections", True),
        "/history": SlashCommand(history._cmd_history, "SQL audit log", True),
        "/resume": SlashCommand(
            resume._cmd_resume, "continue previous session", True,
            subcommands=(
                ("clean", "delete saved sessions"),
            ),
        ),
        "/compact": SlashCommand(_wrap(context._cmd_compact), "compress older messages", False),
        "/context": SlashCommand(_wrap(context._cmd_context), "show context window usage", False),
        "/more": SlashCommand(_wrap(context._cmd_more), "next 20 rows of last SELECT", False),
        "/cost": SlashCommand(_wrap(context._cmd_cost), "token usage and costs", False),
        "/export": SlashCommand(export._cmd_export, "export session to markdown", True),
        "/skills": SlashCommand(_wrap(skills._cmd_skills), "browse available workflows", False),
        "/clear": SlashCommand(_wrap(help_cmd._cmd_clear), "clear the screen (also cls / cs)", False),
        "/cls": SlashCommand(_wrap(help_cmd._cmd_clear), "clear the screen", False),
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
        except Exception as e:
            from payp.ui.errors import show_error

            short = str(e).strip() or type(e).__name__
            logger_name = f"payp.cli.commands.{command.lstrip('/')}"
            show_error(f"Command {command} failed", short, exc=e, logger_name=logger_name)
    else:
        console.print(
            f"[red]Unknown command: {command}[/red]. Type /help for available commands."
        )


def _slash_completer() -> Any:
    """Build a prompt_toolkit Completer from the SLASH_COMMANDS registry.

    Two modes:
      * `/par` -> yield every command whose name starts with `/par`.
      * `/db ...` -> yield the command's declared `subcommands`, filtered by
        whatever the user has already typed after the space. Subcommand
        triggers may contain spaces (e.g. `"scan docker"`); the completion
        replaces the entire arg text so multi-token subs tab-complete in one
        shot rather than needing two separate completions.
    """
    from prompt_toolkit.completion import Completer, Completion

    registry = _get_registry()

    class SlashCompleter(Completer):
        def get_completions(self, document: Any, complete_event: Any) -> Any:
            text = document.text_before_cursor
            if not text.startswith("/"):
                return

            # No space yet -> we're still completing the command name.
            if " " not in text:
                # Prefix matches for command names.
                for cmd, entry in registry.items():
                    if cmd.startswith(text):
                        yield Completion(
                            cmd,
                            start_position=-len(text),
                            display=cmd,
                            display_meta=entry.help,
                        )
                # If the user has finished typing a command that has
                # subcommands, also preview those so they don't have to
                # press space before the hints appear. Selecting one of
                # these rewrites the line to `"/cmd trigger"` in one tab.
                entry = registry.get(text.lower())
                if entry is not None and entry.subcommands:
                    for trigger, meta in entry.subcommands:
                        full = f"{text} {trigger}"
                        yield Completion(
                            full,
                            start_position=-len(text),
                            display=full,
                            display_meta=meta,
                        )
                return

            # Space typed -> complete subcommands for the entered command.
            cmd_name, _, arg_text = text.partition(" ")
            entry = registry.get(cmd_name.lower())
            if entry is None or not entry.subcommands:
                return
            arg_lower = arg_text.lower()
            for trigger, meta in entry.subcommands:
                if trigger.lower().startswith(arg_lower):
                    yield Completion(
                        trigger,
                        start_position=-len(arg_text),
                        display=trigger,
                        display_meta=meta,
                    )

    return SlashCompleter()
