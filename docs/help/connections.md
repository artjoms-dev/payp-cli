# Database Connections

## Connect
- `/db` — list all connections or add a new one
- `/db <name>` — connect directly by name

## Add a new connection
Run `/db` → select "new". payp will ask for:
- Database type (PostgreSQL, MySQL, Oracle)
- Host, port, database name
- Username, password
- A friendly name (e.g., "prod-pg")

## Where credentials are stored
- Connection profiles: `~/.payp/connections/<name>.toml` (host, port, db — safe to share)
- Passwords: `~/.payp/connections/<name>.cred` (local only, chmod 600, never git)

## Supported databases
PoC: PostgreSQL only. MySQL and Oracle coming later.
