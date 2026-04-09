# payp — Technology Stack

## CLI & UI

| Library | Purpose | Notes |
|---------|---------|-------|
| `typer` | CLI framework | Command routing, help generation, argument parsing |
| `rich` | Terminal formatting | Tables, syntax highlighting, progress bars, panels, trees |
| `rich.live` | Live updating displays | Streaming model output, real-time query progress |
| `prompt_toolkit` | Interactive mode | Multi-line SQL input, autocomplete, key bindings, history |
| `textual` | TUI dashboards | Full terminal UI framework (by Rich author), for dashboard mode |

## CLI Data Visualization

| Library | Purpose | Notes |
|---------|---------|-------|
| `plotext` | Terminal plots | Bar, scatter, line, histogram — renders directly in terminal with Unicode/ANSI |
| `rich` tables | Tabular data | Query results, schema views, comparison tables |
| `asciichartpy` | Simple line charts | Lightweight, good for quick metrics/trends |
| `textual-plotext` | Plotext in TUI | Plotext widget for Textual apps — combine dashboards + charts |
| `sparklines` | Inline sparklines | Tiny trend indicators in table cells |

> **Yes, real data visualization in CLI is very doable.** `plotext` supports bar charts, scatter plots, 
> histograms, time series, and even heatmaps — all rendered in the terminal. Combined with `textual` 
> for layout, you can build actual dashboards without leaving the terminal.

## AI / Model Layer

| Library | Purpose | Notes |
|---------|---------|-------|
| `litellm` | Multi-model routing | Unified API for Claude, GPT, Gemini, Qwen, Ollama, etc. |
| `litellm` streaming | Streaming responses | Token-by-token output with `rich.live` |
| `tiktoken` / `tokenizers` | Token counting | Context window management, cost estimation |
| `mcp` (Python SDK) | MCP protocol | Tool/resource server protocol for extensibility |

## Database Connectors

| Library | Purpose | Notes |
|---------|---------|-------|
| `psycopg[binary]` v3 | PostgreSQL | Async support, connection pooling, COPY protocol |
| `mysql-connector-python` | MySQL | Official Oracle-maintained driver |
| `oracledb` | Oracle | Thin mode (no Oracle Client needed), async support |
| `sqlalchemy` (Core only) | Schema introspection | Unified metadata reading across all 3 DBs. **Not used as ORM** |

## SQL Processing

| Library | Purpose | Notes |
|---------|---------|-------|
| `sqlglot` | SQL parsing & transformation | Parse, diff, transpile, optimize SQL across dialects |
| `sqlglot` diff | SQL diffing | Compare two SQL statements/schemas structurally |
| `sqlglot` transpile | Dialect conversion | PostgreSQL ↔ MySQL ↔ Oracle SQL translation |
| `sqlparse` | SQL formatting | Pretty-printing, statement splitting (lighter than sqlglot) |

## Configuration & Data

| Library | Purpose | Notes |
|---------|---------|-------|
| `pydantic-settings` | Config management | Typed settings, env vars, .env files, validation |
| `pydantic` v2 | Data models | Schema models, validation, serialization |
| `toml` / `tomli` | Config files | `payp.toml` project config (like pyproject.toml pattern) |
| `keyring` | Credential storage | OS-level secure storage for DB passwords, API keys |

## Storage & State

| Library | Purpose | Notes |
|---------|---------|-------|
| `sqlite3` (stdlib) | Local state | Transaction log, session history, schema cache |
| `diskcache` | Caching | Schema introspection cache, model response cache |

## Testing & Quality

| Library | Purpose | Notes |
|---------|---------|-------|
| `pytest` | Testing | Unit + integration tests |
| `pytest-asyncio` | Async testing | For async DB operations |
| `testcontainers` | DB testing | Spin up real PostgreSQL/MySQL/Oracle in Docker for tests |
| `ruff` | Linting + formatting | Fast, replaces black + isort + flake8 |
| `mypy` | Type checking | Static analysis |

## Build & Distribution

| Library | Purpose | Notes |
|---------|---------|-------|
| `hatch` / `hatchling` | Build system | Modern Python packaging |
| `pipx` | Installation | User installs payp globally via `pipx install payp` |
