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


def _gather_status_inputs() -> tuple[str | None, int, float, int | None, str | None]:
    """Snapshot model / tokens / cost / ctx% / connection for the status line.

    Returned as a plain tuple so both the live toolbar and the post-turn Rich
    footer can feed it through the same formatter (ui.status_bar). Any
    missing piece (no model yet, no cost tracker, no ctx estimate) is
    returned as None / 0 so the formatter can drop it cleanly.
    """
    from payp.config import load_model_roles

    model: str | None = None
    try:
        model = load_model_roles().executor
    except Exception:
        pass

    total_tok = 0
    total_cost = 0.0
    ctx_pct: int | None = None

    client = _state.get("llm_client")
    if client:
        ct = client.cost_tracker
        total_tok = ct.total_input_tokens + ct.total_output_tokens
        total_cost = ct.total_cost_usd
        if ct.last_input_tokens > 0:
            try:
                from payp.core.compaction import get_model_context_size
                max_ctx = get_model_context_size(client.get_executor_model())
                if max_ctx > 0:
                    ctx_pct = int(ct.last_input_tokens / max_ctx * 100)
            except Exception:
                pass

    return model, total_tok, total_cost, ctx_pct, _state.get("active_connection")


def print_frozen_status_line() -> None:
    """Rich-print the current status line once, just above the prompt.

    Called from the main loop before each `session.prompt()` call. This is
    the single source of status display — no prompt_toolkit bottom-toolbar,
    no post-turn duplicate. The line stays in scrollback so streaming
    output scrolls past it; the next prompt shows a fresh refreshed copy.
    No-ops when no segments qualify (e.g. pre-first-response idle state
    before any model is configured).
    """
    from payp.ui.status_bar import print_frozen_status

    print_frozen_status(console, *_gather_status_inputs())


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
            "db": "#B4E04C",
        }),
    )

    # Initialize LLM client and chat session
    _ensure_chat_session()

    while True:
        # Single source of status — prints right above the prompt every
        # iteration so it stays visible between turns and reflects the
        # latest tokens / cost / ctx / connection after each response.
        print_frozen_status_line()
        try:
            user_input = session.prompt(_build_prompt()).strip()
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
