# payp — Master Implementation Checklist

> Auto-generated from 16 design docs by Opus architect agents.
> Deduplicated and organized by implementation phase.
> Source doc referenced in `[brackets]` for each task.

---

## Phase 1 — Skeleton & Config ✅ DONE

- [x] `pyproject.toml` with all dependencies `[stack.md]`
- [x] Project directory structure (src/payp/, tests/, docs/, builtin_skills/) `[skills-architecture.md]`
- [x] `__init__.py`, `__main__.py` entry point `[skills-architecture.md]`
- [x] `.gitignore` with *.cred, *.secret exclusions `[storage.md]`
- [x] `CLAUDE.md` project instructions `[storage.md]`
- [x] Pydantic data models: ConnectionProfile, ConnectionCredential, ModelProvider, SecurityMode, etc. `[storage.md]`
- [x] `config.py` — load/save config.toml, connections, credentials, models `[storage.md]`
- [x] Connection .toml format parser (name, type, host, port, db, username, ssl) `[storage.md]`
- [x] .cred format parser (password, token, key_file) `[storage.md]`
- [x] Connection resolution order: project-level → global → env var override `[storage.md]`
- [x] `chmod 600` on .cred files and models.toml `[storage.md]`
- [x] `cli.py` — typer app with slash command routing `[functionality.md]`
- [x] `/db` command — list connections, add new, select `[functionality.md]`
- [x] `/models` command — list providers, add new `[auto-secure.md]`
- [x] `/mode` command — switch security modes `[functionality.md]`
- [x] `/help` command — show command reference `[ux-interaction.md]`
- [x] `/cost`, `/export`, `/schema` command stubs `[functionality.md]`
- [x] Interactive chat loop with prompt_toolkit + history `[ux-interaction.md]`
- [x] Slash command routing (/ prefix → command, else → LLM) `[ux-interaction.md]`
- [x] `docker-compose.yml` with postgres:16 `[stack.md]`
- [x] `tests/seed.sql` with sample schema (customers, orders, products, order_items, payments) `[stack.md]`
- [x] Skills placeholder: `skills/loader.py` stub, `builtin_skills/README.md` `[skills-architecture.md]`

---

## Phase 2 — Database Connection

**Connection Management**
- [x] Async psycopg3 connection manager (`db/connection.py`) `[stack.md]`
- [x] Test connection on setup and display server version `[functionality.md]`
- [x] Connection drop auto-reconnect (3 attempts, then inform user) `[connection-check.md]`
- [x] `/db <name>` direct connect shorthand `[cross-db.md]`

**Initial Discovery (first connect)**
- [x] Run initial discovery automatically on first connect `[connection-check.md]`
- [x] Query PostgreSQL DB version via `SELECT version()` `[connection-check.md]`
- [x] Query schema list and table counts via `information_schema.tables` GROUP BY `[connection-check.md]`
- [x] Query total tables and views count `[connection-check.md]`
- [x] Query database size via `pg_database_size()` `[connection-check.md]`
- [x] Query server uptime via `pg_postmaster_start_time()` `[connection-check.md]`
- [x] Query current user and role `[connection-check.md]`
- [x] Display discovery progress with checkmarks (schemas, tables, uptime, size, cache) `[connection-check.md]`
- [x] Ensure discovery does NOT perform column-level introspection, index details, row counts, or data sampling `[connection-check.md]`

**Schema Introspection — T0 + T1**
- [x] Build T0 introspection: schema names + table counts `[schema-context.md]`
- [x] Build T1 introspection: all table names grouped by schema `[schema-context.md]`
- [x] Format T0 as compact text (db name, version, schema list with counts, totals) `[schema-context.md]`
- [x] Format T1 as comma-separated table names under each schema header `[schema-context.md]`

**Schema Cache**
- [x] Create `~/.payp/cache/` directory structure `[schema-context.md]`
- [x] Save T0 to `{connection}_t0.json` `[schema-context.md]`
- [x] Save T1 to `{connection}_t1.json` `[schema-context.md]`
- [x] Save metadata (version, size, user, timestamp) to `{connection}_meta.json` `[connection-check.md]`

**Reconnect Freshness Check**
- [x] On reconnect, run quick table-count check vs cached T0 `[connection-check.md]`
- [x] Display "Schema cache up to date" when counts match `[connection-check.md]`
- [x] Display schema-changed warning with old/new count, suggest `/schema --refresh` `[connection-check.md]`

**Verify:** `payp` → `/db` → connect to Docker PostgreSQL → see discovery summary + schema cache saved

---

## Phase 3 — LLM Integration

**litellm Wrapper**
- [x] `core/llm.py` — litellm wrapper with async streaming `[stack.md]`
- [x] OpenRouter support (`openrouter/provider/model` prefix) `[auto-secure.md]`
- [x] Direct provider support (Anthropic, OpenAI, Gemini) `[auto-secure.md]`
- [x] Ollama local model support `[stack.md]`
- [x] Native function/tool calling via litellm `[skills-architecture.md]`
- [x] Token counting for context window management `[stack.md]`

**Streaming Display**
- [x] `ui/streaming.py` — rich.live display of streaming tokens `[stack.md]`
- [x] Token-by-token output rendering `[cherry-picks.md]`

**Model Management**
- [x] models.toml with per-provider sections + [roles] `[auto-secure.md]`
- [x] `/models` shows configured providers with ✓/✗ status `[auto-secure.md]`
- [x] `/models` shows current executor and reviewer assignments `[auto-secure.md]`
- [x] `/models add <provider>` wizard (OpenRouter, Anthropic, OpenAI, Gemini, Ollama) `[auto-secure.md]`
- [ ] Change executor / Change reviewer actions `[auto-secure.md]`
- [ ] Ping provider list-models endpoint to discover available models `[auto-secure.md]`
- [ ] Only show models user has access to `[auto-secure.md]`

**Cost Tracking**
- [x] Track input/output tokens per operation `[auto-secure.md]`
- [x] Track cost per operation (executor + reviewer separately) `[auto-secure.md]`
- [x] Accumulate session totals `[auto-secure.md]`
- [x] `/cost` command displays breakdown `[auto-secure.md]`

**Tool Definitions**
- [x] JSON schemas for all tools (query, explain, schema_lookup, schema_search, export, file_read, file_write) `[skills-architecture.md]`

**Verify:** `payp` → connect → ask "what tables do you see?" → LLM responds with schema context

---

## Phase 4 — Chat Loop & Tools

**Chat Loop**
- [x] `core/chat.py` — main async chat loop `[functionality.md]`
- [x] Send user input + system prompt + tools to LLM `[system-prompt.md]`
- [x] Process LLM tool call requests, execute tools, return results `[skills-architecture.md]`
- [x] Support multi-step tool call chains (e.g., schema_lookup → query) `[skills-architecture.md]`
- [x] LLM formats and presents final results to user `[skills-architecture.md]`

**System Prompt Builder**
- [x] `prompts/system.py` — dynamic section assembler (sections 1-8) `[system-prompt.md]`
- [x] Section 1: Identity & role (data-specialized AI — DE, architect, analyst, DBA, monitoring, security) `[system-prompt.md]`
- [x] Section 2: Capabilities & constraints (act immediately, dialect awareness, never expose creds) `[system-prompt.md]`
- [x] Section 3: Security mode injection (dynamic, based on current /mode) `[system-prompt.md]`
- [x] Section 4: Connection context (dynamic, injected on /db connect) `[system-prompt.md]`
- [x] Section 5: Schema context (T0+T1 always, T2 per query) `[system-prompt.md]`
- [x] Section 6: Knowledge base (from ./payp/knowledge/, if exists) `[system-prompt.md]`
- [x] Section 7: Tool descriptions (dynamic, based on available tools) `[system-prompt.md]`
- [x] Section 8: Help reference (pointer to help files) `[system-prompt.md]`
- [x] Handle missing connection (section 4 omitted) `[system-prompt.md]`
- [x] Handle empty knowledge dir (section 6 omitted) `[system-prompt.md]`

**Context Window Management**
- [x] `core/context.py` — track context usage `[cherry-picks.md]`
- [x] T2 injection per query (load DDL for conversation-relevant tables) `[schema-context.md]`
- [x] Table matching: Step 1 — exact name match from T1 catalog `[schema-context.md]`
- [x] Table matching: Step 2 — FK traversal (1 hop) `[schema-context.md]`
- [x] Table matching: Step 3 — fuzzy name match `[schema-context.md]`
- [x] Table matching: Step 4 — LLM-assisted (send T1 + query, ask which tables) `[schema-context.md]`
- [x] Context budget: max ~30KB T2 injection per query `[schema-context.md]`
- [ ] Auto-summarize older messages when context fills (like Claude Code) `[cherry-picks.md]`
- [ ] Warn when switching to smaller-context model, offer compact `[ux-interaction.md]`

**Tool Implementations**
- [x] `tools/base.py` — Tool base class (name, description, input_schema, is_read_only, is_destructive, async call) `[skills-architecture.md]`
- [x] `tools/query.py` — execute SQL, return rows/columns/count/ms `[skills-architecture.md]`
- [x] `tools/explain.py` — run EXPLAIN (not ANALYZE), return plan `[dry-run.md]`
- [x] `tools/schema.py` — schema_lookup (T2 DDL loader) + schema_search (T1 catalog search) `[skills-architecture.md]`
- [x] `tools/filesystem.py` — file_read + file_write `[skills-architecture.md]`
- [x] ToolResult return type `[skills-architecture.md]`

**Transparent Workflow Display**
- [x] Show LLM thinking/reasoning as it streams `[cherry-picks.md]`
- [x] Show tool call execution (which tool, params, result summary) `[cherry-picks.md]`
- [x] Show error auto-recovery transparently ("Column 'reigon' not found. Retrying with 'region'...") `[system-prompt.md]`
- [x] 2 silent retries on obvious errors, then ask user `[system-prompt.md]`

**Result Display**
- [x] `ui/display.py` — rich tables for query results `[ux-interaction.md]`
- [x] Auto-format: 1-5 cols ≤20 rows → table `[ux-interaction.md]`
- [x] Auto-format: 6+ cols ≤20 rows → vertical (key:value) `[ux-interaction.md]`
- [x] Auto-format: >20 rows → table with first 20 + summary `[ux-interaction.md]`
- [x] Auto-format: single value → inline `[ux-interaction.md]`
- [x] Auto-format: empty → "No rows returned" `[ux-interaction.md]`
- [x] Default limit 20 rows, show "Showing 20 of ~X rows" `[ux-interaction.md]`
- [x] SQL syntax highlighting in output `[ux-interaction.md]`
- [x] Warnings/risks in colored panels `[ux-interaction.md]`

**Dry-Run (conversational)**
- [x] LLM detects dry-run intent ("check this first", "what would happen if") `[dry-run.md]`
- [x] Generate EXPLAIN (not ANALYZE) for the query `[dry-run.md]`
- [x] Display execution plan in formatted panel `[dry-run.md]`
- [x] LLM interprets plan (scan type, row estimates, missing indexes, cost assessment) `[dry-run.md]`
- [x] Show 5-row sample with `LIMIT 5` alongside plan `[dry-run.md]`
- [x] Show total affected row count `[dry-run.md]`
- [x] After showing plan, wait for user "ok execute" to proceed through normal security flow `[dry-run.md]`

**Verify:** Full conversation: connect → "show me top 10 rows from customers" → formatted results

---

## Phase 5 — Security Modes

**SQL Classification**
- [x] Detect SQL type: SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, TRUNCATE, GRANT, REVOKE `[auto-secure.md]`
- [x] SELECTs bypass all review in all modes `[auto-secure.md]`
- [x] DDL + DML route through security flow `[auto-secure.md]`

**Approval UI**
- [x] `ui/approval.py` — SQL approval flow `[ux-interaction.md]`
- [x] Display generated SQL in bordered panel `[auto-secure.md]`
- [x] Display reverse-SQL alongside DDL `[auto-secure.md]`
- [x] Enter → execute as-is `[ux-interaction.md]`
- [x] Tab → enter edit mode (cursor at end, free navigation) `[ux-interaction.md]`
- [x] Enter in edit mode → execute edited SQL `[ux-interaction.md]`
- [x] Esc → cancel `[ux-interaction.md]`

**Manual Mode**
- [x] Show SQL + reverse-SQL + estimated impact → wait for approval `[auto-secure.md]`
- [x] User decides (Execute / Edit / Cancel) `[auto-secure.md]`

**Secure Mode**
- [x] Send SQL to reviewer model for safety check `[auto-secure.md]`
- [x] Safety check prompt: checks destructive ops, unbounded updates, cascade effects, performance risks `[auto-secure.md]`
- [x] Parse verdict: APPROVE / SAFER / HARD_BLOCK (adapted from original) `[auto-secure.md]`
- [x] Display reviewer assessment with reason `[auto-secure.md]`
- [x] User makes final decision (Execute / Edit / Cancel) `[auto-secure.md]`

**Secure-Auto Mode**
- [x] Reviewer auto-decides: APPROVE → auto-execute `[auto-secure.md]`
- [x] SAFER → use safer SQL automatically (no user prompt) `[auto-secure.md]`
- [x] HARD_BLOCK → user must type OVERRIDE `[auto-secure.md]`

**Review Prompts**
- [x] `core/reviewer.py` — review prompt template `[auto-secure.md]`
- [x] Safer SQL validation (reject templates with placeholders) `[auto-secure.md]`
- [x] Responses limited to 1-2 sentences `[auto-secure.md]`

**Edge Cases**
- [x] Handle reviewer API failure (timeout, rate limit) — fall back to SAFER with caution `[auto-secure.md]`
- [x] Handle unparseable reviewer response `[auto-secure.md]`
- [x] Placeholder/template SAFER SQL rejected `[auto-secure.md]`
- [ ] Handle single-provider setup: different model from same provider as reviewer, warn weaker `[auto-secure.md]`
- [ ] Handle multi-statement SQL `[auto-secure.md]`

**Verify:** Manual mode blocks DDL until approved; secure-auto has Model B review; Tab-to-edit works

---

## Phase 6 — Persistence

**Session Saving**
- [x] `storage/sessions.py` — save conversation as JSONL `[storage.md]`
- [x] JSONL format: ts, role, content, sql, model, reverse_sql, mode, approved `[storage.md]`
- [x] System events: query_executed with rows, ms, connection `[storage.md]`
- [x] File naming: `{date}_{connection}_{4hex}.jsonl` `[storage.md]`
- [x] Store in `~/.payp/sessions/` `[storage.md]`

**Transaction Log**
- [x] `storage/transaction_log.py` — SQLite log `[storage.md]`
- [x] Create `./payp/log/transactions.db` `[storage.md]`
- [x] Schema: id, session_id, timestamp, connection_name, operation_type, sql_executed, reverse_sql, execution_mode, approved_by, status, error_message, rows_affected, execution_ms, model_used, user_id `[storage.md]`
- [x] Indexes on session_id, connection_name, timestamp `[storage.md]`
- [x] Log every operation (including SELECTs) `[storage.md]`

**Export**
- [x] `tools/export.py` — CSV export of query results `[export.md]`
- [x] Default export to `./exports/` directory `[export.md]`
- [x] Auto-create exports directory `[export.md]`
- [x] `/export csv` command `[export.md]`
- [x] LLM-driven export from natural language `[export.md]`
- [x] Descriptive filenames or fallback `query_result_{date}_{time}.csv` `[export.md]`
- [x] For large exports (>100K rows): streaming cursor → file writer, never full dataset in memory `[export.md]`
- [ ] Progress bar for large exports `[export.md]`
- [x] Warn before overwriting existing files `[export.md]`

**Verify:** Session JSONL written, transaction log queryable, CSV export works

---

## Phase 7 — Polish

**First-Run Onboarding**
- [x] Detect first run (no config) → launch wizard `[ux-interaction.md]`
- [x] Branded welcome banner `[ux-interaction.md]`
- [x] Step 1: AI Model selection (OpenRouter recommended, Anthropic, OpenAI, Gemini, Ollama) `[ux-interaction.md]`
- [x] Step 2: Database connection (PostgreSQL, MySQL, Oracle, Skip) `[ux-interaction.md]`
- [x] Post-setup ready message with /help hint `[ux-interaction.md]`

**Help System**
- [x] Create `docs/help/` directory with LLM-readable help files `[ux-interaction.md]`
- [x] Help files: getting-started, connections, models, security-modes, schema, snapshots, export, commands, troubleshooting `[ux-interaction.md]`
- [x] LLM reads help files when user asks "how do I..." about payp `[ux-interaction.md]`

**Schema Commands**
- [x] `/schema` — list full schema (T1) `[functionality.md]`
- [x] `/schema <table>` — show table details (T2) `[functionality.md]`
- [x] `/schema <table> --deep` — show indexes, constraints, triggers, stats (T3) `[schema-context.md]`
- [x] `/schema --refresh` — re-run introspection, update cache `[schema-context.md]`

**Connection Resilience**
- [ ] Auto-reconnect on connection drop (3 attempts) `[connection-check.md]`
- [ ] Display notice on auto-reconnect `[connection-check.md]`
- [ ] Inform user and suggest /db if reconnect fails `[system-prompt.md]`

**Keyboard Shortcuts**
- [x] Ctrl+C — cancel current operation `[ux-interaction.md]`
- [x] Ctrl+D on empty prompt — exit payp `[ux-interaction.md]`
- [x] Up/Down — navigate conversation history `[ux-interaction.md]`

**Verify:** Fresh install → onboarding → productive session end-to-end

---

## Future — Post-PoC

### MySQL & Oracle Support
- [ ] MySQL connection via `mysql-connector-python` `[stack.md]`
- [ ] Oracle connection via `oracledb` thin mode `[stack.md]`
- [ ] MySQL introspection queries (version, schemas, tables, size, uptime, user) `[connection-check.md]`
- [ ] Oracle introspection queries (version, schemas, tables, size, uptime, user) `[connection-check.md]`
- [ ] MySQL T0/T1/T2/T3 introspection `[schema-context.md]`
- [ ] Oracle T0/T1/T2/T3 introspection `[schema-context.md]`
- [ ] sqlglot dialect transpilation (PostgreSQL ↔ MySQL ↔ Oracle) `[stack.md]`

### Cross-DB Operations
- [ ] Multiple simultaneous connections `[cross-db.md]`
- [ ] `/db` lists active connections with ● active / ○ disconnected `[cross-db.md]`
- [ ] LLM routes queries to correct DB based on schema awareness `[cross-db.md]`
- [ ] `@connection-name` prefix syntax `[cross-db.md]`
- [ ] Cross-DB value comparison (run queries on both, show side-by-side) `[cross-db.md]`
- [ ] Cross-DB data diff (find records in one not in other) `[cross-db.md]`
- [ ] Cross-DB schema comparison (columns, types, missing) `[cross-db.md]`
- [ ] Generate migration SQL to sync schemas `[cross-db.md]`
- [ ] Require explicit approval for cross-DB writes regardless of mode `[cross-db.md]`

### DML Snapshots & Reverse-SQL
- [ ] DDL reverse-SQL generation (ALTER ADD → ALTER DROP, etc.) `[snapshots.md]`
- [ ] Before UPDATE/DELETE: count check with same WHERE clause `[snapshots.md]`
- [ ] Size limits: max_rows (10K), max_size_mb (50), warn_rows (1K) `[snapshots.md]`
- [ ] Over limit: 4 options (no snapshot, export first, batch 10K, cancel) `[snapshots.md]`
- [ ] Snapshot capture: SELECT * with same WHERE → JSONL file `[snapshots.md]`
- [ ] JSONL format: _payp_meta header + row data `[snapshots.md]`
- [ ] Storage: `./payp/snapshots/{date}_{table}_{op}_{hash}.jsonl` `[snapshots.md]`
- [ ] manifest.json index `[snapshots.md]`
- [ ] UPDATE reverse: UPDATE SET original_values WHERE id IN (...) `[snapshots.md]`
- [ ] DELETE reverse: INSERT INTO with full row data `[snapshots.md]`
- [ ] `/rollback` command: list recent ops with snapshots, select, show reverse SQL `[snapshots.md]`
- [ ] Concurrent change detection before rollback `[snapshots.md]`
- [ ] Auto-increment / trigger warnings on rollback INSERT `[snapshots.md]`
- [ ] Skip binary columns option `[snapshots.md]`
- [ ] Retention: auto-delete snapshots older than retention_days (default 30) `[snapshots.md]`

### Data Visualization
- [ ] plotext bar charts, line charts, scatter plots, histograms, heatmaps `[stack.md]`
- [ ] asciichartpy for quick trends `[stack.md]`
- [ ] sparklines for inline table indicators `[stack.md]`
- [ ] `/dashboard` TUI mode with textual `[functionality.md]`
- [ ] `/stats <table>` — row count, null %, distributions `[functionality.md]`
- [ ] Data profiling: types, cardinality, outliers `[functionality.md]`

### Export Formats
- [ ] JSON export `[export.md]`
- [ ] Parquet export via pyarrow `[export.md]`
- [ ] Excel export via openpyxl `[export.md]`
- [ ] `/more` command for pagination `[ux-interaction.md]`

### Query Library
- [ ] `./payp/queries/` directory with .sql files `[query-library.md]`
- [ ] Comment header: `-- payp:tags:` and `-- payp:desc:` `[query-library.md]`
- [ ] `/queries` command — list, search, filter by tags `[query-library.md]`
- [ ] `/save <name>` — save last query with tags `[query-library.md]`
- [ ] LLM matches user questions against saved query tags `[query-library.md]`
- [ ] Run saved queries by name `[query-library.md]`

### Knowledge Base
- [ ] `./payp/knowledge/schema-notes.md` — column semantics, enum meanings `[schema-context.md]`
- [ ] `./payp/knowledge/conventions.md` — naming, PK strategy, patterns `[schema-context.md]`
- [ ] `./payp/knowledge/glossary.md` — business terms `[storage.md]`
- [ ] Load into LLM context alongside T2 schemas `[schema-context.md]`

### Pinned Tables
- [ ] `/pin <tables>` — always include in T2 context `[schema-context.md]`
- [ ] `/unpin <table>` — remove from pinned `[schema-context.md]`
- [ ] Store in `./payp/payp.toml` under [schema].pinned_tables `[schema-context.md]`

### Skills & Plugins
- [ ] Skill file format: markdown + YAML frontmatter (name, description, when_to_use, allowed_tools, db_types) `[skills-architecture.md]`
- [ ] Skill discovery: scan builtin_skills/, ~/.payp/skills/, ./payp/skills/ `[skills-architecture.md]`
- [ ] Skill registry `[skills-architecture.md]`
- [ ] Make skills available in system prompt `[skills-architecture.md]`
- [ ] MCP server integration for DB-native MCP servers `[cherry-picks.md]`

### Team Features
- [ ] Shared connection profiles in git (no secrets) `[functionality.md]`
- [ ] Shared knowledge base `[functionality.md]`
- [ ] Shared saved queries `[functionality.md]`
- [ ] Convention rules in payp.toml `[functionality.md]`
- [ ] Shareable session exports for review `[functionality.md]`
- [ ] Audit trail for production operations `[functionality.md]`

### Session Resume
- [ ] `/resume` — list recent sessions, select, load context, reconnect `[storage.md]`

### Advanced Features
- [ ] Schema diff over time (track evolution across sessions) `[cherry-picks.md]`
- [ ] Hook system (pre/post execution hooks) `[cherry-picks.md]`
- [ ] SQL edit format pattern (migration, diff, full DDL) `[cherry-picks.md]`
- [ ] Index recommendations based on query patterns `[functionality.md]`
- [ ] Slow query identification `[functionality.md]`
- [ ] `/history` command — view transaction log `[functionality.md]`
- [ ] `/diff` command — compare schemas/migrations `[functionality.md]`
- [ ] `/credentials` command — view/edit credentials `[functionality.md]`
- [ ] `/switch <name>` command — change active connection `[functionality.md]`
- [ ] Stored procedures/functions introspection `[schema-context.md]`
- [ ] Secure credential storage via OS keyring `[functionality.md]`
