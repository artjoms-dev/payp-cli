"""Reusable interactive selector component.

Arrow-key navigable list with configurable actions.
Used by /db, /snapshots, /queries, /resume, etc.

Features:
- Up/Down arrow navigation
- Visible window of N items (scrolls)
- Enter to select
- Esc/q to cancel
- Custom key actions (e.g., 'd' to delete)
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from rich.console import Console

from payp.ui.theme import PTColor


@dataclass
class SelectorItem:
    """An item in the selector list."""

    label: str  # Display text
    value: Any  # Return value when selected
    description: str = ""  # Optional secondary text
    style: str = ""  # Optional prompt_toolkit style for label prefix (text before first space)


@dataclass
class SelectorAction:
    """A custom key action for the selector."""

    key: str  # Key to press (e.g., 'd', 'r')
    label: str  # Display text (e.g., "delete", "restore")
    callback: Callable[[SelectorItem], bool]  # Called with selected item. Return True to remove from list.


@dataclass
class SelectorResult:
    """Result from the selector."""

    action: str  # "select", "cancel", or custom action key
    item: SelectorItem | None = None


def interactive_select(
    console: Console,
    title: str | list[tuple[str, str]],
    items: list[SelectorItem],
    visible: int = 5,
    actions: list[SelectorAction] | None = None,
    title_style: str = "bold",
    searchable: bool = False,
) -> SelectorResult:
    """Show an interactive selector and return the user's choice.

    Args:
        console: Rich console for pre/post rendering
        title: Header text
        items: List of selectable items
        visible: Number of items visible at once (scrolls if more)
        actions: Custom key actions (e.g., 'd' to delete)
        searchable: Enable type-to-filter (letters filter, backspace deletes)

    Returns:
        SelectorResult with action type and selected item
    """
    if not items:
        console.print("[dim]No items.[/dim]")
        return SelectorResult(action="cancel")

    state: dict[str, Any] = {
        "cursor": 0,
        "offset": 0,
        "items": list(items),
        "all_items": list(items),
        "query": "",
        "result": SelectorResult(action="cancel"),
        "done": False,
    }

    actions = actions or []
    action_map = {a.key: a for a in actions}

    def _apply_filter() -> None:
        q = state["query"].lower()
        if q:
            state["items"] = [
                it for it in state["all_items"]
                if q in it.label.lower() or q in it.description.lower()
            ]
        else:
            state["items"] = list(state["all_items"])
        state["cursor"] = 0
        state["offset"] = 0

    kb = KeyBindings()

    @kb.add("up")
    def _up(event: Any) -> None:
        if state["cursor"] > 0:
            state["cursor"] -= 1
            if state["cursor"] < state["offset"]:
                state["offset"] = state["cursor"]

    @kb.add("down")
    def _down(event: Any) -> None:
        if state["cursor"] < len(state["items"]) - 1:
            state["cursor"] += 1
            if state["cursor"] >= state["offset"] + visible:
                state["offset"] = state["cursor"] - visible + 1

    @kb.add("enter")
    def _select(event: Any) -> None:
        if state["items"]:
            state["result"] = SelectorResult(
                action="select",
                item=state["items"][state["cursor"]],
            )
        state["done"] = True
        event.app.exit()

    @kb.add("escape")
    def _cancel(event: Any) -> None:
        state["result"] = SelectorResult(action="cancel")
        state["done"] = True
        event.app.exit()

    if not searchable:
        @kb.add("q")
        def _quit(event: Any) -> None:
            state["result"] = SelectorResult(action="cancel")
            state["done"] = True
            event.app.exit()

    # Register custom action keys
    for action_key in action_map:
        def _make_handler(key: str) -> Callable:
            def _handler(event: Any) -> None:
                if not state["items"]:
                    return
                item = state["items"][state["cursor"]]
                action = action_map[key]
                should_remove = action.callback(item)
                if should_remove:
                    state["items"].pop(state["cursor"])
                    if state["cursor"] >= len(state["items"]) and state["cursor"] > 0:
                        state["cursor"] -= 1
                    if state["offset"] > 0 and state["offset"] >= len(state["items"]) - visible + 1:
                        state["offset"] = max(0, len(state["items"]) - visible)
                if not state["items"]:
                    state["result"] = SelectorResult(action="cancel")
                    state["done"] = True
                    event.app.exit()
            return _handler
        kb.add(action_key)(_make_handler(action_key))

    if searchable:
        @kb.add("backspace")
        def _backspace(event: Any) -> None:
            if state["query"]:
                state["query"] = state["query"][:-1]
                _apply_filter()

        from prompt_toolkit.filters import Condition
        from prompt_toolkit.keys import Keys

        @Condition
        def _is_searchable() -> bool:
            return True

        @kb.add(Keys.Any, filter=_is_searchable)
        def _char(event: Any) -> None:
            ch = event.data
            if ch.isprintable() and len(ch) == 1:
                state["query"] += ch
                _apply_filter()

    def _get_text() -> list[tuple[str, str]]:
        """Build the formatted text for the selector display."""
        result: list[tuple[str, str]] = []
        try:
            term_width = os.get_terminal_size().columns
        except OSError:
            term_width = 120

        # Title
        if isinstance(title, list):
            result.append(("", " "))
            result.extend(title)
            result.append(("", "\n"))
        else:
            result.append((title_style, f" {title}\n"))

        # Search bar
        if searchable:
            q = state["query"]
            if q:
                result.append(("fg:gray", " filter: "))
                result.append(("bold", q))
                result.append(("fg:gray", f"  ({len(state['items'])} matches)\n"))
            else:
                result.append(("fg:gray", " type to filter...\n"))

        result.append(("", "\n"))

        cur_items = state["items"]

        if not cur_items:
            result.append(("fg:gray", "   no matches\n"))
        else:
            visible_items = cur_items[state["offset"]:state["offset"] + visible]
            for i, item in enumerate(visible_items):
                actual_idx = state["offset"] + i
                is_selected = actual_idx == state["cursor"]

                prefix_len = 3
                label_len = len(item.label)

                if is_selected:
                    result.append((PTColor.BRAND_ALT, " ▸ "))
                    if item.style and " " in item.label:
                        prefix, rest = item.label.split(" ", 1)
                        result.append((item.style, prefix + " "))
                        result.append(("bold", rest))
                    else:
                        result.append(("bold", item.label))
                else:
                    result.append(("", "   "))
                    if item.style and " " in item.label:
                        prefix, rest = item.label.split(" ", 1)
                        result.append((item.style, prefix + " "))
                        result.append(("", rest))
                    else:
                        result.append(("", item.label))

                if item.description:
                    available = term_width - prefix_len - label_len - 2
                    if available > 5:
                        desc = item.description
                        if len(desc) > available:
                            desc = desc[: available - 1] + "…"
                        result.append(("fg:gray", f"  {desc}"))

                result.append(("", "\n"))

            # Scroll indicators
            total = len(cur_items)
            if total > visible:
                if state["offset"] > 0:
                    result.append(("fg:gray", "   ↑ more above\n"))
                if state["offset"] + visible < total:
                    result.append(("fg:gray", "   ↓ more below\n"))

        # Footer
        result.append(("", "\n"))
        footer_parts = ["↑↓ navigate", "Enter select", "Esc cancel"]
        if searchable:
            footer_parts.insert(0, "type to filter")
        for a in actions:
            footer_parts.append(f"'{a.key}' {a.label}")
        result.append(("fg:gray", " " + "  │  ".join(footer_parts)))

        return result

    control = FormattedTextControl(_get_text)
    window = Window(content=control, always_hide_cursor=True)
    layout = Layout(HSplit([window]))

    from prompt_toolkit.input import create_input

    app: Application = Application(  # type: ignore[type-arg]
        layout=layout,
        key_bindings=kb,
        full_screen=False,
        input=create_input(),
    )
    app.ttimeoutlen = 0.05  # Fast Escape detection (default 0.5s)
    app.timeoutlen = 0.05  # Fast key sequence resolution (default 1.0s)
    app.run()

    return state["result"]
