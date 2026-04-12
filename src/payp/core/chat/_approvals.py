from __future__ import annotations

from typing import TYPE_CHECKING, Any

from payp.models import SecurityMode
from payp.tools.base import BaseTool
from payp.ui.approval import ApprovalAction, ask_approval
from payp.ui.streaming import display_tool_call, display_tool_result

if TYPE_CHECKING:
    from ._facade import ChatSession


async def handle_knowledge_proposal(
    session: ChatSession, data: dict[str, Any]
) -> dict[str, Any]:
    """Show a knowledge discovery to the user for approval before saving.

    In YOLO mode the proposal is auto-saved without prompting — the panel
    is still printed so the user can see what was recorded.
    """
    from prompt_toolkit import PromptSession
    from rich.markdown import Markdown
    from rich.panel import Panel

    table = data.get("table", "?")
    conn = data.get("connection", "?")
    discovery = data.get("discovery", "")
    section = data.get("section", "business_logic")

    session.console.print()
    session.console.print(
        Panel(
            Markdown(discovery),
            title=f"📝 New knowledge for [bold]{table}[/bold] ({conn})",
            subtitle=f"section: {section}",
            border_style="yellow",
        )
    )

    # YOLO mode → auto-save, skip prompt
    if session.mode == SecurityMode.YOLO:
        from payp.memory.manager import get_memory_backend

        backend = get_memory_backend()
        content = f"\n### {section.replace('_', ' ').title()}\n{discovery}"
        result = await backend.save(conn, table, content, section=section)
        location = result.get("file") or result.get("id") or "memory"
        session.console.print(
            f"  [bold rgb(180,224,76)]✓ Auto-saved (yolo) to {location}[/bold rgb(180,224,76)]"
        )
        return result

    # Pause AbortWatcher during user prompt
    watcher = getattr(session, "_watcher", None)
    if watcher:
        watcher.pause()
    try:
        prompt_session: PromptSession = PromptSession()  # type: ignore[type-arg]
        answer = (
            await prompt_session.prompt_async("  Save to knowledge base? [y/N/e(edit)]: ")
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"
    finally:
        if watcher:
            watcher.resume()

    if answer == "y":
        from payp.memory.manager import get_memory_backend

        backend = get_memory_backend()
        content = f"\n### {section.replace('_', ' ').title()}\n{discovery}"
        result = await backend.save(conn, table, content, section=section)
        location = result.get("file") or result.get("id") or "memory"
        session.console.print(
            f"  [bold rgb(180,224,76)]✓ Saved to {location}[/bold rgb(180,224,76)]"
        )
        return result
    if answer == "e":
        # Let user edit before saving
        try:
            prompt_session2: PromptSession = PromptSession()  # type: ignore[type-arg]
            edited = await prompt_session2.prompt_async(
                "  Edit (Esc+Enter to submit): ",
                default=discovery,
                multiline=True,
            )
            from payp.memory.manager import get_memory_backend

            backend = get_memory_backend()
            content = f"\n### {section.replace('_', ' ').title()}\n{edited}"
            result = await backend.save(conn, table, content, section=section)
            location = result.get("file") or result.get("id") or "memory"
            session.console.print(
                f"  [bold rgb(180,224,76)]✓ Saved (edited) to {location}[/bold rgb(180,224,76)]"
            )
            return result
        except (EOFError, KeyboardInterrupt):
            session.console.print("  [dim]Cancelled[/dim]")
            return {"saved": False, "reason": "user cancelled edit"}

    session.console.print("  [dim]Discarded[/dim]")
    return {"saved": False, "reason": "user rejected"}


async def execute_code_with_approval(
    session: ChatSession,
    tool: BaseTool,
    args: dict[str, Any],
    context: dict[str, Any],
    tool_name: str,
) -> Any:
    """Approval flow for Python/R code execution (same UX as destructive SQL)."""
    from rich.panel import Panel
    from rich.syntax import Syntax

    # execute_shell uses 'command' field; execute_python/execute_r use 'code'
    code_field = "command" if tool_name == "execute_shell" else "code"
    code = (args.get(code_field) or "").strip()
    if not code:
        return {"error": f"No {code_field} provided"}

    # YOLO mode → run directly
    if session.mode == SecurityMode.YOLO:
        display_tool_call(session.console, tool_name, args)
        result = await tool.call(args, context)
        display_tool_result(session.console, tool_name, result.success, result.summary)
        return result.data if result.success else {"error": result.error}

    # Manual/Secure modes → show code in approval panel
    language = {
        "execute_python": "python",
        "execute_r": "r",
        "execute_shell": "bash",
    }.get(tool_name, "text")
    description = args.get("description", "")
    subtitle = f"{tool_name}" + (f" — {description}" if description else "")

    session.console.print(
        Panel(
            Syntax(code, language, theme="monokai", word_wrap=True),
            title="Code execution — approval required",
            subtitle=subtitle,
            border_style="yellow",
        )
    )

    # Pause AbortWatcher during approval
    watcher = getattr(session, "_watcher", None)
    if watcher:
        watcher.pause()
    try:
        approval = await ask_approval(
            session.console,
            code,
            title=f"{tool_name} — approval required",
            subtitle=f"Mode: {session.mode.value}",
            warning="Code runs in a subprocess with filesystem and network access.",
            hard_block=False,
        )
    finally:
        if watcher:
            watcher.resume()

    if approval.action == ApprovalAction.CANCEL:
        session.console.print("  [dim]Cancelled[/dim]")
        return {"error": "User cancelled code execution"}

    # Execute (possibly edited) code
    args = {**args, code_field: approval.sql}  # reusing .sql field from ApprovalResult
    display_tool_call(session.console, tool_name, {code_field: approval.sql[:80] + "..."})
    result = await tool.call(args, context)
    display_tool_result(session.console, tool_name, result.success, result.summary)
    if result.success and result.data:
        stdout = result.data.get("stdout", "")
        if stdout.strip():
            session.console.print(Panel(stdout.strip(), title="stdout", border_style="dim"))
        files = result.data.get("files_mentioned") or []
        if files:
            session.console.print(
                "  [bold rgb(180,224,76)]✓ Files created:[/bold rgb(180,224,76)] "
                f"{', '.join(files)}"
            )
    elif not result.success and result.data and result.data.get("stderr"):
        session.console.print(
            Panel(result.data["stderr"][-500:], title="stderr", border_style="red")
        )
    return result.data if result.success else {"error": result.error}


async def execute_destructive_with_approval(
    session: ChatSession,
    tool: BaseTool,
    args: dict[str, Any],
    context: dict[str, Any],
) -> Any:
    """Generic approval flow for any tool with is_destructive=True.

    The tool's `preview()` method describes what WILL happen before the
    actual call. In YOLO mode the preview is skipped. In all other modes
    the user sees the preview and must confirm with y/N.

    This wrapper fires for destructive tools that DO NOT have a more
    specialized handler (execute_sql, execute_python, etc.).
    """
    from rich.panel import Panel
    from rich.text import Text

    tool_name = tool.name

    # YOLO → run directly
    if session.mode == SecurityMode.YOLO:
        display_tool_call(session.console, tool_name, args)
        result = await tool.call(args, context)
        display_tool_result(session.console, tool_name, result.success, result.summary)
        return result.data if result.success else {"error": result.error}

    # Build a preview
    try:
        preview = await tool.preview(args, context)
    except Exception as e:
        preview = {"summary": f"Preview failed: {e}", "items": [], "warning": None}

    if preview is None:
        # No preview — build a generic one from args
        preview = {
            "summary": f"{tool_name} would run with args: "
            + ", ".join(f"{k}={v}" for k, v in args.items() if k != "reason"),
            "items": [],
            "warning": None,
        }

    # If preview says nothing matches, short-circuit
    if preview.get("total", None) == 0 or (
        "total" not in preview
        and not preview.get("items")
        and "Would remove 0" in (preview.get("summary") or "")
    ):
        display_tool_call(session.console, tool_name, args)
        session.console.print(f"  [dim]{preview.get('summary', 'Nothing to do.')}[/dim]")
        return {"deleted": [], "summary": preview.get("summary", "nothing to do")}

    # Render preview panel
    body_lines: list[str] = []
    reason = args.get("reason") or ""
    if reason:
        body_lines.append(f"[bold]Why:[/bold] {reason}")
        body_lines.append("")
    body_lines.append(f"[bold]{preview.get('summary', '(no summary)')}[/bold]")

    items = preview.get("items") or []
    if items:
        body_lines.append("")
        for it in items:
            body_lines.append(f"  • {it}")
        if preview.get("truncated"):
            total = preview.get("total", len(items))
            body_lines.append(f"  [dim]... +{total - len(items)} more[/dim]")

    warning = preview.get("warning")
    if warning:
        body_lines.append("")
        body_lines.append(f"[yellow]⚠ {warning}[/yellow]")

    body = Text.from_markup("\n".join(body_lines))

    session.console.print(
        Panel(
            body,
            title=f"[bold]{tool_name}[/bold] — approval required",
            subtitle=f"Mode: {session.mode.value}",
            border_style="yellow",
        )
    )

    # Ask y/N via prompt_toolkit (async — we're inside the event loop)
    from prompt_toolkit import PromptSession

    watcher = getattr(session, "_watcher", None)
    if watcher:
        watcher.pause()
    try:
        prompt_session: PromptSession = PromptSession()  # type: ignore[type-arg]
        answer = (await prompt_session.prompt_async("  Execute? [y/N] ")).strip().lower()
    except (KeyboardInterrupt, EOFError):
        answer = ""
    finally:
        if watcher:
            watcher.resume()

    if answer not in ("y", "yes"):
        session.console.print("  [dim]Cancelled[/dim]")
        return {"error": f"User cancelled {tool_name}", "cancelled": True}

    # Execute
    display_tool_call(session.console, tool_name, args)
    result = await tool.call(args, context)
    display_tool_result(session.console, tool_name, result.success, result.summary)
    return result.data if result.success else {"error": result.error}
