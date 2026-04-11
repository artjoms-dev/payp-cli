"""Slash commands: /db, /credentials."""

from __future__ import annotations

import asyncio
from typing import Any

from payp.cli.runtime import _run_async, command_prompt
from payp.cli.state import _state, console


def _cmd_db(args: str) -> None:
    """Manage database connections."""
    from rich.table import Table

    from payp.config import list_connections, load_connection_profile, load_credential
    from payp.ui.theme import Color

    connections = list_connections()

    if args:
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
        _setup_new_connection()
        return

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
    console.print(
        f"\n[dim]Enter connection number, name, or [/dim]"
        f"[{Color.BRAND_ALT}]new[/{Color.BRAND_ALT}][dim] to add one.[/dim]"
    )

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
    from payp.config import save_connection_profile, save_credential
    from payp.models import ConnectionCredential, ConnectionProfile, DbType
    from payp.ui.theme import Color

    console.print(f"\n[{Color.BRAND}]New Database Connection[/{Color.BRAND}]\n")

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

    _run_async(_connect_to_db(conn_name, profile, credential))


def _cmd_credentials(args: str) -> None:
    """Edit saved connection credentials without re-running the full wizard."""
    from rich.table import Table

    from payp.config import (
        list_connections,
        load_connection_profile,
        load_credential,
        save_connection_profile,
        save_credential,
    )
    from payp.models import ConnectionCredential, ConnectionProfile
    from payp.ui.theme import Color

    connections = list_connections()
    if not connections:
        console.print("[yellow]No connections to edit. Use /db to add one.[/yellow]")
        return

    target_name = args.strip()
    if not target_name:
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

    active_name = _state.get("active_connection")
    if active_name == target_name:
        console.print(
            f"[yellow]You're currently connected to {target_name}. "
            f"Changes take effect on next /db {target_name}[/yellow]"
        )

    console.print(
        f"\n[{Color.BRAND}]Editing credentials for[/{Color.BRAND}]"
        f" [{Color.BRAND_ALT}]{target_name}[/{Color.BRAND_ALT}]"
    )
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
        database = (
            command_prompt("Database: ", default=profile.database).strip() or profile.database
        )
        username = (
            command_prompt("Username: ", default=profile.username).strip() or profile.username
        )

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


async def _connect_to_db(
    name: str,
    profile: Any,
    credential: Any,
) -> None:
    """Connect to a database (or switch to it if already connected), run discovery."""
    from payp.cli.loop import _ensure_chat_session, _log_db_connected_to_session
    from payp.db.cache import get_cached_table_count, has_cache, save_metadata, save_t0, save_t1
    from payp.db.connection import ConnectionError as ConnError
    from payp.db.connection import ConnectionManager
    from payp.db.introspection import discover_t0, discover_t1, get_db_metadata
    from payp.models import DbType
    from payp.ui.theme import Color

    mc = _state.get("multi_conn_manager")
    if mc and mc.has(name):
        mc.set_active(name)
        state = mc.get(name)
        if state:
            mgr = state.manager
            _state["active_connection"] = name
            _state["connection_manager"] = mgr
            _state["t0"] = state.t0
            _state["t1"] = state.t1
            console.print(
                f"[{Color.BRAND_ALT}]Switched to {name} ({mgr.db_version})[/{Color.BRAND_ALT}]"
            )
            _ensure_chat_session()
            _log_db_connected_to_session(name)
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
        console.print(
            "[dim]Check host, port, credentials, and that the database is reachable.[/dim]"
        )
        return

    if mc is None:
        from payp.core.multi_connection import MultiConnectionManager
        mc = MultiConnectionManager()
        _state["multi_conn_manager"] = mc

    from payp.core.multi_connection import ConnectionState
    mc._connections[name] = ConnectionState(name=name, manager=mgr)
    mc._active = name

    _state["active_connection"] = name
    _state["connection_manager"] = mgr
    console.print(f"[{Color.BRAND_ALT}]Connected to {name} ({version})[/{Color.BRAND_ALT}]")

    if has_cache(name):
        cached_count = get_cached_table_count(name)
        t0 = await discover_t0(mgr)
        current_count = t0.total_tables

        if cached_count != current_count:
            console.print(
                f"[yellow]Schema changed since last session "
                f"({cached_count} → {current_count} tables). "
                f"Run /schema --refresh for details.[/yellow]"
            )
            t1 = await discover_t1(mgr)
            save_t0(name, t0)
            save_t1(name, t1)
        else:
            console.print(
                f"[{Color.BRAND_ALT}]Schema cache up to date"
                f" ({current_count} tables)[/{Color.BRAND_ALT}]"
            )
            from payp.db.cache import load_t1
            t1 = load_t1(name)

        _state["t0"] = t0
        _state["t1"] = t1
    else:
        console.print("\n[dim]Running initial discovery...[/dim]")

        t0 = await discover_t0(mgr)
        t1 = await discover_t1(mgr)
        meta = await get_db_metadata(mgr)

        schema_count = len(t0.schemas)
        plural = "s" if schema_count != 1 else ""
        tick = f"[{Color.BRAND_ALT}]✓[/{Color.BRAND_ALT}]"
        console.print(f"  {tick} {schema_count} schema{plural} found")
        console.print(f"  {tick} {t0.total_tables} tables, {t0.view_count} views")
        uptime = meta.get("uptime", "unknown")
        db_size = meta.get("db_size", "unknown")
        console.print(f"  [{Color.BRAND_ALT}]✓[/{Color.BRAND_ALT}] Server uptime: {uptime}")
        console.print(f"  [{Color.BRAND_ALT}]✓[/{Color.BRAND_ALT}] Database size: {db_size}")

        save_t0(name, t0)
        save_t1(name, t1)
        save_metadata(name, meta)
        console.print(f"  [{Color.BRAND_ALT}]✓[/{Color.BRAND_ALT}] Schema cache saved")

        _state["t0"] = t0
        _state["t1"] = t1

    mc = _state.get("multi_conn_manager")
    if mc and mc.has(name) and t0 and t1:
        mc.set_schemas(name, t0, t1)

    _ensure_chat_session()
    _log_db_connected_to_session(name)

    console.print()
