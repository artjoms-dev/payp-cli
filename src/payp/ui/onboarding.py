"""First-run onboarding wizard — guides new users through initial setup.

Two steps:
  1. AI Model configuration
  2. Database connection
"""

from __future__ import annotations

import time

from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel

from payp.ui.theme import Color, section, success


def is_first_run() -> bool:
    """Return True if user has no models AND no connections configured."""
    from payp.config import list_connections, load_models_config
    return not load_models_config() and not list_connections()


def should_prompt_model_setup() -> bool:
    """Return True if no models are configured."""
    from payp.config import load_models_config
    return not load_models_config()


def _render_banner_typing(console: Console, banner: str, duration_ms: int = 500) -> None:
    """Render onboarding banner with a soft typewriter effect."""
    total_chars = len(banner)
    if total_chars <= 0:
        return

    max_frames = 100
    step = max(1, total_chars // max_frames)
    indices = list(range(step, total_chars + 1, step))
    if indices[-1] != total_chars:
        indices.append(total_chars)
    frame_delay = (duration_ms / 1000.0) / len(indices)

    with Live(console=console, auto_refresh=False) as live:
        for idx in indices:
            live.update(
                Panel(
                    banner[:idx],
                    border_style=Color.BRAND,
                    box=box.ROUNDED,
                    padding=(1, 3),
                ),
                refresh=True,
            )
            time.sleep(frame_delay)


def _type_block(console: Console, text: str, duration_ms: int = 450) -> None:
    """Print a plain text block with a typewriter effect."""
    total_chars = len(text)
    if total_chars <= 0:
        return

    max_frames = 100
    step = max(1, total_chars // max_frames)
    indices = list(range(step, total_chars + 1, step))
    if indices[-1] != total_chars:
        indices.append(total_chars)
    frame_delay = (duration_ms / 1000.0) / len(indices)

    with Live(console=console, auto_refresh=False) as live:
        for idx in indices:
            live.update(text[:idx], refresh=True)
            time.sleep(frame_delay)


def render_welcome_banner(console: Console) -> None:
    """Render the onboarding welcome banner."""
    banner = (
        "Welcome to payp\n\n"
        "Your AI database assistant.\n\n"
        f"Let's get you set up in 2 steps:\n"
        "  1. Configure an AI model\n"
        "  2. Connect a database"
    )
    _render_banner_typing(console, banner, duration_ms=500)
    console.print()


def run_onboarding(console: Console) -> None:
    """Run the full first-run onboarding flow.

    Calls out to existing setup wizards for models and connections.
    Pressing Esc inside any sub-wizard cancels that step cleanly
    instead of crashing the CLI.
    """
    from payp.cli import _setup_new_connection, _setup_new_provider
    from payp.cli.runtime import prompt_confirm
    from payp.cli.state import CommandCancelled

    render_welcome_banner(console)

    # Step 1: Model
    _type_block(
        console,
        "\nStep 1 of 2 — AI Model\n"
        "--------------------------------------------------\n"
        "payp needs at least one AI model to generate SQL and analyze data.\n",
        duration_ms=420,
    )
    try:
        _setup_new_provider(animate_intro=True)
    except CommandCancelled:
        console.print("[dim]Skipped. Run [bold]/models add[/bold] later.[/dim]")

    console.print()

    # Step 2: Database
    section(console, "Step 2 of 2 — Database Connection")
    console.print(
        "[dim]Connect to a database now, or skip and run /db later.[/dim]\n"
    )
    try:
        if prompt_confirm("Connect a database now?", default=True):
            _setup_new_connection()
        else:
            console.print("[dim]Skipped. Run [bold]/db[/bold] when ready.[/dim]")
    except CommandCancelled:
        console.print("[dim]Skipped. Run [bold]/db[/bold] when ready.[/dim]")

    console.print()
    success(console, "Setup complete!")
    console.print(
        "[dim]Type naturally to chat with the assistant, "
        "or use slash commands like /help.[/dim]\n"
    )


def prompt_model_setup_only(console: Console) -> None:
    """When DB connections exist but no model — just set up the model."""
    console.print(
        Panel(
            f"[{Color.WARN}]No AI model configured.[/{Color.WARN}]\n\n"
            f"[dim]Configure one to start chatting.[/dim]",
            border_style=Color.WARN,
            box=box.ROUNDED,
            padding=(0, 2),
        )
    )
    console.print()

    from payp.cli import _setup_new_provider
    from payp.cli.state import CommandCancelled
    try:
        _setup_new_provider()
    except CommandCancelled:
        console.print("[dim]Skipped. Run [bold]/models add[/bold] later.[/dim]")
    console.print()
