# payp — Dry-Run Mode

## Overview

Dry-run is not a separate mode — it's a capability the user can request naturally through conversation. The LLM decides when to use EXPLAIN based on context.

## How It Works

User asks the LLM to check a query before running it. The LLM uses EXPLAIN (not EXPLAIN ANALYZE) to show the execution plan without actually executing.

```
User: how would this perform? delete inactive users older than 2 years

payp (LLM) generates and runs:
  EXPLAIN DELETE FROM users WHERE active = false AND last_login < '2024-04-03';

payp shows:
  ┌─ Execution Plan ──────────────────────────────────────────┐
  │ Delete on users  (cost=0.00..1245.00 rows=2341 width=6)  │
  │   → Seq Scan on users                                     │
  │     Filter: (NOT active AND last_login < '2024-04-03')    │
  │     Rows Removed by Filter: 47659                         │
  └───────────────────────────────────────────────────────────┘

  LLM interpretation:
    Sequential scan on 50K rows to find 2,341 matches.
    No index on (active, last_login) — consider adding one.
    Estimated cost is moderate. Safe to proceed.

  Also showing 5-row sample:
  SELECT * FROM users WHERE active = false AND last_login < '2024-04-03' LIMIT 5;

  │ id  │ name       │ active │ last_login │
  │ 101 │ John Doe   │ false  │ 2023-01-15 │
  │ 102 │ Jane Smith │ false  │ 2022-11-03 │
  │ ...                                     │
  (2,341 total rows affected)

User: ok execute it
→ proceeds through normal security mode flow
```

## Key Decisions

- **EXPLAIN only** — no EXPLAIN ANALYZE (avoids actual execution/locks)
- **5-row sample** — show LIMIT 5 preview alongside the plan
- **LLM interprets** — raw EXPLAIN output + natural language explanation
- **Not a global mode** — user just asks naturally ("check this first", "dry run", "what would happen if")
