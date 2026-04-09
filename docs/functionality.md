# payp — Core Functionality Spec

## 1. Connection Management (`/db`)

### First Run Flow
```
$ payp
> Welcome to payp. No database connections configured.
> Run /db to set up your first connection.

$ /db
> Select database type:
>   1. PostgreSQL
>   2. MySQL
>   3. Oracle
> 
> Hostname: prod-db.company.com
> Port [5432]: 
> Database: analytics
> Username: artjoms
> Password: ********
> Connection name: prod-analytics
>
> ✓ Connected to prod-analytics (PostgreSQL 16.2)
> ✓ Schema map loaded: 47 tables, 12 views, 3 procedures
```

### Returning User Flow
```
$ /db
> Your connections:
>   1. prod-analytics (PostgreSQL) — last used 2h ago
>   2. staging-mysql (MySQL) — last used 3d ago  
>   3. oracle-legacy (Oracle) — last used 1w ago
>   ─────────────────
>   + New connection
>
> Select: 1
> ✓ Connected to prod-analytics
```

### Multi-DB Simultaneous
- User can connect to multiple DBs in one session
- Prefix queries with connection name or use active connection
- Example: `@prod-analytics SELECT ...` or `/switch prod-analytics`

### Credential Management
- `/credentials` — view/edit connection info for current or selected DB
- `/credentials prod-analytics` — edit specific connection

### Credential Storage (TBD — needs deeper discussion)
- Options: OS keyring (`keyring` lib), encrypted local file, env vars, vault integration
- Must support team sharing (git-safe connection profiles without secrets)
- Possible: `payp.toml` has connection profiles (host, port, db), secrets stored separately

## 2. Core Loop — Chat-Based Interaction

Chat-style interaction like Claude Code. User sends natural language or SQL, LLM responds.

### Interaction Modes
```
# Natural language → LLM generates SQL, explains, executes
> show me the top 10 customers by revenue last quarter

# Direct SQL → payp executes, shows results
> SELECT customer_name, SUM(amount) FROM orders WHERE ...

# Commands → built-in operations
> /db, /schema, /history, /explain, /diff, /rollback, /dashboard
```

### Response Types
- **Query results** — formatted tables (rich)
- **Explanations** — natural language about schema, performance, data
- **Visualizations** — charts, graphs in terminal (plotext)
- **SQL generation** — show generated SQL before execution (in manual/auto-secure mode)
- **Migrations** — DDL with reverse-SQL
- **Analytics** — aggregated views, dashboards

## 3. Capabilities — Database Swiss Knife

### Routine Operations (most common usage)
- Query execution with smart formatting
- CRUD operations via natural language
- Bulk data operations
- Data export (CSV, JSON, Parquet)

### Schema & Migration
- Schema exploration (`/schema`, `/schema users`, `/schema users.email`)
- Migration generation from natural language ("add a status column to orders")
- Schema diff between environments ("compare prod vs staging schema")
- Reverse-SQL auto-generation for every DDL change

### Performance & Analysis
- EXPLAIN plan analysis with LLM interpretation
- Index recommendations
- Query optimization suggestions
- Slow query identification

### Data Exploration & Visualization
- Statistical summaries (`/stats orders` → row count, null %, distributions)
- Terminal charts (bar, line, scatter, histogram)
- Dashboard mode (`/dashboard` → TUI with live metrics)
- Data profiling (types, cardinality, outliers)

### Knowledge Base
- Schema map (auto-introspected, cached, refreshable)
- Saved queries library
- Team-shared conventions and rules
- Business context annotations ("orders.status: 1=pending, 2=shipped, 3=delivered")

## 4. Security Modes

### Manual Mode (default)
Every SQL statement shown to user before execution. Must approve.
```
> add index on orders.customer_id

payp generated:
  CREATE INDEX idx_orders_customer_id ON orders (customer_id);

Reverse SQL:
  DROP INDEX idx_orders_customer_id;

[Execute] [Edit] [Cancel]
```

### YOLO Mode
Auto-execute everything. For trusted environments (local dev, sandboxes).
```
> /mode yolo
⚠ YOLO mode enabled. All queries will execute without confirmation.
```

### Auto-Secure Mode
Model A generates SQL. Model B (different provider) reviews and approves/rejects.
```
> drop all tables with prefix tmp_

Model A (Claude) generated:
  DROP TABLE tmp_imports;
  DROP TABLE tmp_staging;
  DROP TABLE tmp_cache;

Model B (GPT-4) review:
  ✓ Approved. 3 tables matched prefix tmp_. No production tables affected.
  ⚠ Note: tmp_cache has 2.3M rows. Consider backup first?

[Execute] [Execute with backup] [Cancel]
```

## 5. Team Features

### Shared Knowledge Base
- Connection profiles (without secrets) in git repo
- Schema annotations shared across team
- Saved queries library
- Convention rules (`payp.toml` or `payp.md`)

### Git Integration
- `payp.toml` + `knowledge/` directory tracked in git
- Secrets NEVER in git — stored in OS keyring or vault
- Team members clone repo, add their own credentials locally

### Transaction Log
- Every operation logged locally (like git reflog)
- Shareable session exports for code review
- Audit trail for production operations

## 6. Command Reference (Draft)

| Command | Purpose |
|---------|---------|
| `/db` | Manage connections |
| `/credentials` | Edit connection credentials |
| `/switch <name>` | Switch active connection |
| `/schema [table]` | Explore schema |
| `/stats [table]` | Statistical summary |
| `/explain` | EXPLAIN last/given query |
| `/history` | Transaction log |
| `/rollback` | Show reverse-SQL for last operation |
| `/diff` | Compare schemas, migrations |
| `/dashboard` | Open TUI dashboard |
| `/mode <manual\|yolo\|secure>` | Set security mode |
| `/model <name>` | Switch AI model |
| `/export <format>` | Export last result |
| `/save <name>` | Save query to library |
| `/cost` | Show token usage & costs |
| `/knowledge` | Manage knowledge base |
