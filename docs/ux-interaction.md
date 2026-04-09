# payp — UX & Interaction Design

## Core Principle

**User speaks natural language. AI writes SQL. User approves or edits.**

The user never writes SQL from scratch. The interaction is conversational — like talking to a senior DBA who does the typing for you.

## Input Model

### Standard Flow
```
payp> show me the top 10 customers by revenue last quarter
```
User types natural language. LLM generates SQL. Results displayed.

### SQL Editing (Tab to Edit)
When LLM presents generated SQL, user can:
- **Enter** → approve and execute as-is
- **Tab** → enter edit mode, navigate/modify the SQL, then Enter to execute
- **Esc** or **q** → cancel

```
payp> add index on orders.customer_id

Generated SQL:
  CREATE INDEX idx_orders_customer_id ON orders (customer_id);
  Reverse: DROP INDEX idx_orders_customer_id;

[Enter: execute] [Tab: edit] [Esc: cancel]

# User presses Tab:
> CREATE INDEX idx_orders_customer_id ON orders (customer_id);█
# User can now navigate and edit the SQL freely
# Press Enter when done → executes the edited version
```

### No SQL Input Mode
There is no "SQL mode". If user wants to run specific SQL, they tell the LLM:
```
payp> run this: SELECT COUNT(*) FROM orders WHERE status = 1
```
The LLM recognizes it as a direct SQL request and runs it.

## Output Model

### Query Results — Auto-Format
payp auto-detects the best format based on result shape:

| Result Shape | Format |
|-------------|--------|
| 1-5 columns, ≤20 rows | Rich table |
| 6+ columns, ≤20 rows | Vertical (key: value per row) |
| Any columns, >20 rows | Rich table with first 20 rows + summary |
| Single value | Inline display |
| Empty result | "No rows returned" message |

### Result Limiting
```
payp> show me all orders

  │ id │ customer_id │ status │ total    │ created_at │
  │ 1  │ 401         │ 2      │ 150.00   │ 2026-03-01 │
  │ 2  │ 402         │ 1      │ 89.99    │ 2026-03-02 │
  │ .. │ ...         │ ...    │ ...      │ ...        │
  Showing 20 of ~12,400,000 rows.
  
  [/more] Show next 20  |  [/export csv] Export all  |  [/export json] Export as JSON
```

Default limit: 20 rows. User can:
- `/more` — next page of 20
- `/export csv` or `/export json` or `/export parquet` — full result to file
- Refine: "only show orders from last week"

### LLM Responses
LLM output rendered with rich formatting:
- SQL blocks syntax-highlighted
- Tables auto-formatted
- Explanations concise (not walls of text)
- Warnings/risks in colored panels

## CLI Commands

User types commands with `/` prefix. Everything else is natural language to LLM.

```
payp> /db                    ← command (handled by payp)
payp> show me top customers  ← natural language (sent to LLM)
payp> /export csv            ← command
payp> why is this query slow ← natural language
```

## First Run Experience

### Installation
```
$ pipx install payp
```

### First Launch
```
$ payp

  ╔══════════════════════════════════════════╗
  ║  payp — AI database assistant            ║
  ║  Your Claude Code for data engineering   ║
  ╚══════════════════════════════════════════╝

  Welcome! Let's get you set up.

  Step 1 of 2: AI Model
  ──────────────────────
  payp needs at least one AI model to work.
  
  Select a provider:
    1. Anthropic (Claude)
    2. OpenAI (GPT)
    3. Google (Gemini)
    4. Other (Ollama, Qwen, etc.)
  
  > 1
  Enter API key: sk-ant-********
  ✓ Claude configured. Model: claude-sonnet-4-20250514

  Step 2 of 2: Database Connection
  ─────────────────────────────────
  Connect to your first database.
  
  Select type:
    1. PostgreSQL
    2. MySQL
    3. Oracle
    4. Skip (configure later with /db)
  
  > 1
  Host: localhost
  Port [5432]: 
  Database: mydb
  Username: postgres
  Password: ********
  Connection name: local-pg
  
  ✓ Connected to local-pg (PostgreSQL 16.2)
  ✓ Schema loaded: 23 tables, 4 views

  You're ready! Type naturally to start working with your data.
  Type /help for available commands.

payp>
```

### Built-in Help System

payp ships with internal documentation that the LLM can read to answer user questions about payp itself.

```
payp> how do I set up auto-secure mode?

  To enable auto-secure mode:
  1. You need at least 2 AI models configured. Run /models to check.
  2. Set a reviewer model: /models → select "Change reviewer"
  3. Switch mode: /mode secure-auto
  
  In this mode, your executor model generates SQL and the reviewer 
  validates safety before execution. SELECTs are not reviewed.
```

The LLM reads from `payp/docs/help/` directory — a set of concise help files covering all features. This is NOT user documentation — it's the LLM's own reference for answering "how do I..." questions.

```
payp/
├── docs/
│   └── help/                      # LLM-readable help files
│       ├── getting-started.md
│       ├── connections.md
│       ├── models.md
│       ├── security-modes.md
│       ├── schema.md
│       ├── snapshots.md
│       ├── export.md
│       ├── commands.md
│       └── troubleshooting.md
```

## Keyboard Shortcuts

| Key | Context | Action |
|-----|---------|--------|
| Enter | Input prompt | Send message to LLM |
| Enter | SQL approval | Execute SQL |
| Tab | SQL approval | Enter edit mode |
| Esc | SQL edit / approval | Cancel |
| Ctrl+C | Anywhere | Cancel current operation |
| Ctrl+D | Empty prompt | Exit payp |
| Up/Down | Input prompt | Navigate conversation history |
