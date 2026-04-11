"""Typer application, CLI subcommands, and entry point."""

from __future__ import annotations

from typing import Annotated

import typer

from payp import __version__
from payp.cli.loop import _interactive_loop
from payp.cli.state import console
from payp.cli.welcome import _show_welcome

app = typer.Typer(
    name="payp",
    help="AI-powered CLI for data engineers",
    no_args_is_help=False,
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", "-v", help="Show version")
    ] = False,
) -> None:
    """payp — AI-powered CLI for data engineers."""
    if version:
        console.print(f"payp v{__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        _show_welcome()
        _interactive_loop()


@app.command()
def db(
    name: Annotated[str | None, typer.Argument(help="Connection name")] = None,
) -> None:
    """Manage database connections."""
    from payp.cli.commands.db import _cmd_db
    _cmd_db(name or "")


@app.command()
def models(
    action: Annotated[str | None, typer.Argument(help="Action: add")] = None,
) -> None:
    """Manage AI model providers."""
    from payp.cli.commands.models import _cmd_models
    _cmd_models(action or "")


@app.command()
def mode(
    new_mode: Annotated[str | None, typer.Argument(help="Security mode")] = None,
) -> None:
    """Set security mode."""
    from payp.cli.commands.mode import _cmd_mode
    _cmd_mode(new_mode or "")


@app.command("mcp-serve")
def mcp_serve() -> None:
    """Start payp as an MCP stdio server for external clients (Claude Desktop, Cursor, etc.)."""
    from payp.mcp.server import main as _mcp_main
    _mcp_main()


def app_entry() -> None:
    """Entry point for the payp CLI."""
    app()
