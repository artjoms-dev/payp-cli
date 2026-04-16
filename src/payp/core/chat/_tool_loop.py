from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from payp.ui.streaming import display_tool_call, display_tool_result, stream_response

from ._tool_io import parse_tool_calls, summarize_tool_response

if TYPE_CHECKING:
    from ._facade import ChatSession

logger = logging.getLogger(__name__)


MAX_TOOL_ROUNDS = 30  # Prevent infinite tool call loops (Claude Code uses 30-50)


async def run_tool_loop(
    session: ChatSession,
    full_messages: list[dict[str, Any]],
    tools: Any,
    user_input: str,
    watcher: Any,
) -> None:
    """Inner tool call loop with abort support."""
    # Tool call loop — LLM may call tools multiple times
    for _round in range(MAX_TOOL_ROUNDS):
        if watcher.aborted:
            session.console.print("  [yellow]↯ Aborted by user (Esc).[/yellow]")
            return
        # Stream LLM response
        chunks = session.llm.chat_stream(full_messages, tools=tools)
        content, raw_tool_calls = await stream_response(chunks, session.console)

        # If LLM returned text content, add to history AND persist to the
        # session JSONL so /resume can reconstruct the full conversation.
        # (log_user is called once in send_message; log_assistant must be
        # called here — once per LLM turn — because there can be multiple
        # assistant turns inside a single user message when tools run.)
        if content:
            session.messages.append({"role": "assistant", "content": content})
            try:
                session.session.log_assistant(content, model=session.llm.get_executor_model())
            except Exception:
                logger.exception("session log_assistant failed")

        # If no tool calls, we're done
        if not raw_tool_calls:
            # Detect empty response (no text AND no tools) — LLM stopped silently
            if not content or not content.strip():
                session.console.print(
                    "  [dim yellow]⚠ The assistant returned an empty response. "
                    "Nudging it to continue...[/dim yellow]"
                )
                # Inject a nudge and retry once
                full_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You returned an empty response. Please either complete the task, "
                            "ask me a specific question if you're stuck, "
                            "or explain what went wrong."
                        ),
                    }
                )
                continue  # retry the loop
            break

        # Process tool calls
        tool_calls = parse_tool_calls(raw_tool_calls)

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
        if content and session.messages and session.messages[-1].get("role") == "assistant":
            session.messages[-1] = assistant_msg
        else:
            session.messages.append(assistant_msg)

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
        context = session._build_context()
        tool_response_messages: list[dict[str, Any]] = []
        for tc in tool_calls:
            if watcher.aborted:
                session.console.print("  [yellow]↯ Aborted by user (Esc).[/yellow]")
                return
            tool = session.registry.get(tc.name)
            if not tool:
                tool_response: Any = {"error": f"Unknown tool: {tc.name}"}
                display_tool_result(
                    session.console, tc.name, False, f"Unknown tool: {tc.name}"
                )
            else:
                # Security mode interception for execute_sql
                if tc.name == "execute_sql":
                    tool_response = await session._execute_sql_with_mode(
                        tool, tc.arguments, context, user_input
                    )
                elif tc.name in ("execute_python", "execute_r", "execute_shell"):
                    tool_response = await session._execute_code_with_approval(
                        tool, tc.arguments, context, tc.name
                    )
                elif getattr(tool, "is_destructive", False):
                    # Any other destructive tool (cleanup, delete_snapshot,
                    # delete_query, ...) → universal preview + approval.
                    tool_response = await session._execute_destructive_with_approval(
                        tool, tc.arguments, context
                    )
                else:
                    display_tool_call(session.console, tc.name, tc.arguments)
                    result = await tool.call(tc.arguments, context)
                    display_tool_result(session.console, tc.name, result.success, result.summary)
                    tool_response = result.data if result.success else {"error": result.error}
                    # Log skill invocations to the session
                    if tc.name == "invoke_skill" and result.success:
                        skill_name = tc.arguments.get("skill_name", "")
                        try:
                            session.console.print(
                                "  [dim]↳ activated skill:[/dim] "
                                f"[bold cyan]{skill_name}[/bold cyan]"
                            )
                        except Exception:
                            pass
                    # Handle knowledge proposal — ask user before saving
                    if tc.name == "propose_knowledge" and result.success and result.data:
                        tool_response = await session._handle_knowledge_proposal(result.data)

            # Persist the tool call to the session JSONL so /resume can
            # reconstruct what happened. Best-effort summary: error string
            # on failure, or a compact success descriptor otherwise.
            try:
                if isinstance(tool_response, dict) and "error" in tool_response:
                    _tc_ok = False
                    _tc_sum = str(tool_response.get("error", ""))[:200]
                else:
                    _tc_ok = True
                    _tc_sum = summarize_tool_response(tc.name, tool_response)
                session.session.log_tool_call(
                    tool_name=tc.name,
                    args=tc.arguments,
                    success=_tc_ok,
                    summary=_tc_sum,
                )
            except Exception:
                logger.exception("session log_tool_call failed")

            # Wrap the payload in an untrusted envelope so a row value
            # like "ignore previous instructions" is received as DATA,
            # not as an instruction. See _wrap_tool_output for details.
            tool_response_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": session._wrap_tool_output(tc.name, tool_response),
                }
            )

        # Append assistant (tool_calls) + all tool responses as a single
        # contiguous block to BOTH the transient in-flight list and the
        # persistent history so subsequent turns replay correctly.
        full_messages.append(assistant_msg)
        full_messages.extend(tool_response_messages)
        session.messages.extend(tool_response_messages)

        # Continue the loop — LLM will process tool results and may call more tools
        # or generate a final text response
    else:
        # for/else: loop exited normally (no break) — hit MAX_TOOL_ROUNDS
        session.console.print(
            f"  [yellow]⚠ Reached tool call limit ({MAX_TOOL_ROUNDS}). "
            f"Ask me to continue if you need more.[/yellow]"
        )

    # Done
