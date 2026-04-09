---
name: schema-explorer
description: Deep-dive exploration of a database's schema and relationships
when_to_use: User wants to understand the database structure, see all tables, find relationships between tables, or get an overview of the schema
allowed_tools: [schema_search, schema_lookup, check_cascade]
db_types: [postgresql, mysql, oracle]
author: payp-team
version: 1.0
---

## Schema Explorer Workflow

When the user wants to understand the database:

1. Use schema_search with pattern "" (empty) to get ALL tables from the catalog
2. For each important table found, use schema_lookup to get its full DDL
3. For tables with foreign keys, use check_cascade to map relationships
4. Build a structured summary:
   - Total tables, total views
   - Group tables by schema (if multiple schemas)
   - For each table: column count, primary keys, foreign keys
   - Relationship map: which tables reference which
5. Present to user as a concise overview with a text-based relationship diagram

Be thorough but concise. Do NOT dump all DDLs verbatim — summarize. Highlight:
- Core entity tables (high FK in-degree)
- Join tables (multiple FKs out, few columns)
- Orphaned tables (no FK relationships)

Stop after the summary. Let the user drill down on specific tables by asking.
