# Schema Exploration

## Commands
- `/schema` — list all tables grouped by schema (T1 catalog)
- `/schema <table>` — show full DDL (columns, types, PKs, FKs)
- `/schema <table> --deep` — add indexes, triggers, stats, FK references
- `/schema --refresh` — re-introspect and update the cache

## How payp uses schema
- **T0** (always in LLM context): schema names + table counts
- **T1** (always in LLM context): all table names by schema
- **T2** (on-demand per query): full DDL for tables the user mentions

This scales to thousands of tables without burning context.

## Natural language
Just ask: "show me the orders table structure", "what columns does customers have", "which tables reference orders".
