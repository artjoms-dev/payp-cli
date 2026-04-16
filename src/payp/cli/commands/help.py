"""Slash commands: /help, /quit."""

from __future__ import annotations

import typer
from rich import box

from payp.cli.state import console


def _cmd_help() -> None:
    """Show available commands grouped by category."""
    groups = [
        ("Connection", [
            ("/db", "manage database connections"),
            ("/db <name>", "connect to named connection"),
            ("/credentials", "edit saved credentials"),
        ]),
        ("AI", [
            ("/models", "manage AI providers"),
            ("/models check [openrouter]", "verify key and show usage/balance"),
            ("/mode", "show or set security mode"),
            ("/skills", "browse available workflows"),
        ]),
        ("Data", [
            ("/schema", "explore schema"),
            ("/schema <table>", "show table DDL"),
            ("/stats <table>", "column statistics & data profile"),
            ("/knowledge", "browse business context & notes"),
            ("/knowledge export [conn] [path]", "dump knowledge to .md for sharing"),
            ("/knowledge import [path]", "load shared .md files into backend"),
            ("/memory", "manage knowledge backend"),
            ("/queries", "saved SQL library"),
            ("/snapshots", "manage backups (↑↓ d Enter)"),
            ("/rollback", "restore from snapshot"),
            ("/diff <table>", "compare schema between connections"),
            ("/history", "SQL audit log"),
        ]),
        ("Session", [
            ("/resume", "continue a previous session"),
            ("/resume clean", "purge empty sessions (also --keep N / --older-than Nd / --all)"),
            ("/context", "show context window usage"),
            ("/compact", "compress older messages"),
            ("/more", "next 20 rows of last SELECT"),
            ("/cost", "token usage and costs"),
            ("/export [path]", "export session to markdown"),
            ("/clear", "clear the screen (also cls / cs / /cls)"),
            ("/help", "this help"),
            ("/quit", "exit payp"),
        ]),
    ]

    from payp.ui.theme import Color
    table_widget = __import__("rich.table", fromlist=["Table"]).Table(
        box=box.SIMPLE, show_header=False, padding=(0, 2)
    )
    table_widget.add_column(style="dim", width=10)
    table_widget.add_column(style=Color.BRAND_ALT, width=22)
    table_widget.add_column()

    for group_name, commands in groups:
        for i, (cmd, desc) in enumerate(commands):
            prefix = group_name if i == 0 else ""
            table_widget.add_row(prefix, cmd, desc)
        table_widget.add_row("", "", "")  # spacer

    console.print()
    console.print(table_widget)
    console.print(
        "  [dim]Type naturally (no /) to chat with the AI assistant.[/dim]\n"
        "  [dim]Ctrl+C cancel  •  Ctrl+D exit  •  ↑↓ history[/dim]\n"
    )


def _cmd_quit() -> None:
    """Exit payp."""
    console.print("[dim]Goodbye![/dim]")
    raise typer.Exit()


def _cmd_clear() -> None:
    """Clear the terminal screen without leaving the REPL."""
    # The interactive loop already catches bare cls / clear / cs / their
    # slashed twins before dispatch — this handler keeps the command
    # discoverable via autocomplete and /help.
    console.clear()
