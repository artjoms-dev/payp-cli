# payp — Cherry-Picks from Claude Code & Aider

Patterns and ideas to study and adapt. Not copy-paste — understand the pattern, build our own version for DB context.

## From Claude Code Leaks

### Architecture Patterns

- [ ] **Permission / Security Mode system** — How Claude Code implements manual, auto-allow, and deny modes. Adapt for payp's three modes: manual (approve every SQL), yolo (auto-execute), auto-secure (model B reviews model A)
- [ ] **Hook system** — Pre/post execution hooks (e.g., UserPromptSubmit, tool execution). Adapt for: pre-query validation, post-query logging, schema change notifications
- [ ] **Skill / Tool registration pattern** — How skills are discovered, loaded, and invoked. Adapt for DB-specific skills: migration generators, schema analyzers, performance tuners
- [ ] **MCP server integration** — How Claude Code connects to MCP servers for extensibility. Build DB-native MCP servers (PostgreSQL MCP, Oracle MCP, etc.)
- [ ] **Context window management** — How Claude Code compresses/manages context as it grows. Adapt for schema context: prioritize relevant tables, prune distant schemas
- [ ] **CLAUDE.md / project config pattern** — Per-project instructions that shape behavior. Adapt as `payp.toml` or `payp.md` — DB connection profiles, naming conventions, migration rules

### UX Patterns

- [ ] **Streaming tool output** — Real-time display of model thinking + tool execution
- [ ] **Tool call visualization** — How tool calls are shown to user (approval prompts, results)
- [ ] **Conversation persistence** — Session history, resume conversations

## From Aider

### Core Concepts

- [ ] **Repo map → Schema map** — Aider builds a tree-sitter map of code. payp builds an introspection map of DB schema (tables, columns, types, FKs, indexes, constraints, views, procedures)
- [ ] **Model configuration pattern** — How Aider handles model selection, fallbacks, cost tracking across providers
- [ ] **Chat history / context management** — How Aider manages what's in context vs. what's summarized
- [ ] **Edit format pattern** — Aider's diff/whole/udiff edit formats. Adapt for SQL: migration format, diff format, full DDL format
- [ ] **Cost tracking** — Token usage, cost per query, session totals. Essential for multi-model setup
- [ ] **Linter integration pattern** — Aider runs linters after code changes. payp runs SQL validators, EXPLAIN plans after query generation

### UX Ideas

- [ ] **Auto-commit pattern** — Aider auto-commits code changes. payp auto-logs to transaction log
- [ ] **Voice mode** — Aider supports voice input. Could be useful for data exploration ("show me sales by region")
- [ ] **Browser integration** — Aider can scrape docs. payp could scrape DB documentation, ERD diagrams

## Original payp Innovations (not from either)

These are payp-unique features — no cherry-pick source, build from scratch:

- [ ] **Reverse-SQL generation** — Auto-generate rollback for any DDL/DML
- [ ] **Transaction log** — Git-like history but for DB operations (not file changes)
- [ ] **Dry-run mode** — EXPLAIN + simulate without executing
- [ ] **Auto-secure mode** — Model B validates Model A's output before execution
- [ ] **Schema diff over time** — Track how schema evolves across sessions
- [ ] **CLI data visualization** — Charts, graphs, dashboards in terminal
- [ ] **Multi-DB awareness** — Same session can connect to PG + MySQL + Oracle simultaneously
