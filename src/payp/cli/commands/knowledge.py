"""Slash command: /knowledge."""

from __future__ import annotations

from payp.cli.runtime import _run_async
from payp.cli.state import _state, console


def _cmd_knowledge(args: str = "") -> None:
    """Browse / export / import the knowledge base.

    Usage:
      /knowledge                       — interactive browser
      /knowledge export [connection] [path]   — dump to markdown files
      /knowledge import [path] [connection]   — read markdown files in
      /knowledge migrate-legacy        — move ./payp/knowledge -> ~/.payp/knowledge
    """
    from pathlib import Path

    from payp.memory.manager import (
        export_knowledge,
        get_memory_backend,
        import_knowledge,
    )
    from payp.storage.knowledge import (
        has_legacy_knowledge,
        migrate_legacy_to_global,
    )
    from payp.ui.selector import SelectorAction, SelectorItem, interactive_select
    from payp.ui.theme import Color, PTColor

    parts = args.strip().split() if args.strip() else []
    subcommand = parts[0].lower() if parts else ""

    if subcommand == "export":
        conn_arg = parts[1] if len(parts) > 1 else None
        path_arg = None
        if conn_arg and ("/" in conn_arg or conn_arg.startswith(".") or conn_arg.startswith("~")):
            path_arg = conn_arg
            conn_arg = None
        if len(parts) > 2:
            path_arg = parts[2]
        target = path_arg or "./payp/knowledge"
        try:
            result = _run_async(export_knowledge(target, connection=conn_arg))
        except Exception as e:
            console.print(f"[red]Export failed: {e}[/red]")
            return
        target_abs = Path(result["target"])
        console.print(
            f"[green]✓[/green] Exported [bold]{result['exported']}[/bold] "
            f"table(s) from [cyan]{result['backend']}[/cyan]"
        )
        console.print(f"  [dim]Location:[/dim] {target_abs}")
        if result["exported"]:
            for fp in result["files"][:5]:
                try:
                    rel = Path(fp).relative_to(target_abs)
                except ValueError:
                    rel = Path(fp)
                console.print(f"    [dim]- {rel}[/dim]")
            if len(result["files"]) > 5:
                console.print(f"    [dim]  ... +{len(result['files']) - 5} more[/dim]")

            try:
                rel_to_cwd = target_abs.relative_to(Path.cwd())
                git_add = f"git add {rel_to_cwd}"
            except ValueError:
                git_add = f"git add {target_abs}"
            console.print(
                f"  [dim]Commit & share:[/dim] [dim]{git_add} "
                f"&& git commit -m 'share db knowledge'[/dim]"
            )
        return

    if subcommand == "import":
        path_arg = parts[1] if len(parts) > 1 else "./payp/knowledge"
        conn_arg = parts[2] if len(parts) > 2 else None
        try:
            result = _run_async(import_knowledge(path_arg, connection=conn_arg))
        except Exception as e:
            console.print(f"[red]Import failed: {e}[/red]")
            return
        msg = (
            f"[green]✓[/green] Imported [bold]{result['imported']}[/bold] "
            f"table(s) into [cyan]{result['backend']}[/cyan]"
        )
        if result.get("replaced"):
            msg += f" [dim]({result['replaced']} replaced)[/dim]"
        console.print(msg)
        if result["errors"]:
            console.print(f"[yellow]{len(result['errors'])} error(s):[/yellow]")
            for err in result["errors"][:5]:
                console.print(f"  [dim]- {err}[/dim]")
        return

    if subcommand in ("migrate-legacy", "migrate"):
        if not has_legacy_knowledge():
            console.print("[dim]No legacy ./payp/knowledge/ to migrate.[/dim]")
            return
        result = migrate_legacy_to_global()
        console.print(
            f"[green]✓[/green] Moved [bold]{result['migrated']}[/bold] file(s) "
            f"from [dim]{result['src']}[/dim] → [dim]{result['dst']}[/dim]"
        )
        if result.get("skipped"):
            console.print(
                f"[yellow]{result['skipped']}[/yellow] file(s) already existed "
                "in global — merged with a separator marker."
            )
        console.print(
            "[dim]Safe to [bold]rm -rf ./payp/knowledge[/bold] now if you want.[/dim]"
        )
        return

    # Default: interactive browser
    backend = get_memory_backend()
    backend_name = backend.name

    show_all = subcommand in ("all", "*")
    active_conn = None if show_all else _state.get("active_connection")

    try:
        entries = _run_async(backend.list_all(connection=active_conn))
    except Exception as e:
        console.print(f"[red]Failed to list knowledge: {e}[/red]")
        return

    if not entries:
        scope_hint = f" for [cyan]{active_conn}[/cyan]" if active_conn else ""
        console.print(
            f"[dim]No knowledge entries{scope_hint}. (backend: {backend_name})[/dim]\n"
            "[dim]Knowledge is saved via propose_knowledge after user approval.[/dim]"
        )
        if active_conn:
            console.print("[dim]Use [bold]/knowledge all[/bold] to list every connection.[/dim]")
        return

    items = []
    for e in entries:
        conn = e.get("connection", "?")
        name = e.get("name", "?")
        obj_type = e.get("type", "?")
        if obj_type == "mempalace_drawer":
            drawers = e.get("drawers", 0)
            desc = f"{drawers} drawer{'s' if drawers != 1 else ''}"
            label = f"{conn}/{name}"
        else:
            size = e.get("size", 0)
            desc = f"{size / 1024:.1f}KB" if size >= 1024 else f"{size}B"
            label = f"{conn}/{obj_type}/{name}"
        items.append(SelectorItem(label=label, value=e, description=desc))

    deleted: list[tuple[str, str]] = []
    failed: list[tuple[str, str, str]] = []

    def _delete(item: SelectorItem) -> bool:
        ei = item.value
        conn = ei.get("connection", "")
        table = ei.get("name", "")
        try:
            ok = _run_async(backend.delete(conn, table))
        except Exception as exc:
            failed.append((conn, table, str(exc)))
            return False
        if ok:
            deleted.append((conn, table))
            return True
        return False

    scope_label = f" • {active_conn}" if active_conn else " • all"
    kb_title: list[tuple[str, str]] = [
        (PTColor.BRAND, f"Knowledge Base ({len(items)} entries){scope_label} — backend: {backend_name}"),  # noqa: E501
    ]
    result = interactive_select(
        console=console,
        title=kb_title,
        items=items,
        visible=8,
        actions=[SelectorAction(key="d", label="delete", callback=_delete)],
    )

    for conn, table in deleted:
        console.print(f"  [red]Deleted[/red] {conn}/{table}")
    for conn, table, err in failed:
        console.print(f"  [red]Delete failed[/red] {conn}/{table}: [dim]{err}[/dim]")

    if result.action == "select" and result.item:
        ei = result.item.value
        conn = ei.get("connection", "")
        table = ei.get("name", "")
        try:
            content = _run_async(backend.read(conn, table))
        except Exception as ex:
            console.print(f"[red]Read failed: {ex}[/red]")
            return
        if content:
            from rich.markdown import Markdown
            from rich.panel import Panel
            subtitle = ei.get("file") or f"{backend_name}://{conn}/{table}"
            console.print(Panel(
                Markdown(content),
                title=f"[{Color.BRAND_ALT}]{conn}/{table}[/{Color.BRAND_ALT}]",
                subtitle=str(subtitle),
                border_style="rgb(168,0,111)",
            ))
