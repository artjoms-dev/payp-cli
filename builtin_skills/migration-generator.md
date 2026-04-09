---
name: migration-generator
description: Generate forward + reverse migration SQL from a natural language schema change
when_to_use: User asks to create a migration, alter schema, add a column, drop a column, rename a table, change a type, or evolve database structure
allowed_tools: [schema_lookup, execute_sql, file_write]
db_types: [postgresql, mysql, oracle]
author: payp-team
version: 1.0
---

## Migration Generator Workflow

When the user asks to change the database schema:

1. **Look up current schema** for all affected tables using schema_lookup.
   Never guess column names or types — verify first.
2. **Generate the FORWARD migration SQL** using dialect-correct syntax.
   For Oracle: use VARCHAR2, NUMBER, FETCH FIRST; beware case folding.
   For PostgreSQL: use LIMIT, lowercase identifiers.
   For MySQL: use LIMIT, backticks for reserved words.
3. **Generate the REVERSE migration SQL** (the rollback):
   - ADD COLUMN → DROP COLUMN
   - DROP COLUMN → ADD COLUMN with original type + data preservation note
   - RENAME → reverse rename
   - ALTER TYPE → ALTER back to original type
4. **Show both SQL blocks side-by-side** to the user BEFORE executing.
5. **Wait for approval** (the security mode will handle the approval UI).
6. **Execute the forward migration** via execute_sql.
7. **Optionally save** the forward+reverse pair as a timestamped file in
   `./payp/migrations/` using file_write, named like `2026_04_05_add_email_to_users.sql`.

Refuse to generate destructive migrations (DROP TABLE, TRUNCATE) without
explicitly confirming data-loss implications with the user first.
