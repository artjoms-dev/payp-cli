# payp — Cross-Database Operations

## Overview

payp can connect to multiple databases simultaneously and perform cross-DB operations. The LLM knows which connections are active and routes queries to the right database based on context.

## Active Connections

```
$ /db prod-pg
✓ Connected to prod-pg (PostgreSQL 16.2)

$ /db staging-mysql  
✓ Connected to staging-mysql (MySQL 8.0)

$ /db
> Active connections:
>   ● prod-pg (PostgreSQL) — active ← current
>   ● staging-mysql (MySQL) — active
>   ○ oracle-legacy (Oracle) — disconnected
```

## Context Awareness

The LLM knows all active connections and their schemas. When the user asks a question, the LLM determines which database(s) to query.

### Single-DB (LLM routes automatically)
```
User: show me the latest orders

LLM knows: orders table exists in prod-pg, not in staging-mysql
→ Runs on prod-pg: SELECT * FROM orders ORDER BY created_at DESC LIMIT 10;
```

### Cross-DB Comparison
```
User: compare user counts between prod and staging

LLM generates two queries:
  @prod-pg:      SELECT COUNT(*) AS user_count FROM users;
  @staging-mysql: SELECT COUNT(*) AS user_count FROM users;

payp executes both and presents:
  ┌─ User Count Comparison ───────────────┐
  │ Connection      │ user_count          │
  │ prod-pg         │ 145,230             │
  │ staging-mysql   │ 142,871             │
  │ Difference      │ 2,359 (1.6%)        │
  └─────────────────────────────────────────┘
```

### Cross-DB Analysis
```
User: are there customers in prod that don't exist in staging?

LLM approach:
  1. @prod-pg:      SELECT id, email FROM customers;
  2. @staging-mysql: SELECT id, email FROM customers;
  3. payp compares results in-memory (Python set operations)
  4. Presents diff

payp shows:
  Found 47 customers in prod-pg not present in staging-mysql.
  │ id    │ email                │ created_at │
  │ 4501  │ new1@example.com     │ 2026-04-01 │
  │ 4502  │ new2@example.com     │ 2026-04-02 │
  │ ...                                        │
```

### Schema Comparison Across DBs
```
User: compare the orders table schema between prod and staging

LLM loads T2 for orders from both connections:

  ┌─ Schema Diff: orders ──────────────────────────────────┐
  │                                                         │
  │  Both have: id, customer_id, status, total_amount,     │
  │             created_at, updated_at                      │
  │                                                         │
  │  Only in prod-pg:                                       │
  │    + shipping_method VARCHAR(50) — added 2026-03-15     │
  │    + discount_code VARCHAR(20) — added 2026-03-20       │
  │                                                         │
  │  Only in staging-mysql:                                 │
  │    (none)                                               │
  │                                                         │
  │  Type differences:                                      │
  │    total_amount: NUMERIC(12,2) [pg] vs DECIMAL(12,2)   │
  │    [mysql] — equivalent                                 │
  └─────────────────────────────────────────────────────────┘

  Migration to sync staging:
    ALTER TABLE orders ADD COLUMN shipping_method VARCHAR(50);
    ALTER TABLE orders ADD COLUMN discount_code VARCHAR(20);
```

## Connection Prefixing

When ambiguous, user or LLM prefixes with connection name:
```
@prod-pg SELECT COUNT(*) FROM orders;
@staging-mysql SELECT COUNT(*) FROM orders;
```

LLM uses this internally. User can also use it explicitly but typically just describes what they want in natural language.

## Limitations

- No cross-DB JOINs at the SQL level (different engines)
- Cross-DB operations pull data into payp's memory — large result sets need pagination
- Cross-DB writes (sync data between DBs) require explicit user approval regardless of mode
- Data type mapping between dialects handled by sqlglot where possible
