# payp — Positioning & Value Proposition

## Elevator Pitch

**payp is Claude Code for data engineers.** A CLI tool that connects AI to live databases — with the most secure AI-database workflow available and the best experience for working with AI and data.

## Why Not Just Claude Code + PostgreSQL MCP?

| Capability | Claude Code + MCP | payp |
|-----------|-------------------|------|
| Chat with AI | ✓ | ✓ |
| Run SQL | ✓ (basic) | ✓ (dialect-aware, auto-formatted) |
| Schema awareness | Flat dump | Tiered T0-T3, works with 4000+ tables |
| Reverse-SQL | ✗ | Auto-generated for every DDL/DML |
| DML snapshots | ✗ | Snapshot before UPDATE/DELETE, rollback anytime |
| Security modes | Manual/auto-allow | Manual, YOLO, Secure (reviewer), Secure-Auto |
| Two-model validation | ✗ | Model B reviews Model A before execution |
| Transaction log | ✗ | Full audit trail, every operation logged |
| Cross-DB operations | ✗ | Compare schemas/data across PG, MySQL, Oracle |
| Dry-run with EXPLAIN | ✗ | LLM interprets execution plans |
| Schema diff | ✗ | Compare schemas across environments |
| Export to formats | ✗ | CSV, JSON, Parquet, Excel — streaming for large data |
| CLI visualization | ✗ | Charts, graphs, dashboards in terminal |
| Data-specialized LLM | Generic coder | DE + Architect + Analyst + DBA + Security |
| Knowledge base | Generic repo context | DB-specific: schema notes, business context, conventions |
| Team sharing | Via git repo | Connection profiles + knowledge + queries in git |

## Target Users

### Data Engineers
- Daily database work — queries, migrations, ETL debugging
- Cross-environment validation (prod vs staging)
- Schema evolution management

### Data Analysts
- Get data from source without knowing exact SQL
- Quick preview, then export to Excel/Parquet for analysis
- Build fast dashboards from terminal

### Database Administrators
- Performance analysis with AI-interpreted EXPLAIN plans
- Safe schema changes with reverse-SQL
- Audit trail for compliance

### Teams
- Shared knowledge base (schema annotations, conventions)
- Shared saved queries
- Consistent security policies across team members

## The Moat

1. **Most secure AI-database workflow** — two-model validation, snapshots, reverse-SQL, transaction log. No other tool does all four.
2. **Schema intelligence at scale** — works with 4000+ table databases through tiered context, not just toy projects.
3. **Cross-database operations** — compare, diff, validate across PostgreSQL, MySQL, Oracle in one session.
4. **Data-first, not code-first** — every feature designed for database work, not adapted from a code editor.
