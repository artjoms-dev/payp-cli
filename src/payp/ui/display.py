"""Smart result display — compact horizontal tables with column hiding.

Rules:
- Always use rich Table (horizontal layout)
- Hide low-priority columns when too wide (timestamps, long IDs)
- Show "+ N more columns" hint when hidden
- Truncate long cell values
- Color-code by type (NULL, numbers, booleans)
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from rich.console import Console
from rich.table import Table


# Column name patterns to deprioritize when table is too wide
LOW_PRIORITY_COLUMNS = {
    "created_at", "updated_at", "deleted_at",
    "created_on", "updated_on", "modified_at",
    "timestamp", "ts", "modified", "inserted_at",
}

MAX_COLS_VISIBLE = 6       # Max columns to show before hiding
MAX_CELL_WIDTH = 30        # Truncate cells longer than this
MAX_TABLE_WIDTH = 120      # Soft limit on total width


def display_query_result(
    console: Console,
    columns: list[str],
    rows: list[list[Any]],
    execution_ms: int,
    truncated: bool = False,
    total_rows: int | None = None,
) -> None:
    """Compact table display with smart column handling."""
    if not columns and not rows:
        console.print("  [dim]No rows returned.[/dim]")
        return

    row_count = len(rows)

    # Single value — show inline
    if row_count == 1 and len(columns) == 1:
        val = _format_value(rows[0][0])
        console.print(f"  [bold]{columns[0]}[/bold]: {val} [dim]({execution_ms}ms)[/dim]")
        return

    # Select which columns to show
    visible_cols, hidden_cols = _select_visible_columns(columns, rows)

    # Build caption
    caption_parts = [f"{row_count} row{'s' if row_count != 1 else ''}"]
    caption_parts.append(f"{execution_ms}ms")
    if hidden_cols:
        caption_parts.append(f"+{len(hidden_cols)} hidden: {', '.join(hidden_cols[:3])}{'...' if len(hidden_cols) > 3 else ''}")
    if truncated:
        caption_parts.append("truncated")
    caption = " • ".join(caption_parts)

    table = Table(
        show_header=True,
        header_style="bold rgb(180,224,76)",
        caption=caption,
        caption_style="dim",
        caption_justify="left",
        row_styles=["", "dim"],  # alternating row style
        padding=(0, 1),
    )

    col_indices = [columns.index(c) for c in visible_cols]
    for col in visible_cols:
        table.add_column(col, overflow="fold")

    for row in rows:
        table.add_row(*[_format_value(row[i]) for i in col_indices])

    console.print(table)


def display_dml_result(
    console: Console,
    rows_affected: int,
    execution_ms: int,
) -> None:
    """Display result of a DDL/DML operation."""
    console.print(
        f"  [bold rgb(180,224,76)]✓[/bold rgb(180,224,76)] {rows_affected} row(s) affected [dim]({execution_ms}ms)[/dim]"
    )


def _select_visible_columns(
    columns: list[str], rows: list[list[Any]]
) -> tuple[list[str], list[str]]:
    """Pick which columns to show. Returns (visible, hidden)."""
    if len(columns) <= MAX_COLS_VISIBLE:
        return list(columns), []

    # Score each column by priority (higher = more important to show)
    scores: list[tuple[int, int, str]] = []  # (score, index, name)
    for i, col in enumerate(columns):
        score = 100
        col_lower = col.lower()

        # Deprioritize timestamp-like columns
        if col_lower in LOW_PRIORITY_COLUMNS or col_lower.endswith("_at"):
            score -= 50

        # Prioritize id, name, status-like columns (not email — often long)
        if col_lower in ("id", "name", "title", "status", "type", "key"):
            score += 30

        # Value columns (numbers, counts, amounts) — important
        if any(x in col_lower for x in ("count", "total", "sum", "amount", "price", "revenue", "qty", "quantity")):
            score += 20

        # Prioritize columns with varied short values
        sample = [row[i] for row in rows[:5] if row[i] is not None]
        if sample:
            avg_len = sum(len(str(v)) for v in sample) / len(sample)
            if avg_len > 25:
                score -= 30  # long text like emails, descriptions
            elif avg_len < 15:
                score += 10  # compact

        scores.append((score, i, col))

    # Sort by score desc, keep column order where scores tie
    scores.sort(key=lambda x: (-x[0], x[1]))
    keep_indices = sorted(i for _, i, _ in scores[:MAX_COLS_VISIBLE])

    visible = [columns[i] for i in keep_indices]
    hidden = [c for c in columns if c not in visible]
    return visible, hidden


def _format_value(val: Any) -> str:
    """Format a cell value for display."""
    if val is None:
        return "[dim]·[/dim]"
    if isinstance(val, bool):
        return "[bold rgb(180,224,76)]✓[/bold rgb(180,224,76)]" if val else "[red]✗[/red]"
    if isinstance(val, (datetime, date)):
        # Compact date format
        if isinstance(val, datetime):
            return val.strftime("%Y-%m-%d %H:%M")
        return val.strftime("%Y-%m-%d")
    if isinstance(val, float):
        return f"{val:,.2f}"
    if isinstance(val, int):
        return f"{val:,}" if val >= 1000 else str(val)

    s = str(val)
    if len(s) > MAX_CELL_WIDTH:
        s = s[:MAX_CELL_WIDTH - 1] + "…"
    return s
