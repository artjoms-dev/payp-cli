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
import logging
import os
import sys
from pathlib import Path
from typing import Any

_IS_WINDOWS = sys.platform == "win32"

from payp.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
MAX_OUTPUT = 50_000


class ShellExecTool(BaseTool):
    name = "execute_shell"
    tier = "advanced"
    description = (
        "Execute a shell command via the system shell (cmd.exe on Windows, /bin/sh on Unix) in the current working directory. "
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
                        "Shell command to run (passed to cmd.exe /c on Windows, "
                        "/bin/sh -c on Unix). Supports pipes, redirects, env vars."
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
        # Gate on shell-exec skill being active. FAIL-CLOSED: no registry
        # → refuse unless the operator explicitly opts in. Shell is the
        # highest-risk tool in the catalog — default-deny in MCP.
        skills = context.get("skills")
        if skills is None:
            if not os.environ.get("PAYP_DANGEROUS_UNGATED_CODE_EXEC"):
                return ToolResult(
                    success=False,
                    error=(
                        "Shell execution is disabled in this environment "
                        "(no skill registry available). In MCP/headless mode "
                        "set PAYP_DANGEROUS_UNGATED_CODE_EXEC=1 on the server "
                        "to acknowledge the risk and allow shell commands."
                    ),
                    summary="execute_shell blocked: fail-closed (no skill registry)",
                )
        elif hasattr(skills, "is_active") and not skills.is_active("shell-exec"):
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
            if _IS_WINDOWS:
                shell_cmd = ["cmd.exe", "/c", command]
            else:
                shell_cmd = ["/bin/sh", "-c", command]

            proc = await asyncio.create_subprocess_exec(
                *shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=Path.cwd(),
            )
        except Exception as e:
            logger.exception("ShellExecTool spawn failed")
            return ToolResult(
                success=False,
                error=f"Failed to spawn shell: {e}",
                summary="execute_shell: spawn failed",
            )

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
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
