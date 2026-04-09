---
name: schema-documenter
description: Generate markdown documentation for all tables with columns, relationships, samples, and row counts
when_to_use: User asks to document the database, generate schema docs, create a data dictionary, onboard a new team member, or save schema as markdown
allowed_tools: [schema_search, schema_lookup, check_cascade, table_stats, execute_sql, file_write]
db_types: [postgresql, mysql, oracle]
author: payp-team
version: 1.0
---

# Schema Documenter

Generates comprehensive, git-trackable markdown documentation for a database schema. Produces a single `schema.md` file containing every table's columns, keys, relationships, row counts, and sample rows — suitable for onboarding, data dictionaries, and team knowledge bases.

## When to use

Trigger this skill when the user asks to:
- "Document the database" / "generate schema docs"
- "Create a data dictionary"
- "Onboard a new team member — give them the schema"
- "Save the schema as markdown" / "export schema to a file"
- "What's in this database?" (and they want a persistent artifact, not just a chat answer)

If the user only wants a quick lookup of one table, use `schema-explorer` instead.

## Workflow

### Step 1 — Discover all tables

Call `schema_search(pattern="")` with an empty pattern to retrieve the full T1 table catalog for the active connection. Capture:
- Table names (schema-qualified where applicable)
- Table types (BASE TABLE vs VIEW — document views separately or skip)
- Total count

**If the catalog returns >50 tables**, STOP and ask the user:
> "This database has N tables. Would you like me to:
> (a) document all of them (may take a few minutes and produce a large file),
> (b) document only a specific schema (e.g. `public`, `sales`),
> (c) document only tables matching a pattern (e.g. `customer_*`),
> (d) document a specific list of tables you provide?"

Do not proceed until the user chooses.

### Step 2 — Gather per-table metadata

For each table in scope, collect the following in order:

1. **DDL & column definitions** — `schema_lookup(table=<name>)` to get columns (name, type, nullable, default, PK flag, comment), outgoing foreign keys, indexes.
2. **Row count** — `execute_sql("SELECT COUNT(*) FROM <table>")`. For very large tables (>10M rows on PostgreSQL), prefer `table_stats(table=<name>)` which uses `pg_class.reltuples` for an approximate count and is instant.
3. **Incoming FK references** — `check_cascade(table=<name>)` to find tables that reference this one, including ON DELETE/UPDATE actions.
4. **Sample rows** — `execute_sql("SELECT * FROM <table> FETCH FIRST 3 ROWS ONLY")` (PostgreSQL/Oracle) or `... LIMIT 3` (MySQL).
   - **Skip BLOB/BYTEA/CLOB/JSON/JSONB columns that are large** — replace their values with `<binary>` or `<truncated>` in the rendered sample table.
   - If the table is empty, render `_(no rows)_` instead of a sample section.
   - If sampling fails (permissions, locked table), note `_(sample unavailable)_` and continue.

### Step 3 — Render the markdown document

Assemble a single markdown string with this exact structure:

```markdown
# Database Documentation — {connection_name}

Generated: {YYYY-MM-DD} by payp schema-documenter
Database: {db_type} {version} | Schema(s): {schemas}
Total tables: {N} | Total rows: {M:,}

## Table of Contents

- [customers](#customers)
- [orders](#orders)
- [order_items](#order_items)
...

---

## customers

**Row count:** 30
**Primary key:** `id`
**Description:** Customer master table (from knowledge base, if annotated)

### Columns

| Name | Type | Nullable | Default | Notes |
|------|------|----------|---------|-------|
| id | BIGINT | NO | IDENTITY | Primary key |
| email | VARCHAR(255) | NO | — | Unique |
| name | VARCHAR(100) | YES | — | |
| created_at | TIMESTAMP | NO | NOW() | |

### Foreign Keys (outgoing)

- `region_id` → `regions.id` (ON DELETE SET NULL)

_(or "_(none)_" if the table has no outgoing FKs)_

### Referenced By

- `orders.customer_id` (ON DELETE CASCADE)
- `payments.customer_id` (ON DELETE RESTRICT)

_(or "_(none)_" if no tables reference this one)_

### Indexes

- `idx_customers_email` (UNIQUE) on `email`
- `idx_customers_created_at` on `created_at`

### Sample Rows

| id | email | name | created_at |
|----|-------|------|------------|
| 1 | alice@example.com | Alice | 2024-01-15 09:22:01 |
| 2 | bob@example.com | Bob | 2024-01-16 11:04:18 |
| 3 | carol@example.com | Carol | 2024-01-17 14:51:33 |

---

## orders
...
```

**Formatting rules:**
- Truncate column comments to 1 line (≤ 80 chars). If a comment is longer, keep the first sentence and append `…`.
- Truncate long sample cell values to 60 chars, append `…`.
- Escape pipe characters `|` inside cells as `\|`.
- Replace newlines inside sample values with a single space.
- Format row counts with thousands separators (`30`, `1,204`, `15,003,871`).
- Use `—` (em-dash) for missing defaults, not `NULL` or empty string.
- Separate each table with a horizontal rule (`---`).

### Step 4 — Ask where to save

Prompt the user:
> "Where should I save this documentation?
> (a) `./payp/knowledge/schema.md` — version-controlled, shared with team (recommended)
> (b) `./exports/schema.md` — local export, typically .gitignored
> (c) custom path"

Default to (a) if the user says "yes" or doesn't specify.

### Step 5 — Write the file

Call `file_write(path=<chosen_path>, content=<markdown_string>)`. If the directory does not exist, the tool should create it; if not, create it first via shell or the file_write tool's directory-creation option.

### Step 6 — Report to user

Output a concise summary:

```
Documented: 24 tables, 1,204,883 total rows
Saved to:   ./payp/knowledge/schema.md
File size:  87 KB

Next steps:
  • Commit this file to git so your team has access
  • Re-run this skill after schema migrations to keep docs fresh
  • Use schema-explorer for interactive single-table lookups
```

## Guidance & edge cases

### Large schemas (>50 tables)
Always ask before documenting everything. Documenting 200+ tables can take minutes and produce multi-MB markdown files that are hard to review. Prefer schema-scoped or pattern-scoped runs.

### Binary / BLOB / BYTEA / large JSON columns
Never include raw sample values. Render as `<binary>`, `<jsonb>`, or `<clob>` in the sample row table. This keeps the markdown readable and avoids committing encoded binary data into git.

### Long column comments
Truncate to a single line (≤ 80 chars). If the knowledge base has a richer description, prefer the knowledge-base version but still keep it to one line in the column table. Longer narratives belong in a separate Description paragraph under the table heading.

### Views
Document views in a separate `## Views` section at the end of the file. Include the view definition (DDL) but skip sample rows unless explicitly requested.

### Partitioned tables
For declarative partitions, document only the parent table. List child partitions as a bullet list under a "Partitions" subsection. Do not generate a full section per partition.

### Empty tables
Still include the table in the doc. Show row count `0` and render `_(no rows)_` in place of the sample rows table.

### Permission errors
If `SELECT` is denied on a table, skip the sample section with `_(sample unavailable — insufficient permissions)_` but still render columns, keys, and indexes. Never fail the whole run because of one locked table.

### Incremental updates
If a `schema.md` already exists at the target path, offer to diff against the existing file and show what changed — new tables, dropped tables, new columns. This is useful as a migration review artifact.

## Output quality checklist

Before calling `file_write`, verify:
- [ ] Table of contents links match exact table anchor names (lowercase, hyphenated)
- [ ] Every table has row count, columns, and at least one of (sample rows | empty marker | unavailable marker)
- [ ] No raw binary or encoded data in sample cells
- [ ] All pipe chars inside cells are escaped
- [ ] Horizontal rule `---` separates each table
- [ ] Header includes generation date, db type, and totals
