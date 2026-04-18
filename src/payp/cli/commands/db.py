"""Slash commands: /db, /credentials."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

from payp.cli.runtime import (
    _run_async,
    command_prompt,
    prompt_choice,
    prompt_confirm,
    prompt_int,
    prompt_required,
)
from payp.cli.state import _state, console

if TYPE_CHECKING:
    from payp.db.docker_scan import DetectedDb


def _db_connection_completions() -> tuple[tuple[str, str], ...]:
    """Return saved connections as (name, meta) pairs for autocomplete."""
    from payp.config import list_connections, load_connection_profile

    connections = list_connections()
    if not connections:
        return ()
    result: list[tuple[str, str]] = []
    for i, name in enumerate(connections, 1):
        profile = load_connection_profile(name)
        if profile:
            p = profile
            meta = f"[{i}] {p.db_type.value} @ {p.host}:{p.port}/{p.database}"
        else:
            meta = f"[{i}] saved connection"
        result.append((name, meta))
    return tuple(result)


def _cmd_db(args: str) -> None:
    """Manage database connections."""
    from rich.table import Table

    from payp.config import list_connections, load_connection_profile, load_credential
    from payp.ui.theme import Color

    args_stripped = args.strip()
    if args_stripped.lower().startswith("scan"):
        rest = args_stripped[4:].strip().lower()
        if rest in ("", "docker"):
            _scan_docker_and_prompt()
            return
        console.print(
            f"[red]Unknown scan target '{rest}'. Try /db scan docker.[/red]"
        )
        return

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
        f"\n[dim]Enter connection number, name, [/dim]"
        f"[{Color.BRAND_ALT}]new[/{Color.BRAND_ALT}][dim] to add, [/dim]"
        f"[{Color.BRAND_ALT}]scan[/{Color.BRAND_ALT}][dim] to detect Docker, or [/dim]"
        f"[red]del[/red][dim] to remove.[/dim]"
    )

    # Build a single option map: digits, names (lower), "new", "scan", "del"
    # all map to the action we want to take. prompt_choice loops until valid.
    options: dict[str, str] = {
        "new": "__new__", "del": "__del__", "scan": "__scan__",
    }
    for i, name in enumerate(connections, 1):
        options[str(i)] = name
        options[name.lower()] = name
    action = prompt_choice("Select: ", options)

    if action == "__new__":
        _setup_new_connection()
    elif action == "__del__":
        _delete_connection()
    elif action == "__scan__":
        _scan_docker_and_prompt()
    else:
        _cmd_db(action)


def _setup_new_connection() -> None:
    """Interactive wizard to add a new database connection."""
    from payp.config import save_connection_profile, save_credential
    from payp.models import ConnectionCredential, ConnectionProfile, DbType
    from payp.ui.theme import Color

    console.print(f"\n[{Color.BRAND}]New Database Connection[/{Color.BRAND}]\n")

    console.print("  1. PostgreSQL")
    console.print("  2. MySQL")
    console.print("  3. Oracle")
    console.print("  4. MongoDB")
    db_type = prompt_choice(
        "Select type: ",
        {
            "1": DbType.POSTGRESQL,
            "2": DbType.MYSQL,
            "3": DbType.ORACLE,
            "4": DbType.MONGODB,
        },
    )

    default_ports = {
        DbType.POSTGRESQL: 5432,
        DbType.MYSQL: 3306,
        DbType.ORACLE: 1521,
        DbType.MONGODB: 27017,
    }
    default_port = default_ports[db_type]

    if db_type == DbType.ORACLE:
        console.print("[dim]Oracle uses your username as the schema[/dim]")
    if db_type == DbType.MONGODB:
        console.print("[dim]MongoDB: user auth uses the target database[/dim]")

    host = prompt_required("Host: ")
    port = prompt_int(
        f"Port [{default_port}]: ", default=default_port, min_val=1, max_val=65535
    )
    database_label = "Service Name (PDB): " if db_type == DbType.ORACLE else "Database: "
    database = prompt_required(database_label)

    # Auth is optional: localhost dev setups often run without auth
    # (MongoDB anonymous, Postgres trust, MySQL skip-grant-tables). When
    # the user opts out we leave username/password empty — drivers will
    # then build a connection URI without credentials.
    console.print(
        "[dim]Auth: leave off for localhost dev setups without a password "
        "(Mongo anonymous, Postgres trust, etc).[/dim]"
    )
    if prompt_confirm("Use authentication (username + password)?", default=True):
        username = prompt_required("Username: ")
        password = command_prompt("Password: ", is_password=True)
        if not password:
            console.print(
                "[yellow]No password entered. The server may reject the "
                "connection if it expects one.[/yellow]"
            )
    else:
        username = ""
        password = ""
    conn_name = prompt_required("Connection name: ")

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


def _scan_docker_and_prompt() -> None:
    """Scan Docker for DB containers and offer the user to save one."""
    from rich.table import Table

    from payp.db.docker_scan import list_db_containers
    from payp.ui.theme import Color

    with console.status("Scanning Docker for database containers..."):
        detected = _run_async(list_db_containers())

    if not detected:
        console.print(
            "[dim]No database containers detected on the local Docker daemon.[/dim]"
        )
        return

    table = Table(title=f"[{Color.BRAND}]Docker Database Containers[/{Color.BRAND}]")
    table.add_column("#", style="dim")
    table.add_column("Container", style="bold")
    table.add_column("Image")
    table.add_column("Type")
    table.add_column("Address")
    table.add_column("Auth")
    for i, d in enumerate(detected, 1):
        address = f"{d.host}:{d.port}/{d.database}"
        if d.password:
            auth = f"{d.username or '-'} [dim](pw from env)[/dim]"
        elif d.username:
            auth = f"{d.username} [dim](no pw)[/dim]"
        else:
            auth = "[dim]none[/dim]"
        table.add_row(str(i), d.container_name, d.image, d.db_type.value, address, auth)
    console.print(table)
    console.print(
        "\n[dim]Enter container number to save and connect, or[/dim] "
        "[red]n[/red][dim] to cancel.[/dim]"
    )

    options: dict[str, Any] = {"n": None, "no": None, "cancel": None}
    for i, d in enumerate(detected, 1):
        options[str(i)] = d
    choice = prompt_choice("Select: ", options)
    if choice is None:
        console.print("[dim]Cancelled.[/dim]")
        return
    _save_detected_connection(choice)


def _save_detected_connection(detected: DetectedDb) -> None:
    """Persist a detected container as a named connection, then connect."""
    from payp.config import list_connections, save_connection_profile, save_credential
    from payp.models import ConnectionCredential, ConnectionProfile
    from payp.ui.theme import Color

    existing = set(list_connections())
    default_name = detected.container_name
    if default_name in existing:
        i = 2
        while f"{default_name}-{i}" in existing:
            i += 1
        default_name = f"{default_name}-{i}"

    console.print(
        f"\n[{Color.BRAND}]Save detected connection[/{Color.BRAND}] "
        f"[dim]({detected.db_type.value} @ {detected.host}:{detected.port})[/dim]"
    )
    conn_name = prompt_required(
        f"Connection name [{default_name}]: ", default=default_name
    )

    password: str | None = None
    if detected.password:
        console.print(
            "[dim]A password was found in the container's env vars. "
            "Saving it will write it to ~/.payp/connections/ (chmod 600).[/dim]"
        )
        if prompt_confirm("Use password from Docker env?", default=True):
            password = detected.password
        else:
            pw = command_prompt("Password (empty to skip): ", is_password=True).strip()
            password = pw or None
    elif detected.username:
        pw = command_prompt(
            "Password (empty if the container uses trust auth): ", is_password=True,
        ).strip()
        password = pw or None

    profile = ConnectionProfile(
        name=conn_name,
        db_type=detected.db_type,
        host=detected.host,
        port=detected.port,
        database=detected.database,
        username=detected.username,
    )
    credential = ConnectionCredential(password=password)

    save_connection_profile(profile)
    save_credential(conn_name, credential)

    console.print(
        f"\n[{Color.BRAND_ALT}]Connection '{conn_name}' saved.[/{Color.BRAND_ALT}]"
    )
    _run_async(_connect_to_db(conn_name, profile, credential))


def _delete_connection() -> None:
    """Interactive wizard to delete a saved connection."""
    from rich.table import Table

    from payp.config import delete_connection, list_connections, load_connection_profile
    from payp.db.cache import clear_connection_cache
    from payp.ui.theme import Color

    connections = list_connections()
    if not connections:
        console.print("[yellow]No connections to delete.[/yellow]")
        return

    if len(connections) == 1:
        target_name = connections[0]
    else:
        table = Table(title=f"[{Color.BRAND}]Delete Connection — Select[/{Color.BRAND}]")
        table.add_column("#", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Type")
        table.add_column("Host")
        for i, name in enumerate(connections, 1):
            profile = load_connection_profile(name)
            if profile:
                table.add_row(str(i), name, profile.db_type.value, profile.host)
        console.print(table)
        options: dict[str, str] = {}
        for i, name in enumerate(connections, 1):
            options[str(i)] = name
            options[name.lower()] = name
        try:
            target_name = prompt_choice("Select connection to delete: ", options)
        except (KeyboardInterrupt, EOFError):
            console.print("[dim]Cancelled.[/dim]")
            return

    profile = load_connection_profile(target_name)
    hint = f" ({profile.db_type.value} @ {profile.host})" if profile else ""
    if not prompt_confirm(f"Delete '{target_name}'{hint}?", default=False):
        console.print("[dim]Cancelled.[/dim]")
        return

    # Disconnect if this is the active connection
    active_name = _state.get("active_connection")
    if active_name == target_name:
        console.print(f"[yellow]Disconnecting from active connection '{target_name}'...[/yellow]")
        _state.pop("active_connection", None)
        _state.pop("connection_manager", None)
        _state.pop("t0", None)
        _state.pop("t1", None)
        _state.pop("fk_graph", None)
        mc = _state.get("multi_conn_manager")
        if mc:
            mc._connections.pop(target_name, None)
            if mc._active == target_name:
                mc._active = None

    delete_connection(target_name)
    clear_connection_cache(target_name)
    console.print(f"[{Color.BRAND_ALT}]Connection '{target_name}' deleted.[/{Color.BRAND_ALT}]")


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
        options: dict[str, str] = {}
        for i, name in enumerate(connections, 1):
            options[str(i)] = name
            options[name.lower()] = name
        try:
            target_name = prompt_choice("Select: ", options)
        except (KeyboardInterrupt, EOFError):
            console.print("[yellow]Cancelled, no changes saved.[/yellow]")
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
        host = prompt_required("Host: ", default=profile.host)
        port = prompt_int(
            "Port: ", default=profile.port, min_val=1, max_val=65535
        )
        database = prompt_required("Database: ", default=profile.database)
        username = prompt_required("Username: ", default=profile.username)

        new_password: str | None = None
        if prompt_confirm("Change password?", default=False):
            pw = command_prompt("New password: ", is_password=True).strip()
            if not pw:
                console.print("[yellow]Empty password, keeping existing.[/yellow]")
            else:
                new_password = pw
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
        from payp.ui.errors import show_error

        short_error = str(e).split(", full error:", 1)[0].strip()
        show_error(
            "Connection failed",
            f"Failed to connect to {name}: {short_error}",
            exc=e,
            hint="Check host, port, credentials, and that the database is reachable.",
            logger_name="payp.db",
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

    try:
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
        # FK adjacency graph — built/reloaded here for both paths above
        from payp.db.cache import load_fk_graph, save_fk_graph
        from payp.db.introspection import discover_fk_graph, hash_table_names
        if t1 is not None:
            new_hash = hash_table_names(t1)
            fk_graph = load_fk_graph(name)
            if fk_graph is None or fk_graph.table_names_hash != new_hash:
                fk_graph = await discover_fk_graph(mgr)
                fk_graph.table_names_hash = new_hash
                save_fk_graph(name, fk_graph)
            tick = f"[{Color.BRAND_ALT}]✓[/{Color.BRAND_ALT}]"
            console.print(f"  {tick} FK graph: {len(fk_graph.edges)} relationships")
        else:
            from payp.models import SchemaGraph
            fk_graph = SchemaGraph()
        _state["fk_graph"] = fk_graph
    except Exception as e:
        from payp.ui.errors import show_error
        raw = str(e)
        # MongoDB OperationFailure cramming "full error: {dict}" onto the same line
        # is noise at the CLI level - keep only the sentence before it.
        short_error = raw.split(", full error:", 1)[0].strip()

        msg_lower = raw.lower()
        is_auth = (
            "requires authentication" in msg_lower
            or "unauthorized" in msg_lower
            or "auth" in msg_lower
        )
        if is_auth:
            hint = (
                "The server rejected an unauthenticated request. "
                "It looks like this database requires credentials.\n\n"
                f"Next steps:\n"
                f"  - /credentials {name} to add username and password\n"
                "  - /db -> new to recreate with authentication enabled"
            )
        else:
            hint = (
                "The connection stayed open but schema metadata could "
                "not be loaded.\n"
                "Next step: /schema --refresh once the underlying issue is resolved."
            )

        show_error(
            "Schema discovery failed",
            short_error,
            exc=e,
            hint=hint,
            logger_name=__name__,
        )
        # Roll the half-initialised connection back so the user does not
        # end up chatting with a DB we could not introspect.
        if mc and mc.has(name):
            mc._connections.pop(name, None)
            if mc._active == name:
                mc._active = None
        _state.pop("active_connection", None)
        _state.pop("connection_manager", None)
        _state.pop("t0", None)
        _state.pop("t1", None)
        try:
            await mgr.disconnect()
        except Exception as e:
            logger.warning("Disconnect during cleanup failed: %s", e)
        return

    mc = _state.get("multi_conn_manager")
    if mc and mc.has(name) and t0 and t1:
        mc.set_schemas(name, t0, t1)

    _ensure_chat_session()
    _log_db_connected_to_session(name)

    console.print()
