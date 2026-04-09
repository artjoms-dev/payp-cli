# payp — Auto-Secure Mode Design

## Overview

Two-model system where Model A (executor) generates SQL and Model B (reviewer) validates safety before execution. Applies only to write operations (DDL + DML). SELECTs bypass review.

## Model Configuration

### Setup Flow (`/models`)
```
$ /models
> Configured providers:
>   ✓ Claude (claude-sonnet) — API key set
>   ✓ OpenAI (gpt-4o) — API key set
>   ✗ Gemini — not configured
>   ✗ Qwen — not configured
>
> Current roles:
>   Executor: claude-sonnet
>   Reviewer: gpt-4o
>
> [Change executor] [Change reviewer] [Add provider]
```

### Model Discovery
- API keys stored in `~/.payp/models.toml`
- On `/models` or first run, payp pings each configured provider (list models endpoint)
- Only shows models user has access to
- If user has only 1 provider: reviewer uses a different model from same provider (e.g., Claude Sonnet executes, Claude Haiku reviews) or user is warned that same-provider review is weaker

### Adding a Provider
```
$ /models add gemini
> Enter Gemini API key: ********
> ✓ Gemini configured. Available models: gemini-2.5-pro, gemini-2.5-flash
> Saved to ~/.payp/models.toml
```

### models.toml Format
```toml
[claude]
api_key = "sk-ant-..."
default_model = "claude-sonnet-4-20250514"

[openai]
api_key = "sk-..."
default_model = "gpt-4o"

[gemini]
api_key = "AIza..."
default_model = "gemini-2.5-pro"

[roles]
executor = "claude-sonnet-4-20250514"
reviewer = "gpt-4o"
```

## Security Modes — Detailed Behavior

### Manual Mode
```
User: add index on orders.customer_id

Executor (Claude) generates:
  CREATE INDEX idx_orders_customer_id ON orders (customer_id);

payp shows to user:
  ┌─ Generated SQL ──────────────────────────────────┐
  │ CREATE INDEX idx_orders_customer_id               │
  │   ON orders (customer_id);                        │
  │                                                   │
  │ Reverse: DROP INDEX idx_orders_customer_id;       │
  └───────────────────────────────────────────────────┘
  [Execute] [Edit] [Cancel]
```
No reviewer involved. User decides.

### Secure Mode (manual + reviewer)
```
User: drop all tables with prefix tmp_

Executor (Claude) generates:
  DROP TABLE tmp_imports;
  DROP TABLE tmp_staging;
  DROP TABLE tmp_cache;

Reviewer (GPT-4o) checks:
  Step 1 — Safety check: "Is this SQL safe?"
    → "tmp_cache has 2.3M rows and an active FK from staging_jobs. Dropping it may cascade."

  Step 2 — Correctness check (only if safety passed or user overrides):
    → "SQL correctly targets all 3 tables matching tmp_ prefix."

payp shows to user:
  ┌─ Reviewer Assessment ────────────────────────────┐
  │ ⚠ RISK: tmp_cache has FK dependency from         │
  │   staging_jobs. CASCADE may delete related rows.  │
  │                                                   │
  │ Recommendation: Check staging_jobs dependencies   │
  │   before dropping tmp_cache.                      │
  └───────────────────────────────────────────────────┘
  [Execute anyway] [Edit] [Cancel]
```
User makes final decision.

### Secure-Auto Mode (reviewer decides)
```
User: update all prices by +10%

Executor (Claude) generates:
  UPDATE products SET price = price * 1.10;

Reviewer (GPT-4o) checks:
  Step 1 — Safety: "Unbounded UPDATE on products (45,000 rows). No WHERE clause."
  → BLOCKED

payp shows:
  ✗ Reviewer blocked execution.
  Reason: Unbounded UPDATE affects all 45,000 rows with no WHERE clause.
  
  Suggestion: Add a WHERE clause or run in batches.
  [Override and execute] [Edit] [Cancel]
```

Even in secure-auto, user can override. But the default is: reviewer blocks → user must explicitly override.

## Review Prompt Design

### Safety Check Prompt (always runs first for DDL/DML)
```
You are a database safety reviewer. Analyze this SQL for risks:

SQL: {generated_sql}
Database: {db_type} {db_version}
Target: {connection_name}
Schema context: {relevant_tables_ddl}

Check for:
1. Destructive operations (DROP, TRUNCATE, DELETE without WHERE)
2. Unbounded updates (UPDATE/DELETE without WHERE or with very broad WHERE)
3. Schema changes that could break existing applications
4. Cascade effects from FK relationships
5. Performance risks (locking large tables, missing indexes for WHERE clause)

Respond in 1-2 sentences. Start with SAFE, CAUTION, or DANGEROUS.
```

### Correctness Check Prompt (runs if safety passes)
```
The user asked: "{original_user_request}"
The executor generated: {generated_sql}
Schema context: {relevant_tables_ddl}

Does this SQL correctly fulfill the user's request? Check for:
1. Correct tables and columns referenced
2. Correct JOIN conditions
3. Correct WHERE filters matching user intent
4. Missing conditions or edge cases

Respond in 1-2 sentences. Start with CORRECT, PARTIALLY CORRECT, or INCORRECT.
```

## What Gets Reviewed vs. What Passes Through

| Operation Type | Manual Mode | Secure Mode | Secure-Auto Mode |
|---------------|-------------|-------------|-----------------|
| SELECT | Show → Execute | Execute (no review) | Execute (no review) |
| INSERT | Show → Ask user | Review → Ask user | Review → Auto-decide |
| UPDATE | Show → Ask user | Review → Ask user | Review → Auto-decide |
| DELETE | Show → Ask user | Review → Ask user | Review → Auto-decide |
| CREATE/ALTER | Show → Ask user | Review → Ask user | Review → Auto-decide |
| DROP/TRUNCATE | Show → Ask user | Review → Ask user | Review → Auto-decide |
| GRANT/REVOKE | Show → Ask user | Review → Ask user | Review → Auto-decide |

## Cost Tracking

Every reviewed operation costs 2x tokens. payp tracks:
- Executor tokens + cost
- Reviewer tokens + cost
- Total session cost
- Available via `/cost` command
