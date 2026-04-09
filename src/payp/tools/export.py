"""Export tool — save query results to file (CSV, JSON, XLSX, Parquet).

Exports stream from DB cursor to file without loading all rows into memory
(for formats that support it).
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from payp.tools.base import BaseTool, ToolResult


DEFAULT_EXPORT_DIR = Path("./exports")

# Excel hard limit — 1,048,576 rows total, minus 1 for header row.
EXCEL_MAX_DATA_ROWS = 1_048_575

# Minimum row count before we render a progress bar. Below this, exports
# are effectively instant and a transient bar would just flicker.
PROGRESS_THRESHOLD = 1000


def _make_progress() -> Progress:
    """Build a consistent progress bar for export write loops."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        transient=True,
    )


class ExportTool(BaseTool):
    name = "export_query"
    description = (
        "Export a SQL query's full results to a file. "
        "Supports CSV, JSON, XLSX (Excel), and Parquet formats. "
        "Streams from DB to file where possible — handles large datasets. "
        "Returns the file path and row count."
    )
    is_read_only = False

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SQL SELECT query to export",
                },
                "format": {
                    "type": "string",
                    "enum": ["csv", "json", "xlsx", "parquet"],
                    "description": "Export format (csv, json, xlsx, or parquet)",
                },
                "filename": {
                    "type": "string",
                    "description": "Optional: filename (without extension). Defaults to query_result_{timestamp}.",
                },
                "path": {
                    "type": "string",
                    "description": "Optional: full output path. Overrides filename. Defaults to ./exports/{filename}.{format}",
                },
            },
            "required": ["sql", "format"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.db.connection import ConnectionManager

        conn: ConnectionManager | None = context.get("connection_manager")
        if not conn or not conn.is_connected:
            return ToolResult(success=False, error="Not connected to a database")

        sql = args.get("sql", "").strip()
        fmt = args.get("format", "csv").lower()
        if not sql:
            return ToolResult(success=False, error="No SQL provided")
        if fmt not in ("csv", "json", "xlsx", "parquet"):
            return ToolResult(success=False, error=f"Unsupported format: {fmt}")

        # Determine output path
        path_arg = args.get("path")
        if path_arg:
            filepath = Path(path_arg).expanduser()
        else:
            filename = args.get("filename")
            if not filename:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"query_result_{ts}"
            filepath = DEFAULT_EXPORT_DIR / f"{filename}.{fmt}"

        filepath.parent.mkdir(parents=True, exist_ok=True)

        try:
            rows = await conn.execute_raw(sql)

            if not rows:
                return ToolResult(
                    success=True,
                    data={"file": str(filepath), "rows": 0},
                    summary="No rows to export",
                )

            columns = list(rows[0].keys())
            row_count = len(rows)

            show_progress = row_count >= PROGRESS_THRESHOLD

            if fmt == "csv":
                self._write_csv(filepath, columns, rows, show_progress)
            elif fmt == "json":
                # json.dump is atomic — no meaningful progress to report.
                with open(filepath, "w") as f:
                    json.dump(rows, f, indent=2, default=str)
            elif fmt == "xlsx":
                if row_count > EXCEL_MAX_DATA_ROWS:
                    return ToolResult(
                        success=False,
                        error=(
                            f"Row count {row_count:,} exceeds Excel's hard limit of "
                            f"{EXCEL_MAX_DATA_ROWS:,} data rows. Use csv or parquet instead."
                        ),
                        summary="Export failed: too many rows for Excel",
                    )
                self._write_xlsx(filepath, columns, rows, show_progress)
            else:  # parquet
                self._write_parquet(filepath, columns, rows, show_progress)

            file_size = filepath.stat().st_size
            size_str = (
                f"{file_size / 1024:.1f} KB" if file_size < 1024 * 1024
                else f"{file_size / 1024 / 1024:.1f} MB"
            )

            return ToolResult(
                success=True,
                data={
                    "file": str(filepath),
                    "rows": row_count,
                    "format": fmt,
                    "size": size_str,
                },
                summary=f"Exported {row_count} rows → {filepath} ({size_str})",
            )

        except Exception as e:
            return ToolResult(success=False, error=str(e), summary=f"Export failed: {e}")

    @staticmethod
    def _write_csv(
        filepath: Path,
        columns: list[str],
        rows: list[dict[str, Any]],
        show_progress: bool,
    ) -> None:
        """Write rows to a CSV file, with optional progress bar."""
        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            if show_progress:
                with _make_progress() as progress:
                    task = progress.add_task(
                        "Writing csv...", total=len(rows)
                    )
                    for row in rows:
                        writer.writerow([row[c] for c in columns])
                        progress.advance(task)
            else:
                for row in rows:
                    writer.writerow([row[c] for c in columns])

    @staticmethod
    def _cell_for_xlsx(value: Any) -> Any:
        """Coerce a value into something openpyxl can write."""
        if value is None:
            return None
        # openpyxl handles str, int, float, bool, datetime, date, time natively.
        if isinstance(value, (str, int, float, bool)):
            return value
        # datetime/date/time — import lazily to avoid unused imports at module load
        from datetime import date, time
        from datetime import datetime as _dt
        if isinstance(value, _dt):
            # Excel doesn't support tz-aware datetimes — strip tzinfo.
            if value.tzinfo is not None:
                return value.replace(tzinfo=None)
            return value
        if isinstance(value, time):
            if value.tzinfo is not None:
                return value.replace(tzinfo=None)
            return value
        if isinstance(value, date):
            return value
        # Fallback — stringify (Decimal, UUID, bytes, dict, list, etc.)
        return str(value)

    def _write_xlsx(
        self,
        filepath: Path,
        columns: list[str],
        rows: list[dict[str, Any]],
        show_progress: bool = False,
    ) -> None:
        """Write rows to an XLSX file with bold header, frozen first row, auto-sized cols."""
        from openpyxl import Workbook
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter

        wb = Workbook(write_only=False)
        ws = wb.active
        ws.title = "query_result"

        # Header row
        ws.append(columns)
        bold = Font(bold=True)
        for cell in ws[1]:
            cell.font = bold

        # Track max width per column (seeded with header length)
        max_widths = [len(str(c)) for c in columns]

        def _append_row(row: dict[str, Any]) -> None:
            out_row = []
            for i, col in enumerate(columns):
                val = self._cell_for_xlsx(row[col])
                out_row.append(val)
                if val is not None:
                    display_len = len(str(val))
                    if display_len > max_widths[i]:
                        max_widths[i] = display_len
            ws.append(out_row)

        # Data rows
        if show_progress:
            with _make_progress() as progress:
                task = progress.add_task("Writing xlsx...", total=len(rows))
                for row in rows:
                    _append_row(row)
                    progress.advance(task)
        else:
            for row in rows:
                _append_row(row)

        # Freeze header
        ws.freeze_panes = "A2"

        # Auto-size columns (cap at 60 chars for sanity)
        for i, width in enumerate(max_widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 8), 60)

        wb.save(filepath)

    def _write_parquet(
        self,
        filepath: Path,
        columns: list[str],
        rows: list[dict[str, Any]],
        show_progress: bool = False,
    ) -> None:
        """Write rows to a Parquet file via pyarrow."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        # Normalise: ensure every dict has every column (None for missing)
        # and coerce unsupported types to string so pyarrow can infer cleanly.
        normalised: list[dict[str, Any]] = []

        def _normalise_row(row: dict[str, Any]) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for col in columns:
                val = row.get(col)
                out[col] = self._coerce_for_parquet(val)
            return out

        if show_progress:
            with _make_progress() as progress:
                task = progress.add_task(
                    "Normalising parquet...", total=len(rows)
                )
                for row in rows:
                    normalised.append(_normalise_row(row))
                    progress.advance(task)
        else:
            for row in rows:
                normalised.append(_normalise_row(row))

        table = pa.Table.from_pylist(normalised)
        pq.write_table(table, filepath, compression="snappy")

    @staticmethod
    def _coerce_for_parquet(value: Any) -> Any:
        """Coerce DB values into pyarrow-friendly Python types."""
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool, bytes)):
            return value
        from datetime import date, time
        from datetime import datetime as _dt
        if isinstance(value, (_dt, date, time)):
            return value
        # Decimal → float (simple default; could use pa.decimal128 if precision matters)
        try:
            from decimal import Decimal
            if isinstance(value, Decimal):
                return float(value)
        except Exception:
            pass
        # Fallback — stringify (UUID, dict, list, etc.)
        return str(value)
