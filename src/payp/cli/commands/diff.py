"""Slash command: /diff."""

from __future__ import annotations

from payp.cli.runtime import _run_async, command_prompt
from payp.cli.state import _state, console


def _cmd_diff(args: str) -> None:
    """Compare a table's schema between two active connections."""
    from payp.core.multi_connection import MultiConnectionManager
    from payp.tools.crossdb import CompareSchemasTool
    from payp.ui.selector import SelectorItem, interactive_select
    from payp.ui.theme import Color

    mc: MultiConnectionManager | None = _state.get("multi_conn_manager")  # type: ignore[assignment]
    if not mc:
        console.print("[yellow]No active connections. Use /db to connect first.[/yellow]")
        return

    active_names = mc.names()
    if len(active_names) < 2:
        console.print(
            "[yellow]/diff needs at least 2 active connections. "
            "Use /db to connect another.[/yellow]"
        )
        return

    parts = args.split() if args else []
    table: str | None = None
    conn_a: str | None = None
    conn_b: str | None = None

    if len(parts) >= 1:
        table = parts[0]
    if len(parts) >= 2:
        conn_a = parts[1]
    if len(parts) >= 3:
        conn_b = parts[2]

    for cn in (conn_a, conn_b):
        if cn and cn not in active_names:
            console.print(
                f"[red]Connection '{cn}' is not active.[/red] "
                f"Available: {', '.join(active_names)}"
            )
            return

    def _pick_connection(title: str, exclude: str | None = None) -> str | None:
        items = []
        for info in mc.list_info():
            name = info["name"]
            if name == exclude:
                continue
            desc = (
                f"{info['db_type']} • {info['host']} • {info['database']}"
                + (" • active" if info["is_active"] else "")
            )
            items.append(SelectorItem(label=name, value=name, description=desc))
        result = interactive_select(
            console=console, title=title, items=items, visible=5
        )
        if result.action != "select" or not result.item:
            return None
        return result.item.value  # type: ignore[no-any-return]

    if conn_a is None:
        if len(parts) == 1:
            conn_a = mc.active
            if not conn_a:
                conn_a = _pick_connection("Select connection A")
        else:
            conn_a = _pick_connection("Select connection A")
        if not conn_a:
            console.print("[dim]Cancelled.[/dim]")
            return

    if conn_b is None:
        conn_b = _pick_connection(
            f"Select connection B (comparing against {conn_a})", exclude=conn_a
        )
        if not conn_b:
            console.print("[dim]Cancelled.[/dim]")
            return

    if conn_a == conn_b:
        console.print("[red]Connection A and B must differ.[/red]")
        return

    if not table:
        try:
            table = command_prompt("Table name to compare: ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Cancelled.[/dim]")
            return
        if not table:
            console.print("[dim]Cancelled.[/dim]")
            return

    tool = CompareSchemasTool()
    console.print(f"[dim]Comparing {table}: {conn_a} vs {conn_b}...[/dim]")
    try:
        result = _run_async(
            tool.call(
                {"table": table, "connection_a": conn_a, "connection_b": conn_b},
                {"multi_conn": mc},
            )
        )
    except Exception as e:
        console.print(f"[red]Diff failed: {e}[/red]")
        return

    if not result.success:
        console.print(f"[red]{result.error}[/red]")
        return

    from rich.panel import Panel

    data = result.data or {}
    only_in_a = data.get("only_in_a", [])
    only_in_b = data.get("only_in_b", [])
    type_diffs = data.get("type_differences", [])
    shared = data.get("columns_in_both", 0)
    migration = data.get("migration_sql_to_sync_b_from_a")

    console.print()
    console.print(
        Panel.fit(
            f"[{Color.BRAND}]Comparing {table}[/{Color.BRAND}]: "
            f"[{Color.BRAND_ALT}]{conn_a}[/{Color.BRAND_ALT}] vs [{Color.BRAND_ALT}]{conn_b}[/{Color.BRAND_ALT}]",
            border_style="dim",
        )
    )

    if not only_in_a and not only_in_b and not type_diffs:
        console.print(f"[{Color.BRAND_ALT}]Schemas match — no differences.[/{Color.BRAND_ALT}]")
    else:
        from rich.table import Table as RTable
        diff_table = RTable(show_header=True, header_style=Color.BRAND_ALT)
        diff_table.add_column("Column")
        diff_table.add_column(f"In {conn_a}", justify="center")
        diff_table.add_column(f"In {conn_b}", justify="center")
        diff_table.add_column(f"Type ({conn_a})")
        diff_table.add_column(f"Type ({conn_b})")
        diff_table.add_column("Notes")

        chk = f"[{Color.BRAND_ALT}]✓[/{Color.BRAND_ALT}]"
        for col in only_in_a:
            diff_table.add_row(
                col["name"], chk, "[red]✗[/red]",
                col.get("type", ""), "", f"[yellow]only in {conn_a}[/yellow]",
            )
        for col in only_in_b:
            diff_table.add_row(
                col["name"], "[red]✗[/red]", chk,
                "", col.get("type", ""), f"[yellow]only in {conn_b}[/yellow]",
            )
        for td in type_diffs:
            diff_table.add_row(
                td["column"], chk, chk,
                td.get("type_a", ""), td.get("type_b", ""),
                f"[{Color.BRAND}]type differs[/{Color.BRAND}]",
            )
        console.print(diff_table)

    console.print(
        f"\n[dim]{shared} columns shared, "
        f"{len(only_in_a)} only in {conn_a}, "
        f"{len(only_in_b)} only in {conn_b}, "
        f"{len(type_diffs)} type differences[/dim]"
    )

    if migration:
        console.print()
        console.print(
            Panel(
                migration,
                title=f"[{Color.BRAND}]Migration SQL — sync {conn_b} from {conn_a}[/{Color.BRAND}]",
                border_style="rgb(168,0,111)",
            )
        )
    console.print()
