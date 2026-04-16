"""Slash commands: /compact, /more, /context, /cost."""

from __future__ import annotations

import re

from payp.cli.runtime import _run_async
from payp.cli.state import _state, console
from payp.models import DbType


def _cmd_compact() -> None:
    """Manually compact conversation history."""
    chat = _state.get("chat_session")
    if not chat:
        console.print("[dim]No active chat session.[/dim]")
        return
    if len(chat.messages) < 3:
        console.print("[dim]Nothing to compact yet.[/dim]")
        return

    console.print("[dim]Compacting conversation...[/dim]")
    try:
        stats = _run_async(chat.compact())
        if stats.get("saved_tokens", 0) == 0:
            console.print("[dim]No tokens saved (conversation too short).[/dim]")
    except Exception as e:
        console.print(f"[red]Compaction failed: {e}[/red]")


def _cmd_more() -> None:
    """Fetch the next 20 rows of the last SELECT query."""
    from payp.db.connection import ConnectionManager
    from payp.ui.display import display_query_result

    chat = _state.get("chat_session")
    if not chat or not getattr(chat, "last_select", None):
        console.print("[dim]No recent query to paginate. Run a SELECT first.[/dim]")
        return

    last = chat.last_select

    mgr: ConnectionManager | None = _state.get("connection_manager")  # type: ignore[assignment]
    if not mgr or not mgr.is_connected:
        console.print("[yellow]Connect to a database first with /db[/yellow]")
        return

    # Ensure we're on the same connection the query originally ran on
    if mgr.profile.name != last.get("connection"):
        console.print(
            f"[yellow]Last query was on '{last['connection']}' but active is "
            f"'{mgr.profile.name}'. Switch connections or run a fresh query.[/yellow]"
        )
        return

    offset = last.get("offset", 20)
    page_size = 20
    base_sql = last["sql"].rstrip(";").strip()

    # Strip existing LIMIT / FETCH FIRST / OFFSET clauses so we can add our own
    base_sql = re.sub(r"\s+LIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$", "", base_sql, flags=re.IGNORECASE)
    base_sql = re.sub(
        r"\s+(OFFSET\s+\d+\s+ROWS\s+)?FETCH\s+(FIRST|NEXT)\s+\d+\s+ROWS?\s+ONLY\s*$",
        "",
        base_sql,
        flags=re.IGNORECASE,
    )
    base_sql = re.sub(r"\s+OFFSET\s+\d+\s+ROWS?\s*$", "", base_sql, flags=re.IGNORECASE)
    base_sql = base_sql.rstrip().rstrip(";").strip()

    # Build paginated SQL — dialect-aware
    if mgr.profile.db_type == DbType.ORACLE:
        paged_sql = f"{base_sql} OFFSET {offset} ROWS FETCH NEXT {page_size} ROWS ONLY"
    else:
        paged_sql = f"{base_sql} LIMIT {page_size} OFFSET {offset}"

    console.print(f"[dim]/more → rows {offset + 1}–{offset + page_size}[/dim]")
    try:
        result = _run_async(mgr.execute(paged_sql, limit=page_size))
    except Exception as e:
        console.print(f"[red]Pagination failed: {e}[/red]")
        return

    if not result.columns or result.row_count == 0:
        console.print("[dim]No more rows.[/dim]")
        chat.last_select["truncated"] = False
        return

    display_query_result(
        console,
        result.columns,
        result.rows,
        result.execution_ms,
        truncated=result.truncated,
    )
    # Advance offset for the next /more
    chat.last_select["offset"] = offset + result.row_count
    chat.last_select["truncated"] = result.truncated


def _set_schema_budget(value_str: str) -> None:
    """Set the schema context token budget."""
    from payp.config import load_config, save_config
    from payp.ui.theme import Color

    if not value_str:
        config = load_config()
        console.print(f"  Schema budget: [{Color.BRAND_ALT}]{config.schema_budget:,} tokens[/{Color.BRAND_ALT}]")
        console.print("  [dim]Usage: /context budget 10k | /context budget 50000[/dim]")
        return

    # Parse "10k", "30K", "50000" etc
    raw = value_str.strip().lower().rstrip("tokens").strip()
    if raw.endswith("k"):
        try:
            tokens = int(float(raw[:-1]) * 1000)
        except ValueError:
            console.print(f"[red]Invalid value: '{value_str}'. Use e.g. 10k or 50000.[/red]")
            return
    else:
        try:
            tokens = int(raw)
        except ValueError:
            console.print(f"[red]Invalid value: '{value_str}'. Use e.g. 10k or 50000.[/red]")
            return

    if tokens < 1000:
        console.print("[red]Minimum budget is 1000 tokens (T0 needs room).[/red]")
        return

    config = load_config()
    config.schema_budget = tokens
    save_config(config)
    console.print(f"  Schema budget set to [{Color.BRAND_ALT}]{tokens:,} tokens[/{Color.BRAND_ALT}]")
    console.print("  [dim]Takes effect on next message.[/dim]")


def _cmd_context(args: str) -> None:
    """Show current context window usage or set schema budget."""
    parts = args.strip().split() if args.strip() else []

    if parts and parts[0].lower() == "budget":
        _set_schema_budget(parts[1] if len(parts) > 1 else "")
        return

    chat = _state.get("chat_session")
    if not chat:
        console.print("[dim]No active chat session.[/dim]")
        return

    # Rebuild system prompt to estimate
    try:
        system_prompt = _run_async(chat._get_system_prompt(user_text=""))
    except Exception:
        system_prompt = ""

    stats = chat.get_context_stats(system_prompt)
    pct = stats.usage_ratio * 100

    # Color by usage
    if pct < 50:
        color = "green"
    elif pct < 75:
        color = "yellow"
    else:
        color = "red"

    # Simple bar
    filled = int(stats.usage_ratio * 30)
    bar = "█" * filled + "░" * (30 - filled)

    console.print()
    from payp.ui.theme import Color
    console.print(f"  [{Color.BRAND}]Context Usage[/{Color.BRAND}]")
    console.print(f"  [{color}]{bar}[/{color}]  [{color}]{pct:.1f}%[/{color}]")
    console.print(
        f"  [dim]{stats.used_tokens:,} / {stats.max_tokens:,} tokens  •  "
        f"{stats.message_count} messages[/dim]"
    )
    if stats.should_compact:
        console.print("  [yellow]⚠ Auto-compaction will trigger on next message[/yellow]")
    console.print("  [dim]Run /compact to compress older messages now[/dim]")
    from payp.config import load_config
    budget = load_config().schema_budget
    console.print(f"  [dim]Schema budget: {budget:,} tokens  •  /context budget <N> to change[/dim]")
    console.print()


def _cmd_cost() -> None:
    """Show token usage and costs."""
    from payp.core.llm import LLMClient

    client: LLMClient | None = _state.get("llm_client")
    if not client:
        console.print("[dim]No LLM usage this session.[/dim]")
        return

    summary = client.get_cost_summary()
    from rich.table import Table

    from payp.ui.theme import Color
    table = Table(title=f"[{Color.BRAND}]Session Cost[/{Color.BRAND}]")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Input tokens", f"{summary['input_tokens']:,}")
    table.add_row("Output tokens", f"{summary['output_tokens']:,}")
    table.add_row("Total tokens", f"{summary['total_tokens']:,}")
    table.add_row("Queries", str(summary["query_count"]))
    table.add_row("Estimated cost", f"${summary['total_cost_usd']:.4f}")

    console.print(table)
