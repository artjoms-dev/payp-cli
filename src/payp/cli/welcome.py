"""Startup welcome screen for the payp CLI."""

from __future__ import annotations

from payp import __version__
from payp.cli.state import console, get_config
from payp.config import load_model_roles, load_models_config


def _show_welcome() -> None:
    from payp.storage.snapshots import snapshot_count
    from payp.ui.dashboard import render_compact_hint, render_status_dashboard
    from payp.ui.onboarding import (
        is_first_run,
        prompt_model_setup_only,
        run_onboarding,
        should_prompt_model_setup,
    )

    # True first-run: no models AND no connections → full onboarding
    if is_first_run():
        run_onboarding(console)
        return

    # Returning user but no model → prompt just for model
    if should_prompt_model_setup():
        prompt_model_setup_only(console)

    config = get_config()

    # Resolve model info
    roles = load_model_roles()
    providers = load_models_config()
    model_name: str | None = roles.executor if providers else None
    model_provider: str | None = None
    if model_name:
        parts = model_name.split("/")
        if len(parts) > 1:
            model_provider = parts[0]
            model_name = "/".join(parts[1:])
    reviewer_name: str | None = roles.reviewer if providers and roles.reviewer else None
    if reviewer_name:
        parts = reviewer_name.split("/")
        if len(parts) > 1:
            reviewer_name = "/".join(parts[1:])

    render_status_dashboard(
        console=console,
        version=__version__,
        model_name=model_name,
        model_provider=model_provider,
        reviewer_name=reviewer_name,
        connection_name=None,
        connection_status=None,
        mode=config.default_mode.value,
        snapshot_count=snapshot_count(),
    )

    render_compact_hint(console)

    # Legacy knowledge dir notice — wording depends on whether migration
    # has already happened. If global has data, the legacy dir is just dead
    # weight and the message is softer.
    from payp.storage.knowledge import (
        get_knowledge_dir,
        has_legacy_knowledge,
        list_knowledge_files,
    )
    if has_legacy_knowledge():
        global_populated = bool(list_knowledge_files()) if get_knowledge_dir().exists() else False
        if global_populated:
            console.print(
                "[dim]ℹ Legacy [/dim][dim]./payp/knowledge/[/dim][dim] still on disk "
                "(already migrated). Ask me to clean it up or run "
                "[/dim][dim]/knowledge migrate-legacy[/dim][dim] again.[/dim]"
            )
        else:
            console.print(
                "[yellow]⚠[/yellow] Found legacy [dim]./payp/knowledge/[/dim] — "
                "knowledge is now global. Run [bold]/knowledge migrate-legacy[/bold] to move it."
            )
