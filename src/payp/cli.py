"""payp CLI — main entry point and command routing."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from payp import __version__
from payp.config import (
    list_connections,
    load_config,
    load_connection_profile,
    load_credential,
    load_model_roles,
    load_models_config,
    save_config,
    save_connection_profile,
    save_credential,
    save_models_config,
)
from payp.db.cache import get_cached_table_count, has_cache, save_metadata, save_t0, save_t1
from payp.db.connection import ConnectionManager
from payp.db.introspection import (
    discover_t0,
    discover_t1,
    format_t0_for_context,
    format_t1_for_context,
    get_db_metadata,
)
from payp.models import (
    AppConfig,
    ConnectionCredential,
    ConnectionProfile,
    DbType,
    ModelProvider,
    ModelRoles,
    SecurityMode,
)

_PERSISTENT_LOOP: asyncio.AbstractEventLoop | None = None


def _get_loop() -> asyncio.AbstractEventLoop:
    """Return a single persistent event loop for the entire payp session.

    This prevents 'Event loop is closed' errors when async DB connections
    (aiomysql, oracledb) hold references to a loop that asyncio.run() closed.
    """
    global _PERSISTENT_LOOP
    if _PERSISTENT_LOOP is None or _PERSISTENT_LOOP.is_closed():
        _PERSISTENT_LOOP = asyncio.new_event_loop()
    return _PERSISTENT_LOOP


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from sync code.

    Detects ANY currently-running loop in this thread (prompt_toolkit, click,
    etc.) — not just our persistent loop — and falls back to a worker thread
    when needed. This avoids "Cannot run the event loop while another loop is
    running" inside interactive selectors.
    """
    try:
        asyncio.get_running_loop()
        # We're inside some other loop (e.g. prompt_toolkit inside a selector
        # callback). Execute the coroutine on a dedicated worker thread so it
        # gets its own fresh event loop.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        # No running loop in this thread — use our persistent loop.
        loop = _get_loop()
        return loop.run_until_complete(coro)


app = typer.Typer(
    name="payp",
    help="AI-powered CLI for data engineers",
    no_args_is_help=False,
    add_completion=False,
)
console = Console()


class CommandCancelled(Exception):
    """Raised when user presses Esc during a slash command."""


def command_prompt(message: str = "", **kwargs) -> str:
    """prompt_toolkit prompt with Esc bound to cancel the current command.

    Drop-in replacement for ``pt_prompt`` inside slash commands.
    Raises ``CommandCancelled`` on Esc.
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    @kb.add("escape")
    def _esc(event):
        event.app.exit(exception=CommandCancelled())

    session = PromptSession(key_bindings=kb)
    session.app.ttimeoutlen = 0.05
    session.app.timeoutlen = 0.05
    return session.prompt(message, **kwargs)


# --- State ---

_state: dict[str, Any] = {
    "config": None,
    "active_connection": None,
    "connection_manager": None,  # ConnectionManager instance
    "mode": None,
    "t0": None,  # SchemaIndex
    "t1": None,  # SchemaCatalog
    "llm_client": None,  # LLMClient instance
    "last_select": None,  # {sql, offset, page_size, connection} for /more pagination
}


def get_config() -> AppConfig:
    if _state["config"] is None:
        _state["config"] = load_config()
    return _state["config"]


# --- Main entry ---


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[
        bool, typer.Option("--version", "-v", help="Show version")
    ] = False,
) -> None:
    """payp — AI-powered CLI for data engineers."""
    if version:
        console.print(f"payp v{__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        # No subcommand → start interactive chat
        _show_welcome()
        _interactive_loop()


def _show_welcome() -> None:
    from payp.storage.snapshots import snapshot_count
    from payp.ui.dashboard import render_compact_hint, render_status_dashboard
    from payp.ui.onboarding import is_first_run, run_onboarding, should_prompt_model_setup, prompt_model_setup_only

    # True first-run: no models AND no connections → full onboarding
    if is_first_run():
        run_onboarding(console)
        return

    # Returning user but no model → prompt just for model
    if should_prompt_model_setup():
        prompt_model_setup_only(console)

    config = get_config()

    # Resolve model info
    roles = load_model_roles()
    providers = load_models_config()
    model_name: str | None = roles.executor if providers else None
    model_provider: str | None = None
    if model_name:
        parts = model_name.split("/")
        if len(parts) > 1:
            model_provider = parts[0]
            model_name = "/".join(parts[1:])
    reviewer_name: str | None = roles.reviewer if providers and roles.reviewer else None
    if reviewer_name:
        parts = reviewer_name.split("/")
        if len(parts) > 1:
            reviewer_name = "/".join(parts[1:])

    render_status_dashboard(
        console=console,
        version=__version__,
        model_name=model_name,
        model_provider=model_provider,
        reviewer_name=reviewer_name,
        connection_name=None,
        connection_status=None,
        mode=config.default_mode.value,
        snapshot_count=snapshot_count(),
    )

    render_compact_hint(console)

    # Legacy knowledge dir notice — wording depends on whether migration
    # has already happened. If global has data, the legacy dir is just dead
    # weight and the message is softer.
    from payp.storage.knowledge import (
        get_knowledge_dir,
        has_legacy_knowledge,
        list_knowledge_files,
    )
    if has_legacy_knowledge():
        global_populated = bool(list_knowledge_files()) if get_knowledge_dir().exists() else False
        if global_populated:
            console.print(
                "[dim]ℹ Legacy [/dim][dim]./payp/knowledge/[/dim][dim] still on disk "
                "(already migrated). Ask me to clean it up or run "
                "[/dim][dim]/knowledge migrate-legacy[/dim][dim] again.[/dim]"
            )
        else:
            console.print(
                "[yellow]⚠[/yellow] Found legacy [dim]./payp/knowledge/[/dim] — "
                "knowledge is now global. Run [bold]/knowledge migrate-legacy[/bold] to move it."
            )


def _slash_completer():
    """Build a prompt_toolkit completer for slash commands."""
    from prompt_toolkit.completion import Completer, Completion

    # command → short description (shown in completion menu)
    _COMMANDS = {
        "/db": "manage database connections",
        "/credentials": "edit saved credentials",
        "/models": "manage AI providers",
        "/mode": "show or set security mode",
        "/skills": "browse available workflows",
        "/schema": "explore schema",
        "/stats": "column statistics & data profile",
        "/knowledge": "business context & notes",
        "/memory": "manage knowledge backend",
        "/queries": "saved SQL library",
        "/snapshots": "manage backups",
        "/rollback": "restore from snapshot",
        "/diff": "compare schema between connections",
        "/history": "SQL audit log",
        "/resume": "continue previous session",
        "/context": "show context window usage",
        "/compact": "compress older messages",
        "/more": "next 20 rows of last SELECT",
        "/cost": "token usage and costs",
        "/export": "export session to markdown",
        "/help": "show available commands",
        "/quit": "exit payp",
    }

    class SlashCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            # Only complete when the line starts with /
            if not text.startswith("/"):
                return
            # Match against the typed prefix
            for cmd, desc in _COMMANDS.items():
                if cmd.startswith(text):
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display=cmd,
                        display_meta=desc,
                    )

    return SlashCompleter()


def _interactive_loop() -> None:
    """Main interactive chat loop with LLM integration."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory

    from payp.config import global_dir

    from prompt_toolkit.styles import Style as PTStyle

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
        }),
    )

    # Initialize LLM client and chat session
    _ensure_chat_session()

    while True:
        try:
            user_input = session.prompt("payp> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/dim]")
            break

        if not user_input:
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
                console.print(f"[red]Error: {e}[/red]")


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
    )

    # Restore message history
    if existing_messages:
        new_session.messages = existing_messages

    # Share the CLI's multi_conn manager so the LLM sees all active connections
    mc = _state.get("multi_conn_manager")
    if mc:
        new_session.multi_conn = mc

    _state["chat_session"] = new_session


def _handle_command(cmd: str) -> None:
    """Route slash commands from interactive mode."""
    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    handlers = {
        "/db": lambda: _cmd_db(args),
        "/credentials": lambda: _cmd_credentials(args),
        "/models": lambda: _cmd_models(args),
        "/mode": lambda: _cmd_mode(args),
        "/schema": lambda: _cmd_schema(args),
        "/stats": lambda: _cmd_stats(args),
        "/snapshots": _cmd_snapshots,
        "/rollback": _cmd_rollback,
        "/diff": lambda: _cmd_diff(args),
        "/knowledge": lambda: _cmd_knowledge(args),
        "/memory": lambda: _cmd_memory(args),
        "/queries": lambda: _cmd_queries(args),
        "/resume": lambda: _cmd_resume(args),
        "/history": lambda: _cmd_history(args),
        "/compact": _cmd_compact,
        "/context": _cmd_context,
        "/more": _cmd_more,
        "/cost": _cmd_cost,
        "/export": lambda: _cmd_export(args),
        "/skills": _cmd_skills,
        "/help": _cmd_help,
        "/quit": _cmd_quit,
        "/exit": _cmd_quit,
    }

    handler = handlers.get(command)
    if handler:
        try:
            handler()
        except CommandCancelled:
            console.print("[dim]Cancelled.[/dim]")
        except KeyboardInterrupt:
            console.print("[dim]Cancelled.[/dim]")
    else:
        console.print(f"[red]Unknown command: {command}[/red]. Type /help for available commands.")


# --- Slash command implementations ---


def _cmd_db(args: str) -> None:
    """Manage database connections."""
    connections = list_connections()

    if args:
        # Accept numeric index OR name
        target_name = args
        if args.isdigit() and 1 <= int(args) <= len(connections):
            target_name = connections[int(args) - 1]
        profile = load_connection_profile(target_name)
        if not profile:
            console.print(f"[red]Connection '{target_name}' not found.[/red]")
            return
        cred = load_credential(target_name)
        if not cred:
            console.print(f"[red]No credentials found for '{target_name}'. Run /credentials.[/red]")
            return
        _run_async(_connect_to_db(target_name, profile, cred))
        return

    if not connections:
        # No connections — start setup wizard
        _setup_new_connection()
        return

    # Show connection list
    from payp.ui.theme import Color
    table = Table(title=f"[{Color.BRAND}]Database Connections[/{Color.BRAND}]")
    table.add_column("#", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("Host")
    table.add_column("Status")

    mc = _state.get("multi_conn_manager")
    active_name = _state.get("active_connection")
    for i, name in enumerate(connections, 1):
        profile = load_connection_profile(name)
        if name == active_name:
            status = f"[{Color.BRAND_ALT}]● active[/{Color.BRAND_ALT}]"
        elif mc and mc.has(name):
            status = f"[{Color.BRAND_ALT}]● connected[/{Color.BRAND_ALT}]"
        else:
            status = "[dim]○[/dim]"
        if profile:
            table.add_row(str(i), name, profile.db_type.value, profile.host, status)

    console.print(table)
    console.print(f"\n[dim]Enter connection number, name, or [/dim][{Color.BRAND_ALT}]new[/{Color.BRAND_ALT}][dim] to add one.[/dim]")



    choice = command_prompt("Select: ").strip().lower()

    if choice == "new":
        _setup_new_connection()
    elif choice.isdigit() and 1 <= int(choice) <= len(connections):
        name = connections[int(choice) - 1]
        _cmd_db(name)
    elif choice in connections:
        _cmd_db(choice)


def _setup_new_connection() -> None:
    """Interactive wizard to add a new database connection."""


    from payp.ui.theme import Color
    console.print(f"\n[{Color.BRAND}]New Database Connection[/{Color.BRAND}]\n")

    # DB type selection
    console.print("  1. PostgreSQL")
    console.print("  2. MySQL")
    console.print("  3. Oracle")
    type_choice = command_prompt("Select type: ").strip()
    db_type_map = {"1": DbType.POSTGRESQL, "2": DbType.MYSQL, "3": DbType.ORACLE}
    db_type = db_type_map.get(type_choice)
    if not db_type:
        console.print("[red]Invalid selection.[/red]")
        return

    default_ports = {DbType.POSTGRESQL: 5432, DbType.MYSQL: 3306, DbType.ORACLE: 1521}
    default_port = default_ports[db_type]

    if db_type == DbType.ORACLE:
        console.print("[dim]Oracle uses your username as the schema[/dim]")

    host = command_prompt("Host: ").strip()
    port_str = command_prompt(f"Port [{default_port}]: ").strip()
    port = int(port_str) if port_str else default_port
    database_label = "Service Name (PDB): " if db_type == DbType.ORACLE else "Database: "
    database = command_prompt(database_label).strip()
    username = command_prompt("Username: ").strip()
    password = command_prompt("Password: ", is_password=True).strip()
    conn_name = command_prompt("Connection name: ").strip()

    if not all([host, database, username, conn_name]):
        console.print("[red]All fields are required.[/red]")
        return

    profile = ConnectionProfile(
        name=conn_name,
        db_type=db_type,
        host=host,
        port=port,
        database=database,
        username=username,
    )
    credential = ConnectionCredential(password=password)

    save_connection_profile(profile)
    save_credential(conn_name, credential)

    console.print(f"\n[{Color.BRAND_ALT}]Connection '{conn_name}' saved.[/{Color.BRAND_ALT}]")

    # Test connection + run initial discovery
    _run_async(_connect_to_db(conn_name, profile, credential))


def _cmd_credentials(args: str) -> None:
    """Edit saved connection credentials without re-running the full wizard."""


    connections = list_connections()
    if not connections:
        console.print("[yellow]No connections to edit. Use /db to add one.[/yellow]")
        return

    # Resolve target name
    target_name = args.strip()
    if not target_name:
        # Picker mode
        from payp.ui.theme import Color
        table = Table(title=f"[{Color.BRAND}]Edit Credentials — Select Connection[/{Color.BRAND}]")
        table.add_column("#", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Type")
        table.add_column("Host")
        for i, name in enumerate(connections, 1):
            profile = load_connection_profile(name)
            if profile:
                table.add_row(str(i), name, profile.db_type.value, profile.host)
        console.print(table)
        console.print("\n[dim]Enter connection number or name.[/dim]")
        try:
            choice = command_prompt("Select: ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("[yellow]Cancelled, no changes saved.[/yellow]")
            return
        if not choice:
            console.print("[yellow]Cancelled, no changes saved.[/yellow]")
            return
        if choice.isdigit() and 1 <= int(choice) <= len(connections):
            target_name = connections[int(choice) - 1]
        elif choice in connections:
            target_name = choice
        else:
            console.print(f"[red]Connection '{choice}' not found.[/red]")
            return

    profile = load_connection_profile(target_name)
    if not profile:
        console.print(
            f"[red]Connection '{target_name}' not found.[/red] "
            f"Available: {', '.join(connections) if connections else '(none)'}"
        )
        return

    # Warn if editing active connection
    active_name = _state.get("active_connection")
    if active_name == target_name:
        console.print(
            f"[yellow]You're currently connected to {target_name}. "
            f"Changes take effect on next /db {target_name}[/yellow]"
        )

    # Show current profile
    from payp.ui.theme import Color
    console.print(f"\n[{Color.BRAND}]Editing credentials for[/{Color.BRAND}] [{Color.BRAND_ALT}]{target_name}[/{Color.BRAND_ALT}]")
    console.print(f"  Type:     [dim]{profile.db_type.value}[/dim]")
    console.print(f"  Host:     {profile.host}")
    console.print(f"  Port:     {profile.port}")
    console.print(f"  Database: {profile.database}")
    console.print(f"  Username: {profile.username}")
    console.print("  Password: [dim]********[/dim]")
    console.print("\n[dim]Press Enter to keep current value.[/dim]\n")

    try:
        host = command_prompt("Host: ", default=profile.host).strip() or profile.host
        port_str = command_prompt("Port: ", default=str(profile.port)).strip()
        try:
            port = int(port_str) if port_str else profile.port
        except ValueError:
            console.print(f"[red]Invalid port '{port_str}', keeping {profile.port}.[/red]")
            port = profile.port
        database = command_prompt("Database: ", default=profile.database).strip() or profile.database
        username = command_prompt("Username: ", default=profile.username).strip() or profile.username

        change_pw = command_prompt("Change password? [y/N]: ").strip().lower()
        new_password: str | None = None
        if change_pw in ("y", "yes"):
            new_password = command_prompt("New password: ", is_password=True).strip()
            if not new_password:
                console.print("[yellow]Empty password, keeping existing.[/yellow]")
                new_password = None
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Cancelled, no changes saved.[/yellow]")
        return

    # Build updated profile (name immutable)
    updated_profile = ConnectionProfile(
        name=profile.name,
        db_type=profile.db_type,
        host=host,
        port=port,
        database=database,
        username=username,
        ssl=profile.ssl,
        schema_name=profile.schema_name,
        timeout=profile.timeout,
    )
    save_connection_profile(updated_profile)

    if new_password is not None:
        existing_cred = load_credential(target_name)
        token = existing_cred.token if existing_cred else None
        key_file = existing_cred.key_file if existing_cred else None
        save_credential(
            target_name,
            ConnectionCredential(password=new_password, token=token, key_file=key_file),
        )

    console.print(f"\n[{Color.BRAND_ALT}]Credentials updated for {target_name}[/{Color.BRAND_ALT}]")


def _cmd_models(args: str) -> None:
    """Manage AI model providers."""
    providers = load_models_config()
    roles = load_model_roles()

    if args == "add":
        _setup_new_provider()
        return

    if not providers:
        console.print("[yellow]No AI providers configured.[/yellow]")
        _setup_new_provider()
        return

    from payp.ui.theme import Color
    table = Table(title=f"[{Color.BRAND}]AI Model Providers[/{Color.BRAND}]")
    table.add_column("Provider", style="bold")
    table.add_column("Default Model")
    table.add_column("Status")

    for name, provider in providers.items():
        table.add_row(name, provider.default_model or "—", f"[{Color.BRAND_ALT}]✓ configured[/{Color.BRAND_ALT}]")

    console.print(table)
    console.print(f"\nExecutor: [{Color.BRAND_ALT}]{roles.executor}[/{Color.BRAND_ALT}]")
    if roles.reviewer:
        console.print(f"Reviewer: [{Color.BRAND_ALT}]{roles.reviewer}[/{Color.BRAND_ALT}]")
    else:
        console.print("Reviewer: [dim]not set[/dim]")


def _setup_new_provider() -> None:
    """Interactive wizard to add a new AI provider."""


    from payp.ui.theme import Color
    console.print(f"\n[{Color.BRAND}]Add AI Provider[/{Color.BRAND}]\n")
    console.print(f"  [{Color.BRAND_ALT}]1.[/{Color.BRAND_ALT}] OpenRouter (recommended — one key, all models)")
    console.print(f"  [{Color.BRAND_ALT}]2.[/{Color.BRAND_ALT}] Anthropic (Claude)")
    console.print(f"  [{Color.BRAND_ALT}]3.[/{Color.BRAND_ALT}] OpenAI")
    console.print(f"  [{Color.BRAND_ALT}]4.[/{Color.BRAND_ALT}] Google (Gemini)")
    console.print(f"  [{Color.BRAND_ALT}]5.[/{Color.BRAND_ALT}] Ollama (local)")

    choice = command_prompt("Select: ").strip()
    provider_map = {
        "1": ("openrouter", None),
        "2": ("anthropic", None),
        "3": ("openai", None),
        "4": ("gemini", None),
        "5": ("ollama", "http://localhost:11434"),
    }

    if choice not in provider_map:
        console.print("[red]Invalid selection.[/red]")
        return

    name, base_url = provider_map[choice]

    if name == "ollama":
        url = command_prompt(f"Ollama URL [{base_url}]: ").strip() or base_url
        provider = ModelProvider(api_key="ollama", base_url=url)
    else:
        api_key = command_prompt(f"Enter {name} API key: ", is_password=True).strip()
        if not api_key:
            console.print("[red]API key required.[/red]")
            return
        provider = ModelProvider(api_key=api_key)

    providers = load_models_config()
    providers[name] = provider
    roles = load_model_roles()
    save_models_config(providers, roles)
    console.print(f"\n[{Color.BRAND_ALT}]{name} configured.[/{Color.BRAND_ALT}]")


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
            SelectorItem(label="manual", value=SecurityMode.MANUAL, description="approve every SQL before execution"),
            SelectorItem(label="yolo", value=SecurityMode.YOLO, description="auto-execute everything"),
            SelectorItem(label="secure", value=SecurityMode.SECURE, description="reviewer checks, you decide"),
            SelectorItem(label="secure-auto", value=SecurityMode.SECURE_AUTO, description="reviewer checks and decides"),
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
        SecurityMode.YOLO: "[bold red]YOLO mode enabled. All queries execute without confirmation.[/bold red]",
        SecurityMode.SECURE: f"[{Color.BRAND_ALT}]Secure mode. Reviewer will check, you make final decision.[/{Color.BRAND_ALT}]",
        SecurityMode.SECURE_AUTO: f"[{Color.BRAND_ALT}]Secure-auto mode. Reviewer checks and decides.[/{Color.BRAND_ALT}]",
        SecurityMode.MANUAL: f"[{Color.BRAND_ALT}]Manual mode. You approve every SQL.[/{Color.BRAND_ALT}]",
    }
    console.print(confirmations[new_mode])
    _ensure_chat_session()  # Refresh with new mode


async def _connect_to_db(
    name: str, profile: ConnectionProfile, credential: ConnectionCredential
) -> None:
    """Connect to a database (or switch to it if already connected), run discovery."""
    from payp.db.connection import ConnectionError as ConnError

    # Check multi_conn state — is this connection already active?
    mc = _state.get("multi_conn_manager")
    if mc and mc.has(name):
        # Already connected — just switch active
        mc.set_active(name)
        state = mc.get(name)
        if state:
            mgr = state.manager
            _state["active_connection"] = name
            _state["connection_manager"] = mgr
            _state["t0"] = state.t0
            _state["t1"] = state.t1
            from payp.ui.theme import Color
            console.print(f"[{Color.BRAND_ALT}]Switched to {name} ({mgr.db_version})[/{Color.BRAND_ALT}]")
            _ensure_chat_session()
            return

    mgr = ConnectionManager(profile, credential)
    try:
        if profile.db_type == DbType.ORACLE:
            from payp.ui.abort import AbortWatcher
            console.print(
                "[yellow]Oracle can take up to 90 seconds on first startup...[/yellow] "
                "[dim](press Esc to abort)[/dim]"
            )
            max_attempts = 18
            retry_delay = 5
            async with AbortWatcher() as watcher:
                with console.status(f"Connecting to {name}...") as status:
                    last_exc: Exception | None = None
                    for attempt in range(1, max_attempts + 1):
                        if watcher.aborted:
                            console.print("[yellow]Aborted by user.[/yellow]")
                            return
                        status.update(
                            f"Connecting to {name}... attempt {attempt}/{max_attempts} [Esc=abort]"
                        )
                        try:
                            version = await mgr.connect()
                            last_exc = None
                            break
                        except Exception as e:
                            last_exc = e
                            if attempt < max_attempts:
                                # Sleep but check abort every 500ms
                                for _ in range(retry_delay * 2):
                                    if watcher.aborted:
                                        console.print("[yellow]Aborted by user.[/yellow]")
                                        return
                                    await asyncio.sleep(0.5)
                    if last_exc is not None:
                        raise last_exc
        else:
            with console.status(f"Connecting to {name}..."):
                version = await mgr.connect()
    except (ConnError, OSError, Exception) as e:
        console.print(f"[red]✗ Connection failed:[/red] {e}")
        console.print("[dim]Check host, port, credentials, and that the database is reachable.[/dim]")
        return

    # Register in multi_conn (don't disconnect others)
    if mc is None:
        from payp.core.multi_connection import MultiConnectionManager
        mc = MultiConnectionManager()
        _state["multi_conn_manager"] = mc

    # Insert into multi_conn state directly (we already have a connected mgr)
    from payp.core.multi_connection import ConnectionState
    mc._connections[name] = ConnectionState(name=name, manager=mgr)
    mc._active = name

    _state["active_connection"] = name
    _state["connection_manager"] = mgr
    from payp.ui.theme import Color
    console.print(f"[{Color.BRAND_ALT}]Connected to {name} ({version})[/{Color.BRAND_ALT}]")

    # Check if we have cache (returning connection) or need full discovery (first time)
    if has_cache(name):
        # Freshness check
        cached_count = get_cached_table_count(name)
        t0 = await discover_t0(mgr)
        current_count = t0.total_tables

        if cached_count != current_count:
            console.print(
                f"[yellow]Schema changed since last session "
                f"({cached_count} → {current_count} tables). "
                f"Run /schema --refresh for details.[/yellow]"
            )
            # Update cache with new data
            t1 = await discover_t1(mgr)
            save_t0(name, t0)
            save_t1(name, t1)
        else:
            console.print(f"[{Color.BRAND_ALT}]Schema cache up to date ({current_count} tables)[/{Color.BRAND_ALT}]")
            from payp.db.cache import load_t1
            t1 = load_t1(name)

        _state["t0"] = t0
        _state["t1"] = t1
    else:
        # First connect — full discovery
        console.print("\n[dim]Running initial discovery...[/dim]")

        t0 = await discover_t0(mgr)
        t1 = await discover_t1(mgr)
        meta = await get_db_metadata(mgr)

        schema_count = len(t0.schemas)
        console.print(f"  [{Color.BRAND_ALT}]✓[/{Color.BRAND_ALT}] {schema_count} schema{'s' if schema_count != 1 else ''} found")
        console.print(f"  [{Color.BRAND_ALT}]✓[/{Color.BRAND_ALT}] {t0.total_tables} tables, {t0.view_count} views")
        console.print(f"  [{Color.BRAND_ALT}]✓[/{Color.BRAND_ALT}] Server uptime: {meta.get('uptime', 'unknown')}")
        console.print(f"  [{Color.BRAND_ALT}]✓[/{Color.BRAND_ALT}] Database size: {meta.get('db_size', 'unknown')}")

        save_t0(name, t0)
        save_t1(name, t1)
        save_metadata(name, meta)
        console.print(f"  [{Color.BRAND_ALT}]✓[/{Color.BRAND_ALT}] Schema cache saved")

        _state["t0"] = t0
        _state["t1"] = t1

    # Persist schemas in multi_conn so switching back doesn't re-discover
    mc = _state.get("multi_conn_manager")
    if mc and mc.has(name) and t0 and t1:
        mc.set_schemas(name, t0, t1)

    # Refresh chat session with new connection context
    _ensure_chat_session()

    console.print()


def _cmd_schema(args: str) -> None:
    """Explore database schema."""
    if not _state.get("active_connection"):
        console.print("[red]Not connected. Run /db first.[/red]")
        return

    mgr: ConnectionManager | None = _state.get("connection_manager")  # type: ignore[assignment]
    if not mgr or not mgr.is_connected:
        console.print("[yellow]Connection lost. Run /db to reconnect.[/yellow]")
        return

    if args == "--refresh":
        console.print("[dim]Refreshing schema cache...[/dim]")
        _run_async(_refresh_schema())
        return

    # Show schema overview
    t0 = _state.get("t0")
    t1 = _state.get("t1")

    if args:
        # /schema <table> — show T2 for specific table
        _run_async(_show_table_schema(args))
        return

    if t0:
        console.print(Panel(format_t0_for_context(t0), title="Schema Overview", border_style="rgb(168,0,111)"))
    if t1:
        console.print(Panel(format_t1_for_context(t1), title="Tables", border_style="rgb(168,0,111)"))


async def _refresh_schema() -> None:
    """Re-run introspection and update cache."""
    name = _state["active_connection"]
    mgr: ConnectionManager = _state["connection_manager"]

    t0 = await discover_t0(mgr)
    t1 = await discover_t1(mgr)
    meta = await get_db_metadata(mgr)

    save_t0(name, t0)
    save_t1(name, t1)
    save_metadata(name, meta)

    _state["t0"] = t0
    _state["t1"] = t1

    from payp.ui.theme import Color
    console.print(f"[{Color.BRAND_ALT}]Schema cache refreshed. {t0.total_tables} tables, {t0.view_count} views.[/{Color.BRAND_ALT}]")


async def _show_table_schema(table_name: str) -> None:
    """Show T2 DDL for a specific table."""
    from payp.db.introspection import discover_t2

    mgr: ConnectionManager = _state["connection_manager"]

    # Try to find the table — check if schema.table or just table
    if "." in table_name:
        schema, table = table_name.split(".", 1)
    else:
        schema = "public"
        table = table_name

    ddl = await discover_t2(mgr, schema, table)
    from rich.syntax import Syntax
    console.print(Syntax(ddl, "sql", theme="monokai"))


def _cmd_stats(args: str) -> None:
    """Profile a table — column-level statistics & data quality."""
    if not _state.get("active_connection"):
        console.print("[red]Not connected. Run /db first.[/red]")
        return
    mgr: ConnectionManager | None = _state.get("connection_manager")  # type: ignore[assignment]
    if not mgr or not mgr.is_connected:
        console.print("[yellow]Connection lost. Run /db to reconnect.[/yellow]")
        return
    if not args.strip():
        from payp.ui.theme import Color
        console.print(f"[{Color.BRAND_ALT}]Usage: /stats <table>  (or schema.table)[/{Color.BRAND_ALT}]")
        return

    arg = args.strip()
    if "." in arg:
        schema, table = arg.split(".", 1)
    else:
        schema, table = None, arg

    console.print(f"[dim]Profiling {table}...[/dim]")
    try:
        from payp.tools.stats import profile_table
        profile = _run_async(profile_table(mgr, table, schema))
    except Exception as e:
        console.print(f"[red]Stats failed: {e}[/red]")
        return

    if profile.get("error") and not profile.get("columns"):
        console.print(f"[red]{profile['error']}[/red]")
        return

    _render_stats_table(profile)


def _render_stats_table(profile: dict[str, Any]) -> None:
    """Render a profile dict as a rich table."""
    from rich import box

    total = profile.get("total_rows", 0)
    schema = profile.get("schema") or ""
    table = profile.get("table") or ""
    header = f"{schema}.{table}" if schema else table
    from payp.ui.theme import Color
    console.print(
        Panel(
            f"[bold]{header}[/bold]   [dim]total rows:[/dim] [{Color.BRAND_ALT}]{total:,}[/{Color.BRAND_ALT}]   "
            f"[dim]columns:[/dim] [{Color.BRAND_ALT}]{len(profile.get('columns', []))}[/{Color.BRAND_ALT}]",
            border_style="rgb(168,0,111)",
        )
    )

    t = Table(box=box.SIMPLE_HEAD, show_header=True, header_style=Color.BRAND_ALT, padding=(0, 1))
    t.add_column("column", style="bold")
    t.add_column("type", style="dim")
    t.add_column("nulls", justify="right")
    t.add_column("null%", justify="right")
    t.add_column("distinct", justify="right")
    t.add_column("min / avg_len", justify="right")
    t.add_column("max / max_len", justify="right")
    t.add_column("avg / p50 / p95", justify="right")
    t.add_column("top values", overflow="fold")

    def _fmt(v: Any) -> str:
        if v is None:
            return "-"
        if isinstance(v, float):
            if abs(v) >= 1000 or v != v:  # NaN guard
                return f"{v:,.2f}"
            return f"{v:.2f}"
        if isinstance(v, int):
            return f"{v:,}"
        s = str(v)
        return s if len(s) <= 24 else s[:21] + "..."

    for c in profile.get("columns", []):
        if c.get("skipped"):
            t.add_row(
                c["column"], c["data_type"], "-", "-", "-", "-", "-", "-",
                f"[dim italic]{c['skipped']}[/dim italic]",
            )
            continue

        kind = c.get("kind", "")
        nulls = c.get("null_count")
        null_pct = c.get("null_percent")
        distinct = c.get("distinct_count")

        if kind == "numeric":
            min_col = _fmt(c.get("min"))
            max_col = _fmt(c.get("max"))
            avg_parts = [_fmt(c.get("avg")), _fmt(c.get("p50")), _fmt(c.get("p95"))]
            avg_col = " / ".join(avg_parts)
        elif kind == "text":
            min_col = _fmt(c.get("avg_length"))
            max_col = _fmt(c.get("max_length"))
            avg_col = "-"
        elif kind == "date":
            min_col = _fmt(c.get("min"))
            max_col = _fmt(c.get("max"))
            avg_col = "-"
        else:
            min_col = max_col = avg_col = "-"

        top = c.get("top_values") or []
        if top:
            top_str = ", ".join(f"{_fmt(v['value'])}({v['count']:,})" for v in top[:5])
        else:
            top_str = "-"

        distinct_str = _fmt(distinct)
        if c.get("distinct_sampled"):
            distinct_str = f"~{distinct_str}"

        err = c.get("error")
        col_name = c["column"] + (f" [red](!)[/red]" if err else "")

        t.add_row(
            col_name,
            str(c.get("data_type", ""))[:18],
            _fmt(nulls),
            f"{null_pct:.1f}%" if null_pct is not None else "-",
            distinct_str,
            min_col,
            max_col,
            avg_col,
            top_str,
        )

    console.print(t)
    console.print(
        "[dim]legend: numeric cols show min/max/avg; text cols show avg_len/max_len; "
        "~ prefix = sampled distinct count[/dim]"
    )


def _cmd_compact() -> None:
    """Manually compact conversation history."""
    chat = _state.get("chat_session")
    if not chat:
        console.print("[dim]No active chat session.[/dim]")
        return
    if len(chat.messages) < 3:
        console.print("[dim]Nothing to compact yet.[/dim]")
        return

    console.print("[dim]Compacting conversation...[/dim]")
    try:
        stats = _run_async(chat.compact())
        if stats.get("saved_tokens", 0) == 0:
            console.print("[dim]No tokens saved (conversation too short).[/dim]")
    except Exception as e:
        console.print(f"[red]Compaction failed: {e}[/red]")


def _cmd_more() -> None:
    """Fetch the next 20 rows of the last SELECT query."""
    from payp.models import DbType
    from payp.ui.display import display_query_result

    chat = _state.get("chat_session")
    if not chat or not getattr(chat, "last_select", None):
        console.print("[dim]No recent query to paginate. Run a SELECT first.[/dim]")
        return

    last = chat.last_select
    # Note: we don't gate on truncated=False here because the LLM may have added
    # its own LIMIT to the SQL (which makes truncated=False even if more rows exist).
    # We just try to fetch the next page — if empty, we'll say so.

    mgr: ConnectionManager | None = _state.get("connection_manager")  # type: ignore[assignment]
    if not mgr or not mgr.is_connected:
        console.print("[yellow]Connect to a database first with /db[/yellow]")
        return

    # Ensure we're on the same connection the query originally ran on
    if mgr.profile.name != last.get("connection"):
        console.print(
            f"[yellow]Last query was on '{last['connection']}' but active is "
            f"'{mgr.profile.name}'. Switch connections or run a fresh query.[/yellow]"
        )
        return

    offset = last.get("offset", 20)
    page_size = 20
    base_sql = last["sql"].rstrip(";").strip()

    # Strip existing LIMIT / FETCH FIRST / OFFSET clauses so we can add our own
    import re
    # Remove trailing LIMIT N (optionally with OFFSET M)
    base_sql = re.sub(r"\s+LIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$", "", base_sql, flags=re.IGNORECASE)
    # Remove trailing FETCH FIRST/NEXT N ROWS ONLY (with optional OFFSET)
    base_sql = re.sub(
        r"\s+(OFFSET\s+\d+\s+ROWS\s+)?FETCH\s+(FIRST|NEXT)\s+\d+\s+ROWS?\s+ONLY\s*$",
        "",
        base_sql,
        flags=re.IGNORECASE,
    )
    # Remove standalone trailing OFFSET N ROWS
    base_sql = re.sub(r"\s+OFFSET\s+\d+\s+ROWS?\s*$", "", base_sql, flags=re.IGNORECASE)
    base_sql = base_sql.rstrip().rstrip(";").strip()

    # Build paginated SQL — dialect-aware
    if mgr.profile.db_type == DbType.ORACLE:
        paged_sql = f"{base_sql} OFFSET {offset} ROWS FETCH NEXT {page_size} ROWS ONLY"
    else:
        # PG, MySQL both support LIMIT ... OFFSET
        paged_sql = f"{base_sql} LIMIT {page_size} OFFSET {offset}"

    console.print(f"[dim]/more → rows {offset + 1}–{offset + page_size}[/dim]")
    try:
        result = _run_async(mgr.execute(paged_sql, limit=page_size))
    except Exception as e:
        console.print(f"[red]Pagination failed: {e}[/red]")
        return

    if not result.columns or result.row_count == 0:
        console.print("[dim]No more rows.[/dim]")
        chat.last_select["truncated"] = False
        return

    display_query_result(
        console,
        result.columns,
        result.rows,
        result.execution_ms,
        truncated=result.truncated,
    )
    # Advance offset for the next /more
    chat.last_select["offset"] = offset + result.row_count
    chat.last_select["truncated"] = result.truncated


def _cmd_context() -> None:
    """Show current context window usage."""
    chat = _state.get("chat_session")
    if not chat:
        console.print("[dim]No active chat session.[/dim]")
        return

    # Rebuild system prompt to estimate
    try:
        system_prompt = _run_async(chat._get_system_prompt(user_text=""))
    except Exception:
        system_prompt = ""

    stats = chat.get_context_stats(system_prompt)
    pct = stats.usage_ratio * 100

    # Color by usage
    if pct < 50:
        color = "green"
    elif pct < 75:
        color = "yellow"
    else:
        color = "red"

    # Simple bar
    filled = int(stats.usage_ratio * 30)
    bar = "█" * filled + "░" * (30 - filled)

    console.print()
    from payp.ui.theme import Color
    console.print(f"  [{Color.BRAND}]Context Usage[/{Color.BRAND}]")
    console.print(f"  [{color}]{bar}[/{color}]  [{color}]{pct:.1f}%[/{color}]")
    console.print(
        f"  [dim]{stats.used_tokens:,} / {stats.max_tokens:,} tokens  •  "
        f"{stats.message_count} messages[/dim]"
    )
    if stats.should_compact:
        console.print("  [yellow]⚠ Auto-compaction will trigger on next message[/yellow]")
    console.print("  [dim]Run /compact to compress older messages now[/dim]")
    console.print()


def _cmd_cost() -> None:
    """Show token usage and costs."""
    from payp.core.llm import LLMClient

    client: LLMClient | None = _state.get("llm_client")
    if not client:
        console.print("[dim]No LLM usage this session.[/dim]")
        return

    summary = client.get_cost_summary()
    from payp.ui.theme import Color
    table = Table(title=f"[{Color.BRAND}]Session Cost[/{Color.BRAND}]")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Input tokens", f"{summary['input_tokens']:,}")
    table.add_row("Output tokens", f"{summary['output_tokens']:,}")
    table.add_row("Total tokens", f"{summary['total_tokens']:,}")
    table.add_row("Queries", str(summary["query_count"]))
    table.add_row("Estimated cost", f"${summary['total_cost_usd']:.4f}")

    console.print(table)


def _cmd_export(args: str) -> None:
    """Export the current conversation session as a shareable markdown file."""
    from datetime import datetime
    from payp.ui.theme import Color
    from pathlib import Path

    chat = _state.get("chat_session")
    if not chat or not chat.messages:
        console.print("[dim]No conversation to export yet.[/dim]")
        return

    # Build markdown from messages
    conn_name = _state.get("active_connection") or "no-db"
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    lines: list[str] = []
    lines.append(f"# payp session — {date_str} {time_str} ({conn_name})")
    lines.append("")
    lines.append(f"**Messages:** {len(chat.messages)}  ")
    lines.append(f"**Mode:** {chat.mode.value}  ")
    if chat.conn and chat.conn.is_connected:
        lines.append(f"**Database:** {chat.conn.profile.db_type.value} — {chat.conn.db_version}  ")
    lines.append("")
    lines.append("---")
    lines.append("")

    for msg in chat.messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if not content:
            # Tool calls (assistant) — show name + args briefly
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "?")
                    lines.append(f"**🔧 Tool call:** `{name}`")
                    args_str = fn.get("arguments", "")
                    if args_str and len(args_str) < 200:
                        lines.append(f"```json\n{args_str}\n```")
                    lines.append("")
            continue

        if role == "user":
            lines.append(f"### 👤 User")
            lines.append("")
            lines.append(f"> {content}".replace("\n", "\n> "))
            lines.append("")
        elif role == "assistant":
            lines.append(f"### 🤖 Assistant")
            lines.append("")
            lines.append(content)
            lines.append("")
        elif role == "tool":
            # Tool results — show succinctly
            preview = content[:300].replace("\n", " ")
            lines.append(f"**Tool result:** `{preview}{'…' if len(content) > 300 else ''}`")
            lines.append("")

    # Cost + context footer
    try:
        cost = chat.llm.get_cost_summary()
        lines.append("---")
        lines.append("")
        lines.append(
            f"_Tokens: {cost['total_tokens']:,} "
            f"({cost['input_tokens']:,} in / {cost['output_tokens']:,} out) • "
            f"Cost: ${cost['total_cost_usd']:.4f} • "
            f"Queries: {cost['query_count']}_"
        )
    except Exception:
        pass

    # Write file
    export_dir = Path("./exports")
    export_dir.mkdir(parents=True, exist_ok=True)
    filename = f"session_{date_str}_{now.strftime('%H-%M')}_{conn_name}.md"
    if args.strip():
        # User provided a custom filename/path
        custom = Path(args.strip()).expanduser()
        if custom.suffix != ".md":
            custom = custom.with_suffix(".md")
        filepath = custom
        filepath.parent.mkdir(parents=True, exist_ok=True)
    else:
        filepath = export_dir / filename

    filepath.write_text("\n".join(lines))
    size_kb = filepath.stat().st_size / 1024
    console.print(
        f"[{Color.BRAND_ALT}]✓ Session exported:[/{Color.BRAND_ALT}] [{Color.BRAND_ALT}]{filepath}[/{Color.BRAND_ALT}] "
        f"[dim]({len(chat.messages)} messages, {size_kb:.1f} KB)[/dim]"
    )
    console.print(
        "[dim]Share via Slack, PR, email — sensitive credentials are NOT included.[/dim]"
    )


def _cmd_resume(args: str = "") -> None:
    """Resume a previous conversation session or clean up old ones.

    Usage:
      /resume                            browse & resume interactively
      /resume clean                      remove empty sessions (default)
      /resume clean --keep 20            remove empty + keep only 20 newest
      /resume clean --older-than 7d      also remove sessions older than 7 days
      /resume clean --all                remove ALL sessions except current
    """
    from payp.storage.sessions import (
        build_chat_messages_from_session,
        clean_sessions,
        delete_session,
        list_sessions,
        read_session,
    )
    from payp.ui.selector import SelectorAction, SelectorItem, interactive_select
    from payp.ui.theme import Color, PTColor

    parts = args.strip().split() if args.strip() else []
    subcommand = parts[0].lower() if parts else ""

    # ─── /resume clean [flags] ───
    if subcommand == "clean":
        empty_only = True
        keep_last: int | None = None
        older_than_days: int | None = None

        i = 1
        while i < len(parts):
            tok = parts[i]
            if tok == "--all":
                empty_only = False
                keep_last = 1  # keep only the most recent
                older_than_days = 0  # and delete everything
                i += 1
            elif tok == "--empty":
                empty_only = True
                i += 1
            elif tok == "--keep" and i + 1 < len(parts):
                try:
                    keep_last = int(parts[i + 1])
                except ValueError:
                    console.print(f"[red]Invalid --keep value: {parts[i + 1]}[/red]")
                    return
                i += 2
            elif tok == "--older-than" and i + 1 < len(parts):
                val = parts[i + 1]
                try:
                    older_than_days = int(val.rstrip("d"))
                except ValueError:
                    console.print(f"[red]Invalid --older-than value: {val}[/red]")
                    return
                i += 2
            else:
                console.print(f"[red]Unknown flag: {tok}[/red]")
                return

        # Protect the current session file if chat is active
        current_file: str | None = None
        chat = _state.get("chat_session")
        if chat and getattr(chat, "session_file", None):
            current_file = str(chat.session_file)

        result = clean_sessions(
            empty=empty_only,
            older_than_days=older_than_days,
            keep_last=keep_last,
            keep_current=current_file,
        )

        n = len(result["deleted"])
        if n == 0:
            console.print("[dim]Nothing to clean.[/dim]")
            return

        console.print(
            f"[green]✓[/green] Removed [bold]{n}[/bold] session(s) "
            f"[dim]({result['kept']} kept, {result['total_before']} before)[/dim]"
        )
        # Show first few deleted filenames for visibility
        for fn in result["deleted"][:5]:
            console.print(f"  [dim]- {fn}[/dim]")
        if n > 5:
            console.print(f"  [dim]  ... +{n - 5} more[/dim]")
        return

    # ─── default: interactive browser ───
    sessions = list_sessions()
    if not sessions:
        console.print("[dim]No sessions to resume.[/dim]")
        return

    items = []
    for s in sessions:
        summary = read_session(s["file"])
        topic = summary["summary"] or "(no messages)"
        conn = summary["connection"] or s["connection"]
        age = _format_session_age(summary["last_ts"])
        label = f"{s['date']} • {topic}"
        desc = f"{age} • {summary['query_count']} queries • {conn}"
        items.append(SelectorItem(
            label=label,
            value={"session": s, "summary": summary},
            description=desc,
        ))

    # Collect delete outcomes — do NOT print inside the callback (stdout
    # writes during prompt_toolkit render corrupt the frame). Same fix as
    # /knowledge.
    deleted_files: list[str] = []

    def _delete(item: SelectorItem) -> bool:
        s = item.value["session"]
        if delete_session(s["file"]):
            deleted_files.append(s["filename"])
            return True
        return False

    result = interactive_select(
        console=console,
        title=[(PTColor.BRAND, f"Recent Sessions ({len(items)})")],
        items=items,
        visible=5,
        actions=[SelectorAction(key="d", label="delete", callback=_delete)],
    )

    # Report deletions after the selector tears down its display
    for fn in deleted_files:
        console.print(f"  [red]Deleted[/red] {fn}")

    if result.action != "select" or not result.item:
        return

    session_info = result.item.value["session"]
    summary = result.item.value["summary"]

    # Load messages into current chat session
    chat = _state.get("chat_session")
    if not chat:
        _ensure_chat_session()
        chat = _state.get("chat_session")
    if not chat:
        console.print("[red]No chat session — configure /models first[/red]")
        return

    messages = build_chat_messages_from_session(session_info["file"])
    chat.messages = messages

    console.print(
        f"\n[{Color.BRAND_ALT}]✓[/{Color.BRAND_ALT}] Resumed session with {len(messages)} message(s)"
    )

    # Auto-reconnect to the session's connection if possible and not already
    target_conn = summary.get("connection")
    if target_conn and target_conn != _state.get("active_connection"):
        console.print(f"[dim]Reconnecting to {target_conn}...[/dim]")
        _cmd_db(target_conn)


def _format_session_age(ts: str) -> str:
    """Format ISO timestamp as relative age."""
    if not ts:
        return "unknown"
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - t
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"
    except Exception:
        return "unknown"


def _cmd_queries(args: str) -> None:
    """List and manage saved queries."""
    from payp.storage.queries import delete_query, list_queries
    from payp.ui.selector import SelectorAction, SelectorItem, interactive_select
    from payp.ui.theme import Color, PTColor

    queries = list_queries(filter_tag=args.strip())
    if not queries:
        if args:
            console.print(f"[dim]No queries matching '{args}'.[/dim]")
        else:
            console.print(
                "[dim]No saved queries yet. Ask the assistant to save queries:[/dim]\n"
                f"  [{Color.BRAND_ALT}]save this as 'monthly-revenue'[/{Color.BRAND_ALT}]"
            )
        return

    items = []
    for q in queries:
        label = q["name"]
        tag_str = f"  [{', '.join(q['tags'][:3])}]" if q["tags"] else ""
        desc = (q["description"] or "no description")[:60] + tag_str
        items.append(SelectorItem(label=label, value=q, description=desc))

    def _delete(item: SelectorItem) -> bool:
        q = item.value
        deleted = delete_query(q["name"])
        if deleted:
            console.print(f"  [red]Deleted[/red] {q['filename']}")
        return deleted

    q_title: list[tuple[str, str]] = [(PTColor.BRAND, f"Saved Queries ({len(items)})")]
    if args:
        q_title.append(("", f" — filter: {args}"))

    result = interactive_select(
        console=console,
        title=q_title,
        items=items,
        visible=5,
        actions=[SelectorAction(key="d", label="delete", callback=_delete)],
    )

    if result.action == "select" and result.item:
        q = result.item.value
        from rich.panel import Panel
        from rich.syntax import Syntax

        meta = f"[dim]{q['description']}[/dim]\n"
        if q["tags"]:
            meta += f"[dim]tags: {', '.join(q['tags'])}[/dim]\n"
        console.print()
        console.print(meta, end="")
        console.print(Panel(
            Syntax(q["sql"], "sql", theme="monokai", word_wrap=True),
            title=f"[{Color.BRAND_ALT}]{q['name']}[/{Color.BRAND_ALT}]",
            border_style="rgb(168,0,111)",
        ))
        console.print(
            f"[dim]Ask the assistant: [{Color.BRAND_ALT}]run the {q['name']} query[/{Color.BRAND_ALT}][/dim]\n"
        )


def _cmd_knowledge(args: str = "") -> None:
    """Browse / export / import the knowledge base.

    Usage:
      /knowledge                       — interactive browser
      /knowledge export [connection] [path]   — dump to markdown files
      /knowledge import [path] [connection]   — read markdown files in
      /knowledge migrate-legacy        — move ./payp/knowledge → ~/.payp/knowledge
    """
    from pathlib import Path

    from payp.memory.manager import (
        export_knowledge,
        get_memory_backend,
        import_knowledge,
    )
    from payp.storage.knowledge import (
        has_legacy_knowledge,
        migrate_legacy_to_global,
    )
    from payp.ui.selector import SelectorAction, SelectorItem, interactive_select
    from payp.ui.theme import Color, PTColor

    parts = args.strip().split() if args.strip() else []
    subcommand = parts[0].lower() if parts else ""

    # ─── /knowledge export [connection] [path] ───
    if subcommand == "export":
        conn_arg = parts[1] if len(parts) > 1 else None
        # Treat a path-like arg as path, not connection
        path_arg = None
        if conn_arg and ("/" in conn_arg or conn_arg.startswith(".") or conn_arg.startswith("~")):
            path_arg = conn_arg
            conn_arg = None
        if len(parts) > 2:
            path_arg = parts[2]
        target = path_arg or "./payp/knowledge"
        try:
            result = _run_async(export_knowledge(target, connection=conn_arg))
        except Exception as e:
            console.print(f"[red]Export failed: {e}[/red]")
            return
        target_abs = Path(result["target"])
        console.print(
            f"[green]✓[/green] Exported [bold]{result['exported']}[/bold] "
            f"table(s) from [cyan]{result['backend']}[/cyan]"
        )
        console.print(f"  [dim]Location:[/dim] {target_abs}")
        if result["exported"]:
            # Show files written
            for fp in result["files"][:5]:
                try:
                    rel = Path(fp).relative_to(target_abs)
                except ValueError:
                    rel = Path(fp)
                console.print(f"    [dim]- {rel}[/dim]")
            if len(result["files"]) > 5:
                console.print(f"    [dim]  ... +{len(result['files']) - 5} more[/dim]")

            # Figure out a usable git-add command
            try:
                rel_to_cwd = target_abs.relative_to(Path.cwd())
                git_add = f"git add {rel_to_cwd}"
            except ValueError:
                git_add = f"git add {target_abs}"
            console.print(
                f"  [dim]Commit & share:[/dim] [dim]{git_add} "
                f"&& git commit -m 'share db knowledge'[/dim]"
            )
        return

    # ─── /knowledge import [path] [connection] ───
    if subcommand == "import":
        path_arg = parts[1] if len(parts) > 1 else "./payp/knowledge"
        conn_arg = parts[2] if len(parts) > 2 else None
        try:
            result = _run_async(import_knowledge(path_arg, connection=conn_arg))
        except Exception as e:
            console.print(f"[red]Import failed: {e}[/red]")
            return
        msg = (
            f"[green]✓[/green] Imported [bold]{result['imported']}[/bold] "
            f"table(s) into [cyan]{result['backend']}[/cyan]"
        )
        if result.get("replaced"):
            msg += f" [dim]({result['replaced']} replaced)[/dim]"
        console.print(msg)
        if result["errors"]:
            console.print(f"[yellow]{len(result['errors'])} error(s):[/yellow]")
            for err in result["errors"][:5]:
                console.print(f"  [dim]- {err}[/dim]")
        return

    # ─── /knowledge migrate-legacy ───
    if subcommand in ("migrate-legacy", "migrate"):
        if not has_legacy_knowledge():
            console.print("[dim]No legacy ./payp/knowledge/ to migrate.[/dim]")
            return
        result = migrate_legacy_to_global()
        console.print(
            f"[green]✓[/green] Moved [bold]{result['migrated']}[/bold] file(s) "
            f"from [dim]{result['src']}[/dim] → [dim]{result['dst']}[/dim]"
        )
        if result.get("skipped"):
            console.print(
                f"[yellow]{result['skipped']}[/yellow] file(s) already existed "
                "in global — merged with a separator marker."
            )
        console.print(
            "[dim]Safe to [bold]rm -rf ./payp/knowledge[/bold] now if you want.[/dim]"
        )
        return

    # ─── default: interactive browser ───
    backend = get_memory_backend()
    backend_name = backend.name

    try:
        entries = _run_async(backend.list_all())
    except Exception as e:
        console.print(f"[red]Failed to list knowledge: {e}[/red]")
        return

    if not entries:
        console.print(
            f"[dim]No knowledge entries yet. (backend: {backend_name})[/dim]\n"
            "[dim]Knowledge is saved via propose_knowledge after user approval.[/dim]"
        )
        return

    items = []
    for e in entries:
        conn = e.get("connection", "?")
        name = e.get("name", "?")
        obj_type = e.get("type", "?")
        if obj_type == "mempalace_drawer":
            drawers = e.get("drawers", 0)
            desc = f"{drawers} drawer{'s' if drawers != 1 else ''}"
            label = f"{conn}/{name}"
        else:
            size = e.get("size", 0)
            desc = f"{size / 1024:.1f}KB" if size >= 1024 else f"{size}B"
            label = f"{conn}/{obj_type}/{name}"
        items.append(SelectorItem(label=label, value=e, description=desc))

    # Collect delete outcomes — do NOT print inside the callback (that writes
    # to stdout while prompt_toolkit is rendering and corrupts the frame).
    deleted: list[tuple[str, str]] = []
    failed: list[tuple[str, str, str]] = []

    def _delete(item: SelectorItem) -> bool:
        ei = item.value
        conn = ei.get("connection", "")
        table = ei.get("name", "")
        try:
            ok = _run_async(backend.delete(conn, table))
        except Exception as exc:
            failed.append((conn, table, str(exc)))
            return False
        if ok:
            deleted.append((conn, table))
            return True
        return False

    kb_title: list[tuple[str, str]] = [
        (PTColor.BRAND, f"Knowledge Base ({len(items)} entries) — backend: {backend_name}"),
    ]
    result = interactive_select(
        console=console,
        title=kb_title,
        items=items,
        visible=8,
        actions=[SelectorAction(key="d", label="delete", callback=_delete)],
    )

    # Report deletions now that the selector has torn down its display
    for conn, table in deleted:
        console.print(f"  [red]Deleted[/red] {conn}/{table}")
    for conn, table, err in failed:
        console.print(f"  [red]Delete failed[/red] {conn}/{table}: [dim]{err}[/dim]")

    if result.action == "select" and result.item:
        ei = result.item.value
        conn = ei.get("connection", "")
        table = ei.get("name", "")
        try:
            content = _run_async(backend.read(conn, table))
        except Exception as ex:
            console.print(f"[red]Read failed: {ex}[/red]")
            return
        if content:
            from rich.markdown import Markdown
            from rich.panel import Panel
            subtitle = ei.get("file") or f"{backend_name}://{conn}/{table}"
            console.print(Panel(
                Markdown(content),
                title=f"[{Color.BRAND_ALT}]{conn}/{table}[/{Color.BRAND_ALT}]",
                subtitle=str(subtitle),
                border_style="rgb(168,0,111)",
            ))


def _cmd_memory(args: str) -> None:
    """Manage memory backend: status, switch, migrate."""
    from payp.memory.manager import VALID_BACKENDS, backend_status, switch_backend
    from payp.ui.theme import Color

    parts = args.strip().split() if args.strip() else []
    subcommand = parts[0] if parts else ""

    if subcommand == "switch":
        # /memory switch <backend>
        if len(parts) < 2:
            console.print(f"[dim]Usage: /memory switch <{'|'.join(VALID_BACKENDS)}>[/dim]")
            return
        target = parts[1].lower()
        if target not in VALID_BACKENDS:
            console.print(f"[red]Unknown backend '{target}'. Valid: {', '.join(VALID_BACKENDS)}[/red]")
            return

        current = backend_status()
        if current["name"] == target:
            console.print(f"[dim]Already using '{target}' backend.[/dim]")
            return

        # Check availability before asking about migration
        if target == "mempalace":
            try:
                import importlib
                importlib.import_module("payp.memory.mempalace_bridge")
            except ImportError:
                console.print(
                    "[red]mempalace is not installed.[/red]\n"
                    "  [dim]Install with:  pip install mempalace[/dim]\n"
                    "  [dim]Then retry:    /memory switch mempalace[/dim]"
                )
                return

        # Ask about migration
        migrate = False
        if current["entries"] > 0:
            from prompt_toolkit import prompt as pt_prompt

            answer = pt_prompt(
                f"  Migrate {current['entries']} entries from '{current['name']}' to '{target}'? [y/N] "
            ).strip().lower()
            migrate = answer in ("y", "yes")

        try:
            result = switch_backend(target, migrate=migrate)
        except RuntimeError as exc:
            console.print(f"[red]{exc}[/red]")
            return

        console.print(f"  [{Color.BRAND_ALT}]Switched[/{Color.BRAND_ALT}] {result['from']} -> {result['to']}")
        if result.get("migration"):
            stats = result["migration"]
            console.print(f"  Migrated: {stats.get('migrated', 0)} entries")
            if stats.get("errors"):
                for err in stats["errors"]:
                    console.print(f"  [red]Error:[/red] {err}")

        # Inform user that the session continues normally
        console.print("  [dim]Session continues — next knowledge read/write uses new backend.[/dim]")
        return

    if subcommand == "migrate":
        # /memory migrate — migrate from current to the other backend
        current = backend_status()
        other = "mempalace" if current["name"] == "native" else "native"
        console.print(f"[dim]Migrating from '{current['name']}' to '{other}'...[/dim]")
        try:
            result = switch_backend(other, migrate=True)
        except RuntimeError as exc:
            console.print(f"[red]{exc}[/red]")
            return
        if result.get("switched"):
            stats = result.get("migration", {})
            console.print(f"  Migrated {stats.get('migrated', 0)} entries to '{other}'")
            if stats.get("errors"):
                for err in stats["errors"]:
                    console.print(f"  [red]Error:[/red] {err}")
        else:
            console.print(f"[dim]{result.get('reason', 'No change.')}[/dim]")
        return

    # Default: /memory or /memory status — show current backend info
    status = backend_status()
    size = status.get("size", 0)
    if size >= 1024 * 1024:
        size_str = f"{size / (1024 * 1024):.1f} MB"
    elif size >= 1024:
        size_str = f"{size / 1024:.1f} KB"
    else:
        size_str = f"{size} B"

    from rich.table import Table
    from rich import box

    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column(style="dim", width=14)
    table.add_column(style=Color.BRAND_ALT)
    table.add_row("Backend", status["name"])
    table.add_row("Entries", str(status.get("entries", 0)))
    table.add_row("Size", size_str)
    table.add_row("Healthy", "[green]yes[/green]" if status.get("healthy") else "[red]no[/red]")
    console.print()
    console.print(table)
    console.print(
        "  [dim]Usage: /memory switch <native|mempalace> | /memory migrate | /memory status[/dim]\n"
    )


def _cmd_history(args: str) -> None:
    """Show recent transaction log entries."""
    from payp.storage.transaction_log import count_transactions, query_history
    from payp.ui.theme import Color

    active = _state.get("active_connection")
    rows = query_history(connection_name=active, limit=20)

    if not rows:
        console.print("[dim]No transactions logged yet.[/dim]")
        return

    total = count_transactions(connection_name=active)
    title = f"Transaction History ({len(rows)} of {total})"
    if active:
        title += f" — {active}"

    table = Table(title=f"[{Color.BRAND}]{title}[/{Color.BRAND}]")
    table.add_column("Time", style="dim")
    table.add_column("Op", style="bold")
    table.add_column("Mode")
    table.add_column("By")
    table.add_column("Status")
    table.add_column("Rows")
    table.add_column("SQL", max_width=50)

    for r in rows:
        ts = r["timestamp"].split("T")[1][:8] if "T" in r["timestamp"] else r["timestamp"]
        status_color = Color.BRAND_ALT if r["status"] == "success" else ("yellow" if r["status"] == "cancelled" else "red")
        rows_str = str(r["rows_affected"]) if r["rows_affected"] is not None else "-"
        sql_preview = r["sql_executed"][:80].replace("\n", " ")
        table.add_row(
            ts,
            r["operation_type"],
            r["execution_mode"],
            r["approved_by"] or "-",
            f"[{status_color}]{r['status']}[/{status_color}]",
            rows_str,
            sql_preview,
        )

    console.print(table)


def _cmd_snapshots() -> None:
    """Manage data snapshots with interactive selector."""
    from payp.storage.snapshots import (
        delete_snapshot,
        format_snapshot_age,
        list_snapshots,
    )
    from payp.ui.selector import SelectorAction, SelectorItem, interactive_select
    from payp.ui.theme import Color, PTColor

    snapshots = list_snapshots()
    if not snapshots:
        console.print("[dim]No snapshots found.[/dim]")
        return

    # Build selector items
    items = []
    for s in snapshots:
        age = format_snapshot_age(s["timestamp"])
        label = f"{s['table']} ({s['operation']}) — {s['row_count']} rows"
        desc = f"{age} • {s['size']} • {s['connection']}"
        items.append(SelectorItem(label=label, value=s, description=desc))

    def _delete(item: SelectorItem) -> bool:
        """Delete the selected snapshot."""
        snap = item.value
        deleted = delete_snapshot(snap["file"])
        if deleted:
            console.print(f"  [red]Deleted[/red] {snap['filename']}")
        return deleted

    result = interactive_select(
        console=console,
        title=[(PTColor.BRAND, f"Snapshots ({len(items)})")],
        items=items,
        visible=5,
        actions=[SelectorAction(key="d", label="delete", callback=_delete)],
    )

    if result.action == "select" and result.item:
        snap = result.item.value
        console.print(f"\nSnapshot: [{Color.BRAND}]{snap['filename']}[/{Color.BRAND}]")
        console.print(f"  Table: {snap['table']}")
        console.print(f"  Operation: {snap['operation']}")
        console.print(f"  Rows: {snap['row_count']}")
        console.print(f"  Where: {snap['where']}")
        console.print(f"  File: {snap['file']}")
        console.print(f"\n[dim]Ask the assistant to restore this snapshot, or use /snapshots again to delete.[/dim]")


def _cmd_rollback() -> None:
    """Restore data from a snapshot — interactive picker with preview + confirmation."""
    import json
    from pathlib import Path

    from payp.storage.snapshots import format_snapshot_age, list_snapshots
    from payp.ui.selector import SelectorItem, interactive_select
    from payp.ui.theme import Color, PTColor

    mgr: ConnectionManager | None = _state.get("connection_manager")  # type: ignore[assignment]
    if not mgr or not mgr.is_connected:
        console.print("[yellow]Connect to a database first with /db[/yellow]")
        return

    snapshots = list_snapshots()
    if not snapshots:
        console.print("[dim]No snapshots available.[/dim]")
        return

    # Build selector items (newest first — list_snapshots already sorts)
    items = []
    for s in snapshots:
        age = format_snapshot_age(s["timestamp"])
        label = f"{s['table']} ({s['operation']}) — {s['row_count']} rows"
        desc = f"{age} • {s['size']} • {s['connection']}"
        items.append(SelectorItem(label=label, value=s, description=desc))

    result = interactive_select(
        console=console,
        title=[(PTColor.BRAND, f"Rollback — select snapshot to restore ({len(items)})")],
        items=items,
        visible=5,
    )

    if result.action != "select" or not result.item:
        console.print("[dim]Cancelled.[/dim]")
        return

    snap = result.item.value
    filepath = Path(snap["file"])

    # Show metadata
    console.print(f"\n[{Color.BRAND}]Snapshot:[/{Color.BRAND}] {snap['filename']}")
    console.print(f"  Table:     {snap['table']}")
    console.print(f"  Operation: {snap['operation']}")
    console.print(f"  Rows:      {snap['row_count']}")
    console.print(f"  Where:     {snap['where']}")
    console.print(f"  Taken:     {format_snapshot_age(snap['timestamp'])}")

    # Read + preview first 3 data rows
    try:
        lines = filepath.read_text().strip().split("\n")
        if len(lines) < 2:
            console.print("[red]Snapshot file has no data rows.[/red]")
            return
        preview_rows = [json.loads(line) for line in lines[1:4]]
    except Exception as e:
        console.print(f"[red]Failed to read snapshot: {e}[/red]")
        return

    if preview_rows:
        console.print(f"\n[{Color.BRAND}]Preview[/{Color.BRAND}] (first {len(preview_rows)} of {snap['row_count']} rows):")
        cols = list(preview_rows[0].keys())
        preview_table = Table(show_header=True, header_style=Color.BRAND_ALT, show_lines=False)
        for col in cols:
            preview_table.add_column(col, overflow="fold")
        for row in preview_rows:
            preview_table.add_row(*[str(row.get(c, "")) for c in cols])
        console.print(preview_table)

    # Confirm


    console.print()
    try:
        answer = command_prompt("Execute restore? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]Cancelled.[/dim]")
        return

    if answer != "y":
        console.print("[dim]Cancelled.[/dim]")
        return

    # Execute via RestoreSnapshotTool (reuses tested logic)
    from payp.tools.snapshot import RestoreSnapshotTool

    tool = RestoreSnapshotTool()
    console.print(f"[dim]Restoring {snap['row_count']} rows to {snap['table']}...[/dim]")
    try:
        tool_result = _run_async(
            tool.call({"file": str(filepath)}, {"connection_manager": mgr})
        )
    except Exception as e:
        console.print(f"[red]Restore failed: {e}[/red]")
        return

    if tool_result.success:
        restored = tool_result.data.get("restored", 0) if tool_result.data else 0
        table_name = tool_result.data.get("table", snap["table"]) if tool_result.data else snap["table"]

        # Extract restored IDs from snapshot data rows for visibility
        restored_ids = []
        try:
            for line in lines[1:]:
                row = json.loads(line)
                # Find primary-key-ish column
                for key in ("id", "ID", "pk"):
                    if key in row:
                        restored_ids.append(row[key])
                        break
        except Exception:
            pass

        ids_str = ""
        if restored_ids:
            if len(restored_ids) <= 10:
                ids_str = f" (ids: {', '.join(str(i) for i in restored_ids)})"
            else:
                ids_str = f" (ids: {', '.join(str(i) for i in restored_ids[:5])}, ...+{len(restored_ids)-5} more)"

        console.print(f"[{Color.BRAND_ALT}]✓ Restored {restored} rows to {table_name}[/{Color.BRAND_ALT}]{ids_str}")

        # Inject a system event into the chat session so the LLM knows what happened
        chat = _state.get("chat_session")
        if chat:
            where_clause = snap.get("where", "")
            notice = (
                f"[System notice — user ran /rollback] "
                f"Restored {restored} rows to {table_name} from snapshot {snap['filename']}. "
                f"Original WHERE clause: {where_clause}. "
            )
            if restored_ids:
                notice += f"Restored primary keys: {restored_ids[:30]}"
                if len(restored_ids) > 30:
                    notice += f" (+{len(restored_ids)-30} more)"
            chat.messages.append({"role": "assistant", "content": notice})
            try:
                chat.session.log_assistant(notice)
                chat.tx_log.log(
                    connection_name=mgr.profile.name,
                    operation_type="ROLLBACK",
                    sql_executed=f"RESTORE from {snap['filename']} WHERE {where_clause}",
                    execution_mode="rollback",
                    approved_by="user",
                    status="success",
                    rows_affected=restored,
                    model_used="cli-command",
                )
            except Exception:
                pass
    else:
        console.print(f"[red]Restore failed: {tool_result.error}[/red]")


def _cmd_diff(args: str) -> None:
    """Compare a table's schema between two active connections."""

    from payp.core.multi_connection import MultiConnectionManager
    from payp.ui.theme import Color
    from payp.tools.crossdb import CompareSchemasTool
    from payp.ui.selector import SelectorItem, interactive_select

    mc: MultiConnectionManager | None = _state.get("multi_conn_manager")  # type: ignore[assignment]
    if not mc:
        console.print("[yellow]No active connections. Use /db to connect first.[/yellow]")
        return

    active_names = mc.names()
    if len(active_names) < 2:
        console.print(
            "[yellow]/diff needs at least 2 active connections. "
            "Use /db to connect another.[/yellow]"
        )
        return

    parts = args.split() if args else []
    table: str | None = None
    conn_a: str | None = None
    conn_b: str | None = None

    if len(parts) >= 1:
        table = parts[0]
    if len(parts) >= 2:
        conn_a = parts[1]
    if len(parts) >= 3:
        conn_b = parts[2]

    for cn in (conn_a, conn_b):
        if cn and cn not in active_names:
            console.print(
                f"[red]Connection '{cn}' is not active.[/red] "
                f"Available: {', '.join(active_names)}"
            )
            return

    def _pick_connection(title: str, exclude: str | None = None) -> str | None:
        items = []
        for info in mc.list_info():
            name = info["name"]
            if name == exclude:
                continue
            desc = (
                f"{info['db_type']} • {info['host']} • {info['database']}"
                + (" • active" if info["is_active"] else "")
            )
            items.append(SelectorItem(label=name, value=name, description=desc))
        result = interactive_select(
            console=console, title=title, items=items, visible=5
        )
        if result.action != "select" or not result.item:
            return None
        return result.item.value  # type: ignore[no-any-return]

    if conn_a is None:
        if len(parts) == 1:
            conn_a = mc.active
            if not conn_a:
                conn_a = _pick_connection("Select connection A")
        else:
            conn_a = _pick_connection("Select connection A")
        if not conn_a:
            console.print("[dim]Cancelled.[/dim]")
            return

    if conn_b is None:
        conn_b = _pick_connection(
            f"Select connection B (comparing against {conn_a})", exclude=conn_a
        )
        if not conn_b:
            console.print("[dim]Cancelled.[/dim]")
            return

    if conn_a == conn_b:
        console.print("[red]Connection A and B must differ.[/red]")
        return

    if not table:
        try:
            table = command_prompt("Table name to compare: ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Cancelled.[/dim]")
            return
        if not table:
            console.print("[dim]Cancelled.[/dim]")
            return

    tool = CompareSchemasTool()
    console.print(f"[dim]Comparing {table}: {conn_a} vs {conn_b}...[/dim]")
    try:
        result = _run_async(
            tool.call(
                {"table": table, "connection_a": conn_a, "connection_b": conn_b},
                {"multi_conn": mc},
            )
        )
    except Exception as e:
        console.print(f"[red]Diff failed: {e}[/red]")
        return

    if not result.success:
        console.print(f"[red]{result.error}[/red]")
        return

    data = result.data or {}
    only_in_a = data.get("only_in_a", [])
    only_in_b = data.get("only_in_b", [])
    type_diffs = data.get("type_differences", [])
    shared = data.get("columns_in_both", 0)
    migration = data.get("migration_sql_to_sync_b_from_a")

    console.print()
    console.print(
        Panel.fit(
            f"[{Color.BRAND}]Comparing {table}[/{Color.BRAND}]: "
            f"[{Color.BRAND_ALT}]{conn_a}[/{Color.BRAND_ALT}] vs [{Color.BRAND_ALT}]{conn_b}[/{Color.BRAND_ALT}]",
            border_style="dim",
        )
    )

    if not only_in_a and not only_in_b and not type_diffs:
        console.print(f"[{Color.BRAND_ALT}]Schemas match — no differences.[/{Color.BRAND_ALT}]")
    else:
        diff_table = Table(show_header=True, header_style=Color.BRAND_ALT)
        diff_table.add_column("Column")
        diff_table.add_column(f"In {conn_a}", justify="center")
        diff_table.add_column(f"In {conn_b}", justify="center")
        diff_table.add_column(f"Type ({conn_a})")
        diff_table.add_column(f"Type ({conn_b})")
        diff_table.add_column("Notes")

        chk = f"[{Color.BRAND_ALT}]✓[/{Color.BRAND_ALT}]"
        for col in only_in_a:
            diff_table.add_row(
                col["name"], chk, "[red]✗[/red]",
                col.get("type", ""), "", f"[yellow]only in {conn_a}[/yellow]",
            )
        for col in only_in_b:
            diff_table.add_row(
                col["name"], "[red]✗[/red]", chk,
                "", col.get("type", ""), f"[yellow]only in {conn_b}[/yellow]",
            )
        for td in type_diffs:
            diff_table.add_row(
                td["column"], chk, chk,
                td.get("type_a", ""), td.get("type_b", ""),
                f"[{Color.BRAND}]type differs[/{Color.BRAND}]",
            )
        console.print(diff_table)

    console.print(
        f"\n[dim]{shared} columns shared, "
        f"{len(only_in_a)} only in {conn_a}, "
        f"{len(only_in_b)} only in {conn_b}, "
        f"{len(type_diffs)} type differences[/dim]"
    )

    if migration:
        console.print()
        console.print(
            Panel(
                migration,
                title=f"[{Color.BRAND}]Migration SQL — sync {conn_b} from {conn_a}[/{Color.BRAND}]",
                border_style="rgb(168,0,111)",
            )
        )
    console.print()


def _cmd_skills() -> None:
    """Browse and activate/deactivate skills (pre-defined workflows)."""
    from payp.skills.registry import discover_skills
    from payp.storage.active_skills import toggle_skill
    from payp.ui.selector import SelectorAction, SelectorItem, interactive_select
    from payp.ui.theme import Color, PTColor

    registry = discover_skills()
    skills = registry.all()
    if not skills:
        console.print(
            "[dim]No skills installed. Place .md skill files in "
            "builtin_skills/, ~/.payp/skills/, or ./payp/skills/[/dim]"
        )
        return

    # Filter by current dialect if a connection is active
    mgr: ConnectionManager | None = _state.get("connection_manager")  # type: ignore[assignment]
    dialect = None
    if mgr and mgr.is_connected:
        dialect = mgr.profile.db_type.value
        skills = [s for s in skills if s.supports_dialect(dialect)]
        if not skills:
            console.print(f"[dim]No skills support dialect: {dialect}[/dim]")
            return

    all_names = {s.name for s in registry.all()}
    active_names = registry.active_names()

    def _build_items() -> list[SelectorItem]:
        items = []
        for s in skills:
            is_on = s.name in active_names
            status = "●" if is_on else "○"
            dbs = ", ".join(s.frontmatter.db_types) or "any"
            label = f"{status} {s.name}"
            desc = f"{s.description} — {dbs} • {s.source_scope}"
            style = PTColor.BRAND_ALT if is_on else ""
            items.append(SelectorItem(label=label, value=s, description=desc, style=style))
        return items

    items = _build_items()

    def _toggle(item: SelectorItem) -> bool:
        skill = item.value
        now_active, updated_set = toggle_skill(skill.name, all_names)
        active_names.clear()
        active_names.update(updated_set)
        # Don't console.print() here — prompt_toolkit owns the terminal
        status = "●" if now_active else "○"
        item.label = f"{status} {skill.name}"
        item.style = PTColor.BRAND_ALT if now_active else ""
        return False  # don't remove from list

    title: list[tuple[str, str]] = [
        (PTColor.BRAND, f"Skills ({len(items)})"),
        ("", "  —  "),
        (PTColor.BRAND_ALT, "● active"),
        ("", "  "),
        ("", "○ inactive"),
    ]
    if dialect:
        title.append(("", f"  —  dialect: {dialect}"))

    while True:
        items = _build_items()
        result = interactive_select(
            console=console,
            title=title,
            items=items,
            visible=8,
            actions=[SelectorAction(key="a", label="toggle active", callback=_toggle)],
        )

        if result.action != "select" or not result.item:
            break

        # Show skill detail
        skill = result.item.value
        console.print()
        is_on = skill.name in active_names
        console.print(
            Panel(
                skill.body,
                title=f"[{Color.BRAND_ALT}]{skill.name}[/{Color.BRAND_ALT}] — {skill.description}",
                subtitle=(
                    f"when_to_use: {skill.when_to_use}  •  "
                    f"db_types: {', '.join(skill.frontmatter.db_types) or 'any'}  •  "
                    f"tools: {', '.join(skill.frontmatter.allowed_tools) or 'any'}  •  "
                    f"scope: {skill.source_scope}"
                ),
                border_style="rgb(168,0,111)",
            )
        )
        active_str = f"[{Color.BRAND_ALT}]● ACTIVE[/{Color.BRAND_ALT}]" if is_on else "○ INACTIVE"
        console.print(f"  [dim]Source: {skill.source_path}[/dim]")
        console.print(f"  Status: {active_str}")
        console.print()

        # Action prompt — activate/deactivate, back, or done
        action_label = "deactivate" if is_on else "activate"
        console.print(f"  [dim]a = {action_label}  •  Enter = back to list  •  Esc = done[/dim]")
        try:
            choice = command_prompt("  ").strip().lower()
        except CommandCancelled:
            break
        if choice == "a":
            _toggle(result.item)
        # Any other input (including empty Enter) → loop back to selector


def _cmd_help() -> None:
    """Show available commands grouped by category."""
    from rich import box

    groups = [
        ("Connection", [
            ("/db", "manage database connections"),
            ("/db <name>", "connect to named connection"),
            ("/credentials", "edit saved credentials"),
        ]),
        ("AI", [
            ("/models", "manage AI providers"),
            ("/mode", "show or set security mode"),
            ("/skills", "browse available workflows"),
        ]),
        ("Data", [
            ("/schema", "explore schema"),
            ("/schema <table>", "show table DDL"),
            ("/stats <table>", "column statistics & data profile"),
            ("/knowledge", "browse business context & notes"),
            ("/knowledge export [conn] [path]", "dump knowledge to .md for sharing"),
            ("/knowledge import [path]", "load shared .md files into backend"),
            ("/memory", "manage knowledge backend"),
            ("/queries", "saved SQL library"),
            ("/snapshots", "manage backups (↑↓ d Enter)"),
            ("/rollback", "restore from snapshot"),
            ("/diff <table>", "compare schema between connections"),
            ("/history", "SQL audit log"),
        ]),
        ("Session", [
            ("/resume", "continue a previous session"),
            ("/resume clean", "purge empty sessions (also --keep N / --older-than Nd / --all)"),
            ("/context", "show context window usage"),
            ("/compact", "compress older messages"),
            ("/more", "next 20 rows of last SELECT"),
            ("/cost", "token usage and costs"),
            ("/export [path]", "export session to markdown"),
            ("/help", "this help"),
            ("/quit", "exit payp"),
        ]),
    ]

    from payp.ui.theme import Color
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column(style="dim", width=10)
    table.add_column(style=Color.BRAND_ALT, width=22)
    table.add_column()

    for group_name, commands in groups:
        for i, (cmd, desc) in enumerate(commands):
            prefix = group_name if i == 0 else ""
            table.add_row(prefix, cmd, desc)
        table.add_row("", "", "")  # spacer

    console.print()
    console.print(table)
    console.print(
        "  [dim]Type naturally (no /) to chat with the AI assistant.[/dim]\n"
        "  [dim]Ctrl+C cancel  •  Ctrl+D exit  •  ↑↓ history[/dim]\n"
    )


def _cmd_quit() -> None:
    """Exit payp."""
    console.print("[dim]Goodbye![/dim]")
    raise typer.Exit()


# --- CLI subcommands (for non-interactive use) ---


@app.command()
def db(
    name: Annotated[Optional[str], typer.Argument(help="Connection name")] = None,
) -> None:
    """Manage database connections."""
    _cmd_db(name or "")


@app.command()
def models(
    action: Annotated[Optional[str], typer.Argument(help="Action: add")] = None,
) -> None:
    """Manage AI model providers."""
    _cmd_models(action or "")


@app.command()
def mode(
    new_mode: Annotated[Optional[str], typer.Argument(help="Security mode")] = None,
) -> None:
    """Set security mode."""
    _cmd_mode(new_mode or "")


@app.command("mcp-serve")
def mcp_serve() -> None:
    """Start payp as an MCP stdio server for external clients (Claude Desktop, Cursor, etc.)."""
    from payp.mcp.server import main
    main()


def app_entry() -> None:
    """Entry point for the payp CLI."""
    app()
