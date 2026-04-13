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

from typing import Any

from rich.console import Console

from payp.core.llm import LLMClient
from payp.db.connection import ConnectionManager
from payp.models import SchemaCatalog, SchemaGraph, SchemaIndex, SecurityMode
from payp.prompts.system import build_system_prompt
from payp.skills.registry import SkillRegistry, discover_skills
from payp.storage.sessions import SessionWriter
from payp.storage.transaction_log import TransactionLog
from payp.tools.base import BaseTool, ToolRegistry
from payp.tools.registry import build_cli_registry
from payp.ui.display import display_dml_result, display_query_result

from ._approvals import (
    execute_code_with_approval,
    execute_destructive_with_approval,
    handle_knowledge_proposal,
)
from ._sql_pipeline import execute_sql_with_mode, log_sql_execution
from ._tool_io import wrap_tool_output
from ._tool_loop import run_tool_loop


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
        fk_graph: SchemaGraph | None = None,
    ) -> None:
        self.llm = llm
        self.console = console
        self.conn = connection_manager
        self.mode = mode
        self.t0 = t0
        self.t1 = t1
        self.fk_graph = fk_graph
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
        """Register all available tools for the interactive CLI.

        The catalog lives in `payp.tools.registry` — single source of
        truth shared with the MCP server. `write_knowledge` is excluded
        because the CLI uses the propose_knowledge → user approval flow.
        """
        return build_cli_registry()

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
        return wrap_tool_output(tool_name, payload)

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
        log_sql_execution(
            self,
            sql,
            operation_type,
            result,
            approved_by,
            reviewer_verdict=reviewer_verdict,
            reviewer_reason=reviewer_reason,
            reviewer_model=reviewer_model,
            reviewer_overridden=reviewer_overridden,
            static_block_reason=static_block_reason,
            consensus_verdict=consensus_verdict,
        )

    async def _handle_knowledge_proposal(self, data: dict[str, Any]) -> dict[str, Any]:
        return await handle_knowledge_proposal(self, data)

    async def _execute_code_with_approval(
        self,
        tool: BaseTool,
        args: dict[str, Any],
        context: dict[str, Any],
        tool_name: str,
    ) -> Any:
        return await execute_code_with_approval(self, tool, args, context, tool_name)

    async def _execute_destructive_with_approval(
        self,
        tool: BaseTool,
        args: dict[str, Any],
        context: dict[str, Any],
    ) -> Any:
        return await execute_destructive_with_approval(self, tool, args, context)

    async def _execute_sql_with_mode(
        self,
        tool: BaseTool,
        args: dict[str, Any],
        context: dict[str, Any],
        user_request: str,
    ) -> Any:
        return await execute_sql_with_mode(self, tool, args, context, user_request)

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
            fk_graph=self.fk_graph,
            t2_context=t2_context if t2_context else None,
            knowledge_dir=get_knowledge_dir(),
            active_connections=active_connections,
            skills=skills_for_prompt or None,
        )

    def get_context_stats(self, system_prompt: str = "") -> Any:
        """Return current context usage statistics."""
        from payp.core.compaction import count_tokens, get_context_stats
        model = self.llm.get_executor_model()
        sys_tokens = (
            count_tokens([{"role": "system", "content": system_prompt}], model)
            if system_prompt
            else 0
        )
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
        return await run_tool_loop(self, full_messages, tools, user_input, watcher)
