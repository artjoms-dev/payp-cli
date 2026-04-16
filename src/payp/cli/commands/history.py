"""Slash command: /history."""

from __future__ import annotations

from payp.cli.state import _state, console


def _cmd_history(args: str) -> None:
    """Show recent transaction log entries."""
    from rich.table import Table

    from payp.storage.transaction_log import count_transactions, query_history
    from payp.ui.theme import Color

    active = _state.get("active_connection")
    rows = query_history(connection_name=active, limit=20)

    if not rows:
        console.print("[dim]No transactions logged yet.[/dim]")
        return

    total = count_transactions(connection_name=active)
    title = f"Transaction History ({len(rows)} of {total})"
    if active:
        title += f" — {active}"

    table = Table(title=f"[{Color.BRAND}]{title}[/{Color.BRAND}]")
    table.add_column("Time", style="dim")
    table.add_column("Op", style="bold")
    table.add_column("Mode")
    table.add_column("By")
    table.add_column("Status")
    table.add_column("Rows")
    table.add_column("SQL", max_width=50)

    for r in rows:
        ts = r["timestamp"].split("T")[1][:8] if "T" in r["timestamp"] else r["timestamp"]
        status_color = (
            Color.BRAND_ALT if r["status"] == "success"
            else ("yellow" if r["status"] == "cancelled" else "red")
        )
        rows_str = str(r["rows_affected"]) if r["rows_affected"] is not None else "-"
        sql_preview = r["sql_executed"][:80].replace("\n", " ")
        table.add_row(
            ts,
            r["operation_type"],
            r["execution_mode"],
            r["approved_by"] or "-",
            f"[{status_color}]{r['status']}[/{status_color}]",
            rows_str,
            sql_preview,
        )

    console.print(table)
