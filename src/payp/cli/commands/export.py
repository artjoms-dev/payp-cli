"""Slash command: /export."""

from __future__ import annotations

from payp.cli.state import _state, console


def _cmd_export(args: str) -> None:
    """Export the current conversation session as a shareable markdown file."""
    from datetime import datetime
    from pathlib import Path

    from payp.ui.theme import Color

    chat = _state.get("chat_session")
    if not chat or not chat.messages:
        console.print("[dim]No conversation to export yet.[/dim]")
        return

    conn_name = _state.get("active_connection") or "no-db"
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    lines: list[str] = []
    lines.append(f"# payp session — {date_str} {time_str} ({conn_name})")
    lines.append("")
    lines.append(f"**Messages:** {len(chat.messages)}  ")
    lines.append(f"**Mode:** {chat.mode.value}  ")
    if chat.conn and chat.conn.is_connected:
        lines.append(f"**Database:** {chat.conn.profile.db_type.value} — {chat.conn.db_version}  ")
    lines.append("")
    lines.append("---")
    lines.append("")

    for msg in chat.messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if not content:
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "?")
                    lines.append(f"**🔧 Tool call:** `{name}`")
                    args_str = fn.get("arguments", "")
                    if args_str and len(args_str) < 200:
                        lines.append(f"```json\n{args_str}\n```")
                    lines.append("")
            continue

        if role == "user":
            lines.append("### 👤 User")
            lines.append("")
            lines.append(f"> {content}".replace("\n", "\n> "))
            lines.append("")
        elif role == "assistant":
            lines.append("### 🤖 Assistant")
            lines.append("")
            lines.append(content)
            lines.append("")
        elif role == "tool":
            preview = content[:300].replace("\n", " ")
            lines.append(f"**Tool result:** `{preview}{'…' if len(content) > 300 else ''}`")
            lines.append("")

    # Cost + context footer
    try:
        cost = chat.llm.get_cost_summary()
        lines.append("---")
        lines.append("")
        lines.append(
            f"_Tokens: {cost['total_tokens']:,} "
            f"({cost['input_tokens']:,} in / {cost['output_tokens']:,} out) • "
            f"Cost: ${cost['total_cost_usd']:.4f} • "
            f"Queries: {cost['query_count']}_"
        )
    except Exception:
        pass

    export_dir = Path("./exports")
    export_dir.mkdir(parents=True, exist_ok=True)
    filename = f"session_{date_str}_{now.strftime('%H-%M')}_{conn_name}.md"
    if args.strip():
        custom = Path(args.strip()).expanduser()
        if custom.suffix != ".md":
            custom = custom.with_suffix(".md")
        filepath = custom
        filepath.parent.mkdir(parents=True, exist_ok=True)
    else:
        filepath = export_dir / filename

    filepath.write_text("\n".join(lines))
    size_kb = filepath.stat().st_size / 1024
    console.print(
        f"[{Color.BRAND_ALT}]✓ Session exported:[/{Color.BRAND_ALT}] [{Color.BRAND_ALT}]{filepath}[/{Color.BRAND_ALT}] "
        f"[dim]({len(chat.messages)} messages, {size_kb:.1f} KB)[/dim]"
    )
    console.print(
        "[dim]Share via Slack, PR, email — sensitive credentials are NOT included.[/dim]"
    )
