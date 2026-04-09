# payp

AI-powered CLI for data engineers. Think Claude Code, but for databases.

Chat with your PostgreSQL, MySQL, or Oracle database using natural language. payp generates SQL, explains query plans, visualizes data in the terminal, and keeps a full audit trail — with security modes that range from manual approval to two-model AI validation.

> **Status:** Early development (alpha). Not production-ready yet.

## Features

- **Multi-database** — PostgreSQL, MySQL, Oracle in one session
- **Natural language to SQL** — ask questions, get queries
- **Security modes** — manual approval, YOLO, auto-secure (Model B reviews Model A)
- **Schema intelligence** — tiered introspection that scales to thousands of tables
- **DML snapshots** — automatic backup before UPDATE/DELETE, rollback anytime
- **Terminal charts & dashboards** — bar, line, scatter, histogram, live TUI dashboards
- **Export** — CSV, JSON, Parquet, Excel with streaming for large datasets
- **Cross-DB operations** — compare schemas and data across different databases
- **Knowledge base** — annotate schemas, save queries, share conventions with your team
- **Transaction log** — full audit trail of every operation
- **Multi-model** — use any LLM provider via OpenRouter/litellm

## Quick Start

```bash
pip install -e .
payp
```

On first run, payp will guide you through connecting to your database.

## Requirements

- Python 3.11+
- A database to connect to (PostgreSQL, MySQL, or Oracle)
- An API key for your LLM provider (OpenRouter recommended)

## Authors

- **Artjoms Zelenkevics** — Data Engineer, University of Latvia
- **Viktors Afanasjevs** — Lead Software Developer

## License

MIT
