# Cross-DB Operations — Design Notes

## State Changes

### Before (single-connection)
```python
class ChatSession:
    conn: ConnectionManager | None  # single active connection
    t0: SchemaIndex | None
    t1: SchemaCatalog | None
```

### After (multi-connection)
```python
class ChatSession:
    connections: dict[str, ConnectionManager]  # name -> manager
    active: str | None                          # current default connection
    schemas: dict[str, tuple[SchemaIndex, SchemaCatalog]]  # per-connection schemas
```

## @name Prefix Syntax

User / LLM can prefix queries to target a specific connection:

```
User: show me customer counts @prod and @staging
LLM: @prod SELECT COUNT(*) FROM customers
     @staging SELECT COUNT(*) FROM customers
```

payp parses the prefix, routes the query to the named connection.

Tool interface:
```python
execute_sql(sql="SELECT ...", connection="prod")  # explicit
execute_sql(sql="SELECT ...")                     # uses active connection
```

## New Tools

### compare_schemas
```python
compare_schemas(table="orders", connection_a="prod", connection_b="staging")
→ returns structured diff:
  - columns_only_in_a: [(name, type), ...]
  - columns_only_in_b: [(name, type), ...]
  - type_differences: [(col, type_a, type_b), ...]
  - migration_sql: "ALTER TABLE orders ADD COLUMN ..."
```

### compare_data
```python
compare_data(sql_a, connection_a, sql_b, connection_b, key_column)
→ executes both, diffs by key:
  - in_a_not_b: [rows...]
  - in_b_not_a: [rows...]
  - differences: [(key, diff_cols)]
```

### list_active_connections
```python
list_active_connections() → list of {name, db_type, version, is_active}
```

### switch_connection
```python
switch_connection(name="staging") → sets active connection
```

## CLI Commands

- `/db <name>` — if already connected, marks as active; if not, connects and adds
- `/db` — shows list with ● active / ○ disconnected indicators
- `/switch <name>` — change active connection (same as /db <name>)
- `/disconnect <name>` — close one connection
- `/disconnect all` — close all

## System Prompt Updates

New section in connection context:
```
## Active Connections
Current: prod (PostgreSQL 16.2)  ← default for unprefixed queries
Also active: staging (MySQL 8.0), oracle-legacy (Oracle 23)

When user mentions data that lives in a specific database, prefix the query:
  @staging SELECT ...
  @oracle-legacy SELECT ...

For cross-DB comparisons, generate multiple queries and use compare_schemas/compare_data tools.
```

## Migration Path

1. Wait for Agent 9A to finish refactoring connection.py + introspection.py
2. Create src/payp/core/multi_connection.py with MultiConnectionManager
3. Create src/payp/tools/crossdb.py with compare_schemas, compare_data, list_connections, switch_connection
4. Refactor ChatSession to use MultiConnectionManager
5. Parse @prefix in user input, pass as connection parameter
6. Update system prompt builder to show all active connections
7. Update /db command in cli.py for multi-connection
8. Add /switch, /disconnect commands
9. Test: prod-pg + staging-mysql + oracle schema comparison
