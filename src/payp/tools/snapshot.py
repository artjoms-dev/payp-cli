"""Snapshot tools — save, list, restore, and delete snapshot backups.

Snapshots are JSONL backup files created before DELETE/UPDATE operations.
They are the safety net that LLM memory cannot provide.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from hashlib import md5
from pathlib import Path
from typing import Any

from payp.tools.base import BaseTool, ToolResult


class SnapshotBeforeDeleteTool(BaseTool):
    name = "snapshot_before_delete"
    description = (
        "REQUIRED before any DELETE or UPDATE operation. "
        "Saves all affected rows to a JSONL backup file so they can be restored later. "
        "Pass the same WHERE clause that will be used in the DELETE/UPDATE. "
        "Returns the snapshot file path and row count."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "Table name (e.g., 'customers')",
                },
                "where_clause": {
                    "type": "string",
                    "description": "WHERE clause matching the DELETE/UPDATE (without the WHERE keyword). Use empty string for all rows.",
                },
                "operation": {
                    "type": "string",
                    "enum": ["DELETE", "UPDATE"],
                    "description": "The operation type that will follow",
                },
            },
            "required": ["table", "operation"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.db.connection import ConnectionManager

        conn: ConnectionManager | None = context.get("connection_manager")
        if not conn or not conn.is_connected:
            return ToolResult(success=False, error="Not connected to a database")

        table = args.get("table", "").strip()
        where = args.get("where_clause", "").strip()
        operation = args.get("operation", "DELETE")

        if not table:
            return ToolResult(success=False, error="No table specified")

        # Build SELECT to capture affected rows
        sql = f"SELECT * FROM {table}"
        if where:
            sql += f" WHERE {where}"

        try:
            # Get all affected rows (no limit for snapshot)
            rows = await conn.execute_raw(sql)

            if not rows:
                return ToolResult(
                    success=True,
                    data={"row_count": 0, "file": None},
                    summary=f"No rows match the condition — nothing to snapshot",
                )

            # Check size limit
            row_count = len(rows)
            if row_count > 10000:
                return ToolResult(
                    success=False,
                    error=f"Too many rows to snapshot ({row_count:,}). Max is 10,000. "
                          f"Consider adding a WHERE clause to limit scope.",
                    summary=f"Snapshot too large: {row_count:,} rows",
                )

            # Save to JSONL file
            snapshot_dir = Path("./payp/snapshots")
            snapshot_dir.mkdir(parents=True, exist_ok=True)

            date_str = datetime.now().strftime("%Y-%m-%d")
            hash_str = md5(f"{table}{where}{datetime.now().isoformat()}".encode()).hexdigest()[:4]
            filename = f"{date_str}_{table}_{operation.lower()}_{hash_str}.jsonl"
            filepath = snapshot_dir / filename

            # Write JSONL
            with open(filepath, "w") as f:
                # Metadata line
                meta = {
                    "_payp_meta": {
                        "table": table,
                        "operation": operation,
                        "where": where or "(all rows)",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "connection": conn.profile.name,
                        "row_count": row_count,
                    }
                }
                f.write(json.dumps(meta, default=str) + "\n")

                # Data rows
                for row in rows:
                    f.write(json.dumps(row, default=str) + "\n")

            file_size = os.path.getsize(filepath)
            size_str = f"{file_size / 1024:.1f} KB" if file_size < 1024 * 1024 else f"{file_size / 1024 / 1024:.1f} MB"

            return ToolResult(
                success=True,
                data={
                    "row_count": row_count,
                    "file": str(filepath),
                    "size": size_str,
                    "table": table,
                },
                summary=f"Snapshot saved: {row_count} rows from {table} → {filepath} ({size_str})",
            )

        except Exception as e:
            return ToolResult(success=False, error=str(e), summary=f"Snapshot failed: {e}")


class RestoreSnapshotTool(BaseTool):
    name = "restore_snapshot"
    description = (
        "Restore data from a snapshot JSONL file created by snapshot_before_delete. "
        "Reads the file and generates INSERT statements to restore the deleted rows."
    )
    is_read_only = False

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Path to the snapshot JSONL file (e.g., './payp/snapshots/2026-04-03_customers_delete_a3f2.jsonl')",
                },
            },
            "required": ["file"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.db.connection import ConnectionManager

        conn: ConnectionManager | None = context.get("connection_manager")
        if not conn or not conn.is_connected:
            return ToolResult(success=False, error="Not connected to a database")

        filepath = Path(args.get("file", ""))
        if not filepath.exists():
            # Try listing available snapshots
            snapshot_dir = Path("./payp/snapshots")
            if snapshot_dir.exists():
                files = sorted(snapshot_dir.glob("*.jsonl"), reverse=True)
                available = [str(f) for f in files[:10]]
                return ToolResult(
                    success=False,
                    error=f"File not found: {filepath}. Available snapshots: {', '.join(available)}",
                )
            return ToolResult(success=False, error=f"File not found: {filepath}")

        try:
            lines = filepath.read_text().strip().split("\n")
            if not lines:
                return ToolResult(success=False, error="Snapshot file is empty")

            # Parse metadata
            meta = json.loads(lines[0])
            meta_info = meta.get("_payp_meta", {})
            table = meta_info.get("table", "unknown")

            # Parse data rows
            data_rows = [json.loads(line) for line in lines[1:]]
            if not data_rows:
                return ToolResult(success=True, data={"restored": 0}, summary="No data rows in snapshot")

            # Build and execute INSERT statements in batches
            columns = list(data_rows[0].keys())
            total_restored = 0
            batch_size = 100

            for i in range(0, len(data_rows), batch_size):
                batch = data_rows[i:i + batch_size]
                values_list = []
                for row in batch:
                    vals = []
                    for col in columns:
                        v = row.get(col)
                        if v is None:
                            vals.append("NULL")
                        elif isinstance(v, (int, float)):
                            vals.append(str(v))
                        elif isinstance(v, bool):
                            vals.append("TRUE" if v else "FALSE")
                        else:
                            escaped = str(v).replace("'", "''")
                            vals.append(f"'{escaped}'")
                    values_list.append(f"({', '.join(vals)})")

                sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES\n" + ",\n".join(values_list) + ";"
                await conn.execute_raw(sql)
                total_restored += len(batch)

            return ToolResult(
                success=True,
                data={"restored": total_restored, "table": table, "file": str(filepath)},
                summary=f"Restored {total_restored} rows to {table} from {filepath}",
            )

        except Exception as e:
            return ToolResult(success=False, error=str(e), summary=f"Restore failed: {e}")


class ListSnapshotsTool(BaseTool):
    name = "list_snapshots"
    description = (
        "List all snapshot backup files in ./payp/snapshots/. "
        "Returns file paths, tables, operations, row counts, timestamps. "
        "Use this when the user asks about their snapshots or wants to clean up."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filter_table": {
                    "type": "string",
                    "description": "Optional: only show snapshots for this table name",
                },
                "filter_operation": {
                    "type": "string",
                    "enum": ["DELETE", "UPDATE"],
                    "description": "Optional: only show snapshots for this operation type",
                },
            },
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.storage.snapshots import format_snapshot_age, list_snapshots

        snapshots = list_snapshots()
        filter_table = args.get("filter_table", "").strip()
        filter_op = args.get("filter_operation", "").strip()

        if filter_table:
            snapshots = [s for s in snapshots if s["table"] == filter_table]
        if filter_op:
            snapshots = [s for s in snapshots if s["operation"] == filter_op]

        if not snapshots:
            return ToolResult(
                success=True,
                data={"snapshots": [], "count": 0},
                summary="No snapshots found",
            )

        # Enrich with age
        for s in snapshots:
            s["age"] = format_snapshot_age(s["timestamp"])

        return ToolResult(
            success=True,
            data={"snapshots": snapshots, "count": len(snapshots)},
            summary=f"{len(snapshots)} snapshot(s) found",
        )


class DeleteSnapshotTool(BaseTool):
    name = "delete_snapshot"
    description = (
        "Delete one or more snapshot backup files. "
        "Pass the file path(s) returned by list_snapshots. "
        "This permanently deletes the backup files — warn the user first."
    )
    is_read_only = False
    is_destructive = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Snapshot file paths to delete (from list_snapshots output)",
                },
            },
            "required": ["files"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.storage.snapshots import delete_snapshot

        files = args.get("files", [])
        if not files:
            return ToolResult(success=False, error="No files specified")

        deleted = []
        failed = []
        for filepath in files:
            if delete_snapshot(filepath):
                deleted.append(filepath)
            else:
                failed.append(filepath)

        summary = f"Deleted {len(deleted)} snapshot(s)"
        if failed:
            summary += f", {len(failed)} failed"

        return ToolResult(
            success=len(failed) == 0,
            data={"deleted": deleted, "failed": failed},
            summary=summary,
        )
