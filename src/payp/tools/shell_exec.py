"""Shell command execution tool — isolated subprocess runner.

Runs shell commands in a subprocess for:
- git operations (status, commit, log, diff)
- cloud CLIs (aws, gcloud, kubectl, docker)
- database backup/restore (pg_dump, mysqldump)
- HTTP calls (curl, wget)
- file system utilities
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from payp.tools.base import BaseTool, ToolResult


DEFAULT_TIMEOUT = 30
MAX_OUTPUT = 50_000


class ShellExecTool(BaseTool):
    name = "execute_shell"
    description = (
        "Execute a shell command via /bin/sh -c in the current working directory. "
        "Use for: git operations, pg_dump/mysqldump backups, curl/wget HTTP calls, "
        "aws/gcloud/kubectl/docker CLIs, file system utilities. "
        "Supports pipes, redirects, and environment variables. "
        "Captures stdout + stderr. 30 second timeout (max 300). "
        "Does NOT have direct access to the active database connection — "
        "use execute_sql for queries, this tool for OS-level operations."
    )
    is_read_only = False
    is_destructive = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "Shell command to run (passed to /bin/sh -c). "
                        "Supports pipes, redirects, env vars."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": "Optional timeout in seconds (default 30, max 300)",
                },
                "description": {
                    "type": "string",
                    "description": "Short explanation of what the command does",
                },
            },
            "required": ["command"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        # Gate on shell-exec skill being active
        skills = context.get("skills")
        if skills is not None and hasattr(skills, "is_active"):
            if not skills.is_active("shell-exec"):
                return ToolResult(
                    success=False,
                    error=(
                        "Shell execution is disabled. Activate the 'shell-exec' "
                        "skill via /skills to enable it."
                    ),
                    summary="execute_shell blocked: skill not active",
                )

        command = (args.get("command") or "").strip()
        if not command:
            return ToolResult(success=False, error="No command provided")

        timeout = min(int(args.get("timeout", DEFAULT_TIMEOUT)), 300)

        try:
            proc = await asyncio.create_subprocess_exec(
                "/bin/sh",
                "-c",
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=Path.cwd(),
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to spawn shell: {e}",
                summary="execute_shell: spawn failed",
            )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult(
                success=False,
                error=f"Shell command timed out after {timeout}s",
                summary=f"Shell: timeout ({timeout}s)",
            )

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        if len(stdout) > MAX_OUTPUT:
            stdout = stdout[:MAX_OUTPUT] + f"\n... [truncated, total {len(stdout)} chars]"
        if len(stderr) > MAX_OUTPUT:
            stderr = stderr[:MAX_OUTPUT] + "\n... [truncated]"

        if proc.returncode == 0:
            return ToolResult(
                success=True,
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "return_code": 0,
                },
                summary=f"Shell ran OK ({len(stdout)} chars stdout)",
            )
        return ToolResult(
            success=False,
            data={
                "stdout": stdout,
                "stderr": stderr,
                "return_code": proc.returncode,
            },
            error=stderr[-500:] if stderr else f"Exit code {proc.returncode}",
            summary=f"Shell failed (exit {proc.returncode})",
        )
