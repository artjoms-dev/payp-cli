"""Slash command: /mode."""

from __future__ import annotations

from payp.cli.loop import _ensure_chat_session
from payp.cli.state import _state, console, get_config
from payp.config import save_config
from payp.models import SecurityMode


def _cmd_mode(args: str) -> None:
    """Switch security mode via selector or direct argument."""
    from payp.ui.selector import SelectorItem, interactive_select
    from payp.ui.theme import Color, PTColor

    config = get_config()

    if args:
        try:
            new_mode = SecurityMode(args.lower())
        except ValueError:
            console.print(f"[red]Unknown mode: {args}[/red]")
            return
    else:
        current_val = config.default_mode.value
        modes = [
            SelectorItem(
                label="manual",
                value=SecurityMode.MANUAL,
                description="approve every SQL before execution",
            ),
            SelectorItem(
                label="yolo",
                value=SecurityMode.YOLO,
                description="auto-execute everything",
            ),
            SelectorItem(
                label="secure",
                value=SecurityMode.SECURE,
                description="reviewer checks, you decide",
            ),
            SelectorItem(
                label="secure-auto",
                value=SecurityMode.SECURE_AUTO,
                description="reviewer checks and decides",
            ),
        ]
        title: list[tuple[str, str]] = [
            (PTColor.BRAND, "Security Mode"),
            ("", "  (current: "),
            (PTColor.BRAND_ALT, current_val),
            ("", ")"),
        ]
        result = interactive_select(
            console=console,
            title=title,
            items=modes,
        )
        if result.action != "select" or result.item is None:
            return
        new_mode = result.item.value

    config.default_mode = new_mode
    save_config(config)
    _state["config"] = config

    confirmations = {
        SecurityMode.YOLO: (
            "[bold red]YOLO mode enabled. All queries execute without confirmation.[/bold red]"
        ),
        SecurityMode.SECURE: (
            f"[{Color.BRAND_ALT}]Secure mode. Reviewer will check, you make final decision."
            f"[/{Color.BRAND_ALT}]"
        ),
        SecurityMode.SECURE_AUTO: (
            f"[{Color.BRAND_ALT}]Secure-auto mode. Reviewer checks and decides.[/{Color.BRAND_ALT}]"
        ),
        SecurityMode.MANUAL: (
            f"[{Color.BRAND_ALT}]Manual mode. You approve every SQL.[/{Color.BRAND_ALT}]"
        ),
    }
    console.print(confirmations[new_mode])
    _ensure_chat_session()  # Refresh with new mode
