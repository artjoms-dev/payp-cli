"""Slash command: /skills."""

from __future__ import annotations

from payp.cli.runtime import command_prompt
from payp.cli.state import CommandCancelled, _state, console


def _cmd_skills() -> None:
    """Browse and activate/deactivate skills (pre-defined workflows)."""
    from payp.db.connection import ConnectionManager
    from payp.skills.registry import discover_skills
    from payp.storage.active_skills import toggle_skill
    from payp.ui.selector import SelectorAction, SelectorItem, interactive_select
    from payp.ui.theme import Color, PTColor

    registry = discover_skills()
    skills = registry.all()
    if not skills:
        console.print(
            "[dim]No skills installed. Place .md skill files in "
            "builtin_skills/, ~/.payp/skills/, or ./payp/skills/[/dim]"
        )
        return

    mgr: ConnectionManager | None = _state.get("connection_manager")  # type: ignore[assignment]
    dialect = None
    if mgr and mgr.is_connected:
        dialect = mgr.profile.db_type.value
        skills = [s for s in skills if s.supports_dialect(dialect)]
        if not skills:
            console.print(f"[dim]No skills support dialect: {dialect}[/dim]")
            return

    all_names = {s.name for s in registry.all()}
    active_names = registry.active_names()

    def _build_items() -> list[SelectorItem]:
        items = []
        for s in skills:
            is_on = s.name in active_names
            status = "●" if is_on else "○"
            dbs = ", ".join(s.frontmatter.db_types) or "any"
            label = f"{status} {s.name}"
            desc = f"{s.description} — {dbs} • {s.source_scope}"
            style = PTColor.BRAND_ALT if is_on else ""
            items.append(SelectorItem(label=label, value=s, description=desc, style=style))
        return items

    def _toggle(item: SelectorItem) -> bool:
        skill = item.value
        now_active, updated_set = toggle_skill(skill.name, all_names)
        active_names.clear()
        active_names.update(updated_set)
        status = "●" if now_active else "○"
        item.label = f"{status} {skill.name}"
        item.style = PTColor.BRAND_ALT if now_active else ""
        return False  # don't remove from list

    title: list[tuple[str, str]] = [
        (PTColor.BRAND, f"Skills ({len(skills)})"),
        ("", "  —  "),
        (PTColor.BRAND_ALT, "● active"),
        ("", "  "),
        ("", "○ inactive"),
    ]
    if dialect:
        title.append(("", f"  —  dialect: {dialect}"))

    while True:
        items = _build_items()
        result = interactive_select(
            console=console,
            title=title,
            items=items,
            visible=8,
            actions=[SelectorAction(key="a", label="toggle active", callback=_toggle)],
        )

        if result.action != "select" or not result.item:
            break

        skill = result.item.value
        console.print()
        is_on = skill.name in active_names
        console.print(
            __import__("rich.panel", fromlist=["Panel"]).Panel(
                skill.body,
                title=f"[{Color.BRAND_ALT}]{skill.name}[/{Color.BRAND_ALT}] — {skill.description}",
                subtitle=(
                    f"when_to_use: {skill.when_to_use}  •  "
                    f"db_types: {', '.join(skill.frontmatter.db_types) or 'any'}  •  "
                    f"tools: {', '.join(skill.frontmatter.allowed_tools) or 'any'}  •  "
                    f"scope: {skill.source_scope}"
                ),
                border_style="rgb(168,0,111)",
            )
        )
        active_str = f"[{Color.BRAND_ALT}]● ACTIVE[/{Color.BRAND_ALT}]" if is_on else "○ INACTIVE"
        console.print(f"  [dim]Source: {skill.source_path}[/dim]")
        console.print(f"  Status: {active_str}")
        console.print()

        action_label = "deactivate" if is_on else "activate"
        console.print(f"  [dim]a = {action_label}  •  Enter = back to list  •  Esc = done[/dim]")
        try:
            choice = command_prompt("  ").strip().lower()
        except CommandCancelled:
            break
        if choice == "a":
            _toggle(result.item)
