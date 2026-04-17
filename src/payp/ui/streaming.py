"""Streaming display for LLM responses.

Uses rich.live for token-by-token rendering with markdown support.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax

from payp.core.llm import StreamChunk


async def stream_response(
    chunks: AsyncIterator[StreamChunk],
    console: Console,
) -> tuple[str, list[dict] | None]:
    """Stream LLM response to terminal, return full content and any tool calls.

    Shows token-by-token output with rich formatting.
    Returns (full_content, tool_calls_or_none).
    """
    full_content = ""
    tool_calls = None

    with Live("", console=console, refresh_per_second=15, vertical_overflow="visible") as live:
        async for chunk in chunks:
            if chunk.content:
                full_content += chunk.content
                # Render as markdown for formatting
                live.update(Markdown(full_content))

            if chunk.tool_calls:
                tool_calls = chunk.tool_calls

    # Print final newline
    if full_content:
        console.print()

    return full_content, tool_calls


def display_tool_call(console: Console, tool_name: str, args: dict) -> None:
    """Display a tool call being executed — transparent workflow."""
    # Show what the LLM is doing
    args_summary = ", ".join(f"{k}={_truncate(str(v), 60)}" for k, v in args.items())
    console.print(f"  [dim]→ {tool_name}({args_summary})[/dim]")


def display_tool_result(console: Console, tool_name: str, success: bool, summary: str) -> None:
    """Display the result of a tool call."""
    from payp.ui.theme import Color
    icon = f"[{Color.BRAND_ALT}]✓[/{Color.BRAND_ALT}]" if success else "[red]✗[/red]"
    console.print(f"  {icon} [dim]{tool_name}: {_truncate(summary, 80)}[/dim]")


def display_retry(console: Console, error: str, fix: str) -> None:
    """Display an auto-retry attempt — transparent error recovery."""
    console.print(f"  [yellow]↻[/yellow] [dim]{error}. Retrying with {fix}...[/dim]")


def display_sql(console: Console, sql: str, reverse_sql: str | None = None) -> None:
    """Display generated SQL with syntax highlighting."""
    console.print(Panel(
        Syntax(sql.strip(), "sql", theme="monokai", word_wrap=True),
        title="Generated SQL",
        border_style="rgb(168,0,111)",
    ))
    if reverse_sql:
        console.print(Panel(
            Syntax(reverse_sql.strip(), "sql", theme="monokai", word_wrap=True),
            title="Reverse SQL",
            border_style="dim",
        ))


def display_usage_line(
    console: Console,
    input_tokens: int,
    output_tokens: int,
    cost: float,
    context_pct: float | None = None,
) -> None:
    """Show a right-aligned dim usage line after an LLM response."""
    parts: list[str] = []
    total = input_tokens + output_tokens
    if total > 0:
        parts.append(f"{_fmt_tokens(input_tokens)} in / {_fmt_tokens(output_tokens)} out")
    if cost > 0:
        parts.append(f"${cost:.4f}")
    if context_pct is not None:
        parts.append(f"ctx {context_pct:.0f}%")
    if not parts:
        return
    line = "  ".join(parts)
    console.print(f"[dim]{line}[/dim]", justify="right")


def _fmt_tokens(n: int) -> str:
    """Format token count: 340, 1.2k, 1.2M."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        v = n / 1000
        return f"{v:.1f}k" if v < 100 else f"{v:.0f}k"
    v = n / 1_000_000
    return f"{v:.1f}M"


def _truncate(s: str, max_len: int) -> str:
    """Truncate string with ellipsis."""
    return s[:max_len] + "..." if len(s) > max_len else s
