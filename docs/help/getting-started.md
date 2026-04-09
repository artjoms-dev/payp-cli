# Getting Started with payp

payp is an AI database assistant. You chat in natural language, it generates and runs SQL.

## First steps
1. Run `/db` to connect to a database (or select an existing connection)
2. Ask anything: "show me top 10 customers", "add a status column", "what tables exist"
3. payp handles the SQL

## How it works
- You type natural language
- payp's AI model generates SQL
- Before executing anything destructive, payp shows you the SQL and asks for approval (depends on mode)
- Results are formatted as tables in the terminal

## Your safety
- DELETE/UPDATE automatically creates snapshots (backups) before executing
- Every operation is logged in `/history`
- You can restore from snapshots via `/snapshots` or by asking the assistant
