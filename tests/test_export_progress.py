"""Test ExportTool progress bar rendering across all 4 formats.

Mocks the DB layer so the test is self-contained — no Postgres needed.
Verifies:
  - Files are created successfully for large result sets (>= 1000 rows).
  - Small result sets (< 1000 rows) still export correctly without a progress bar.
  - Progress threshold is respected.

Run directly:

    python tests/test_export_progress.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any
from unittest.mock import patch

from payp.tools.export import PROGRESS_THRESHOLD, ExportTool


class FakeConnectionManager:
    """Minimal stand-in for ConnectionManager with a synthetic result set."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.is_connected = True

    async def execute_raw(self, sql: str) -> list[dict[str, Any]]:
        return self._rows


def make_rows(n: int) -> list[dict[str, Any]]:
    return [
        {"id": i, "name": f"user_{i}", "score": i * 1.5, "note": None}
        for i in range(n)
    ]


async def export_all_formats(
    rows: list[dict[str, Any]], tmpdir: Path, label: str
) -> dict[str, bool]:
    tool = ExportTool()
    conn = FakeConnectionManager(rows)
    context = {"connection_manager": conn}

    results: dict[str, bool] = {}
    for fmt in ("csv", "json", "xlsx", "parquet"):
        out = tmpdir / f"{label}.{fmt}"
        res = await tool.call(
            {"sql": "SELECT 1", "format": fmt, "path": str(out)},
            context,
        )
        ok = res.success and out.exists() and out.stat().st_size > 0
        rows_written = res.data.get("rows") if res.success else None
        status = "OK" if ok else "FAIL"
        print(
            f"  [{label}][{fmt}] {status} success={res.success} "
            f"rows={rows_written} size={out.stat().st_size if out.exists() else 0}B"
        )
        if not ok and res.error:
            print(f"    error: {res.error}")
        results[fmt] = ok
    return results


async def test_progress_gating() -> bool:
    """Verify Progress is instantiated only when row_count >= PROGRESS_THRESHOLD."""
    print("\n-- progress gating --")
    tmpdir = Path(tempfile.mkdtemp(prefix="payp_progress_gate_"))

    import payp.tools.export as export_mod

    # Small export — should NOT use progress bar
    small_rows = make_rows(10)
    with patch.object(
        export_mod, "_make_progress", wraps=export_mod._make_progress
    ) as spy:
        tool = ExportTool()
        conn = FakeConnectionManager(small_rows)
        for fmt in ("csv", "xlsx", "parquet"):
            out = tmpdir / f"small.{fmt}"
            res = await tool.call(
                {"sql": "SELECT 1", "format": fmt, "path": str(out)},
                {"connection_manager": conn},
            )
            assert res.success, f"small {fmt} failed: {res.error}"
        small_calls = spy.call_count
        print(f"  small ({len(small_rows)} rows): _make_progress calls = {small_calls}")
        if small_calls != 0:
            print("  FAIL: progress bar triggered for small export")
            return False

    # Large export — SHOULD use progress bar for csv/xlsx/parquet (not json)
    large_rows = make_rows(PROGRESS_THRESHOLD)
    with patch.object(
        export_mod, "_make_progress", wraps=export_mod._make_progress
    ) as spy:
        tool = ExportTool()
        conn = FakeConnectionManager(large_rows)
        for fmt in ("csv", "json", "xlsx", "parquet"):
            out = tmpdir / f"large.{fmt}"
            res = await tool.call(
                {"sql": "SELECT 1", "format": fmt, "path": str(out)},
                {"connection_manager": conn},
            )
            assert res.success, f"large {fmt} failed: {res.error}"
        large_calls = spy.call_count
        print(f"  large ({len(large_rows)} rows): _make_progress calls = {large_calls}")
        # csv, xlsx, parquet -> 3 progress bars; json is skipped intentionally
        if large_calls != 3:
            print(f"  FAIL: expected 3 progress bars, got {large_calls}")
            return False

    print("  OK: progress gating works as expected")
    return True


async def main() -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="payp_progress_"))
    print(f"export dir: {tmpdir}")

    print("\n-- small export (10 rows) --")
    small_results = await export_all_formats(make_rows(10), tmpdir, "small")

    print(f"\n-- large export ({PROGRESS_THRESHOLD + 500} rows) --")
    large_results = await export_all_formats(
        make_rows(PROGRESS_THRESHOLD + 500), tmpdir, "large"
    )

    gating_ok = await test_progress_gating()

    total = len(small_results) + len(large_results) + 1
    passed = (
        sum(small_results.values())
        + sum(large_results.values())
        + (1 if gating_ok else 0)
    )
    print(f"\nresult: {passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception:
        traceback.print_exc()
        sys.exit(1)
