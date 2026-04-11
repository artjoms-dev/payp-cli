"""Slash command: /memory."""

from __future__ import annotations

from payp.cli.loop import _log_memory_backend_to_session
from payp.cli.state import _state, console


def _cmd_memory(args: str) -> None:
    """Manage memory backend: status, switch, migrate."""
    from payp.memory.manager import VALID_BACKENDS, backend_status, switch_backend
    from payp.ui.theme import Color

    parts = args.strip().split() if args.strip() else []
    subcommand = parts[0] if parts else ""

    if subcommand == "switch":
        if len(parts) < 2:
            console.print(
                f"[dim]Usage: /memory switch <{'|'.join(VALID_BACKENDS)}> [all][/dim]"
            )
            return
        target = parts[1].lower()
        if target not in VALID_BACKENDS:
            console.print(f"[red]Unknown backend '{target}'. Valid: {', '.join(VALID_BACKENDS)}[/red]")
            return

        migrate_all = len(parts) >= 3 and parts[2].lower() in ("all", "*")
        active_conn = _state.get("active_connection") if not migrate_all else None

        current = backend_status(connection=active_conn)
        if current["name"] == target:
            console.print(f"[dim]Already using '{target}' backend.[/dim]")
            return

        if target == "mempalace":
            try:
                import importlib
                importlib.import_module("payp.memory.mempalace_bridge")
            except ImportError:
                console.print(
                    "[red]mempalace is not installed.[/red]\n"
                    "  [dim]Install with:  pip install mempalace[/dim]\n"
                    "  [dim]Then retry:    /memory switch mempalace[/dim]"
                )
                return

        migrate = False
        if current["entries"] > 0:
            from prompt_toolkit import prompt as pt_prompt

            scope_label = (
                f"{active_conn} (+ shared)" if active_conn else "all connections"
            )
            answer = pt_prompt(
                f"  Migrate {current['entries']} entries "
                f"[{scope_label}] from '{current['name']}' to '{target}'? [y/N] "
            ).strip().lower()
            migrate = answer in ("y", "yes")
            if not migrate_all and active_conn:
                console.print(
                    "  [dim]Tip: use [bold]/memory switch "
                    f"{target} all[/bold][dim] to migrate every connection.[/dim]"
                )

        try:
            result = switch_backend(target, migrate=migrate, connection=active_conn)
        except RuntimeError as exc:
            console.print(f"[red]{exc}[/red]")
            return

        _log_memory_backend_to_session(target)

        console.print(f"  [{Color.BRAND_ALT}]Switched[/{Color.BRAND_ALT}] {result['from']} -> {result['to']}")
        if result.get("migration"):
            stats = result["migration"]
            console.print(f"  Migrated: {stats.get('migrated', 0)} entries")
            if stats.get("errors"):
                for err in stats["errors"]:
                    console.print(f"  [red]Error:[/red] {err}")

        console.print("  [dim]Session continues — next knowledge read/write uses new backend.[/dim]")
        return

    if subcommand == "migrate":
        current = backend_status()
        other = "mempalace" if current["name"] == "native" else "native"
        console.print(f"[dim]Migrating from '{current['name']}' to '{other}'...[/dim]")
        try:
            result = switch_backend(other, migrate=True)
        except RuntimeError as exc:
            console.print(f"[red]{exc}[/red]")
            return
        if result.get("switched"):
            stats = result.get("migration", {})
            console.print(f"  Migrated {stats.get('migrated', 0)} entries to '{other}'")
            if stats.get("errors"):
                for err in stats["errors"]:
                    console.print(f"  [red]Error:[/red] {err}")
        else:
            console.print(f"[dim]{result.get('reason', 'No change.')}[/dim]")
        return

    # Default: status
    show_all = subcommand in ("all", "*")
    active_conn = None if show_all else _state.get("active_connection")
    status = backend_status(connection=active_conn)
    size = status.get("size", 0)
    if size >= 1024 * 1024:
        size_str = f"{size / (1024 * 1024):.1f} MB"
    elif size >= 1024:
        size_str = f"{size / 1024:.1f} KB"
    else:
        size_str = f"{size} B"

    from rich import box
    from rich.table import Table

    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column(style="dim", width=14)
    table.add_column(style=Color.BRAND_ALT)
    table.add_row("Backend", status["name"])
    table.add_row("Scope", active_conn or "all connections")
    table.add_row("Entries", str(status.get("entries", 0)))
    table.add_row("Size", size_str)
    table.add_row("Healthy", "[green]yes[/green]" if status.get("healthy") else "[red]no[/red]")
    console.print()
    console.print(table)
    console.print(
        "  [dim]Usage: /memory [all] | /memory switch <native|mempalace> | /memory migrate | /memory status[/dim]\n"
    )
