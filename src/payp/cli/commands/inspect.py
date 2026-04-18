"""`/inspect` — dump the next outgoing LLM request to a file for analysis.

Captures exactly what payp would send on the next turn: the split system
prompt (static + dynamic), tool schemas, and conversation history. Writes
the full payload as JSON plus a human-readable section breakdown with
per-section token counts, so the user can paste either into an LLM for
a token-diet review.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from payp.cli.runtime import _run_async
from payp.cli.state import _state, console


def _count(messages: list[dict[str, Any]], model: str) -> int:
    from payp.core.compaction import count_tokens
    return count_tokens(messages, model)


def _tool_tokens(tool_def: Any, model: str) -> int:
    """Serialize a tool definition the way litellm sends it, then count."""
    from payp.core.llm import _tool_to_dict
    payload = _tool_to_dict(tool_def)
    return _count([{"role": "system", "content": json.dumps(payload)}], model)


def _cmd_inspect(args: str) -> None:
    chat = _state.get("chat_session")
    if not chat:
        console.print("[dim]No active chat session.[/dim]")
        return

    client = _state.get("llm_client")
    if not client:
        console.print("[yellow]No LLM client — run /models first.[/yellow]")
        return

    model = client.get_executor_model()

    try:
        sp = _run_async(chat._get_system_prompt(user_text=""))
    except Exception as e:
        console.print(f"[red]Failed to build system prompt: {e}[/red]")
        return

    static_msg = [{"role": "system", "content": sp.static}]
    dynamic_msg = [{"role": "system", "content": sp.dynamic}]
    history = list(chat.messages)

    static_tok = _count(static_msg, model)
    dynamic_tok = _count(dynamic_msg, model)
    history_tok = _count(history, model) if history else 0

    tools = chat.registry.core_definitions()
    tool_rows: list[tuple[str, int, int]] = []
    tools_total = 0
    for t in tools:
        name = getattr(t, "name", "?")
        desc = getattr(t, "description", "") or ""
        t_tok = _tool_tokens(t, model)
        tool_rows.append((name, len(desc), t_tok))
        tools_total += t_tok
    tool_rows.sort(key=lambda r: -r[2])

    grand_total = static_tok + dynamic_tok + tools_total + history_tok

    payload = {
        "model": model,
        "system_static": sp.static,
        "system_dynamic": sp.dynamic,
        "tools": [
            {
                "name": getattr(t, "name", "?"),
                "description": getattr(t, "description", ""),
                "parameters": t.get_parameters_schema() if hasattr(t, "get_parameters_schema") else None,
            }
            for t in tools
        ],
        "messages": history,
        "token_counts": {
            "system_static": static_tok,
            "system_dynamic": dynamic_tok,
            "tools_total": tools_total,
            "history": history_tok,
            "grand_total": grand_total,
            "by_tool": [{"name": n, "tokens": tok, "desc_chars": c} for n, c, tok in tool_rows],
        },
    }

    out_path = Path(args.strip()) if args.strip() else Path(tempfile.gettempdir()) / "payp_inspect.json"
    try:
        out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        console.print(f"[red]Could not write {out_path}: {e}[/red]")
        return

    from rich.table import Table

    from payp.ui.theme import Color

    table = Table(title=f"[{Color.BRAND}]Outgoing prompt breakdown[/{Color.BRAND}] ({model})")
    table.add_column("Section", style="bold")
    table.add_column("Tokens", justify="right")
    table.add_column("% of total", justify="right")
    for label, tok in (
        ("system_static", static_tok),
        ("system_dynamic", dynamic_tok),
        ("tools (all)", tools_total),
        ("history", history_tok),
    ):
        pct = (tok / grand_total * 100) if grand_total else 0.0
        table.add_row(label, f"{tok:,}", f"{pct:.1f}%")
    table.add_row("[bold]TOTAL[/bold]", f"[bold]{grand_total:,}[/bold]", "100.0%")
    console.print(table)

    if tool_rows:
        t2 = Table(title="Top tools by token weight")
        t2.add_column("Tool", style="bold")
        t2.add_column("Tokens", justify="right")
        t2.add_column("Desc chars", justify="right")
        for name, chars, tok in tool_rows[:10]:
            t2.add_row(name, f"{tok:,}", f"{chars:,}")
        console.print(t2)

    console.print(f"  [dim]Full payload (JSON) written to:[/dim] [{Color.BRAND_ALT}]{out_path}[/{Color.BRAND_ALT}]")
    console.print("  [dim]Paste that file or cat sections of it into an LLM for review.[/dim]")
