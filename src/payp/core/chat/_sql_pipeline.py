from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from payp.core.classifier import SqlCategory, classify_sql, statically_hard_blocked
from payp.core.reviewer import Reviewer, Verdict
from payp.models import SecurityMode
from payp.tools.base import BaseTool
from payp.ui.approval import ApprovalAction, ask_approval
from payp.ui.streaming import display_tool_call, display_tool_result

if TYPE_CHECKING:
    from ._facade import ChatSession

logger = logging.getLogger(__name__)


def log_sql_execution(
    session: ChatSession,
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
    conn_name = session.conn.profile.name if session.conn else "no-db"
    status = "success" if result.success else "failed"
    error = result.error if not result.success else None
    rows_affected = None
    execution_ms = None
    if result.success and result.data:
        d = result.data
        rows_affected = d.get("row_count") or d.get("rows_affected")
        execution_ms = d.get("execution_ms")
    try:
        session.tx_log.log(
            connection_name=conn_name,
            operation_type=operation_type,
            sql_executed=sql,
            execution_mode=session.mode.value,
            approved_by=approved_by,
            status=status,
            error_message=error,
            rows_affected=rows_affected,
            execution_ms=execution_ms,
            model_used=session.llm.get_executor_model(),
            reviewer_verdict=reviewer_verdict,
            reviewer_reason=reviewer_reason,
            reviewer_model=reviewer_model,
            reviewer_overridden=reviewer_overridden,
            static_block_reason=static_block_reason,
            consensus_verdict=consensus_verdict,
        )
    except Exception as e:
        logger.warning("Transaction log write failed: %s", e)


async def execute_sql_with_mode(
    session: ChatSession,
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
    prefix_match = re.match(r"^@(\S+)\s+", sql)
    if prefix_match:
        prefix_name = prefix_match.group(1)
        sql = sql[prefix_match.end() :].strip()
        args = {**args, "sql": sql}
        # If user's multi_conn has this connection, route to it temporarily
        if session.multi_conn and session.multi_conn.has(prefix_name):
            target_mgr = session.multi_conn.get(prefix_name).manager
            context = {**context, "connection_manager": target_mgr}
            session.console.print(
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
        display_tool_call(session.console, "execute_sql", args)
        result = await tool.call(args, context)
        display_tool_result(session.console, "execute_sql", result.success, result.summary)
        session._display_tool_data("execute_sql", result, sql=sql)
        log_sql_execution(session, sql, classification.statement_type, result, approved_by="auto")
        return result.data if result.success else {"error": result.error}

    # Mode: YOLO — execute immediately, EXCEPT for statically-hard-blocked
    # operations. Yolo means "I trust the LLM", not "I consent to DROP
    # DATABASE and pg_terminate_backend forever".
    if session.mode == SecurityMode.YOLO and not static_block:
        display_tool_call(session.console, "execute_sql", args)
        result = await tool.call(args, context)
        display_tool_result(session.console, "execute_sql", result.success, result.summary)
        session._display_tool_data("execute_sql", result, sql=sql)
        log_sql_execution(session, sql, classification.statement_type, result, approved_by="yolo")
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
    elif session.mode in (SecurityMode.SECURE, SecurityMode.SECURE_AUTO):
        reviewer = Reviewer(session.llm)
        conn_name = session.conn.profile.name if session.conn else "unknown"
        history_tail = session.messages[-6:] if session.messages else []

        # Destructive operations in SECURE_AUTO mode → consensus review.
        # Two independent models must agree before we auto-execute.
        use_consensus = (
            session.mode == SecurityMode.SECURE_AUTO
            and classification.category in (
                SqlCategory.DDL,
                SqlCategory.HARD_BLOCK,
                SqlCategory.GRANT,
            )
        )
        # Unbounded DML also triggers consensus
        if (
            session.mode == SecurityMode.SECURE_AUTO
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
        reviewer_model = review.reviewer_model or session.llm.get_reviewer_model()
        if secondary is not None:
            consensus_verdict_str = secondary.verdict.value.upper()
            session.console.print(
                f"  [dim]Reviewer A ({review.reviewer_model}): "
                f"[bold]{reviewer_verdict}[/bold] — {review.reason}[/dim]"
            )
            session.console.print(
                f"  [dim]Reviewer B ({secondary.reviewer_model}): "
                f"[bold]{consensus_verdict_str}[/bold] — {secondary.reason}[/dim]"
            )
        else:
            session.console.print(
                f"  [dim]Reviewer ({reviewer_model}): "
                f"[bold]{reviewer_verdict}[/bold] — {review.reason}[/dim]"
            )

        if review.verdict == Verdict.HARD_BLOCK:
            classification.is_hard_block = True
            review_reason = review.reason
        elif review.verdict == Verdict.SAFER and review.safer_sql:
            session.console.print(
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
            secondary_ok = secondary is not None and secondary.verdict == Verdict.APPROVE
            consensus_ok_auto = primary_ok and secondary_ok
            if secondary is not None and secondary.verdict == Verdict.HARD_BLOCK:
                classification.is_hard_block = True
                review_reason = (
                    f"{review.reason} | second reviewer: {secondary.reason}"
                )

        # SECURE-AUTO with APPROVE (and consensus if applicable) → execute
        if (
            session.mode == SecurityMode.SECURE_AUTO
            and review.verdict == Verdict.APPROVE
            and consensus_ok_auto
            and not classification.is_hard_block
        ):
            display_tool_call(session.console, "execute_sql", {"sql": approved_sql})
            args = {**args, "sql": approved_sql}
            result = await tool.call(args, context)
            display_tool_result(session.console, "execute_sql", result.success, result.summary)
            session._display_tool_data("execute_sql", result, sql=approved_sql)
            log_sql_execution(
                session,
                approved_sql,
                classification.statement_type,
                result,
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
            session.mode == SecurityMode.SECURE_AUTO
            and review.verdict == Verdict.SAFER
            and not classification.is_hard_block
        ):
            display_tool_call(session.console, "execute_sql", {"sql": approved_sql})
            args = {**args, "sql": approved_sql}
            result = await tool.call(args, context)
            display_tool_result(session.console, "execute_sql", result.success, result.summary)
            session._display_tool_data("execute_sql", result, sql=approved_sql)
            log_sql_execution(
                session,
                approved_sql,
                classification.statement_type,
                result,
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
    watcher = getattr(session, "_watcher", None)
    if watcher:
        watcher.pause()
    try:
        approval = await ask_approval(
            session.console,
            approved_sql,
            title=f"{classification.statement_type} — approval required",
            subtitle=f"Mode: {session.mode.value}",
            warning=warning,
            hard_block=classification.is_hard_block,
        )
    finally:
        if watcher:
            watcher.resume()

    if approval.action == ApprovalAction.CANCEL:
        session.console.print("  [dim]Cancelled[/dim]")
        session.tx_log.log(
            connection_name=session.conn.profile.name if session.conn else "no-db",
            operation_type=classification.statement_type,
            sql_executed=approved_sql,
            execution_mode=session.mode.value,
            approved_by="user-cancelled",
            status="cancelled",
            model_used=session.llm.get_executor_model(),
            reviewer_verdict=reviewer_verdict,
            reviewer_reason=reviewer_reason,
            reviewer_model=reviewer_model,
            static_block_reason=static_block,
            consensus_verdict=consensus_verdict_str,
        )
        return {"error": "User cancelled the operation"}

    # Execute (possibly edited) SQL
    args = {**args, "sql": approval.sql}
    display_tool_call(session.console, "execute_sql", args)
    result = await tool.call(args, context)
    display_tool_result(session.console, "execute_sql", result.success, result.summary)
    session._display_tool_data("execute_sql", result, sql=approval.sql)
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
    log_sql_execution(
        session,
        approval.sql,
        classification.statement_type,
        result,
        approved_by=approved_by,
        reviewer_verdict=reviewer_verdict,
        reviewer_reason=reviewer_reason,
        reviewer_model=reviewer_model,
        reviewer_overridden=overridden,
        static_block_reason=static_block,
        consensus_verdict=consensus_verdict_str,
    )
    return result.data if result.success else {"error": result.error}
