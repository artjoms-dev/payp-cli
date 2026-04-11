"""Slash command: /queries."""

from __future__ import annotations

from payp.cli.state import console


def _cmd_queries(args: str) -> None:
    """List and manage saved queries."""
    from payp.storage.queries import delete_query, list_queries
    from payp.ui.selector import SelectorAction, SelectorItem, interactive_select
    from payp.ui.theme import Color, PTColor

    queries = list_queries(filter_tag=args.strip())
    if not queries:
        if args:
            console.print(f"[dim]No queries matching '{args}'.[/dim]")
        else:
            console.print(
                "[dim]No saved queries yet. Ask the assistant to save queries:[/dim]\n"
                f"  [{Color.BRAND_ALT}]save this as 'monthly-revenue'[/{Color.BRAND_ALT}]"
            )
        return

    items = []
    for q in queries:
        label = q["name"]
        tag_str = f"  [{', '.join(q['tags'][:3])}]" if q["tags"] else ""
        desc = (q["description"] or "no description")[:60] + tag_str
        items.append(SelectorItem(label=label, value=q, description=desc))

    def _delete(item: SelectorItem) -> bool:
        q = item.value
        deleted = delete_query(q["name"])
        if deleted:
            console.print(f"  [red]Deleted[/red] {q['filename']}")
        return deleted

    q_title: list[tuple[str, str]] = [(PTColor.BRAND, f"Saved Queries ({len(items)})")]
    if args:
        q_title.append(("", f" — filter: {args}"))

    result = interactive_select(
        console=console,
        title=q_title,
        items=items,
        visible=5,
        actions=[SelectorAction(key="d", label="delete", callback=_delete)],
    )

    if result.action == "select" and result.item:
        q = result.item.value
        from rich.panel import Panel
        from rich.syntax import Syntax

        meta = f"[dim]{q['description']}[/dim]\n"
        if q["tags"]:
            meta += f"[dim]tags: {', '.join(q['tags'])}[/dim]\n"
        console.print()
        console.print(meta, end="")
        console.print(Panel(
            Syntax(q["sql"], "sql", theme="monokai", word_wrap=True),
            title=f"[{Color.BRAND_ALT}]{q['name']}[/{Color.BRAND_ALT}]",
            border_style="rgb(168,0,111)",
        ))
        console.print(
            f"[dim]Ask the assistant: [{Color.BRAND_ALT}]run the {q['name']} query[/{Color.BRAND_ALT}][/dim]\n"
        )
