"""Slash commands: /models. Also exports _setup_new_provider for onboarding."""

from __future__ import annotations

from payp.cli.runtime import command_prompt
from payp.cli.state import console
from payp.config import load_model_roles, load_models_config, save_models_config
from payp.models import ModelProvider


def _cmd_models(args: str) -> None:
    """Manage AI model providers."""
    providers = load_models_config()
    roles = load_model_roles()

    if args == "add":
        _setup_new_provider()
        return

    if not providers:
        console.print("[yellow]No AI providers configured.[/yellow]")
        _setup_new_provider()
        return

    from rich.table import Table

    from payp.ui.theme import Color
    table = Table(title=f"[{Color.BRAND}]AI Model Providers[/{Color.BRAND}]")
    table.add_column("Provider", style="bold")
    table.add_column("Default Model")
    table.add_column("Status")

    for name, provider in providers.items():
        status_cell = f"[{Color.BRAND_ALT}]✓ configured[/{Color.BRAND_ALT}]"
        table.add_row(name, provider.default_model or "—", status_cell)

    console.print(table)
    console.print(f"\nExecutor: [{Color.BRAND_ALT}]{roles.executor}[/{Color.BRAND_ALT}]")
    if roles.reviewer:
        console.print(f"Reviewer: [{Color.BRAND_ALT}]{roles.reviewer}[/{Color.BRAND_ALT}]")
    else:
        console.print("Reviewer: [dim]not set[/dim]")


def _setup_new_provider() -> None:
    """Interactive wizard to add a new AI provider."""
    from payp.ui.theme import Color
    console.print(f"\n[{Color.BRAND}]Add AI Provider[/{Color.BRAND}]\n")
    console.print(
        f"  [{Color.BRAND_ALT}]1.[/{Color.BRAND_ALT}] OpenRouter (recommended — one key, all models)"  # noqa: E501
    )
    console.print(f"  [{Color.BRAND_ALT}]2.[/{Color.BRAND_ALT}] Anthropic (Claude)")
    console.print(f"  [{Color.BRAND_ALT}]3.[/{Color.BRAND_ALT}] OpenAI")
    console.print(f"  [{Color.BRAND_ALT}]4.[/{Color.BRAND_ALT}] Google (Gemini)")
    console.print(f"  [{Color.BRAND_ALT}]5.[/{Color.BRAND_ALT}] Ollama (local)")

    choice = command_prompt("Select: ").strip()
    provider_map = {
        "1": ("openrouter", None),
        "2": ("anthropic", None),
        "3": ("openai", None),
        "4": ("gemini", None),
        "5": ("ollama", "http://localhost:11434"),
    }

    if choice not in provider_map:
        console.print("[red]Invalid selection.[/red]")
        return

    name, base_url = provider_map[choice]

    if name == "ollama":
        url = command_prompt(f"Ollama URL [{base_url}]: ").strip() or base_url
        provider = ModelProvider(api_key="ollama", base_url=url)
    else:
        api_key = command_prompt(f"Enter {name} API key: ", is_password=True).strip()
        if not api_key:
            console.print("[red]API key required.[/red]")
            return
        provider = ModelProvider(api_key=api_key)

    providers = load_models_config()
    providers[name] = provider
    roles = load_model_roles()
    save_models_config(providers, roles)
    console.print(f"\n[{Color.BRAND_ALT}]{name} configured.[/{Color.BRAND_ALT}]")
