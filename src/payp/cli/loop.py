"""Chat session management and session-log helpers for the payp CLI.

Houses _ensure_chat_session (rebuilds the ChatSession when connection or mode
changes) and the session-log helpers (_log_db_connected_to_session,
_log_memory_backend_to_session, _short).

_interactive_loop lives here too - it is populated in dispatch.py's init
so that the completer and handler are available first. See dispatch.py.
"""

from __future__ import annotations

from payp.cli.runtime import _run_async
from payp.cli.state import _state, console, get_config
from payp.config import load_models_config


def _stop_banner_animator() -> None:
    """Stop the welcome-banner shimmer thread if it is still running.

    Pops the animator out of state so subsequent calls are no-ops.
    """
    anim = _state.pop("banner_anim", None)
    if anim is not None:
        anim.stop()


def _ensure_chat_session() -> None:
    """Initialize or reinitialize the chat session."""
    from payp.core.chat import ChatSession
    from payp.core.llm import LLMClient

    # Check if models are configured
    providers = load_models_config()
    if not providers:
        return

    # Create LLM client
    if not _state.get("llm_client"):
        _state["llm_client"] = LLMClient()

    config = get_config()

    # Preserve message history if chat session already exists
    existing_messages = None
    existing_chat = _state.get("chat_session")
    if existing_chat:
        existing_messages = existing_chat.messages

    new_session = ChatSession(
        llm=_state["llm_client"],
        console=console,
        connection_manager=_state.get("connection_manager"),
        mode=config.default_mode,
        t0=_state.get("t0"),
        t1=_state.get("t1"),
        fk_graph=_state.get("fk_graph"),
    )

    # Restore message history
    if existing_messages:
        new_session.messages = existing_messages

    # Share the CLI's multi_conn manager so the LLM sees all active connections
    mc = _state.get("multi_conn_manager")
    if mc:
        new_session.multi_conn = mc

    _state["chat_session"] = new_session

    # Record the active memory backend so /resume can restore it.
    try:
        from payp.memory.manager import get_memory_backend
        new_session.session.log_memory_backend(get_memory_backend().name)
    except Exception:
        pass


def _log_db_connected_to_session(connection_name: str) -> None:
    """Record the active DB in the current chat session's JSONL file.

    Used by /resume to auto-reconnect to the same database. Safe no-op
    if the chat session isn't initialised yet (e.g. models not configured).
    """
    chat = _state.get("chat_session")
    if not chat:
        return
    session = getattr(chat, "session", None)
    if not session:
        return
    try:
        session.log_db_connected(connection_name)
    except Exception:
        # Persistence must never break the connect flow.
        pass


def _short(s: str, max_len: int) -> str:
    """Truncate a string with an ellipsis for one-line previews."""
    s = s.replace("\n", " ")
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _log_memory_backend_to_session(backend_name: str) -> None:
    """Record the active memory backend in the current session's JSONL.

    Used by /resume to restore native vs mempalace across sessions.
    Safe no-op if no chat session is active yet.
    """
    chat = _state.get("chat_session")
    if not chat:
        return
    session = getattr(chat, "session", None)
    if not session:
        return
    try:
        session.log_memory_backend(backend_name)
    except Exception:
        pass


def _build_toolbar() -> str:
    """Build the persistent status line content."""
    from payp.config import load_model_roles

    parts: list[str] = []

    # Model name
    try:
        roles = load_model_roles()
        model = roles.executor
        # Shorten: "azure/gpt-5.4" -> "gpt-5.4", "openrouter/google/gemma..." -> "gemma..."
        short_model = model.rsplit("/", 1)[-1] if "/" in model else model
        if len(short_model) > 20:
            short_model = short_model[:17] + "..."
        parts.append(short_model)
    except Exception:
        pass

    # Token usage and cost
    client = _state.get("llm_client")
    if client:
        ct = client.cost_tracker
        total_tok = ct.total_input_tokens + ct.total_output_tokens
        if total_tok > 0:
            if total_tok >= 1000:
                parts.append(f"{total_tok / 1000:.1f}k tok")
            else:
                parts.append(f"{total_tok} tok")
        if ct.total_cost_usd > 0:
            parts.append(f"${ct.total_cost_usd:.4f}")

        # Context usage - last turn's input tokens vs model context window
        if ct.last_input_tokens > 0:
            try:
                from payp.core.compaction import get_model_context_size
                model_name = client.get_executor_model()
                max_ctx = get_model_context_size(model_name)
                if max_ctx > 0:
                    pct = int(ct.last_input_tokens / max_ctx * 100)
                    parts.append(f"ctx {pct}%")
            except Exception:
                pass

    # Connection
    conn_name = _state.get("active_connection")
    if conn_name:
        parts.append(conn_name)

    return " \u2502 ".join(parts) if parts else ""


def _build_prompt():  # -> FormattedText
    """Build formatted prompt showing connected DB name in green."""
    from prompt_toolkit.formatted_text import FormattedText

    db = _state.get("active_connection")
    if db:
        return FormattedText([
            ("", "payp "),
            ("class:db", f"({db})"),
            ("", " > "),
        ])
    return FormattedText([("", "payp> ")])


def _interactive_loop() -> None:
    """Main interactive chat loop with LLM integration."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.styles import Style as PTStyle

    from payp.cli.dispatch import _handle_command, _slash_completer
    from payp.config import global_dir

    history_file = global_dir() / "history"
    session: PromptSession = PromptSession(  # type: ignore[type-arg]
        history=FileHistory(str(history_file)),
        completer=_slash_completer(),
        complete_while_typing=True,
        style=PTStyle.from_dict({
            "completion-menu": "noinherit",
            "completion-menu.completion": "noinherit",
            "completion-menu.completion.current": "noinherit underline",
            "completion-menu.meta.completion": "noinherit #888888",
            "completion-menu.meta.completion.current": "noinherit #888888 underline",
            "bottom-toolbar": "bg:#333333 #aaaaaa",
            "db": "#B4E04C",
        }),
    )

    # Initialize LLM client and chat session
    _ensure_chat_session()

    while True:
        try:
            user_input = session.prompt(_build_prompt(), bottom_toolbar=_build_toolbar).strip()
        except (EOFError, KeyboardInterrupt):
            _stop_banner_animator()
            console.print("\n[dim]Goodbye![/dim]")
            break

        _stop_banner_animator()

        if not user_input:
            continue

        if user_input.lower() in ("cls", "clear", "cs", "/cls", "/clear", "/cs"):
            console.clear()
            continue

        # Route slash commands
        if user_input.startswith("/"):
            _handle_command(user_input)
        else:
            # Send to LLM chat loop
            chat = _state.get("chat_session")
            if not chat:
                _ensure_chat_session()
                chat = _state.get("chat_session")

            if not chat:
                console.print("[yellow]No AI provider configured. Run /models to set up.[/yellow]")
                continue

            try:
                _run_async(chat.send_message(user_input))
            except KeyboardInterrupt:
                console.print("\n[dim]Cancelled.[/dim]")
            except Exception as e:
                from payp.ui.errors import show_error
                short = str(e).strip() or type(e).__name__
                show_error(
                    "Couldn't process that input",
                    short,
                    exc=e,
                    hint=(
                        "Try /help for available commands, "
                        "/models to configure the AI provider, "
                        "or /db to check your database connection."
                    ),
                    logger_name="payp.chat",
                )
