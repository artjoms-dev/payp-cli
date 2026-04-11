"""Slash commands: /snapshots, /rollback."""

from __future__ import annotations

import json
from pathlib import Path

from payp.cli.runtime import _run_async, command_prompt
from payp.cli.state import _state, console


def _cmd_snapshots() -> None:
    """Manage data snapshots with interactive selector."""
    from payp.storage.snapshots import (
        delete_snapshot,
        format_snapshot_age,
        list_snapshots,
    )
    from payp.ui.selector import SelectorAction, SelectorItem, interactive_select
    from payp.ui.theme import Color, PTColor

    snapshots = list_snapshots()
    if not snapshots:
        console.print("[dim]No snapshots found.[/dim]")
        return

    items = []
    for s in snapshots:
        age = format_snapshot_age(s["timestamp"])
        label = f"{s['table']} ({s['operation']}) — {s['row_count']} rows"
        desc = f"{age} • {s['size']} • {s['connection']}"
        items.append(SelectorItem(label=label, value=s, description=desc))

    def _delete(item: SelectorItem) -> bool:
        snap = item.value
        deleted = delete_snapshot(snap["file"])
        if deleted:
            console.print(f"  [red]Deleted[/red] {snap['filename']}")
        return deleted

    result = interactive_select(
        console=console,
        title=[(PTColor.BRAND, f"Snapshots ({len(items)})")],
        items=items,
        visible=5,
        actions=[SelectorAction(key="d", label="delete", callback=_delete)],
    )

    if result.action == "select" and result.item:
        snap = result.item.value
        console.print(f"\nSnapshot: [{Color.BRAND}]{snap['filename']}[/{Color.BRAND}]")
        console.print(f"  Table: {snap['table']}")
        console.print(f"  Operation: {snap['operation']}")
        console.print(f"  Rows: {snap['row_count']}")
        console.print(f"  Where: {snap['where']}")
        console.print(f"  File: {snap['file']}")
        console.print(
            "\n[dim]Ask the assistant to restore this snapshot, or use /snapshots again to delete.[/dim]"
        )


def _cmd_rollback() -> None:
    """Restore data from a snapshot — interactive picker with preview + confirmation."""
    from payp.storage.snapshots import format_snapshot_age, list_snapshots
    from payp.ui.selector import SelectorItem, interactive_select
    from payp.ui.theme import Color, PTColor

    mgr = _state.get("connection_manager")
    if not mgr or not mgr.is_connected:
        console.print("[yellow]Connect to a database first with /db[/yellow]")
        return

    snapshots = list_snapshots()
    if not snapshots:
        console.print("[dim]No snapshots available.[/dim]")
        return

    items = []
    for s in snapshots:
        age = format_snapshot_age(s["timestamp"])
        label = f"{s['table']} ({s['operation']}) — {s['row_count']} rows"
        desc = f"{age} • {s['size']} • {s['connection']}"
        items.append(SelectorItem(label=label, value=s, description=desc))

    result = interactive_select(
        console=console,
        title=[(PTColor.BRAND, f"Rollback — select snapshot to restore ({len(items)})")],
        items=items,
        visible=5,
    )

    if result.action != "select" or not result.item:
        console.print("[dim]Cancelled.[/dim]")
        return

    snap = result.item.value
    filepath = Path(snap["file"])

    console.print(f"\n[{Color.BRAND}]Snapshot:[/{Color.BRAND}] {snap['filename']}")
    console.print(f"  Table:     {snap['table']}")
    console.print(f"  Operation: {snap['operation']}")
    console.print(f"  Rows:      {snap['row_count']}")
    console.print(f"  Where:     {snap['where']}")
    console.print(f"  Taken:     {format_snapshot_age(snap['timestamp'])}")

    try:
        lines = filepath.read_text().strip().split("\n")
        if len(lines) < 2:
            console.print("[red]Snapshot file has no data rows.[/red]")
            return
        preview_rows = [json.loads(line) for line in lines[1:4]]
    except Exception as e:
        console.print(f"[red]Failed to read snapshot: {e}[/red]")
        return

    if preview_rows:
        from rich.table import Table
        console.print(f"\n[{Color.BRAND}]Preview[/{Color.BRAND}] (first {len(preview_rows)} of {snap['row_count']} rows):")
        cols = list(preview_rows[0].keys())
        preview_table = Table(show_header=True, header_style=Color.BRAND_ALT, show_lines=False)
        for col in cols:
            preview_table.add_column(col, overflow="fold")
        for row in preview_rows:
            preview_table.add_row(*[str(row.get(c, "")) for c in cols])
        console.print(preview_table)

    console.print()
    try:
        answer = command_prompt("Execute restore? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]Cancelled.[/dim]")
        return

    if answer != "y":
        console.print("[dim]Cancelled.[/dim]")
        return

    from payp.tools.snapshot import RestoreSnapshotTool

    tool = RestoreSnapshotTool()
    console.print(f"[dim]Restoring {snap['row_count']} rows to {snap['table']}...[/dim]")
    try:
        tool_result = _run_async(
            tool.call({"file": str(filepath)}, {"connection_manager": mgr})
        )
    except Exception as e:
        console.print(f"[red]Restore failed: {e}[/red]")
        return

    if tool_result.success:
        restored = tool_result.data.get("restored", 0) if tool_result.data else 0
        table_name = tool_result.data.get("table", snap["table"]) if tool_result.data else snap["table"]

        restored_ids = []
        try:
            for line in lines[1:]:
                row = json.loads(line)
                for key in ("id", "ID", "pk"):
                    if key in row:
                        restored_ids.append(row[key])
                        break
        except Exception:
            pass

        ids_str = ""
        if restored_ids:
            if len(restored_ids) <= 10:
                ids_str = f" (ids: {', '.join(str(i) for i in restored_ids)})"
            else:
                ids_str = (
                    f" (ids: {', '.join(str(i) for i in restored_ids[:5])}, "
                    f"...+{len(restored_ids) - 5} more)"
                )

        console.print(
            f"[{Color.BRAND_ALT}]✓ Restored {restored} rows to {table_name}[/{Color.BRAND_ALT}]{ids_str}"
        )

        chat = _state.get("chat_session")
        if chat:
            where_clause = snap.get("where", "")
            notice = (
                f"[System notice — user ran /rollback] "
                f"Restored {restored} rows to {table_name} from snapshot {snap['filename']}. "
                f"Original WHERE clause: {where_clause}. "
            )
            if restored_ids:
                notice += f"Restored primary keys: {restored_ids[:30]}"
                if len(restored_ids) > 30:
                    notice += f" (+{len(restored_ids) - 30} more)"
            chat.messages.append({"role": "assistant", "content": notice})
            try:
                chat.session.log_assistant(notice)
                chat.tx_log.log(
                    connection_name=mgr.profile.name,
                    operation_type="ROLLBACK",
                    sql_executed=f"RESTORE from {snap['filename']} WHERE {where_clause}",
                    execution_mode="rollback",
                    approved_by="user",
                    status="success",
                    rows_affected=restored,
                    model_used="cli-command",
                )
            except Exception:
                pass
    else:
        console.print(f"[red]Restore failed: {tool_result.error}[/red]")
