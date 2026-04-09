# payp — Storage & File Structure

## Two-Level Configuration

payp uses two config locations: global (user-level) and project-level.

### Global Config: `~/.payp/`

User's personal payp installation. Never shared, never in git.

```
~/.payp/
├── config.toml                    # Global settings
│   ├── default_model              # e.g., "claude-sonnet-4-20250514"
│   ├── default_mode               # manual | yolo | secure
│   ├── theme                      # color scheme
│   └── editor                     # external editor preference
│
├── connections/                    # Connection profiles + credentials
│   ├── prod-analytics.toml        # Connection info (host, port, db, user)
│   ├── prod-analytics.cred        # Secret only (password or token)
│   ├── staging-mysql.toml
│   ├── staging-mysql.cred
│   ├── oracle-legacy.toml
│   └── oracle-legacy.cred
│
├── sessions/                       # Conversation history
│   ├── 2026-04-03_prod-analytics_a3f2.jsonl
│   ├── 2026-04-03_staging-mysql_b1c4.jsonl
│   └── ...
│
├── cache/                          # Schema cache, model response cache
│   ├── prod-analytics_schema.json  # Cached schema introspection
│   └── ...
│
└── models.toml                     # Model configurations & API keys
    ├── [claude]
    ├── [openai]
    ├── [gemini]
    └── [qwen]
```

### Project Config: `./payp/`

Shared with team via git. Contains project-specific settings, knowledge, and shared resources.

```
project-repo/
├── payp/                           # Git-tracked project config
│   ├── payp.toml                   # Project settings & conventions
│   ├── connections/                # Shared connection profiles (NO secrets)
│   │   ├── prod-analytics.toml    # host, port, db — no password
│   │   └── staging-mysql.toml
│   ├── knowledge/                  # Schema annotations, business context
│   │   ├── schema-notes.md        # "orders.status: 1=pending, 2=shipped"
│   │   ├── conventions.md         # "all tables use snake_case, UUIDs as PK"
│   │   └── glossary.md            # Business term definitions
│   ├── queries/                    # Saved query library
│   │   ├── revenue-by-region.sql
│   │   └── daily-active-users.sql
│   └── log/                        # Transaction log
│       └── transactions.db         # SQLite: all DB operations history
│
└── .gitignore                      # Must include: *.cred, *.secret
```

## File Formats

### Connection Profile (`.toml`)
```toml
# ~/.payp/connections/prod-analytics.toml
# OR ./payp/connections/prod-analytics.toml (shared, no password)

[connection]
name = "prod-analytics"
type = "postgresql"         # postgresql | mysql | oracle
host = "prod-db.company.com"
port = 5432
database = "analytics"
username = "artjoms"
ssl = true

[options]
schema = "public"           # default schema
timeout = 30                # connection timeout seconds
pool_size = 3               # connection pool
```

### Credential File (`.cred`)
```toml
# ~/.payp/connections/prod-analytics.cred
# NEVER committed to git. Local only.

password = "secret123"
# OR
token = "Bearer eyJ..."
# OR
key_file = "/path/to/client-key.pem"
```

### Connection Resolution Order
When user runs `/db prod-analytics`:
1. Check `./payp/connections/prod-analytics.toml` (project-level profile)
2. Fall back to `~/.payp/connections/prod-analytics.toml` (global profile)
3. Credentials always from `~/.payp/connections/prod-analytics.cred` (never in project)
4. Env var override: `PAYP_PROD_ANALYTICS_PASSWORD` takes precedence

### Session File (`.jsonl`)
```jsonl
{"ts":"2026-04-03T10:15:00Z","role":"user","content":"show me top customers"}
{"ts":"2026-04-03T10:15:02Z","role":"assistant","content":"...","sql":"SELECT ...","model":"claude-sonnet"}
{"ts":"2026-04-03T10:15:03Z","role":"system","event":"query_executed","rows":10,"ms":45,"connection":"prod-analytics"}
{"ts":"2026-04-03T10:16:00Z","role":"user","content":"add index on orders.customer_id"}
{"ts":"2026-04-03T10:16:02Z","role":"assistant","sql":"CREATE INDEX ...","reverse_sql":"DROP INDEX ...","mode":"manual","approved":true}
```

### Transaction Log (SQLite schema)
```sql
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,           -- links to session file
    timestamp TEXT NOT NULL,            -- ISO 8601
    connection_name TEXT NOT NULL,      -- which DB
    operation_type TEXT NOT NULL,       -- query | ddl | dml | migration
    sql_executed TEXT NOT NULL,         -- what was run
    reverse_sql TEXT,                   -- rollback statement (NULL for SELECTs)
    execution_mode TEXT NOT NULL,       -- manual | yolo | secure
    approved_by TEXT,                   -- user | model_name (for secure mode)
    status TEXT NOT NULL,               -- success | failed | rolled_back
    error_message TEXT,                 -- if failed
    rows_affected INTEGER,
    execution_ms INTEGER,
    model_used TEXT,                    -- which LLM generated the SQL
    user_id TEXT                        -- for team audit trail
);

CREATE INDEX idx_transactions_session ON transactions(session_id);
CREATE INDEX idx_transactions_connection ON transactions(connection_name);
CREATE INDEX idx_transactions_timestamp ON transactions(timestamp);
```

## Resume Flow

```
$ /resume
> Recent sessions:
>   1. [2h ago] prod-analytics — 23 queries, "customer analysis"
>   2. [1d ago] staging-mysql — 8 queries, "migration testing"
>   3. [3d ago] oracle-legacy — 5 queries, "data export"
>
> Select: 1
> ✓ Resuming session. Loading context...
> ✓ Connected to prod-analytics
> ✓ 23 previous messages loaded
```

## Security Rules

- `.cred` files: `chmod 600`, owned by current user only
- `.cred` files: NEVER in git (enforced by payp-generated .gitignore)
- API keys in `~/.payp/models.toml`: also `chmod 600`
- Transaction log may contain sensitive SQL — team decides if `log/` is git-tracked
- Session files contain full conversation — stored in `~/.payp/sessions/` (local only by default)
