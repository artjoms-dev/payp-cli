"""MCP stdio server for payp.

Exposes payp tools (SQL execution, schema introspection, snapshots, knowledge,
queries, exports, charts) to external MCP clients such as Claude Desktop and
Cursor — while enforcing payp's safety layers.

IMPORTANT: stdio transport uses stdout for protocol frames. All logs go to
stderr. Never print() to stdout.

Run with:
    python -m payp.mcp.server

Optional env vars:
    PAYP_MCP_CONNECTION=<name>   Auto-connect to a saved connection on startup
    PAYP_MCP_MODE=<manual|yolo>  Security mode for DML/DDL (default: manual)
                                 In MCP there is no interactive approval, so:
                                   - manual: block all DML/DDL (read-only effectively)
                                   - yolo: allow DML/DDL but still hard-block
                                           DROP DATABASE/SCHEMA/TRUNCATE
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

import mcp.types as mcp_types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from payp.config import list_connections, load_connection_profile, load_credential
from payp.core.classifier import SqlCategory, classify_sql
from payp.db.connection import ConnectionError as PaypConnectionError
from payp.db.connection import ConnectionManager
from payp.mcp.tools_adapter import (
    build_payp_registry,
    error_content,
    info_content,
    payp_tool_to_mcp,
    tool_result_to_mcp,
)
from payp.models import SecurityMode
from payp.tools.base import ToolResult

# --- logging: always stderr for stdio MCP servers ---
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="[payp-mcp] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("payp.mcp")


# --- MCP-specific custom tools ---

MCP_LIST_CONNECTIONS = "mcp_list_connections"
MCP_CONNECT_DATABASE = "mcp_connect_database"
MCP_CURRENT_CONNECTION = "mcp_current_connection"

CUSTOM_TOOLS: list[mcp_types.Tool] = [
    mcp_types.Tool(
        name=MCP_LIST_CONNECTIONS,
        description="List saved payp database connection profile names.",
        inputSchema={"type": "object", "properties": {}},
    ),
    mcp_types.Tool(
        name=MCP_CONNECT_DATABASE,
        description=(
            "Connect to a saved payp database connection by name. "
            "Must be called before using execute_sql or schema tools."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the saved connection profile.",
                },
            },
            "required": ["name"],
        },
    ),
    mcp_types.Tool(
        name=MCP_CURRENT_CONNECTION,
        description="Show the active database connection (name, host, database, version).",
        inputSchema={"type": "object", "properties": {}},
    ),
]


class ServerState:
    """Shared mutable state across MCP tool calls."""

    def __init__(self, mode: SecurityMode = SecurityMode.MANUAL) -> None:
        self.connection_manager: ConnectionManager | None = None
        self.mode: SecurityMode = mode
        self.lock = asyncio.Lock()

    async def connect(self, name: str) -> str:
        """Connect to a saved profile. Returns db version string."""
        profile = load_connection_profile(name)
        if profile is None:
            raise PaypConnectionError(
                f"Connection '{name}' not found. "
                f"Available: {list_connections()}"
            )
        cred = load_credential(name)
        if cred is None:
            raise PaypConnectionError(
                f"No credential found for connection '{name}'. "
                f"Set PAYP_{name.upper().replace('-', '_')}_PASSWORD or run `payp connect` to save one."
            )
        # Close any prior connection first
        if self.connection_manager is not None:
            try:
                await self.connection_manager.disconnect()
            except Exception:
                pass
        mgr = ConnectionManager(profile, cred)
        version = await mgr.connect()
        self.connection_manager = mgr
        return version


def _resolve_security_mode() -> SecurityMode:
    raw = os.environ.get("PAYP_MCP_MODE", "manual").strip().lower()
    try:
        return SecurityMode(raw)
    except ValueError:
        logger.warning("Unknown PAYP_MCP_MODE=%r, defaulting to manual", raw)
        return SecurityMode.MANUAL


def _resolve_allow_code_exec() -> bool:
    raw = os.environ.get("PAYP_MCP_ALLOW_CODE_EXEC", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _enforce_tool_policy(
    tool: Any,
    tool_name: str,
    args: dict[str, Any],
    mode: SecurityMode,
) -> str | None:
    """Return an error string if the tool call should be blocked.

    Two-layer policy (MCP has no interactive approval):

    1. SQL classification for `execute_sql`:
       - DROP DATABASE / DROP SCHEMA / TRUNCATE: ALWAYS blocked
       - MANUAL mode: block DML_WRITE / DDL / GRANT
       - YOLO mode: allow all except hard-blocks
       - SELECT: always allowed

    2. Generic destructive-tool check (is_destructive=True):
       - MANUAL mode: blocked (no approval UI in MCP)
       - YOLO mode: allowed
    """
    # --- Layer 1: SQL classification for execute_sql ---
    if tool_name == "execute_sql":
        sql = (args.get("sql") or "").strip()
        if sql:
            try:
                cls = classify_sql(sql)
            except Exception as e:
                logger.warning("classify_sql failed: %s", e)
                cls = None

            if cls is not None:
                if cls.is_hard_block or cls.category == SqlCategory.HARD_BLOCK:
                    return (
                        f"Blocked by payp safety policy: {cls.statement_type}. "
                        f"{cls.risk_reason or 'Irreversible operation.'} "
                        f"This operation is blocked over MCP regardless of mode."
                    )
                if mode == SecurityMode.MANUAL and cls.category in (
                    SqlCategory.DML_WRITE,
                    SqlCategory.DDL,
                    SqlCategory.GRANT,
                ):
                    return (
                        f"Blocked: {cls.statement_type} requires interactive approval. "
                        f"MCP runs in MANUAL (read-only) mode by default. "
                        f"Set PAYP_MCP_MODE=yolo on the server to enable writes, "
                        f"or run this SQL via the payp interactive CLI."
                    )
        return None

    # --- Layer 2: generic destructive-tool check (non-SQL tools) ---
    if getattr(tool, "is_destructive", False) and mode == SecurityMode.MANUAL:
        return (
            f"Blocked: '{tool_name}' is a destructive operation. "
            f"MCP has no interactive approval flow in manual mode. "
            f"Set PAYP_MCP_MODE=yolo on the server to allow, "
            f"or use this tool via the interactive payp CLI."
        )

    return None


def build_server(state: ServerState | None = None) -> tuple[Server, ServerState]:
    """Build the MCP server, registering list_tools and call_tool handlers."""
    state = state or ServerState(mode=_resolve_security_mode())
    allow_code_exec = _resolve_allow_code_exec()
    registry = build_payp_registry(
        mode=state.mode.value,
        allow_code_exec=allow_code_exec,
    )
    if allow_code_exec:
        logger.warning(
            "PAYP_MCP_ALLOW_CODE_EXEC=1 — execute_python/r/shell are exposed. "
            "These tools ALSO require PAYP_DANGEROUS_UNGATED_CODE_EXEC=1 to "
            "actually run, since they fail-closed when no skill registry is "
            "present. Set both to acknowledge the risk."
        )
    server: Server = Server("payp")

    # Precompute MCP tool descriptors for payp tools
    mcp_tool_defs: list[mcp_types.Tool] = [
        payp_tool_to_mcp(t) for t in registry.all_tools()
    ] + CUSTOM_TOOLS

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        return mcp_tool_defs

    @server.call_tool()
    async def _call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> list[mcp_types.TextContent]:
        args = arguments or {}
        logger.info("call_tool name=%s", name)

        # --- custom MCP tools ---
        if name == MCP_LIST_CONNECTIONS:
            names = list_connections()
            return info_content(
                f"Found {len(names)} saved connection(s).",
                {"connections": names},
            )

        if name == MCP_CONNECT_DATABASE:
            conn_name = args.get("name")
            if not conn_name:
                return error_content("Missing required argument: name")
            async with state.lock:
                try:
                    version = await state.connect(conn_name)
                except PaypConnectionError as e:
                    return error_content(str(e))
                except Exception as e:
                    logger.exception("connect failed")
                    return error_content(f"Unexpected error: {e}")
            mgr = state.connection_manager
            assert mgr is not None
            return info_content(
                f"Connected to '{conn_name}' ({version})",
                {
                    "name": mgr.profile.name,
                    "host": mgr.profile.host,
                    "database": mgr.profile.database,
                    "db_type": mgr.profile.db_type.value,
                    "version": version,
                },
            )

        if name == MCP_CURRENT_CONNECTION:
            mgr = state.connection_manager
            if mgr is None or not mgr.is_connected:
                return info_content("No active connection. Use mcp_connect_database first.")
            return info_content(
                f"Active connection: {mgr.profile.name}",
                {
                    "name": mgr.profile.name,
                    "host": mgr.profile.host,
                    "database": mgr.profile.database,
                    "db_type": mgr.profile.db_type.value,
                    "version": mgr.db_version,
                    "mode": state.mode.value,
                },
            )

        # --- payp tools ---
        tool = registry.get(name)
        if tool is None:
            return error_content(f"Unknown tool: {name}")

        # Tools that need a DB connection
        needs_db = name in {
            "execute_sql",
            "explain_query",
            "schema_lookup",
            "schema_search",
            "check_cascade",
            "snapshot_before_delete",
            "restore_snapshot",
            "list_snapshots",
            "delete_snapshot",
            "export_query",
        }
        if needs_db and (
            state.connection_manager is None or not state.connection_manager.is_connected
        ):
            return error_content(
                f"Tool '{name}' requires a database connection. "
                f"Call mcp_connect_database first."
            )

        # Enforce SQL safety policy
        policy_err = _enforce_tool_policy(tool, name, args, state.mode)
        if policy_err:
            return error_content(policy_err)

        context = {
            "connection_manager": state.connection_manager,
            "t0": None,
            "t1": None,
            "mode": state.mode,
        }

        try:
            async with state.lock:
                result: ToolResult = await tool.call(args, context)
        except Exception as e:
            logger.exception("tool %s raised", name)
            return error_content(f"Tool execution failed: {e}")

        return tool_result_to_mcp(result)

    return server, state


async def _maybe_autoconnect(state: ServerState) -> None:
    auto = os.environ.get("PAYP_MCP_CONNECTION", "").strip()
    if not auto:
        return
    try:
        version = await state.connect(auto)
        logger.info("auto-connected to %s (%s)", auto, version)
    except Exception as e:
        logger.warning("auto-connect to %s failed: %s", auto, e)


async def _run() -> None:
    server, state = build_server()
    await _maybe_autoconnect(state)
    logger.info(
        "payp MCP server starting (mode=%s, tools=%d)",
        state.mode.value,
        len(build_payp_registry().all_tools()) + len(CUSTOM_TOOLS),
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("shutting down")


if __name__ == "__main__":
    main()
