"""Python code execution tool — isolated subprocess runner.

Runs Python code in a subprocess for:
- matplotlib/plotly chart generation
- pandas analysis
- custom data transformations and exports
- format conversions
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from payp.tools.base import BaseTool, ToolResult

DEFAULT_TIMEOUT = 30
MAX_OUTPUT = 50_000


class PythonExecTool(BaseTool):
    name = "execute_python"
    tier = "advanced"
    description = (
        "Execute Python code in a subprocess. Use for: generating charts "
        "(matplotlib/plotly/seaborn), pandas analysis, custom exports, "
        "numeric computations, file format conversions. "
        "Runs in the current working directory so files can be read/written. "
        "Captures stdout + stderr. 30 second timeout. "
        "Does NOT have access to the active database connection — query first "
        "via execute_sql or export_query, THEN analyze via Python."
    )
    is_read_only = False
    is_destructive = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to run. Use print() for visible output.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Optional timeout in seconds (default 30, max 300)",
                },
                "description": {
                    "type": "string",
                    "description": "Short explanation of what the code does",
                },
            },
            "required": ["code"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        # Gate on python-analytics skill being active. FAIL-CLOSED: if the
        # context has no skill registry at all (e.g. MCP headless mode),
        # refuse unless an explicit env var opts in.
        skills = context.get("skills")
        if skills is None:
            if not os.environ.get("PAYP_DANGEROUS_UNGATED_CODE_EXEC"):
                return ToolResult(
                    success=False,
                    error=(
                        "Python execution is disabled in this environment "
                        "(no skill registry available). In MCP/headless mode "
                        "set PAYP_DANGEROUS_UNGATED_CODE_EXEC=1 on the server "
                        "to acknowledge the risk and allow code execution."
                    ),
                    summary="execute_python blocked: fail-closed (no skill registry)",
                )
        elif hasattr(skills, "is_active") and not skills.is_active("python-analytics"):
            return ToolResult(
                success=False,
                error=(
                    "Python execution is disabled. Activate the 'python-analytics' "
                    "skill via /skills to enable it."
                ),
                summary="execute_python blocked: skill not active",
            )

        code = args.get("code", "").strip()
        if not code:
            return ToolResult(success=False, error="No code provided")

        timeout = min(int(args.get("timeout", DEFAULT_TIMEOUT)), 300)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = Path(f.name)

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(tmp_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=Path.cwd(),
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
                    error=f"Code execution timed out after {timeout}s",
                    summary=f"Python: timeout ({timeout}s)",
                )

            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")

            if len(stdout) > MAX_OUTPUT:
                stdout = stdout[:MAX_OUTPUT] + f"\n... [truncated, total {len(stdout)} chars]"
            if len(stderr) > MAX_OUTPUT:
                stderr = stderr[:MAX_OUTPUT] + "\n... [truncated]"

            if proc.returncode == 0:
                files_hint = _detect_created_files(code)
                return ToolResult(
                    success=True,
                    data={
                        "stdout": stdout,
                        "stderr": stderr,
                        "return_code": 0,
                        "files_mentioned": files_hint,
                    },
                    summary=f"Python ran OK ({len(stdout)} chars stdout)",
                )
            return ToolResult(
                success=False,
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "return_code": proc.returncode,
                },
                error=stderr[-500:] if stderr else f"Exit code {proc.returncode}",
                summary=f"Python failed (exit {proc.returncode})",
            )
        finally:
            try:
                tmp_path.unlink()
            except Exception:
                pass


def _detect_created_files(code: str) -> list[str]:
    """Scan code for file-writing patterns to hint user what was created."""
    patterns = [
        r"(?:savefig|to_csv|to_excel|to_parquet|to_json|write_csv|write_image|write_html)\s*\(\s*['\"]([^'\"]+)['\"]",
        r"Path\s*\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\.write",
    ]
    found: list[str] = []
    for pat in patterns:
        for m in re.finditer(pat, code):
            path = m.group(1)
            if path not in found:
                found.append(path)
    return found
