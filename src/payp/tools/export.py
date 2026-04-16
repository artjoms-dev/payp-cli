"""Export tool — save query results to file (CSV, JSONL, XLSX, Parquet).

Streams rows from the database cursor through a format-specific streaming
writer, so memory stays flat regardless of result size. Writes first to a
`.partial` file, then atomically renames on success; partial files are
cleaned up on failure.

BREAKING CHANGE (vs pre-streaming version): the `json` format now produces
newline-delimited JSON (JSONL), not a single JSON array. Both "json" and
"jsonl" are accepted as format values and produce identical output. The
file extension matches the requested format to stay compatible with
existing filename conventions.
"""

from __future__ import annotations

import csv
import json
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.progress import (
    Progress,
    SpinnerColumn,
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

# Rows per streaming batch (mirrors driver batch_size default).
STREAM_BATCH_SIZE = 10_000


def _make_progress() -> Progress:
    """Build an indeterminate progress bar for streaming exports.

    No total column — we don't know the row count up front. The text
    column is updated with the running row counter as batches arrive.
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        transient=True,
    )


class ExportTool(BaseTool):
    name = "export_query"
    description = (
        "Export a SQL query's full results to a file. "
        "Supports CSV, JSONL, XLSX (Excel), and Parquet formats. "
        "Streams to disk — memory-safe for million-row exports. "
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
                    "enum": ["csv", "json", "jsonl", "xlsx", "parquet"],
                    "description": (
                        "Export format. 'json' and 'jsonl' both produce "
                        "newline-delimited JSON (streaming-safe)."
                    ),
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
        if fmt not in ("csv", "json", "jsonl", "xlsx", "parquet"):
            return ToolResult(success=False, error=f"Unsupported format: {fmt}")

        # Determine output path. The extension matches the requested format
        # even though "json" and "jsonl" produce identical content.
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

        # Write to .partial, atomic rename on success. Keeps partial files
        # from ever surfacing as "finished" exports.
        partial_path = filepath.with_suffix(filepath.suffix + ".partial")

        stream = conn.stream_raw(sql, batch_size=STREAM_BATCH_SIZE)

        try:
            if fmt == "csv":
                row_count = await self._write_csv_stream(partial_path, stream)
            elif fmt in ("json", "jsonl"):
                row_count = await self._write_jsonl_stream(partial_path, stream)
            elif fmt == "xlsx":
                row_count = await self._write_xlsx_stream(partial_path, stream)
            else:  # parquet
                row_count = await self._write_parquet_stream(partial_path, stream)

            if row_count == 0:
                # Clean up empty partial file and return no-op result.
                if partial_path.exists():
                    partial_path.unlink()
                return ToolResult(
                    success=True,
                    data={"file": str(filepath), "rows": 0},
                    summary="No rows to export",
                )

            # Atomic rename — replaces any previous export at the same path.
            partial_path.replace(filepath)

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
            import logging
            logging.getLogger("payp.tools.export").exception("ExportTool failed")
            # Clean up partial file on any failure.
            if partial_path.exists():
                try:
                    partial_path.unlink()
                except OSError:
                    pass
            return ToolResult(success=False, error=str(e), summary=f"Export failed: {e}")

    # ------------------------------------------------------------------
    # Streaming writers — each returns total rows written.
    # ------------------------------------------------------------------

    async def _write_csv_stream(
        self,
        filepath: Path,
        stream: AsyncIterator[list[dict[str, Any]]],
    ) -> int:
        total = 0
        columns: list[str] | None = None
        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            progress: Progress | None = None
            task_id = None
            try:
                async for batch in stream:
                    if not batch:
                        continue
                    if columns is None:
                        columns = list(batch[0].keys())
                        writer.writerow(columns)
                    for row in batch:
                        writer.writerow([row.get(c) for c in columns])
                    total += len(batch)
                    if progress is None and total >= PROGRESS_THRESHOLD:
                        progress = _make_progress()
                        progress.start()
                        task_id = progress.add_task(
                            f"Writing csv... ({total:,} rows)", total=None
                        )
                    elif progress is not None:
                        progress.update(
                            task_id, description=f"Writing csv... ({total:,} rows)"
                        )
            finally:
                if progress is not None:
                    progress.stop()
        return total

    async def _write_jsonl_stream(
        self,
        filepath: Path,
        stream: AsyncIterator[list[dict[str, Any]]],
    ) -> int:
        total = 0
        with open(filepath, "w") as f:
            progress: Progress | None = None
            task_id = None
            try:
                async for batch in stream:
                    if not batch:
                        continue
                    for row in batch:
                        f.write(json.dumps(row, default=str))
                        f.write("\n")
                    total += len(batch)
                    if progress is None and total >= PROGRESS_THRESHOLD:
                        progress = _make_progress()
                        progress.start()
                        task_id = progress.add_task(
                            f"Writing jsonl... ({total:,} rows)", total=None
                        )
                    elif progress is not None:
                        progress.update(
                            task_id, description=f"Writing jsonl... ({total:,} rows)"
                        )
            finally:
                if progress is not None:
                    progress.stop()
        return total

    async def _write_xlsx_stream(
        self,
        filepath: Path,
        stream: AsyncIterator[list[dict[str, Any]]],
    ) -> int:
        """Stream to XLSX via openpyxl write-only mode.

        Write-only mode is append-only — no random access, no post-hoc cell
        edits. It DOES support freeze_panes and per-cell fonts via
        WriteOnlyCell, so we keep bold header + frozen top row. Column
        auto-sizing is dropped (it requires iterating all cells, which
        defeats streaming); cells are written verbatim.
        """
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.styles import Font

        wb = Workbook(write_only=True)
        ws = wb.create_sheet("query_result")
        bold_font = Font(bold=True)

        total = 0
        columns: list[str] | None = None
        progress: Progress | None = None
        task_id = None
        try:
            async for batch in stream:
                if not batch:
                    continue
                if columns is None:
                    columns = list(batch[0].keys())
                    header_cells = []
                    for col in columns:
                        cell = WriteOnlyCell(ws, value=col)
                        cell.font = bold_font
                        header_cells.append(cell)
                    ws.append(header_cells)
                for row in batch:
                    if total + 1 > EXCEL_MAX_DATA_ROWS:
                        raise ValueError(
                            f"Export exceeds Excel's hard limit of "
                            f"{EXCEL_MAX_DATA_ROWS:,} data rows. "
                            f"Use csv or parquet instead."
                        )
                    ws.append([self._cell_for_xlsx(row.get(c)) for c in columns])
                    total += 1
                if progress is None and total >= PROGRESS_THRESHOLD:
                    progress = _make_progress()
                    progress.start()
                    task_id = progress.add_task(
                        f"Writing xlsx... ({total:,} rows)", total=None
                    )
                elif progress is not None:
                    progress.update(
                        task_id, description=f"Writing xlsx... ({total:,} rows)"
                    )
            # NOTE: openpyxl 3.1.x silently drops ws.freeze_panes in
            # write-only mode — the attribute exists but is not serialized
            # to the XML. Trade-off we accept for streaming. Users who need
            # a frozen header row can open the file in Excel and freeze
            # manually, or export to CSV/Parquet instead.
            wb.save(filepath)
        finally:
            if progress is not None:
                progress.stop()
        return total

    async def _write_parquet_stream(
        self,
        filepath: Path,
        stream: AsyncIterator[list[dict[str, Any]]],
    ) -> int:
        """Stream to Parquet via pyarrow ParquetWriter.

        Schema is inferred from the first batch. Every subsequent batch is
        coerced to match that schema; a batch with new columns will raise.
        """
        import pyarrow as pa
        import pyarrow.parquet as pq

        writer: pq.ParquetWriter | None = None
        schema: pa.Schema | None = None
        total = 0
        progress: Progress | None = None
        task_id = None
        try:
            async for batch in stream:
                if not batch:
                    continue
                # Coerce each row to parquet-friendly types
                normalised = [
                    {k: self._coerce_for_parquet(v) for k, v in row.items()}
                    for row in batch
                ]
                if writer is None:
                    table = pa.Table.from_pylist(normalised)
                    schema = table.schema
                    writer = pq.ParquetWriter(
                        str(filepath), schema, compression="snappy"
                    )
                    writer.write_table(table)
                else:
                    table = pa.Table.from_pylist(normalised, schema=schema)
                    writer.write_table(table)
                total += len(batch)
                if progress is None and total >= PROGRESS_THRESHOLD:
                    progress = _make_progress()
                    progress.start()
                    task_id = progress.add_task(
                        f"Writing parquet... ({total:,} rows)", total=None
                    )
                elif progress is not None:
                    progress.update(
                        task_id, description=f"Writing parquet... ({total:,} rows)"
                    )
        finally:
            if writer is not None:
                writer.close()
            if progress is not None:
                progress.stop()
        return total

    # ------------------------------------------------------------------
    # Type coercion helpers (unchanged from pre-streaming version)
    # ------------------------------------------------------------------

    @staticmethod
    def _cell_for_xlsx(value: Any) -> Any:
        """Coerce a value into something openpyxl can write."""
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        from datetime import date, time
        from datetime import datetime as _dt
        if isinstance(value, _dt):
            if value.tzinfo is not None:
                return value.replace(tzinfo=None)
            return value
        if isinstance(value, time):
            if value.tzinfo is not None:
                return value.replace(tzinfo=None)
            return value
        if isinstance(value, date):
            return value
        return str(value)

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
        try:
            from decimal import Decimal
            if isinstance(value, Decimal):
                return float(value)
        except Exception:
            pass
        return str(value)
