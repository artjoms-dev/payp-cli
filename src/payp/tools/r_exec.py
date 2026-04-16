"""R code execution tool — isolated subprocess runner via Rscript.

Runs R code for:
- ggplot2 chart generation
- dplyr/tidyr data manipulation
- statistical modeling (lm, glm, etc.)
- Custom exports in formats like .RData
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from payp.tools.base import BaseTool, ToolResult

DEFAULT_TIMEOUT = 30
MAX_OUTPUT = 50_000


class RExecTool(BaseTool):
    name = "execute_r"
    tier = "advanced"
    description = (
        "Execute R code via Rscript in a subprocess. Use for: ggplot2 charts, "
        "dplyr/tidyr data manipulation, statistical modeling (lm/glm/aov), "
        "custom exports. Runs in the current working directory — can read/write files. "
        "Captures stdout + stderr. 30 second default timeout. "
        "Does NOT have database access — query via execute_sql or export_query first, "
        "THEN read the CSV/Parquet from R."
    )
    is_read_only = False
    is_destructive = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "R code to run. Use cat() or print() for visible output.",
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
        # Gate on r-analytics skill being active. FAIL-CLOSED: no registry
        # → refuse unless the operator explicitly opts in.
        skills = context.get("skills")
        if skills is None:
            if not os.environ.get("PAYP_DANGEROUS_UNGATED_CODE_EXEC"):
                return ToolResult(
                    success=False,
                    error=(
                        "R execution is disabled in this environment "
                        "(no skill registry available). In MCP/headless mode "
                        "set PAYP_DANGEROUS_UNGATED_CODE_EXEC=1 on the server "
                        "to acknowledge the risk and allow code execution."
                    ),
                    summary="execute_r blocked: fail-closed (no skill registry)",
                )
        elif hasattr(skills, "is_active") and not skills.is_active("r-analytics"):
            return ToolResult(
                success=False,
                error=(
                    "R execution is disabled. Activate the 'r-analytics' "
                    "skill via /skills to enable it."
                ),
                summary="execute_r blocked: skill not active",
            )

        # Check Rscript is installed
        rscript = shutil.which("Rscript")
        if rscript is None:
            return ToolResult(
                success=False,
                error=(
                    "Rscript not found on PATH. Install R first:\n"
                    "  macOS: brew install r\n"
                    "  Ubuntu/Debian: apt install r-base\n"
                    "  Windows: https://cran.r-project.org/"
                ),
                summary="R not installed",
            )

        code = args.get("code", "").strip()
        if not code:
            return ToolResult(success=False, error="No code provided")

        timeout = min(int(args.get("timeout", DEFAULT_TIMEOUT)), 300)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".R", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = Path(f.name)

        try:
            proc = await asyncio.create_subprocess_exec(
                rscript,
                "--vanilla",
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
                    error=f"R execution timed out after {timeout}s",
                    summary=f"R: timeout ({timeout}s)",
                )

            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")

            if len(stdout) > MAX_OUTPUT:
                stdout = stdout[:MAX_OUTPUT] + f"\n... [truncated, total {len(stdout)} chars]"
            if len(stderr) > MAX_OUTPUT:
                stderr = stderr[:MAX_OUTPUT] + "\n... [truncated]"

            if proc.returncode == 0:
                files_hint = _detect_created_files_r(code)
                return ToolResult(
                    success=True,
                    data={
                        "stdout": stdout,
                        "stderr": stderr,
                        "return_code": 0,
                        "files_mentioned": files_hint,
                    },
                    summary=f"R ran OK ({len(stdout)} chars stdout)",
                )
            return ToolResult(
                success=False,
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "return_code": proc.returncode,
                },
                error=stderr[-500:] if stderr else f"Exit code {proc.returncode}",
                summary=f"R failed (exit {proc.returncode})",
            )
        finally:
            try:
                tmp_path.unlink()
            except Exception:
                pass


def _detect_created_files_r(code: str) -> list[str]:
    """Scan R code for file-writing patterns."""
    patterns = [
        r"ggsave\s*\(\s*['\"]([^'\"]+)['\"]",
        r"(?:write\.csv|write_csv|write\.table|fwrite|write_rds|saveRDS|write_xlsx)\s*\(\s*[^,]+,\s*(?:file\s*=\s*)?['\"]([^'\"]+)['\"]",
        r"png\s*\(\s*['\"]([^'\"]+)['\"]",
        r"pdf\s*\(\s*['\"]([^'\"]+)['\"]",
        r"jpeg\s*\(\s*['\"]([^'\"]+)['\"]",
    ]
    found: list[str] = []
    for pat in patterns:
        for m in re.finditer(pat, code):
            path = m.group(1)
            if path not in found:
                found.append(path)
    return found
