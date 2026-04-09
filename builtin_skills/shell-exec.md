---
name: shell-exec
description: Run shell commands (git, aws cli, pg_dump, curl, docker, kubectl, etc.) in the current working directory
when_to_use: User asks to run a shell command, backup a database, interact with git, aws, gcloud, kubectl, docker, or any external CLI tool
allowed_tools: [execute_shell]
db_types: [postgresql, mysql, oracle]
author: payp-team
version: 1.0
---

## Shell Execution Workflow

Use this skill when the user wants to run **OS-level commands** — git, cloud CLIs, database dumps, HTTP calls, file utilities. This is distinct from SQL execution; use `execute_sql` for database queries.

### Core Rules

- **Commands run via `/bin/sh -c`** — pipes, redirects, env vars, and command chaining all work.
- **Always show the command before running.** The approval UI handles this automatically in manual/secure modes.
- **Working directory** is the user's current directory. Relative paths are resolved from there.
- **Timeout** defaults to 30s, max 300s. Long-running commands (big backups) need an explicit `timeout`.
- **Capture stdout + stderr.** Always report both to the user.

### Safety Rules

- Never run destructive commands (`rm -rf`, `DROP`, `git push --force`) without explicit user intent.
- Prefer read-only operations first (`git status` before `git commit`, `ls` before `rm`).
- If a command requires credentials, verify they are available in the environment before running.
- For DB backups, write to a dated path inside `./backups/` or `./exports/`.

### Example: git status and commit

```bash
git status
```

```bash
git add -A && git commit -m "chore: update exports"
```

### Example: PostgreSQL backup with pg_dump

```bash
mkdir -p ./backups && pg_dump -h localhost -U myuser -d mydb -Fc -f ./backups/mydb_$(date +%Y%m%d_%H%M%S).dump
```

Use `timeout: 300` for large databases.

### Example: MySQL dump

```bash
mkdir -p ./backups && mysqldump -h localhost -u myuser -p mydb > ./backups/mydb_$(date +%Y%m%d).sql
```

### Example: HTTP API call with curl

```bash
curl -s -H "Accept: application/json" https://api.example.com/v1/status | head -100
```

### Example: upload backup to S3

```bash
aws s3 cp ./backups/mydb_20260405.dump s3://my-bucket/db-backups/
```

### Example: list Kubernetes pods

```bash
kubectl get pods -n production
```

### Example: docker container inspect

```bash
docker ps --filter "name=postgres" --format "{{.ID}} {{.Status}}"
```

### Reporting to user

After the command runs, tell the user:
- Exit code
- Key lines from stdout
- Any warnings/errors from stderr
- Files created (if applicable) and where
