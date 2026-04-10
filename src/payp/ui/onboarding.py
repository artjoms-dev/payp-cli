"""First-run onboarding wizard — guides new users through initial setup.

Two steps:
  1. AI Model configuration
  2. Database connection
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_toolkit import prompt as pt_prompt
from rich import box
from rich.console import Console
from rich.panel import Panel

from payp.ui.theme import Color, section, success

if TYPE_CHECKING:
    pass


def is_first_run() -> bool:
    """Return True if user has no models AND no connections configured."""
    from payp.config import list_connections, load_models_config
    return not load_models_config() and not list_connections()


def should_prompt_model_setup() -> bool:
    """Return True if no models are configured."""
    from payp.config import load_models_config
    return not load_models_config()


def render_welcome_banner(console: Console) -> None:
    """Render the onboarding welcome banner."""
    banner = (
        f"[{Color.BRAND}]Welcome to payp[/{Color.BRAND}]\n\n"
        f"[dim]Your AI database assistant.[/dim]\n\n"
        f"Let's get you set up in 2 steps:\n"
        f"  [{Color.INFO}]1.[/{Color.INFO}] Configure an AI model\n"
        f"  [{Color.INFO}]2.[/{Color.INFO}] Connect a database"
    )
    console.print(
        Panel(
            banner,
            border_style=Color.BRAND,
            box=box.ROUNDED,
            padding=(1, 3),
        )
    )
    console.print()


def run_onboarding(console: Console) -> None:
    """Run the full first-run onboarding flow.

    Calls out to existing setup wizards for models and connections.
    """
    render_welcome_banner(console)

    # Step 1: Model
    section(console, "Step 1 of 2 — AI Model")
    console.print(
        "[dim]payp needs at least one AI model to generate SQL and "
        "analyze data.[/dim]\n"
    )

    # Import here to avoid circular imports
    from payp.cli import _setup_new_provider
    _setup_new_provider()

    console.print()

    # Step 2: Database
    section(console, "Step 2 of 2 — Database Connection")
    console.print(
        "[dim]Connect to a database now, or skip and run /db later.[/dim]\n"
    )

    choice = pt_prompt("Connect a database now? [Y/n]: ").strip().lower()
    if choice in ("", "y", "yes"):
        from payp.cli import _setup_new_connection
        _setup_new_connection()
    else:
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
    _setup_new_provider()
    console.print()
