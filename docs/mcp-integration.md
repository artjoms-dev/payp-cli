# MCP Integration — payp as an MCP Server

payp ships with an MCP (Model Context Protocol) server that exposes its
database tools — SQL execution, schema introspection, snapshots, query
libraries, knowledge base, exports, charts — to any MCP-compatible client
(Claude Desktop, Cursor, Windsurf, VS Code, custom agents).

All calls go through payp's safety layers: SQL classifier, destructive-operation
blocks, and security modes.

## How to run

```bash
# Stdio MCP server — reads from stdin, writes frames to stdout, logs to stderr
python -m payp.mcp.server
```

Or once CLI integration lands:

```bash
payp mcp-serve
```

### Environment variables

| Var | Purpose | Default |
|---|---|---|
| `PAYP_MCP_CONNECTION` | Auto-connect to a saved profile on startup | _(none)_ |
| `PAYP_MCP_MODE` | Security mode: `manual` (read-only) or `yolo` (writes allowed) | `manual` |
| `PAYP_<NAME>_PASSWORD` | Password for connection `<name>` (upper, dashes → underscores) | _(reads `~/.payp/connections/<name>.cred`)_ |

**MCP has no interactive approval flow**, so security rules are enforced at
the tool boundary:

- `DROP DATABASE` / `DROP SCHEMA` / `TRUNCATE` — **always blocked**, regardless of mode.
- `manual` (default) — blocks all DML writes, DDL, and GRANT/REVOKE.
  The server is effectively read-only — safest for agents.
- `yolo` — allows DML/DDL, still hard-blocks the three statements above.

## Claude Desktop config

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "payp": {
      "command": "python",
      "args": ["-m", "payp.mcp.server"],
      "env": {
        "PAYP_MCP_CONNECTION": "local-pg",
        "PAYP_MCP_MODE": "manual"
      }
    }
  }
}
```

If you installed payp in a virtualenv, point `command` at that interpreter:

```json
{
  "mcpServers": {
    "payp": {
      "command": "/Users/you/.venvs/payp/bin/python",
      "args": ["-m", "payp.mcp.server"],
      "env": { "PAYP_MCP_CONNECTION": "local-pg" }
    }
  }
}
```

## Cursor config

Cursor uses `~/.cursor/mcp.json` (or workspace-level `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "payp": {
      "command": "python",
      "args": ["-m", "payp.mcp.server"],
      "env": { "PAYP_MCP_CONNECTION": "local-pg" }
    }
  }
}
```

## VS Code (Continue / generic MCP clients)

```json
{
  "servers": {
    "payp": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "payp.mcp.server"]
    }
  }
}
```

## Exposed tools

### MCP-specific

| Tool | Purpose |
|---|---|
| `mcp_list_connections` | List saved payp connection names |
| `mcp_connect_database` | Connect to a saved profile by name |
| `mcp_current_connection` | Show the active connection details |

### payp tools (20)

**Database:**
- `execute_sql` — run a SQL query (subject to policy)
- `explain_query` — run EXPLAIN / EXPLAIN ANALYZE
- `schema_lookup` — fetch DDL for a table/view
- `schema_search` — find tables/columns by name
- `check_cascade` — inspect FK cascades before DELETE

**Snapshots:**
- `snapshot_before_delete`, `restore_snapshot`, `list_snapshots`, `delete_snapshot`

**Export:**
- `export_query` — export query results (CSV/JSON/Parquet)

**Query library:**
- `save_query`, `list_queries`, `load_query`, `delete_query`

**Knowledge base:**
- `read_knowledge`, `write_knowledge`, `append_knowledge`, `list_knowledge`

**Analysis:**
- `chart` — render terminal charts from query results
- `payp_help` — self-documentation tool

## Pre-configuring a connection

1. Save a connection using the payp CLI:
   ```bash
   payp connect add local-pg --host localhost --port 5432 --database mydb --user myuser
   ```
2. The password is saved to `~/.payp/connections/local-pg.cred` (chmod 600),
   OR export `PAYP_LOCAL_PG_PASSWORD` in the MCP client's env.
3. Set `PAYP_MCP_CONNECTION=local-pg` in the MCP client config so the server
   auto-connects at startup.

Otherwise, instruct the agent: _"Use `mcp_connect_database` with name `local-pg`
before running any SQL."_

## Security notes

- The MCP server **never exposes API keys or credentials** via any tool.
- Credential files are loaded from disk / env vars only, at connect time.
- A single `asyncio.Lock` serializes tool execution to avoid concurrent use
  of a single `ConnectionManager`.
- All logs are written to **stderr** — stdout is reserved for MCP protocol
  frames. Do not redirect stdout.
- If an agent asks for DROP/TRUNCATE, it will always be refused with a clear
  message. No override exists over MCP.

## Troubleshooting

- **Client hangs on startup** — check stderr; the server logs `payp MCP
  server starting (...)` once initialized.
- **`Tool 'execute_sql' requires a database connection`** — call
  `mcp_connect_database` first, or set `PAYP_MCP_CONNECTION`.
- **Connection auto-connect fails silently** — check stderr for
  `auto-connect to X failed: ...`.
- **Writes blocked in manual mode** — set `PAYP_MCP_MODE=yolo` or use
  interactive payp CLI for write operations.
