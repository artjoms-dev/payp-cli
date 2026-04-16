"""Startup welcome screen for the payp CLI."""

from __future__ import annotations

from payp import __version__
from payp.cli.state import _state, console, get_config
from payp.config import load_model_roles, load_models_config


class _RowCountingFile:
    """Wraps a file-like object and counts rows (newlines) written through it.

    Used during welcome rendering to compute how many rows sit between the
    future REPL prompt line and the banner's top row, so the background
    BannerAnimator knows how many lines to cursor-up before redrawing.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.rows = 0

    def write(self, s: str) -> int:
        self.rows += s.count("\n")
        return self._inner.write(s)

    def flush(self) -> None:
        self._inner.flush()

    def isatty(self) -> bool:
        try:
            return self._inner.isatty()
        except Exception:
            return False

    def fileno(self) -> int:
        return self._inner.fileno()

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def _show_welcome() -> None:
    from payp.ui.dashboard import BannerAnimator
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

    # Swap console.file for a row counter so we know exactly how many rows
    # sit between the REPL prompt line and the banner's top row. All writes
    # still hit the real terminal; we just tally newlines on the way through.
    original_file = console.file
    counter = _RowCountingFile(original_file)
    console.file = counter  # type: ignore[assignment]
    try:
        _render_welcome_body()
    finally:
        console.file = original_file  # type: ignore[assignment]

    # Start the continuous banner shimmer. Animator cursor-ups `counter.rows`
    # lines from the prompt to reach the banner top row. It will be stopped
    # by the REPL on first user submit.
    animator = BannerAnimator(console, rows_above=counter.rows)
    if animator.start():
        _state["banner_anim"] = animator


def _render_welcome_body() -> None:
    """Print the full welcome (dashboard, hint, docker suggestion, legacy notice)."""
    from payp.storage.snapshots import snapshot_count
    from payp.ui.dashboard import render_compact_hint, render_status_dashboard

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

    _maybe_suggest_docker_scan()

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


def _maybe_suggest_docker_scan() -> None:
    """If the user has no saved connections, offer to import one from Docker.

    Runs `docker ps` with a short timeout; silently no-ops when docker is
    missing, the daemon is down, or no DB containers are running.
    """
    from payp.cli.runtime import _run_async
    from payp.config import list_connections

    if list_connections():
        return

    try:
        from payp.db.docker_scan import list_db_containers
        detected = _run_async(list_db_containers(timeout=0.8))
    except Exception:
        return
    if not detected:
        return

    from payp.ui.theme import Color

    console.print(
        f"\n[{Color.BRAND}]Found database containers running in Docker:[/{Color.BRAND}]"
    )
    for i, d in enumerate(detected, 1):
        auth = "[dim](pw from env)[/dim]" if d.password else "[dim](no pw in env)[/dim]"
        console.print(
            f"  [dim]{i}.[/dim] [bold]{d.container_name}[/bold] "
            f"[dim]({d.db_type.value}, {d.host}:{d.port}/{d.database})[/dim] {auth}"
        )
    console.print(
        f"\n[dim]Run [/dim][{Color.BRAND_ALT}]/db scan docker[/{Color.BRAND_ALT}]"
        f"[dim] to save one of these and connect, or [/dim]"
        f"[{Color.BRAND_ALT}]/db[/{Color.BRAND_ALT}][dim] to configure manually.[/dim]"
    )

