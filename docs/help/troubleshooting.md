# Troubleshooting

## Connection fails
- Verify the database is running and reachable (`pg_isready` for PostgreSQL)
- Check credentials: `cat ~/.payp/connections/<name>.toml`
- Test password: try connecting with `psql` directly
- Firewall: confirm port is open

## AI errors
- **"is not a valid model ID"** — model name wrong in `~/.payp/models.toml`. OpenRouter uses `openrouter/<provider>/<model>` format (e.g., `openrouter/anthropic/claude-sonnet-4`)
- **API key invalid** — regenerate key, update `~/.payp/models.toml`
- **Rate limited** — wait or switch to another provider

## Approval UI not showing
If payp is running SQL without showing the approval panel, check `/mode`. Might be set to `yolo`.

## LLM asks "proceed?" in text instead of showing approval UI
The LLM should call `execute_sql` directly — payp intercepts and shows the approval. If it's asking in text, the system prompt may need adjustment.

## Snapshots missing
Check `./payp/snapshots/` in current directory. Snapshots are project-level, not global.

## Schema cache stale
Run `/schema --refresh` to re-introspect.
