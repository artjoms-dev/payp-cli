"""Unified error display + file logging.

Any user-visible failure should route through :func:`show_error`: it
writes a full traceback to ``~/.payp/logs/payp.log`` via the root
logger and prints a clean red Panel with a pointer to the log file,
so users always know where to look when something breaks.
"""

from __future__ import annotations

import logging

from rich.panel import Panel

from payp.cli.state import console
from payp.logging_setup import log_file_path


def show_error(
    title: str,
    short_message: str,
    *,
    exc: BaseException | None = None,
    hint: str | None = None,
    logger_name: str = "payp",
) -> None:
    """Display a red Panel and log the exception (if any) to the log file.

    ``short_message`` is shown to the user. ``exc`` (when provided) is
    logged with full traceback via ``logger.exception``. ``hint`` appears
    as a yellow line inside the panel; the log path is always appended
    in dim so users can find the traceback.
    """
    if exc is not None:
        logging.getLogger(logger_name).exception(
            "%s: %s", title, short_message,
        )

    lines = [f"[red]{short_message}[/red]"]
    if hint:
        lines.append(f"[yellow]{hint}[/yellow]")
    lines.append(f"[dim]Full traceback: {log_file_path()}[/dim]")
    body = "\n\n".join(lines)

    console.print()
    console.print(
        Panel(
            body,
            title=f"[bold red]{title}[/bold red]",
            border_style="red",
            padding=(1, 2),
            expand=True,
        )
    )
