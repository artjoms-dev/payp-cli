# payp — Skills & Tools Architecture

## Overview (adapted from Claude Code pattern)

Claude Code uses markdown files with YAML frontmatter as skill definitions, and a Tool interface for LLM-callable tools. payp adapts this pattern for database operations.

## Two Concepts: Tools vs Skills

### Tools
Low-level operations the LLM can call. Built into payp core.

```python
# Each tool is a Python class/dict with this interface:
class Tool:
    name: str                    # "query", "explain", "export"
    description: str             # LLM reads this to know when to use it
    input_schema: dict           # JSON schema for parameters
    is_read_only: bool           # True for SELECT, schema_lookup
    is_destructive: bool         # True for DROP, TRUNCATE
    
    async def call(self, args, context) -> ToolResult:
        """Execute the tool and return result."""
        ...
```

### Skills
Higher-level workflows composed of multiple tool calls. Loaded from markdown files.

```
payp/skills/                        # Project-level skills
~/.payp/skills/                     # User-level skills
payp/builtin_skills/                # Shipped with payp
```

## Built-in Tools (PoC)

| Tool | Type | Description |
|------|------|-------------|
| `query` | read/write | Execute SQL against connected database |
| `explain` | read-only | Run EXPLAIN on a query |
| `schema_lookup` | read-only | Load T2 DDL for specified tables |
| `schema_search` | read-only | Search T1 catalog for table names |
| `export` | read-only | Export result to CSV/JSON/Parquet/Excel |
| `snapshot` | read-only | Snapshot rows before DML |
| `file_read` | read-only | Read a file from filesystem |
| `file_write` | write | Write content to a file |
| `connection_info` | read-only | Get current connection details |

## Skill File Format (placeholder for future)

Adapted from Claude Code's markdown + YAML frontmatter pattern:

```markdown
---
name: migration-generator
description: Generate a migration with reverse-SQL from natural language
when_to_use: User asks to create a migration, alter schema, or evolve database structure
allowed_tools: [query, schema_lookup, explain, file_write]
db_types: [postgresql, mysql, oracle]   # which DBs this skill works with
---

## Migration Generator

When the user asks to change the database schema:

1. Look up current schema for affected tables using schema_lookup
2. Generate the forward migration SQL
3. Generate the reverse migration SQL
4. Show both to user for approval
5. If approved, execute forward migration
6. Log both forward and reverse to transaction log
7. Optionally save as migration file in ./payp/migrations/
```

## Skill Discovery (future)

```
1. On startup, scan:
   - payp/builtin_skills/          (shipped with payp)
   - ~/.payp/skills/               (user-installed)
   - ./payp/skills/                (project-specific)

2. Parse YAML frontmatter from each .md file
3. Register with skill registry
4. Make available to LLM via system prompt
```

## For PoC: Placeholder Structure

No skills implemented yet. Just the directory structure and a README:

```
payp/
├── src/
│   └── payp/
│       ├── tools/               # Built-in tools (implemented)
│       │   ├── __init__.py
│       │   ├── base.py          # Tool base class / interface
│       │   ├── query.py
│       │   ├── explain.py
│       │   ├── schema.py
│       │   ├── export.py
│       │   ├── snapshot.py
│       │   └── filesystem.py
│       │
│       └── skills/              # Skill loader (placeholder)
│           ├── __init__.py
│           ├── loader.py        # Discovers and parses skill .md files
│           └── registry.py      # Registers skills for LLM access
│
├── builtin_skills/              # Shipped skill definitions (future)
│   └── README.md                # "Skills coming soon. See docs/skills-architecture.md"
```

## Tool → LLM Integration

Tools are described in the system prompt (see system-prompt.md Section 7).
The LLM requests tool calls in its response, payp executes them, returns results.

```
User: "show me orders from last week"

LLM thinks: need to query orders table, need schema first
LLM calls: schema_lookup(tables=["orders"])
payp returns: CREATE TABLE orders (id BIGINT, ..., created_at TIMESTAMPTZ)

LLM generates SQL: SELECT * FROM orders WHERE created_at > NOW() - INTERVAL '7 days'
LLM calls: query(sql="SELECT ...")
payp executes, returns: [{id: 1, ...}, ...]

LLM formats and presents results to user
```
