"""Slash commands: /schema, /stats."""

from __future__ import annotations

from typing import Any

from payp.cli.runtime import _run_async
from payp.cli.state import _state, console


def _cmd_schema(args: str) -> None:
    """Explore database schema."""
    from rich.panel import Panel
    from payp.db.introspection import format_t0_for_context, format_t1_for_context

    if not _state.get("active_connection"):
        console.print("[red]Not connected. Run /db first.[/red]")
        return

    mgr = _state.get("connection_manager")
    if not mgr or not mgr.is_connected:
        console.print("[yellow]Connection lost. Run /db to reconnect.[/yellow]")
        return

    if args == "--refresh":
        console.print("[dim]Refreshing schema cache...[/dim]")
        _run_async(_refresh_schema())
        return

    t0 = _state.get("t0")
    t1 = _state.get("t1")

    if args:
        _run_async(_show_table_schema(args))
        return

    if t0:
        console.print(Panel(format_t0_for_context(t0), title="Schema Overview", border_style="rgb(168,0,111)"))
    if t1:
        console.print(Panel(format_t1_for_context(t1), title="Tables", border_style="rgb(168,0,111)"))


async def _refresh_schema() -> None:
    """Re-run introspection and update cache."""
    from payp.db.cache import save_metadata, save_t0, save_t1
    from payp.db.introspection import discover_t0, discover_t1, get_db_metadata
    from payp.ui.theme import Color

    name = _state["active_connection"]
    mgr = _state["connection_manager"]

    t0 = await discover_t0(mgr)
    t1 = await discover_t1(mgr)
    meta = await get_db_metadata(mgr)

    save_t0(name, t0)
    save_t1(name, t1)
    save_metadata(name, meta)

    _state["t0"] = t0
    _state["t1"] = t1

    console.print(
        f"[{Color.BRAND_ALT}]Schema cache refreshed. {t0.total_tables} tables, {t0.view_count} views.[/{Color.BRAND_ALT}]"
    )


async def _show_table_schema(table_name: str) -> None:
    """Show T2 DDL for a specific table."""
    from payp.db.introspection import discover_t2
    from rich.syntax import Syntax

    mgr = _state["connection_manager"]

    if "." in table_name:
        schema, table = table_name.split(".", 1)
    else:
        schema = "public"
        table = table_name

    ddl = await discover_t2(mgr, schema, table)
    console.print(Syntax(ddl, "sql", theme="monokai"))


def _cmd_stats(args: str) -> None:
    """Profile a table — column-level statistics & data quality."""
    from payp.ui.theme import Color

    if not _state.get("active_connection"):
        console.print("[red]Not connected. Run /db first.[/red]")
        return
    mgr = _state.get("connection_manager")
    if not mgr or not mgr.is_connected:
        console.print("[yellow]Connection lost. Run /db to reconnect.[/yellow]")
        return
    if not args.strip():
        console.print(f"[{Color.BRAND_ALT}]Usage: /stats <table>  (or schema.table)[/{Color.BRAND_ALT}]")
        return

    arg = args.strip()
    if "." in arg:
        schema, table = arg.split(".", 1)
    else:
        schema, table = None, arg

    console.print(f"[dim]Profiling {table}...[/dim]")
    try:
        from payp.tools.stats import profile_table
        profile = _run_async(profile_table(mgr, table, schema))
    except Exception as e:
        console.print(f"[red]Stats failed: {e}[/red]")
        return

    if profile.get("error") and not profile.get("columns"):
        console.print(f"[red]{profile['error']}[/red]")
        return

    _render_stats_table(profile)


def _render_stats_table(profile: dict[str, Any]) -> None:
    """Render a profile dict as a rich table."""
    from rich import box
    from rich.panel import Panel
    from rich.table import Table
    from payp.ui.theme import Color

    total = profile.get("total_rows", 0)
    schema = profile.get("schema") or ""
    table_name = profile.get("table") or ""
    header = f"{schema}.{table_name}" if schema else table_name
    console.print(
        Panel(
            f"[bold]{header}[/bold]   [dim]total rows:[/dim] [{Color.BRAND_ALT}]{total:,}[/{Color.BRAND_ALT}]   "
            f"[dim]columns:[/dim] [{Color.BRAND_ALT}]{len(profile.get('columns', []))}[/{Color.BRAND_ALT}]",
            border_style="rgb(168,0,111)",
        )
    )

    t = Table(box=box.SIMPLE_HEAD, show_header=True, header_style=Color.BRAND_ALT, padding=(0, 1))
    t.add_column("column", style="bold")
    t.add_column("type", style="dim")
    t.add_column("nulls", justify="right")
    t.add_column("null%", justify="right")
    t.add_column("distinct", justify="right")
    t.add_column("min / avg_len", justify="right")
    t.add_column("max / max_len", justify="right")
    t.add_column("avg / p50 / p95", justify="right")
    t.add_column("top values", overflow="fold")

    def _fmt(v: Any) -> str:
        if v is None:
            return "-"
        if isinstance(v, float):
            if abs(v) >= 1000 or v != v:
                return f"{v:,.2f}"
            return f"{v:.2f}"
        if isinstance(v, int):
            return f"{v:,}"
        s = str(v)
        return s if len(s) <= 24 else s[:21] + "..."

    for c in profile.get("columns", []):
        if c.get("skipped"):
            t.add_row(
                c["column"], c["data_type"], "-", "-", "-", "-", "-", "-",
                f"[dim italic]{c['skipped']}[/dim italic]",
            )
            continue

        kind = c.get("kind", "")
        nulls = c.get("null_count")
        null_pct = c.get("null_percent")
        distinct = c.get("distinct_count")

        if kind == "numeric":
            min_col = _fmt(c.get("min"))
            max_col = _fmt(c.get("max"))
            avg_parts = [_fmt(c.get("avg")), _fmt(c.get("p50")), _fmt(c.get("p95"))]
            avg_col = " / ".join(avg_parts)
        elif kind == "text":
            min_col = _fmt(c.get("avg_length"))
            max_col = _fmt(c.get("max_length"))
            avg_col = "-"
        elif kind == "date":
            min_col = _fmt(c.get("min"))
            max_col = _fmt(c.get("max"))
            avg_col = "-"
        else:
            min_col = max_col = avg_col = "-"

        top = c.get("top_values") or []
        if top:
            top_str = ", ".join(f"{_fmt(v['value'])}({v['count']:,})" for v in top[:5])
        else:
            top_str = "-"

        distinct_str = _fmt(distinct)
        if c.get("distinct_sampled"):
            distinct_str = f"~{distinct_str}"

        err = c.get("error")
        col_name = c["column"] + (" [red](!)[/red]" if err else "")

        t.add_row(
            col_name,
            str(c.get("data_type", ""))[:18],
            _fmt(nulls),
            f"{null_pct:.1f}%" if null_pct is not None else "-",
            distinct_str,
            min_col,
            max_col,
            avg_col,
            top_str,
        )

    console.print(t)
    console.print(
        "[dim]legend: numeric cols show min/max/avg; text cols show avg_len/max_len; "
        "~ prefix = sampled distinct count[/dim]"
    )
