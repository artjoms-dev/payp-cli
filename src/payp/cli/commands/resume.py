"""Slash command: /resume."""

from __future__ import annotations

from datetime import UTC

from payp.cli.loop import _ensure_chat_session, _log_memory_backend_to_session, _short
from payp.cli.runtime import _run_async
from payp.cli.state import _state, console


def _cmd_resume(args: str = "") -> None:
    """Resume a previous conversation session or clean up old ones.

    Usage:
      /resume                            browse & resume interactively
      /resume clean                      remove empty sessions (default)
      /resume clean --keep 20            remove empty + keep only 20 newest
      /resume clean --older-than 7d      also remove sessions older than 7 days
      /resume clean --all                remove ALL sessions except current
    """
    from payp.config import list_connections
    from payp.storage.sessions import (
        build_chat_messages_from_session,
        clean_sessions,
        delete_session,
        list_sessions,
        read_session,
    )
    from payp.ui.selector import SelectorAction, SelectorItem, interactive_select
    from payp.ui.theme import Color, PTColor

    parts = args.strip().split() if args.strip() else []
    subcommand = parts[0].lower() if parts else ""

    # /resume clean [flags]
    if subcommand == "clean":
        empty_only = True
        keep_last: int | None = None
        older_than_days: int | None = None

        i = 1
        while i < len(parts):
            tok = parts[i]
            if tok == "--all":
                empty_only = False
                keep_last = 1
                older_than_days = 0
                i += 1
            elif tok == "--empty":
                empty_only = True
                i += 1
            elif tok == "--keep" and i + 1 < len(parts):
                try:
                    keep_last = int(parts[i + 1])
                except ValueError:
                    console.print(f"[red]Invalid --keep value: {parts[i + 1]}[/red]")
                    return
                i += 2
            elif tok == "--older-than" and i + 1 < len(parts):
                val = parts[i + 1]
                try:
                    older_than_days = int(val.rstrip("d"))
                except ValueError:
                    console.print(f"[red]Invalid --older-than value: {val}[/red]")
                    return
                i += 2
            else:
                console.print(f"[red]Unknown flag: {tok}[/red]")
                return

        current_file: str | None = None
        chat = _state.get("chat_session")
        if chat and getattr(chat, "session_file", None):
            current_file = str(chat.session_file)

        result = clean_sessions(
            empty=empty_only,
            older_than_days=older_than_days,
            keep_last=keep_last,
            keep_current=current_file,
        )

        n = len(result["deleted"])
        if n == 0:
            console.print("[dim]Nothing to clean.[/dim]")
            return

        console.print(
            f"[green]✓[/green] Removed [bold]{n}[/bold] session(s) "
            f"[dim]({result['kept']} kept, {result['total_before']} before)[/dim]"
        )
        for fn in result["deleted"][:5]:
            console.print(f"  [dim]- {fn}[/dim]")
        if n > 5:
            console.print(f"  [dim]  ... +{n - 5} more[/dim]")
        return

    # Default: interactive browser
    sessions = list_sessions()
    if not sessions:
        console.print("[dim]No sessions to resume.[/dim]")
        return

    items = []
    for s in sessions:
        summary = read_session(s["file"])
        topic = summary["summary"] or "(no messages)"
        conn = summary["connection"] or s["connection"]
        age = _format_session_age(summary["last_ts"])
        label = f"{s['date']} • {topic}"
        desc = f"{age} • {summary['query_count']} queries • {conn}"
        items.append(SelectorItem(
            label=label,
            value={"session": s, "summary": summary},
            description=desc,
        ))

    deleted_files: list[str] = []

    def _delete(item: SelectorItem) -> bool:
        s = item.value["session"]
        if delete_session(s["file"]):
            deleted_files.append(s["filename"])
            return True
        return False

    result = interactive_select(
        console=console,
        title=[(PTColor.BRAND, f"Recent Sessions ({len(items)})")],
        items=items,
        visible=5,
        actions=[SelectorAction(key="d", label="delete", callback=_delete)],
    )

    for fn in deleted_files:
        console.print(f"  [red]Deleted[/red] {fn}")

    if result.action != "select" or not result.item:
        return

    session_info = result.item.value["session"]
    summary = result.item.value["summary"]

    chat = _state.get("chat_session")
    if not chat:
        _ensure_chat_session()
        chat = _state.get("chat_session")
    if not chat:
        console.print("[red]No chat session — configure /models first[/red]")
        return

    messages = build_chat_messages_from_session(session_info["file"])
    chat.messages = messages

    console.print(
        f"\n[{Color.BRAND_ALT}]✓[/{Color.BRAND_ALT}] Resumed session with {len(messages)} message(s)"
    )

    events = summary.get("events") or []
    if events:
        from rich.markdown import Markdown
        from rich.rule import Rule

        console.print(Rule(style="dim"))
        user_count = 0
        assistant_count = 0
        for ev in events:
            role = ev.get("role")
            etype = ev.get("event")
            content = (ev.get("content") or "").strip() if isinstance(ev.get("content"), str) else ""

            if role == "user" and content:
                user_count += 1
                console.print(f"[bold {Color.BRAND}]you[/bold {Color.BRAND}] › {content}")
                console.print()
            elif role == "assistant" and content:
                assistant_count += 1
                console.print(f"[bold {Color.BRAND_ALT}]payp[/bold {Color.BRAND_ALT}] ›")
                try:
                    console.print(Markdown(content))
                except Exception:
                    console.print(content)
                console.print()
            elif etype == "tool_call":
                tool = ev.get("tool", "?")
                ok = ev.get("success", True)
                tsum = ev.get("summary") or ""
                icon = f"[{Color.BRAND_ALT}]✓[/{Color.BRAND_ALT}]" if ok else "[red]✗[/red]"
                ev_args = ev.get("args") or {}
                arg_preview = ", ".join(
                    f"{k}={_short(str(v), 40)}" for k, v in list(ev_args.items())[:4]
                )
                console.print(f"  [dim]-> {tool}({arg_preview})[/dim]")
                console.print(f"  {icon} [dim]{tool}: {_short(tsum, 90)}[/dim]")
            elif etype == "sql_generated":
                sql = (ev.get("sql") or "").strip().splitlines()
                first = sql[0] if sql else ""
                more = f" [dim](+{len(sql) - 1} lines)[/dim]" if len(sql) > 1 else ""
                console.print(f"  [dim]sql:[/dim] [cyan]{_short(first, 90)}[/cyan]{more}")
            elif etype == "query_executed":
                rows = ev.get("rows")
                ms = ev.get("ms")
                status = ev.get("status", "ok")
                err = ev.get("error")
                if err:
                    console.print(f"  [red]query error:[/red] [dim]{_short(err, 90)}[/dim]")
                else:
                    console.print(
                        f"  [dim]↳ {rows} row{'s' if rows != 1 else ''} in {ms}ms "
                        f"({status})[/dim]"
                    )
        console.print(Rule(style="dim"))

        if assistant_count == 0 and user_count > 0:
            console.print(
                "[dim]⚠ No assistant replies stored on disk for this session "
                "(it was recorded before the log_assistant fix). New turns "
                "from now on will be persisted fully.[/dim]"
            )

    target_backend = summary.get("memory_backend")
    if target_backend:
        from payp.memory.manager import backend_status as _bs, switch_backend as _sb
        if _bs().get("name") != target_backend:
            try:
                _sb(target_backend, migrate=False)
                console.print(f"[dim]Memory backend restored -> {target_backend}[/dim]")
                _log_memory_backend_to_session(target_backend)
            except (RuntimeError, ValueError) as exc:
                console.print(
                    f"[dim]Could not restore memory backend "
                    f"'{target_backend}': {exc}[/dim]"
                )

    target_conn = summary.get("connection") or session_info.get("connection")
    if target_conn in ("no-db", "?", "", None):
        target_conn = None
    if target_conn and target_conn != _state.get("active_connection"):
        if target_conn in list_connections():
            console.print(f"[dim]Reconnecting to {target_conn}...[/dim]")
            from payp.cli.commands.db import _cmd_db
            _cmd_db(target_conn)
        else:
            console.print(
                f"[dim]Session was on [bold]{target_conn}[/bold] but that "
                "connection no longer exists — use /db to pick one.[/dim]"
            )


def _format_session_age(ts: str) -> str:
    """Format ISO timestamp as relative age."""
    if not ts:
        return "unknown"
    try:
        from datetime import datetime
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        delta = datetime.now(UTC) - t
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"
    except Exception:
        return "unknown"
