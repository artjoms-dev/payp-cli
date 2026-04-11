"""Main chat loop for payp.

Handles the conversation flow:
1. User sends message
2. Build system prompt with current context
3. Stream LLM response
4. Process tool calls (multi-step chains)
5. Display results
6. Repeat
"""

from __future__ import annotations

import json
import re
from typing import Any

from rich.console import Console

from payp.core.classifier import SqlCategory, classify_sql, statically_hard_blocked
from payp.core.llm import LLMClient, ToolCall
from payp.core.reviewer import Reviewer, Verdict
from payp.db.connection import ConnectionManager
from payp.models import SchemaCatalog, SchemaIndex, SecurityMode
from payp.prompts.system import build_system_prompt
from payp.skills.registry import SkillRegistry, discover_skills
from payp.storage.sessions import SessionWriter
from payp.storage.transaction_log import TransactionLog
from payp.tools.base import BaseTool, ToolRegistry
from payp.tools.bulk_insert import BulkInsertTool
from payp.tools.chart import ChartTool
from payp.tools.crossdb import (
    CompareDataTool,
    CompareSchemasTool,
    ListConnectionsTool,
    SwitchConnectionTool,
)
from payp.tools.dashboard import DashboardTool
from payp.tools.explain import ExplainTool
from payp.tools.export import ExportTool
from payp.tools.help_tool import PaypHelpTool
from payp.tools.knowledge import (
    ListKnowledgeTool,
    ProposeKnowledgeTool,
    ReadKnowledgeTool,
    SearchKnowledgeTool,
)
from payp.tools.python_exec import PythonExecTool
from payp.tools.queries import (
    DeleteQueryTool,
    ListQueriesTool,
    LoadQueryTool,
    SaveQueryTool,
)
from payp.tools.query import QueryTool
from payp.tools.r_exec import RExecTool
from payp.tools.schema import CheckCascadeTool, SchemaLookupTool, SchemaSearchTool
from payp.tools.shell_exec import ShellExecTool
from payp.tools.skills_tool import InvokeSkillTool, ListSkillsTool
from payp.tools.snapshot import (
    DeleteSnapshotTool,
    ListSnapshotsTool,
    RestoreSnapshotTool,
    SnapshotBeforeDeleteTool,
)
from payp.tools.stats import StatsTool
from payp.tools.web_fetch import WebFetchTool
from payp.ui.approval import ApprovalAction, ask_approval
from payp.ui.display import display_dml_result, display_query_result
from payp.ui.streaming import (
    display_tool_call,
    display_tool_result,
    stream_response,
)

MAX_TOOL_ROUNDS = 30  # Prevent infinite tool call loops (Claude Code uses 30-50)


class ChatSession:
    """Manages a chat session with the LLM."""

    def __init__(
        self,
        llm: LLMClient,
        console: Console,
        connection_manager: ConnectionManager | None = None,
        mode: SecurityMode = SecurityMode.MANUAL,
        t0: SchemaIndex | None = None,
        t1: SchemaCatalog | None = None,
    ) -> None:
        self.llm = llm
        self.console = console
        self.conn = connection_manager
        self.mode = mode
        self.t0 = t0
        self.t1 = t1
        self.messages: list[dict[str, Any]] = []
        # Load skills lazily — safe if directories are missing
        try:
            self.skills: SkillRegistry = discover_skills()
        except Exception:
            self.skills = SkillRegistry()
        self.registry = self._build_registry()
        self.last_select: dict[str, Any] | None = None  # For /more pagination

        # Multi-connection support (for cross-DB operations)
        from payp.core.multi_connection import MultiConnectionManager
        self.multi_conn = MultiConnectionManager()

        # Persistence
        conn_name = connection_manager.profile.name if connection_manager else "no-db"
        self.session = SessionWriter(connection_name=conn_name)
        self.tx_log = TransactionLog(session_id=self.session.session_id)

    def _build_registry(self) -> ToolRegistry:
        """Register all available tools."""
        reg = ToolRegistry()
        reg.register(QueryTool())
        reg.register(ExplainTool())
        reg.register(SchemaLookupTool())
        reg.register(SchemaSearchTool())
        reg.register(CheckCascadeTool())
        reg.register(StatsTool())
        reg.register(SnapshotBeforeDeleteTool())
        reg.register(RestoreSnapshotTool())
        reg.register(ListSnapshotsTool())
        reg.register(DeleteSnapshotTool())
        reg.register(ExportTool())
        reg.register(PaypHelpTool())
        reg.register(ReadKnowledgeTool())
        reg.register(ProposeKnowledgeTool())
        reg.register(ListKnowledgeTool())
        reg.register(SearchKnowledgeTool())
        # write_knowledge and append_knowledge are NOT exposed to LLM
        # — knowledge writing happens only via propose_knowledge → user approval
        reg.register(SaveQueryTool())
        reg.register(ListQueriesTool())
        reg.register(LoadQueryTool())
        reg.register(DeleteQueryTool())
        reg.register(ChartTool())
        reg.register(BulkInsertTool())
        reg.register(DashboardTool())
        reg.register(ListConnectionsTool())
        reg.register(SwitchConnectionTool())
        reg.register(CompareSchemasTool())
        reg.register(CompareDataTool())
        reg.register(InvokeSkillTool())
        reg.register(ListSkillsTool())
        reg.register(PythonExecTool())
        reg.register(RExecTool())
        reg.register(ShellExecTool())
        reg.register(WebFetchTool())
        from payp.tools.cleanup import CleanupTool
        reg.register(CleanupTool())
        return reg

    def _build_context(self) -> dict[str, Any]:
        """Build context dict passed to tool calls."""
        return {
            "connection_manager": self.conn,
            "multi_conn": getattr(self, "multi_conn", None),
            "t0": self.t0,
            "t1": self.t1,
            "mode": self.mode,
            "skills": self.skills,
            "chat_session": self,  # so tools can reach self.session_file, etc.
        }

    def _display_tool_data(self, tool_name: str, result: Any, sql: str = "") -> None:
        """Auto-display result data for tools that return tabular/chartable data."""
        if not result.success or not result.data:
            return

        if tool_name == "execute_sql":
            d = result.data
            if "columns" in d and d["columns"]:
                display_query_result(
                    self.console,
                    d["columns"],
                    d["rows"],
                    d["execution_ms"],
                    d.get("truncated", False),
                )
                # Remember this SELECT for /more pagination
                if sql:
                    self.last_select = {
                        "sql": sql,
                        "connection": self.conn.profile.name if self.conn else None,
                        "offset": 20,  # next page starts after this batch
                        "truncated": d.get("truncated", False),
                    }
            elif "rows_affected" in d:
                display_dml_result(
                    self.console,
                    d["rows_affected"],
                    d["execution_ms"],
                )

    @staticmethod
    def _wrap_tool_output(tool_name: str, payload: Any) -> str:
        """Wrap tool results in an XML isolation envelope before sending
        to the LLM.

        Tool results flow back into the model's context as authoritative
        text. Any row value in a database can contain strings like
        "ignore previous instructions" and a confused model may follow
        them. To raise the bar on this, we:

        1. Wrap the serialized payload in <tool_output> tags with
           untrusted="true" so the system prompt can reference it.
        2. Neutralise any inner </tool_output> tokens (identical technique
           to the reviewer's <untrusted> wrapper).
        3. Leave the raw values untouched — the model still needs them
           to answer the user's question. This is isolation, not
           sanitisation.
        """
        body = json.dumps(payload, default=str)
        # Neutralise escape attempts — if a row value contains our
        # closing tag verbatim, swap angle brackets for look-alikes.
        body = re.sub(
            r"</?\s*tool_output\b[^>]*>",
            lambda m: m.group(0).replace("<", "⟨").replace(">", "⟩"),
            body,
            flags=re.IGNORECASE,
        )
        # Sanitise the tool_name attribute to prevent attribute-injection
        # via malicious tool names (shouldn't happen from a registry, but
        # defense-in-depth is cheap).
        safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", tool_name)[:64]
        return (
            f'<tool_output tool="{safe_name}" untrusted="true">\n'
            f"{body}\n"
            f"</tool_output>"
        )

    def _log_sql_execution(
        self,
        sql: str,
        operation_type: str,
        result: Any,
        approved_by: str,
        *,
        reviewer_verdict: str | None = None,
        reviewer_reason: str | None = None,
        reviewer_model: str | None = None,
        reviewer_overridden: bool | None = None,
        static_block_reason: str | None = None,
        consensus_verdict: str | None = None,
    ) -> None:
        """Log an executed SQL statement to the transaction log.

        Reviewer audit fields are optional — only populated when the SQL
        went through the security pipeline (not for read-only SELECTs).
        """
        conn_name = self.conn.profile.name if self.conn else "no-db"
        status = "success" if result.success else "failed"
        error = result.error if not result.success else None
        rows_affected = None
        execution_ms = None
        if result.success and result.data:
            d = result.data
            rows_affected = d.get("row_count") or d.get("rows_affected")
            execution_ms = d.get("execution_ms")
        try:
            self.tx_log.log(
                connection_name=conn_name,
                operation_type=operation_type,
                sql_executed=sql,
                execution_mode=self.mode.value,
                approved_by=approved_by,
                status=status,
                error_message=error,
                rows_affected=rows_affected,
                execution_ms=execution_ms,
                model_used=self.llm.get_executor_model(),
                reviewer_verdict=reviewer_verdict,
                reviewer_reason=reviewer_reason,
                reviewer_model=reviewer_model,
                reviewer_overridden=reviewer_overridden,
                static_block_reason=static_block_reason,
                consensus_verdict=consensus_verdict,
            )
        except Exception:
            pass  # Don't let logging errors break the chat flow

    async def _handle_knowledge_proposal(self, data: dict[str, Any]) -> dict[str, Any]:
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

        self.console.print()
        self.console.print(Panel(
            Markdown(discovery),
            title=f"📝 New knowledge for [bold]{table}[/bold] ({conn})",
            subtitle=f"section: {section}",
            border_style="yellow",
        ))

        # YOLO mode → auto-save, skip prompt
        if self.mode == SecurityMode.YOLO:
            from payp.memory.manager import get_memory_backend
            backend = get_memory_backend()
            content = f"\n### {section.replace('_', ' ').title()}\n{discovery}"
            result = await backend.save(conn, table, content, section=section)
            location = result.get("file") or result.get("id") or "memory"
            self.console.print(
                f"  [bold rgb(180,224,76)]✓ Auto-saved (yolo) to {location}[/bold rgb(180,224,76)]"
            )
            return result

        # Pause AbortWatcher during user prompt
        watcher = getattr(self, "_watcher", None)
        if watcher:
            watcher.pause()
        try:
            session: PromptSession = PromptSession()  # type: ignore[type-arg]
            answer = (await session.prompt_async(
                "  Save to knowledge base? [y/N/e(edit)]: "
            )).strip().lower()
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
            self.console.print(f"  [bold rgb(180,224,76)]✓ Saved to {location}[/bold rgb(180,224,76)]")
            return result
        elif answer == "e":
            # Let user edit before saving
            try:
                session2: PromptSession = PromptSession()  # type: ignore[type-arg]
                edited = await session2.prompt_async(
                    "  Edit (Esc+Enter to submit): ",
                    default=discovery,
                    multiline=True,
                )
                from payp.memory.manager import get_memory_backend
                backend = get_memory_backend()
                content = f"\n### {section.replace('_', ' ').title()}\n{edited}"
                result = await backend.save(conn, table, content, section=section)
                location = result.get("file") or result.get("id") or "memory"
                self.console.print(f"  [bold rgb(180,224,76)]✓ Saved (edited) to {location}[/bold rgb(180,224,76)]")
                return result
            except (EOFError, KeyboardInterrupt):
                self.console.print("  [dim]Cancelled[/dim]")
                return {"saved": False, "reason": "user cancelled edit"}
        else:
            self.console.print("  [dim]Discarded[/dim]")
            return {"saved": False, "reason": "user rejected"}

    async def _execute_code_with_approval(
        self,
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
        if self.mode == SecurityMode.YOLO:
            display_tool_call(self.console, tool_name, args)
            result = await tool.call(args, context)
            display_tool_result(self.console, tool_name, result.success, result.summary)
            return result.data if result.success else {"error": result.error}

        # Manual/Secure modes → show code in approval panel
        language = {
            "execute_python": "python",
            "execute_r": "r",
            "execute_shell": "bash",
        }.get(tool_name, "text")
        description = args.get("description", "")
        subtitle = f"{tool_name}" + (f" — {description}" if description else "")

        self.console.print(
            Panel(
                Syntax(code, language, theme="monokai", word_wrap=True),
                title="Code execution — approval required",
                subtitle=subtitle,
                border_style="yellow",
            )
        )

        # Pause AbortWatcher during approval
        watcher = getattr(self, "_watcher", None)
        if watcher:
            watcher.pause()
        try:
            approval = await ask_approval(
                self.console,
                code,
                title=f"{tool_name} — approval required",
                subtitle=f"Mode: {self.mode.value}",
                warning="Code runs in a subprocess with filesystem and network access.",
                hard_block=False,
            )
        finally:
            if watcher:
                watcher.resume()

        if approval.action == ApprovalAction.CANCEL:
            self.console.print("  [dim]Cancelled[/dim]")
            return {"error": "User cancelled code execution"}

        # Execute (possibly edited) code
        args = {**args, code_field: approval.sql}  # reusing .sql field from ApprovalResult
        display_tool_call(self.console, tool_name, {code_field: approval.sql[:80] + "..."})
        result = await tool.call(args, context)
        display_tool_result(self.console, tool_name, result.success, result.summary)
        if result.success and result.data:
            stdout = result.data.get("stdout", "")
            if stdout.strip():
                self.console.print(Panel(stdout.strip(), title="stdout", border_style="dim"))
            files = result.data.get("files_mentioned") or []
            if files:
                self.console.print(f"  [bold rgb(180,224,76)]✓ Files created:[/bold rgb(180,224,76)] {', '.join(files)}")
        elif not result.success and result.data and result.data.get("stderr"):
            self.console.print(
                Panel(result.data["stderr"][-500:], title="stderr", border_style="red")
            )
        return result.data if result.success else {"error": result.error}

    async def _execute_destructive_with_approval(
        self,
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
        if self.mode == SecurityMode.YOLO:
            display_tool_call(self.console, tool_name, args)
            result = await tool.call(args, context)
            display_tool_result(self.console, tool_name, result.success, result.summary)
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
            "total" not in preview and not preview.get("items") and "Would remove 0" in (preview.get("summary") or "")
        ):
            display_tool_call(self.console, tool_name, args)
            self.console.print(f"  [dim]{preview.get('summary', 'Nothing to do.')}[/dim]")
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

        self.console.print(
            Panel(
                body,
                title=f"[bold]{tool_name}[/bold] — approval required",
                subtitle=f"Mode: {self.mode.value}",
                border_style="yellow",
            )
        )

        # Ask y/N via prompt_toolkit (async — we're inside the event loop)
        from prompt_toolkit import PromptSession

        watcher = getattr(self, "_watcher", None)
        if watcher:
            watcher.pause()
        try:
            session: PromptSession = PromptSession()  # type: ignore[type-arg]
            answer = (await session.prompt_async("  Execute? [y/N] ")).strip().lower()
        except (KeyboardInterrupt, EOFError):
            answer = ""
        finally:
            if watcher:
                watcher.resume()

        if answer not in ("y", "yes"):
            self.console.print("  [dim]Cancelled[/dim]")
            return {"error": f"User cancelled {tool_name}", "cancelled": True}

        # Execute
        display_tool_call(self.console, tool_name, args)
        result = await tool.call(args, context)
        display_tool_result(self.console, tool_name, result.success, result.summary)
        return result.data if result.success else {"error": result.error}

    async def _execute_sql_with_mode(
        self,
        tool: BaseTool,
        args: dict[str, Any],
        context: dict[str, Any],
        user_request: str,
    ) -> Any:
        """Route execute_sql through the active security mode."""
        sql = args.get("sql", "").strip()
        if not sql:
            return {"error": "No SQL provided"}

        # Strip @connection-name prefix if LLM accidentally put it in the SQL
        # (these should be routed via the `connection` parameter or switch_connection)
        import re
        prefix_match = re.match(r"^@(\S+)\s+", sql)
        if prefix_match:
            prefix_name = prefix_match.group(1)
            sql = sql[prefix_match.end():].strip()
            args = {**args, "sql": sql}
            # If user's multi_conn has this connection, route to it temporarily
            if self.multi_conn and self.multi_conn.has(prefix_name):
                target_mgr = self.multi_conn.get(prefix_name).manager
                context = {**context, "connection_manager": target_mgr}
                self.console.print(
                    f"  [dim]→ routing via @{prefix_name}[/dim]"
                )

        # Determine real dialect from the connection manager — fall back
        # to "postgres" for DB-less sessions (e.g. scratchpad mode).
        target_mgr = context.get("connection_manager")
        dialect = "postgres"
        if target_mgr is not None:
            try:
                dialect = target_mgr.profile.db_type.value
            except Exception:
                dialect = "postgres"

        # ---- Layer 1: deterministic static pre-filter ---------------------
        static_block = statically_hard_blocked(sql, dialect=dialect)

        # Classify the SQL (dialect-aware)
        classification = classify_sql(sql, dialect=dialect)
        if static_block:
            classification.is_hard_block = True
            classification.risk_reason = static_block

        # SELECTs always bypass review/approval (unless hard-blocked by the
        # static pre-filter, e.g. SELECT pg_terminate_backend(...))
        if classification.category == SqlCategory.SELECT and not static_block:
            display_tool_call(self.console, "execute_sql", args)
            result = await tool.call(args, context)
            display_tool_result(self.console, "execute_sql", result.success, result.summary)
            self._display_tool_data("execute_sql", result, sql=sql)
            self._log_sql_execution(sql, classification.statement_type, result, approved_by="auto")
            return result.data if result.success else {"error": result.error}

        # Mode: YOLO — execute immediately, EXCEPT for statically-hard-blocked
        # operations. Yolo means "I trust the LLM", not "I consent to DROP
        # DATABASE and pg_terminate_backend forever".
        if self.mode == SecurityMode.YOLO and not static_block:
            display_tool_call(self.console, "execute_sql", args)
            result = await tool.call(args, context)
            display_tool_result(self.console, "execute_sql", result.success, result.summary)
            self._display_tool_data("execute_sql", result, sql=sql)
            self._log_sql_execution(sql, classification.statement_type, result, approved_by="yolo")
            return result.data if result.success else {"error": result.error}

        # ---- Layer 2: LLM reviewer (SECURE / SECURE_AUTO) ------------------
        review_reason = ""
        approved_sql = sql
        reviewer_verdict: str | None = None
        reviewer_reason: str | None = None
        reviewer_model: str | None = None
        consensus_verdict_str: str | None = None
        review = None  # type: ignore[assignment]
        secondary = None  # type: ignore[assignment]

        if static_block:
            # Skip the LLM — a poisoned reviewer cannot approve away a
            # statically-blocked operation. We go straight to the approval
            # UI with the hard_block flag set.
            review_reason = f"Static pre-filter: {static_block}"
        elif self.mode in (SecurityMode.SECURE, SecurityMode.SECURE_AUTO):
            reviewer = Reviewer(self.llm)
            conn_name = self.conn.profile.name if self.conn else "unknown"
            history_tail = self.messages[-6:] if self.messages else []

            # Destructive operations in SECURE_AUTO mode → consensus review.
            # Two independent models must agree before we auto-execute.
            use_consensus = (
                self.mode == SecurityMode.SECURE_AUTO
                and classification.category in (
                    SqlCategory.DDL,
                    SqlCategory.HARD_BLOCK,
                    SqlCategory.GRANT,
                )
            )
            # Unbounded DML also triggers consensus
            if (
                self.mode == SecurityMode.SECURE_AUTO
                and classification.category == SqlCategory.DML_WRITE
                and not classification.has_where
            ):
                use_consensus = True

            if use_consensus:
                review, secondary = await reviewer.review_with_consensus(
                    sql=sql,
                    user_request=user_request,
                    dialect=dialect,
                    stmt_type=classification.statement_type,
                    connection=conn_name,
                    conversation_tail=history_tail,
                )
            else:
                review = await reviewer.review(
                    sql=sql,
                    user_request=user_request,
                    dialect=dialect,
                    stmt_type=classification.statement_type,
                    connection=conn_name,
                    conversation_tail=history_tail,
                )

            reviewer_verdict = review.verdict.value.upper()
            reviewer_reason = review.reason
            reviewer_model = review.reviewer_model or self.llm.get_reviewer_model()
            if secondary is not None:
                consensus_verdict_str = secondary.verdict.value.upper()
                self.console.print(
                    f"  [dim]Reviewer A ({review.reviewer_model}): "
                    f"[bold]{reviewer_verdict}[/bold] — {review.reason}[/dim]"
                )
                self.console.print(
                    f"  [dim]Reviewer B ({secondary.reviewer_model}): "
                    f"[bold]{consensus_verdict_str}[/bold] — {secondary.reason}[/dim]"
                )
            else:
                self.console.print(
                    f"  [dim]Reviewer ({reviewer_model}): "
                    f"[bold]{reviewer_verdict}[/bold] — {review.reason}[/dim]"
                )

            if review.verdict == Verdict.HARD_BLOCK:
                classification.is_hard_block = True
                review_reason = review.reason
            elif review.verdict == Verdict.SAFER and review.safer_sql:
                self.console.print(
                    "  [yellow]→ Using safer version suggested by reviewer[/yellow]"
                )
                approved_sql = review.safer_sql
                review_reason = review.reason

            # Consensus gating: any reviewer saying HARD_BLOCK wins.
            # If consensus was required and the two disagree on APPROVE vs
            # anything else, fall through to the manual approval UI.
            consensus_ok_auto = True
            if use_consensus:
                primary_ok = review.verdict == Verdict.APPROVE
                secondary_ok = (
                    secondary is not None and secondary.verdict == Verdict.APPROVE
                )
                consensus_ok_auto = primary_ok and secondary_ok
                if (
                    secondary is not None
                    and secondary.verdict == Verdict.HARD_BLOCK
                ):
                    classification.is_hard_block = True
                    review_reason = (
                        f"{review.reason} | second reviewer: {secondary.reason}"
                    )

            # SECURE-AUTO with APPROVE (and consensus if applicable) → execute
            if (
                self.mode == SecurityMode.SECURE_AUTO
                and review.verdict == Verdict.APPROVE
                and consensus_ok_auto
                and not classification.is_hard_block
            ):
                display_tool_call(self.console, "execute_sql", {"sql": approved_sql})
                args = {**args, "sql": approved_sql}
                result = await tool.call(args, context)
                display_tool_result(self.console, "execute_sql", result.success, result.summary)
                self._display_tool_data("execute_sql", result, sql=approved_sql)
                self._log_sql_execution(
                    approved_sql, classification.statement_type, result,
                    approved_by="reviewer",
                    reviewer_verdict=reviewer_verdict,
                    reviewer_reason=reviewer_reason,
                    reviewer_model=reviewer_model,
                    reviewer_overridden=False,
                    consensus_verdict=consensus_verdict_str,
                )
                return result.data if result.success else {"error": result.error}

            # SECURE-AUTO with SAFER → execute the safer version
            if (
                self.mode == SecurityMode.SECURE_AUTO
                and review.verdict == Verdict.SAFER
                and not classification.is_hard_block
            ):
                display_tool_call(self.console, "execute_sql", {"sql": approved_sql})
                args = {**args, "sql": approved_sql}
                result = await tool.call(args, context)
                display_tool_result(self.console, "execute_sql", result.success, result.summary)
                self._display_tool_data("execute_sql", result, sql=approved_sql)
                self._log_sql_execution(
                    approved_sql, classification.statement_type, result,
                    approved_by="reviewer-safer",
                    reviewer_verdict=reviewer_verdict,
                    reviewer_reason=reviewer_reason,
                    reviewer_model=reviewer_model,
                    reviewer_overridden=False,
                    consensus_verdict=consensus_verdict_str,
                )
                return result.data if result.success else {"error": result.error}

        # ---- Layer 3: Manual approval UI -----------------------------------
        warning = None
        if classification.risk_reason:
            warning = classification.risk_reason
        if review_reason:
            warning = f"{review_reason}\n\n{warning}" if warning else review_reason

        # Pause AbortWatcher — approval UI needs exclusive stdin access
        watcher = getattr(self, "_watcher", None)
        if watcher:
            watcher.pause()
        try:
            approval = await ask_approval(
                self.console,
                approved_sql,
                title=f"{classification.statement_type} — approval required",
                subtitle=f"Mode: {self.mode.value}",
                warning=warning,
                hard_block=classification.is_hard_block,
            )
        finally:
            if watcher:
                watcher.resume()

        if approval.action == ApprovalAction.CANCEL:
            self.console.print("  [dim]Cancelled[/dim]")
            self.tx_log.log(
                connection_name=self.conn.profile.name if self.conn else "no-db",
                operation_type=classification.statement_type,
                sql_executed=approved_sql,
                execution_mode=self.mode.value,
                approved_by="user-cancelled",
                status="cancelled",
                model_used=self.llm.get_executor_model(),
                reviewer_verdict=reviewer_verdict,
                reviewer_reason=reviewer_reason,
                reviewer_model=reviewer_model,
                static_block_reason=static_block,
                consensus_verdict=consensus_verdict_str,
            )
            return {"error": "User cancelled the operation"}

        # Execute (possibly edited) SQL
        args = {**args, "sql": approval.sql}
        display_tool_call(self.console, "execute_sql", args)
        result = await tool.call(args, context)
        display_tool_result(self.console, "execute_sql", result.success, result.summary)
        self._display_tool_data("execute_sql", result, sql=approval.sql)
        approved_by = "user-override" if approval.action == ApprovalAction.OVERRIDE else "user"
        # Reviewer was overridden if it said anything other than APPROVE
        # (or if the static pre-filter blocked) and the user chose OVERRIDE.
        overridden = (
            approval.action == ApprovalAction.OVERRIDE
            and (
                static_block is not None
                or (reviewer_verdict is not None and reviewer_verdict != "APPROVE")
            )
        )
        self._log_sql_execution(
            approval.sql, classification.statement_type, result,
            approved_by=approved_by,
            reviewer_verdict=reviewer_verdict,
            reviewer_reason=reviewer_reason,
            reviewer_model=reviewer_model,
            reviewer_overridden=overridden,
            static_block_reason=static_block,
            consensus_verdict=consensus_verdict_str,
        )
        return result.data if result.success else {"error": result.error}

    async def _get_system_prompt(self, user_text: str = "") -> str:
        """Build the dynamic system prompt with smart T2 injection."""
        conn_name = None
        db_version = None
        db_type = "postgresql"
        if self.conn and self.conn.is_connected:
            conn_name = self.conn.profile.name
            db_version = self.conn.db_version
            db_type = self.conn.profile.db_type.value

        # Smart T2 injection — load DDL for tables mentioned in user input
        t2_context = ""
        if self.conn and self.conn.is_connected and self.t1 and user_text:
            from payp.core.context import build_t2_context
            try:
                t2_context = await build_t2_context(
                    conn=self.conn,
                    catalog=self.t1,
                    user_text=user_text,
                )
            except Exception:
                pass

        from payp.storage.knowledge import get_knowledge_dir
        active_connections = (
            self.multi_conn.list_info() if self.multi_conn else None
        )
        # Filter skills by the active dialect (or show all if disconnected)
        skills_for_prompt = (
            self.skills.for_dialect(db_type) if self.conn and self.conn.is_connected
            else self.skills.all()
        )
        return build_system_prompt(
            mode=self.mode,
            connection_name=conn_name,
            db_version=db_version,
            db_type=db_type,
            t0=self.t0,
            t1=self.t1,
            t2_context=t2_context if t2_context else None,
            knowledge_dir=get_knowledge_dir(),
            active_connections=active_connections,
            skills=skills_for_prompt or None,
        )

    def get_context_stats(self, system_prompt: str = "") -> Any:
        """Return current context usage statistics."""
        from payp.core.compaction import count_tokens, get_context_stats
        model = self.llm.get_executor_model()
        sys_tokens = count_tokens([{"role": "system", "content": system_prompt}], model) if system_prompt else 0
        return get_context_stats(self.messages, model, system_prompt_tokens=sys_tokens)

    async def _auto_compact_if_needed(self, system_prompt: str) -> None:
        """Auto-compact if context usage >= 75%."""
        stats = self.get_context_stats(system_prompt)
        if stats.should_compact:
            self.console.print(
                f"  [yellow]⚠ Context at {stats.usage_ratio*100:.0f}% "
                f"({stats.used_tokens:,}/{stats.max_tokens:,} tokens). "
                f"Compacting older messages...[/yellow]"
            )
            await self.compact()

    async def compact(self, keep_recent: int = 8) -> dict[str, int]:
        """Summarize older messages to free up context.

        Returns stats: {before_tokens, after_tokens, saved_tokens}.
        """
        from payp.core.compaction import compact_messages
        model = self.llm.get_executor_model()
        new_messages, stats = await compact_messages(
            self.messages, model, self.llm, keep_recent=keep_recent
        )
        self.messages = new_messages
        if stats["saved_tokens"] > 0:
            self.console.print(
                f"  [bold rgb(180,224,76)]✓ Compacted:[/bold rgb(180,224,76)] "
                f"{stats['before_tokens']:,} → {stats['after_tokens']:,} tokens "
                f"[dim](saved {stats['saved_tokens']:,})[/dim]"
            )
        return stats

    async def send_message(self, user_input: str) -> None:
        """Process a user message through the full chat loop."""
        from payp.ui.abort import AbortWatcher

        # Add user message to history
        self.messages.append({"role": "user", "content": user_input})
        self.session.log_user(user_input)

        # Build messages with system prompt
        system_prompt = await self._get_system_prompt(user_text=user_input)

        # Check context usage and auto-compact if needed
        await self._auto_compact_if_needed(system_prompt)

        full_messages = [
            {"role": "system", "content": system_prompt},
            *self.messages,
        ]

        tools = self.registry.all_definitions()

        # Wrap the whole loop with Esc-abort (AbortController pattern from Claude Code)
        async with AbortWatcher() as watcher:
            self._watcher = watcher  # expose for approval UI to pause/resume
            try:
                await self._run_tool_loop(full_messages, tools, user_input, watcher)
            finally:
                self._watcher = None

    async def _run_tool_loop(
        self,
        full_messages: list[dict[str, Any]],
        tools: Any,
        user_input: str,
        watcher: Any,
    ) -> None:
        """Inner tool call loop with abort support."""
        # Tool call loop — LLM may call tools multiple times
        for _round in range(MAX_TOOL_ROUNDS):
            if watcher.aborted:
                self.console.print("  [yellow]↯ Aborted by user (Esc).[/yellow]")
                return
            # Stream LLM response
            chunks = self.llm.chat_stream(full_messages, tools=tools)
            content, raw_tool_calls = await stream_response(chunks, self.console)

            # If LLM returned text content, add to history AND persist to the
            # session JSONL so /resume can reconstruct the full conversation.
            # (log_user is called once in send_message; log_assistant must be
            # called here — once per LLM turn — because there can be multiple
            # assistant turns inside a single user message when tools run.)
            if content:
                self.messages.append({"role": "assistant", "content": content})
                try:
                    self.session.log_assistant(
                        content, model=self.llm.get_executor_model()
                    )
                except Exception:
                    # Logging must never break the chat loop.
                    pass

            # If no tool calls, we're done
            if not raw_tool_calls:
                # Detect empty response (no text AND no tools) — LLM stopped silently
                if not content or not content.strip():
                    self.console.print(
                        "  [dim yellow]⚠ The assistant returned an empty response. "
                        "Nudging it to continue...[/dim yellow]"
                    )
                    # Inject a nudge and retry once
                    full_messages.append({
                        "role": "user",
                        "content": (
                            "You returned an empty response. Please either complete the task, "
                            "ask me a specific question if you're stuck, or explain what went wrong."
                        ),
                    })
                    continue  # retry the loop
                break

            # Process tool calls
            tool_calls = _parse_tool_calls(raw_tool_calls)

            # Add assistant message with tool calls to history
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in tool_calls
                ],
            }
            # Replace last assistant message if we added content
            if content and self.messages and self.messages[-1].get("role") == "assistant":
                self.messages[-1] = assistant_msg
            else:
                self.messages.append(assistant_msg)

            # Execute each tool call.
            # We collect every tool response into a per-round list and then
            # append the assistant message ONCE followed by all responses.
            # This keeps the OpenAI-required ordering intact — every
            # tool_call must be matched by a `tool` message in sequence —
            # and the same block is persisted into self.messages so the
            # NEXT user turn sees a valid history. (Previously: assistant
            # was appended once per tool call, responses never reached
            # self.messages, and strict providers like Azure OpenAI 400'd
            # with "No tool output found for function call ...".)
            context = self._build_context()
            tool_response_messages: list[dict[str, Any]] = []
            for tc in tool_calls:
                if watcher.aborted:
                    self.console.print("  [yellow]↯ Aborted by user (Esc).[/yellow]")
                    return
                tool = self.registry.get(tc.name)
                if not tool:
                    tool_response: Any = {"error": f"Unknown tool: {tc.name}"}
                    display_tool_result(self.console, tc.name, False, f"Unknown tool: {tc.name}")
                else:
                    # Security mode interception for execute_sql
                    if tc.name == "execute_sql":
                        tool_response = await self._execute_sql_with_mode(
                            tool, tc.arguments, context, user_input
                        )
                    elif tc.name in ("execute_python", "execute_r", "execute_shell"):
                        tool_response = await self._execute_code_with_approval(
                            tool, tc.arguments, context, tc.name
                        )
                    elif getattr(tool, "is_destructive", False):
                        # Any other destructive tool (cleanup, delete_snapshot,
                        # delete_query, ...) → universal preview + approval.
                        tool_response = await self._execute_destructive_with_approval(
                            tool, tc.arguments, context
                        )
                    else:
                        display_tool_call(self.console, tc.name, tc.arguments)
                        result = await tool.call(tc.arguments, context)
                        display_tool_result(self.console, tc.name, result.success, result.summary)
                        tool_response = result.data if result.success else {"error": result.error}
                        # Log skill invocations to the session
                        if tc.name == "invoke_skill" and result.success:
                            skill_name = tc.arguments.get("skill_name", "")
                            try:
                                self.console.print(
                                    f"  [dim]↳ activated skill:[/dim] [bold cyan]{skill_name}[/bold cyan]"
                                )
                            except Exception:
                                pass
                        # Handle knowledge proposal — ask user before saving
                        if tc.name == "propose_knowledge" and result.success and result.data:
                            tool_response = await self._handle_knowledge_proposal(result.data)

                # Persist the tool call to the session JSONL so /resume can
                # reconstruct what happened. Best-effort summary: error string
                # on failure, or a compact success descriptor otherwise.
                try:
                    if isinstance(tool_response, dict) and "error" in tool_response:
                        _tc_ok = False
                        _tc_sum = str(tool_response.get("error", ""))[:200]
                    else:
                        _tc_ok = True
                        _tc_sum = _summarize_tool_response(tc.name, tool_response)
                    self.session.log_tool_call(
                        tool_name=tc.name,
                        args=tc.arguments,
                        success=_tc_ok,
                        summary=_tc_sum,
                    )
                except Exception:
                    pass

                # Wrap the payload in an untrusted envelope so a row value
                # like "ignore previous instructions" is received as DATA,
                # not as an instruction. See _wrap_tool_output for details.
                tool_response_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": self._wrap_tool_output(tc.name, tool_response),
                })

            # Append assistant (tool_calls) + all tool responses as a single
            # contiguous block to BOTH the transient in-flight list and the
            # persistent history so subsequent turns replay correctly.
            full_messages.append(assistant_msg)
            full_messages.extend(tool_response_messages)
            self.messages.extend(tool_response_messages)

            # Continue the loop — LLM will process tool results and may call more tools
            # or generate a final text response
        else:
            # for/else: loop exited normally (no break) — hit MAX_TOOL_ROUNDS
            self.console.print(
                f"  [yellow]⚠ Reached tool call limit ({MAX_TOOL_ROUNDS}). "
                f"Ask me to continue if you need more.[/yellow]"
            )

        # Done


def _summarize_tool_response(tool_name: str, response: Any) -> str:
    """Build a compact one-line summary of a tool response for session logs.

    Kept free of the actual payload so session files stay small — the row
    data itself is intentionally not persisted.
    """
    if not isinstance(response, dict):
        return f"{tool_name}: ok"
    d = response
    # execute_sql-style results
    if "row_count" in d or "rows_affected" in d or "execution_ms" in d:
        rows = d.get("row_count")
        if rows is None:
            rows = d.get("rows_affected")
        ms = d.get("execution_ms")
        bits = []
        if rows is not None:
            bits.append(f"{rows} row{'s' if rows != 1 else ''}")
        if ms is not None:
            bits.append(f"{ms}ms")
        if bits:
            return ", ".join(bits)
    # propose_knowledge + chart tools often return a small descriptor
    if "table" in d and "section" in d:
        return f"knowledge proposal for {d.get('table')}"
    if "chart_type" in d or "points" in d:
        pts = d.get("points") or d.get("point_count")
        t = d.get("chart_type", "chart")
        return f"{t} rendered" + (f" ({pts} points)" if pts else "")
    if "saved" in d and d.get("saved"):
        return f"saved → {d.get('file') or d.get('id') or 'memory'}"
    if "count" in d:
        return f"{d['count']} item(s)"
    return f"{tool_name}: ok"


def _parse_tool_calls(raw_calls: list[dict]) -> list[ToolCall]:
    """Parse raw tool call dicts from streaming into ToolCall objects."""
    result = []
    for raw in raw_calls:
        try:
            arguments = json.loads(raw.get("arguments", "{}"))
        except json.JSONDecodeError:
            arguments = {}

        result.append(ToolCall(
            id=raw.get("id", ""),
            name=raw.get("name", ""),
            arguments=arguments,
        ))
    return result
