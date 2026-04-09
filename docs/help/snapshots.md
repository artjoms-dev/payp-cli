# Snapshots

Snapshots are JSONL backup files created BEFORE any DELETE/UPDATE operation. They're your safety net.

## Where they live
`./payp/snapshots/` (inside your current project directory).

## How they're created
payp automatically calls `snapshot_before_delete` before destructive DML. You don't need to ask.

## Manage snapshots
- `/snapshots` — interactive list (↑↓ navigate, `d` delete, Enter for details, Esc to cancel)
- Ask the assistant: "list my snapshots", "delete old snapshots", "restore from the orders snapshot"

## Restore data
Ask: "restore orders from the snapshot". The assistant will:
1. List available snapshots for that table
2. Read the JSONL file
3. Generate INSERT statements
4. Execute (with approval per your mode)

## Limits
- Max 10,000 rows per snapshot (configurable)
- Snapshots persist between sessions
- Not auto-deleted — clean up manually or ask the assistant
