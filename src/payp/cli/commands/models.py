"""Slash commands: /models. Also exports _setup_new_provider for onboarding."""

from __future__ import annotations

import time
from typing import Any

import httpx
from rich.live import Live

from payp.cli.runtime import prompt_choice, prompt_confirm, prompt_required
from payp.cli.state import console
from payp.config import load_model_roles, load_models_config, save_models_config
from payp.models import ModelProvider

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"


def _cmd_models(args: str) -> None:
    """Manage AI model providers."""
    providers = load_models_config()
    roles = load_model_roles()
    parts = args.split() if args else []
    subcommand = parts[0].lower() if parts else ""

    if subcommand == "add":
        _setup_new_provider()
        return

    if subcommand in {"check", "balance"}:
        provider_name = parts[1].lower() if len(parts) > 1 else "openrouter"
        _check_provider(provider_name, providers)
        return

    if not providers:
        console.print("[yellow]No AI providers configured.[/yellow]")
        _setup_new_provider()
        return

    provider_names = list(providers.keys())

    # Direct access by name or number: /models 1  or  /models azure
    if args and subcommand not in {"add", "check", "balance"}:
        target_name = args
        if args.isdigit() and 1 <= int(args) <= len(provider_names):
            target_name = provider_names[int(args) - 1]
        provider = providers.get(target_name)
        if provider:
            _show_provider_details(target_name, provider)
            return
        console.print(f"[red]Provider '{target_name}' not found.[/red]")
        return

    from rich.table import Table
    from payp.ui.theme import Color

    table = Table(title=f"[{Color.BRAND}]AI Model Providers[/{Color.BRAND}]")
    table.add_column("#", style="dim")
    table.add_column("Provider", style="bold")
    table.add_column("Default Model")
    table.add_column("Status")

    for i, name in enumerate(provider_names, 1):
        provider = providers[name]
        status_cell = f"[{Color.BRAND_ALT}]✓ configured[/{Color.BRAND_ALT}]"
        table.add_row(str(i), name, provider.default_model or "—", status_cell)

    console.print(table)
    console.print(f"\nExecutor: [{Color.BRAND_ALT}]{roles.executor}[/{Color.BRAND_ALT}]")
    if roles.reviewer:
        console.print(f"Reviewer: [{Color.BRAND_ALT}]{roles.reviewer}[/{Color.BRAND_ALT}]")
    else:
        console.print("Reviewer: [dim]not set[/dim]")

    console.print(
        f"\n[dim]Enter provider number, name, [/dim]"
        f"[{Color.BRAND_ALT}]new[/{Color.BRAND_ALT}][dim] to add, or [/dim]"
        f"[red]del[/red][dim] to remove.[/dim]"
    )
    options: dict[str, str] = {"new": "__new__", "del": "__del__"}
    for i, name in enumerate(provider_names, 1):
        options[str(i)] = name
        options[name.lower()] = name
    action = prompt_choice("Select: ", options)

    if action == "__new__":
        _setup_new_provider()
    elif action == "__del__":
        _delete_provider(providers)
    else:
        _show_provider_details(action, providers[action])


def _show_provider_details(name: str, provider: ModelProvider) -> None:
    from rich.panel import Panel
    from payp.config import load_model_roles, save_models_config
    from payp.ui.theme import Color

    masked_key = provider.api_key
    if len(masked_key) > 12:
        masked_key = f"{masked_key[:4]}...{masked_key[-4:]}"

    lines = [
        f"Provider:       [{Color.BRAND_ALT}]{name}[/{Color.BRAND_ALT}]",
        f"Default model:  {provider.default_model or '—'}",
        f"Base URL:       {provider.base_url or '—'}",
    ]
    if provider.api_version:
        lines.append(f"API version:    {provider.api_version}")
    lines.append(f"API key:        {masked_key}")
    console.print(Panel("\n".join(lines), title="Provider Details", border_style=Color.BRAND))

    model_ref = f"{name}/{provider.default_model}" if provider.default_model else name
    roles = load_model_roles()

    console.print(
        f"\n[dim]Set role for[/dim] [{Color.BRAND_ALT}]{model_ref}[/{Color.BRAND_ALT}]:"
    )
    console.print(f"  [{Color.BRAND_ALT}]1.[/{Color.BRAND_ALT}] Executor")
    console.print(f"  [{Color.BRAND_ALT}]2.[/{Color.BRAND_ALT}] Reviewer")
    console.print(f"  [{Color.BRAND_ALT}]3.[/{Color.BRAND_ALT}] Both")
    console.print(f"  [{Color.BRAND_ALT}]4.[/{Color.BRAND_ALT}] None — just view")

    choice = prompt_choice(
        "Select: ",
        {"1": "executor", "2": "reviewer", "3": "both", "4": "none"},
    )

    if choice == "executor":
        roles.executor = model_ref
    elif choice == "reviewer":
        roles.reviewer = model_ref
    elif choice == "both":
        roles.executor = model_ref
        roles.reviewer = model_ref
    else:
        return

    providers = load_models_config()
    save_models_config(providers, roles)
    console.print(f"[{Color.BRAND_ALT}]Roles updated.[/{Color.BRAND_ALT}]")
    console.print(f"  Executor: {roles.executor}")
    console.print(f"  Reviewer: {roles.reviewer or '[dim]not set[/dim]'}")


def _delete_provider(providers: dict[str, ModelProvider]) -> None:
    """Interactive wizard to delete a saved provider."""
    from rich.table import Table
    from payp.config import load_model_roles, save_models_config
    from payp.models import ModelRoles
    from payp.ui.theme import Color

    if not providers:
        console.print("[yellow]No providers to delete.[/yellow]")
        return

    provider_names = list(providers.keys())
    if len(provider_names) == 1:
        target_name = provider_names[0]
    else:
        table = Table(title=f"[{Color.BRAND}]Delete Provider — Select[/{Color.BRAND}]")
        table.add_column("#", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Default Model")
        for i, name in enumerate(provider_names, 1):
            provider = providers[name]
            table.add_row(str(i), name, provider.default_model or "—")
        console.print(table)
        options: dict[str, str] = {}
        for i, name in enumerate(provider_names, 1):
            options[str(i)] = name
            options[name.lower()] = name
        try:
            target_name = prompt_choice("Select provider to delete: ", options)
        except (KeyboardInterrupt, EOFError):
            console.print("[dim]Cancelled.[/dim]")
            return

    provider = providers[target_name]
    model_str = f" ({provider.default_model or 'no model'})"
    if not prompt_confirm(f"Delete '{target_name}'{model_str}?", default=False):
        console.print("[dim]Cancelled.[/dim]")
        return

    del providers[target_name]
    roles = load_model_roles()
    # Clear roles that reference the deleted provider
    if roles.executor and (roles.executor == target_name or roles.executor.startswith(target_name + "/")):
        roles.executor = ModelRoles().executor
    if roles.reviewer and (roles.reviewer == target_name or roles.reviewer.startswith(target_name + "/")):
        roles.reviewer = None
    save_models_config(providers, roles)
    console.print(f"[{Color.BRAND_ALT}]Provider '{target_name}' deleted.[/{Color.BRAND_ALT}]")


def _type_print(text: str, duration_ms: int = 500) -> None:
    """Print text with a short typewriter effect."""
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


def _setup_new_provider(*, animate_intro: bool = False) -> None:
    """Interactive wizard to add a new AI provider."""
    from payp.ui.theme import Color
    if animate_intro:
        intro = (
            "\nAdd AI Provider\n\n"
            "  1. OpenRouter (recommended — one key, all models)\n"
            "  2. Anthropic (Claude)\n"
            "  3. OpenAI\n"
            "  4. Google (Gemini)\n"
            "  5. Ollama (local)\n"
            "  6. Azure"
        )
        _type_print(intro, duration_ms=500)
    else:
        console.print(f"\n[{Color.BRAND}]Add AI Provider[/{Color.BRAND}]\n")
        console.print(
            f"  [{Color.BRAND_ALT}]1.[/{Color.BRAND_ALT}] OpenRouter (recommended — one key, all models)"
        )
        console.print(f"  [{Color.BRAND_ALT}]2.[/{Color.BRAND_ALT}] Anthropic (Claude)")
        console.print(f"  [{Color.BRAND_ALT}]3.[/{Color.BRAND_ALT}] OpenAI")
        console.print(f"  [{Color.BRAND_ALT}]4.[/{Color.BRAND_ALT}] Google (Gemini)")
        console.print(f"  [{Color.BRAND_ALT}]5.[/{Color.BRAND_ALT}] Ollama (local)")
        console.print(f"  [{Color.BRAND_ALT}]6.[/{Color.BRAND_ALT}] Azure")

    provider_map: dict[str, tuple[str, str | None]] = {
        "1": ("openrouter", None),
        "2": ("anthropic", None),
        "3": ("openai", None),
        "4": ("gemini", None),
        "5": ("ollama", "http://localhost:11434"),
        "6": ("azure", None),
    }
    name, base_url = prompt_choice("Select: ", provider_map)

    if name == "ollama":
        url = prompt_required(f"Ollama URL [{base_url}]: ", default=base_url)
        provider = ModelProvider(api_key="ollama", base_url=url)
    elif name == "azure":
        api_key = prompt_required("Enter Azure API key: ", is_password=True)
        url = prompt_required(
            "Azure endpoint base URL (e.g. https://<resource>.openai.azure.com/): "
        )
        model_name = prompt_required("Deployment name (e.g. gpt-5.4): ")
        api_version = prompt_required("API version (e.g. 2024-12-01-preview): ")
        provider = ModelProvider(
            api_key=api_key, base_url=url,
            default_model=model_name, api_version=api_version,
        )
    elif name == "anthropic":
        api_key = prompt_required("Enter Anthropic API key: ", is_password=True)
        if prompt_confirm("Use custom base URL (e.g. for Azure-hosted Claude)?", default=False):
            url = prompt_required("Anthropic base URL: ")
            model_name = prompt_required("Model name (e.g. claude-sonnet-4-6): ")
            provider = ModelProvider(api_key=api_key, base_url=url, default_model=model_name)
        else:
            provider = ModelProvider(api_key=api_key)
    else:
        while True:
            api_key = prompt_required(f"Enter {name} API key: ", is_password=True)
            if name != "openrouter":
                provider = ModelProvider(api_key=api_key)
                break
            if _check_openrouter_key(api_key):
                provider = ModelProvider(api_key=api_key)
                break
            if not prompt_confirm(
                "OpenRouter key is unavailable. Try entering it again?", default=True
            ):
                console.print("[yellow]OpenRouter provider was not added.[/yellow]")
                return

    providers = load_models_config()
    old_provider = providers.get(name)
    providers[name] = provider
    roles = load_model_roles()

    # When reconfiguring an existing provider whose default model changed,
    # update any roles that still reference the old provider/model combo.
    if old_provider and old_provider.default_model and provider.default_model:
        old_ref = f"{name}/{old_provider.default_model}"
        new_ref = f"{name}/{provider.default_model}"
        if old_ref != new_ref:
            if roles.executor == old_ref:
                roles.executor = new_ref
            if roles.reviewer == old_ref:
                roles.reviewer = new_ref

    save_models_config(providers, roles)
    console.print(f"\n[{Color.BRAND_ALT}]{name} configured.[/{Color.BRAND_ALT}]")


def _check_provider(provider_name: str, providers: dict[str, ModelProvider]) -> None:
    """Check provider auth and credit metadata without running a model."""
    if provider_name != "openrouter":
        console.print(
            "[yellow]/models check currently supports only 'openrouter'.[/yellow]"
        )
        return

    provider = providers.get("openrouter")
    if not provider:
        console.print(
            "[yellow]OpenRouter is not configured. Run /models add and select OpenRouter.[/yellow]"
        )
        return

    key_ok = _check_openrouter_key(provider.api_key)
    if key_ok:
        return

    if not prompt_confirm("OpenRouter key is unavailable. Enter a new key now?", default=True):
        return

    new_key = prompt_required("Enter openrouter API key: ", is_password=True)
    providers["openrouter"] = ModelProvider(
        api_key=new_key,
        base_url=provider.base_url,
        default_model=provider.default_model,
    )
    roles = load_model_roles()
    save_models_config(providers, roles)
    console.print("[green]OpenRouter key updated.[/green]")
    _check_openrouter_key(new_key)


def _check_openrouter_key(api_key: str) -> bool:
    """Check OpenRouter key health and available balance/usage metadata."""
    from rich.table import Table

    from payp.ui.theme import Color

    console.print("[dim]Checking OpenRouter key via /key and /credits...[/dim]")

    key_status, key_payload, key_error = _openrouter_get("/key", api_key)
    table = Table(title=f"[{Color.BRAND}]OpenRouter API Key Status[/{Color.BRAND}]")
    table.add_column("Field", style="bold")
    table.add_column("Value")

    if key_status == 200 and isinstance(key_payload, dict):
        data = key_payload.get("data")
        key_data = data if isinstance(data, dict) else {}
        table.add_row("Key status", f"[{Color.BRAND_ALT}]working[/{Color.BRAND_ALT}]")
        table.add_row("Key label", _mask_label(key_data.get("label")))
        table.add_row("Key limit remaining", _format_limit(key_data.get("limit_remaining")))
        table.add_row("Usage (all time)", _format_money(key_data.get("usage")))
        table.add_row("Usage (UTC day)", _format_money(key_data.get("usage_daily")))
        table.add_row("Usage (UTC month)", _format_money(key_data.get("usage_monthly")))
    else:
        table.add_row("Key status", "[red]not working[/red]")
        if key_status is not None:
            table.add_row("HTTP status", str(key_status))
        if key_error:
            table.add_row("Error", key_error)
        console.print(table)
        console.print(
            "[dim]No model inference request was made, so this check spent 0 tokens.[/dim]"
        )
        return False

    credits_status, credits_payload, credits_error = _openrouter_get("/credits", api_key)
    if credits_status == 200 and isinstance(credits_payload, dict):
        data = credits_payload.get("data")
        credits_data = data if isinstance(data, dict) else {}
        total = _as_float(credits_data.get("total_credits"))
        used = _as_float(credits_data.get("total_usage"))
        if total is not None:
            table.add_row("Total credits", _format_money(total))
        if used is not None:
            table.add_row("Total usage", _format_money(used))
        if total is not None and used is not None:
            table.add_row("Balance", _format_money(total - used))
    else:
        if credits_status is not None:
            table.add_row("Credits endpoint", f"unavailable ({credits_status})")
        if credits_error:
            table.add_row("Credits note", credits_error)

    console.print(table)
    console.print(
        "[dim]No model inference request was made, so this check spent 0 tokens.[/dim]"
    )
    return True


def _openrouter_get(
    path: str, api_key: str
) -> tuple[int | None, dict[str, Any] | None, str | None]:
    """GET JSON from OpenRouter API, returning (status, payload, error_message)."""
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    url = f"{OPENROUTER_API_BASE}{path}"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=headers)
    except httpx.TimeoutException:
        return None, None, "Request timed out."
    except httpx.HTTPError as exc:
        return None, None, f"Network error: {exc}"

    payload: dict[str, Any] | None = None
    try:
        body = resp.json()
        if isinstance(body, dict):
            payload = body
    except ValueError:
        payload = None

    if resp.is_error:
        return resp.status_code, payload, _extract_error_message(resp.text, payload)
    return resp.status_code, payload, None


def _extract_error_message(raw_text: str, payload: dict[str, Any] | None) -> str:
    """Extract the best available API error message."""
    if payload:
        err = payload.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
        if isinstance(err, str) and err.strip():
            return err.strip()
        msg = payload.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
    text = raw_text.strip()
    if not text:
        return "HTTP error."
    first_line = text.splitlines()[0].strip()
    return first_line[:180] if first_line else "HTTP error."


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _format_money(value: Any) -> str:
    numeric = _as_float(value)
    if numeric is None:
        return "—"
    return f"${numeric:,.2f}"


def _format_limit(value: Any) -> str:
    if value is None:
        return "unlimited"
    return _format_money(value)


def _str_or_dash(value: Any) -> str:
    return value if isinstance(value, str) and value else "—"


def _mask_label(value: Any) -> str:
    label = _str_or_dash(value)
    if label == "—" or len(label) <= 12:
        return label
    return f"{label[:6]}...{label[-4:]}"
