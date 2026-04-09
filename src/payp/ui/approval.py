"""SQL approval UI — show SQL and get user decision.

Simple one-key prompt:
  [Enter] → execute as-is
  [e] → edit SQL, then execute
  [c/Esc] → cancel
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.lexers import PygmentsLexer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

try:
    from pygments.lexers.sql import SqlLexer
    _SQL_LEXER: PygmentsLexer | None = PygmentsLexer(SqlLexer)
except Exception:
    _SQL_LEXER = None


class ApprovalAction(str, Enum):
    EXECUTE = "execute"
    CANCEL = "cancel"
    OVERRIDE = "override"


@dataclass
class ApprovalResult:
    action: ApprovalAction
    sql: str  # Potentially edited SQL


async def ask_approval(
    console: Console,
    sql: str,
    title: str = "Generated SQL",
    subtitle: str | None = None,
    warning: str | None = None,
    hard_block: bool = False,
) -> ApprovalResult:
    """Show SQL and ask for user approval (async — runs in existing event loop)."""
    if warning:
        console.print(Panel(warning, border_style="red", title="⚠ Warning"))

    panel = Panel(
        Syntax(sql.strip(), "sql", theme="monokai", word_wrap=True),
        title=title,
        subtitle=subtitle,
        border_style="red" if hard_block else ("yellow" if warning else "rgb(168,0,111)"),
    )
    console.print(panel)

    if hard_block:
        console.print(
            "[red bold]HARD BLOCK.[/red bold] "
            "Type [bold]OVERRIDE[/bold] to proceed, anything else cancels."
        )
        session: PromptSession = PromptSession()  # type: ignore[type-arg]
        response = (await session.prompt_async("> ")).strip()
        if response == "OVERRIDE":
            return ApprovalResult(action=ApprovalAction.OVERRIDE, sql=sql)
        return ApprovalResult(action=ApprovalAction.CANCEL, sql=sql)

    # Capture single key: Enter/e/c/Esc
    action = await _capture_approval_key(console)

    if action == ApprovalAction.EXECUTE:
        return ApprovalResult(action=ApprovalAction.EXECUTE, sql=sql)

    if action == "edit":
        console.print("[dim]Edit SQL (Esc then Enter to submit, Ctrl+C to cancel):[/dim]")
        try:
            session_edit: PromptSession = PromptSession()  # type: ignore[type-arg]
            edited = await session_edit.prompt_async(
                "> ",
                default=sql.strip(),
                multiline=True,
                lexer=_SQL_LEXER,
            )
            return ApprovalResult(action=ApprovalAction.EXECUTE, sql=edited)
        except (KeyboardInterrupt, EOFError):
            return ApprovalResult(action=ApprovalAction.CANCEL, sql=sql)

    return ApprovalResult(action=ApprovalAction.CANCEL, sql=sql)


async def _capture_approval_key(console: Console) -> str:
    """Capture Enter/e/c/Esc in a mini-app. Returns action string."""
    state = {"action": ApprovalAction.CANCEL}

    kb = KeyBindings()

    @kb.add("enter")
    def _(event):
        state["action"] = ApprovalAction.EXECUTE
        event.app.exit()

    @kb.add("e")
    def _(event):
        state["action"] = "edit"
        event.app.exit()

    @kb.add("c")
    @kb.add("escape")
    @kb.add("q")
    def _(event):
        state["action"] = ApprovalAction.CANCEL
        event.app.exit()

    @kb.add("c-c")
    def _(event):
        state["action"] = ApprovalAction.CANCEL
        event.app.exit()

    def _text():
        return [("fg:gray", "  [Enter] execute   [e] edit   [c/Esc] cancel ")]

    window = Window(content=FormattedTextControl(_text), height=1, always_hide_cursor=True)
    app: Application = Application(  # type: ignore[type-arg]
        layout=Layout(HSplit([window])),
        key_bindings=kb,
        full_screen=False,
    )
    await app.run_async()

    return state["action"]
