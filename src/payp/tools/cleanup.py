"""Universal cleanup tool — unified destructive maintenance for payp.

The LLM calls this tool when the user asks to clean up sessions, queries,
cached schemas, snapshots, or exports. The tool:

1. Computes a preview of what WOULD be deleted (via `preview()`).
2. The chat dispatcher shows that preview in an approval panel (unless
   mode == YOLO).
3. On approval, `call()` actually performs the deletion.

Because the tool is marked `is_destructive = True`, it is routed through
`_execute_destructive_with_approval` in chat.py automatically — the LLM
cannot bypass the user confirmation step outside of YOLO mode.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from payp.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

VALID_TARGETS = (
    "sessions",
    "queries",
    "cache",
    "snapshots",
    "exports",
    "legacy_knowledge",
)


class CleanupTool(BaseTool):
    name = "cleanup"
    description = (
        "Delete stored payp artifacts. Targets:\n"
        "  - sessions: chat session history (JSONL files)\n"
        "  - queries: saved SQL from the query library\n"
        "  - cache: T0/T1/T2 schema cache for a connection\n"
        "  - snapshots: pre-DML row backups\n"
        "  - exports: CSV/Parquet files in ./exports/\n"
        "  - legacy_knowledge: the old project-local ./payp/knowledge/ directory "
        "that exists on upgraded installs (use this when the user asks to remove "
        "'legacy knowledge', 'old knowledge dir', or acts on the legacy banner)\n\n"
        "Common filters: empty (sessions only), all, older_than_days, keep_last, "
        "connection (cache), tag (queries). "
        "This tool ALWAYS shows the user what will be deleted and waits for "
        "confirmation before executing (except in YOLO mode). Use it when the "
        "user asks to clean up, purge, remove old, or free space."
    )
    is_read_only = False
    is_destructive = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": list(VALID_TARGETS),
                    "description": "What to clean up.",
                },
                "empty": {
                    "type": "boolean",
                    "description": "(sessions) Only remove sessions with no user messages and no executed queries. Default true when target=sessions and no other filter is given.",
                },
                "all": {
                    "type": "boolean",
                    "description": "Remove ALL items of the target (except protected ones like the active session). Use when the user says 'all', 'everything', 'wipe', or 'clear'. This is the most destructive option — the approval panel will show every affected item.",
                },
                "older_than_days": {
                    "type": "integer",
                    "description": "Delete items whose mtime is older than N days. Applies to sessions, snapshots, exports. Pass 0 to match any age (combine with keep_last as a safety net).",
                },
                "keep_last": {
                    "type": "integer",
                    "description": "Always preserve the N most-recent items (hard guard, overrides other filters).",
                },
                "connection": {
                    "type": "string",
                    "description": "(cache) Limit to a specific connection name. Omit to clean cache for all connections.",
                },
                "tag": {
                    "type": "string",
                    "description": "(queries) Only delete queries matching this tag/name/description substring.",
                },
                "force": {
                    "type": "boolean",
                    "description": "(legacy_knowledge) Skip the 'migration must be done first' safety check. Only set if the user explicitly says they want to delete without migrating.",
                },
                "reason": {
                    "type": "string",
                    "description": "Short human-readable explanation of WHY you're cleaning. Shown to the user in the approval panel.",
                },
            },
            "required": ["target"],
        }

    # ------------------------------------------------------------------
    # Preview (dry-run) — used by chat approval wrapper
    # ------------------------------------------------------------------

    async def preview(
        self, args: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any] | None:
        target = (args.get("target") or "").strip().lower()
        if target not in VALID_TARGETS:
            return {
                "summary": f"Invalid target '{target}'. Must be one of: {', '.join(VALID_TARGETS)}",
                "items": [],
                "warning": None,
            }

        if target == "sessions":
            return self._preview_sessions(args, context)
        if target == "queries":
            return self._preview_queries(args)
        if target == "cache":
            return self._preview_cache(args)
        if target == "snapshots":
            return self._preview_snapshots(args)
        if target == "exports":
            return self._preview_exports(args)
        if target == "legacy_knowledge":
            return self._preview_legacy_knowledge(args)
        return None

    # ------------------------------------------------------------------
    # Execute — called after user approval
    # ------------------------------------------------------------------

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        target = (args.get("target") or "").strip().lower()
        if target not in VALID_TARGETS:
            return ToolResult(
                success=False,
                error=f"Invalid target '{target}'. Must be one of: {', '.join(VALID_TARGETS)}",
            )

        try:
            if target == "sessions":
                result = self._clean_sessions(args, context)
            elif target == "queries":
                result = self._clean_queries(args)
            elif target == "cache":
                result = self._clean_cache(args)
            elif target == "snapshots":
                result = self._clean_snapshots(args)
            elif target == "exports":
                result = self._clean_exports(args)
            elif target == "legacy_knowledge":
                result = self._clean_legacy_knowledge(args)
            else:
                return ToolResult(success=False, error=f"Unsupported target: {target}")
        except Exception as e:
            logger.exception("cleanup_tool failed")
            return ToolResult(success=False, error=f"{target} cleanup failed: {e}")

        n = len(result.get("deleted", []))
        return ToolResult(
            success=True,
            data=result,
            summary=f"Cleaned {n} {target} item(s)",
        )

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def _preview_sessions(
        self, args: dict[str, Any], context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        from payp.storage.sessions import is_empty_session, list_sessions

        sessions = list_sessions()
        all_flag = args.get("all", False)
        older_than = args.get("older_than_days")
        keep_last = args.get("keep_last")

        # Default empty-only filter only applies if no other filter is specified
        if "empty" in args:
            empty_only = args["empty"]
        else:
            empty_only = not all_flag and older_than is None

        # Protect the active session if we have chat context
        protected: set[str] = set()
        if context:
            chat = context.get("chat_session") or context.get("chat")
            if chat and getattr(chat, "session_file", None):
                protected.add(str(chat.session_file))
        if keep_last is not None and keep_last > 0:
            for s in sessions[:keep_last]:
                protected.add(s["file"])

        cutoff = time.time() - older_than * 86400 if older_than is not None else None

        candidates: list[str] = []
        for s in sessions:
            if s["file"] in protected:
                continue
            match = False
            if all_flag:
                match = True
            if empty_only and is_empty_session(s):
                match = True
            if cutoff is not None and s.get("mtime", time.time()) < cutoff:
                match = True
            if match:
                candidates.append(s["filename"])

        filter_desc = self._describe_session_filters(
            empty_only, older_than, keep_last, all_flag
        )
        # Note about active session protection (shown in panel)
        warning_parts = []
        if candidates:
            warning_parts.append("Deleted sessions cannot be recovered from /resume.")
        if all_flag:
            warning_parts.append("The active session will be preserved automatically.")
        return {
            "summary": f"Would remove {len(candidates)} of {len(sessions)} sessions {filter_desc}",
            "items": candidates[:20],
            "truncated": len(candidates) > 20,
            "total": len(candidates),
            "warning": " ".join(warning_parts) if warning_parts else None,
        }

    def _clean_sessions(self, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        from payp.storage.sessions import clean_sessions

        # Protect the active session if chat is running
        current_file = None
        chat = context.get("chat_session") or context.get("chat")
        if chat and getattr(chat, "session_file", None):
            current_file = str(chat.session_file)

        all_flag = args.get("all", False)
        older_than = args.get("older_than_days")
        # Figure out empty default same as preview
        if "empty" in args:
            empty_only = args["empty"]
        else:
            empty_only = not all_flag and older_than is None

        # When all=True, treat it as "any age" by passing older_than_days=0
        effective_older_than = 0 if all_flag else older_than

        return clean_sessions(
            empty=empty_only,
            older_than_days=effective_older_than,
            keep_last=args.get("keep_last"),
            keep_current=current_file,
        )

    def _describe_session_filters(
        self,
        empty_only: bool,
        older_than: int | None,
        keep_last: int | None,
        all_flag: bool = False,
    ) -> str:
        parts = []
        if all_flag:
            parts.append("ALL")
        if empty_only and not all_flag:
            parts.append("empty")
        if older_than is not None:
            parts.append(f"older than {older_than}d" if older_than > 0 else "any age")
        if keep_last is not None:
            parts.append(f"keeping {keep_last} newest")
        return f"({', '.join(parts)})" if parts else ""

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def _preview_queries(self, args: dict[str, Any]) -> dict[str, Any]:
        from payp.storage.queries import list_queries

        all_flag = args.get("all", False)
        tag = args.get("tag", "") or ""
        older_than = args.get("older_than_days")
        keep_last = args.get("keep_last")

        all_queries = list_queries()
        all_queries.sort(
            key=lambda q: Path(q.get("file", "")).stat().st_mtime
            if q.get("file") and Path(q["file"]).exists()
            else 0,
            reverse=True,
        )

        protected: set[str] = set()
        if keep_last is not None and keep_last > 0:
            for q in all_queries[:keep_last]:
                protected.add(q["name"])

        cutoff = time.time() - older_than * 86400 if older_than is not None else None

        candidates: list[str] = []
        for q in all_queries:
            if q["name"] in protected:
                continue
            match = False
            if all_flag:
                match = True
            if tag:
                tag_lower = tag.lower()
                if (
                    any(tag_lower in t.lower() for t in q.get("tags", []))
                    or tag_lower in q.get("name", "").lower()
                    or tag_lower in q.get("description", "").lower()
                ):
                    match = True
            if cutoff is not None and q.get("file"):
                try:
                    if Path(q["file"]).stat().st_mtime < cutoff:
                        match = True
                except OSError:
                    pass
            if not all_flag and not tag and older_than is None and keep_last is None:
                continue
            if match:
                candidates.append(q["name"])

        filter_desc = []
        if all_flag:
            filter_desc.append("ALL")
        if tag:
            filter_desc.append(f"tag~'{tag}'")
        if older_than is not None:
            filter_desc.append(f"older than {older_than}d" if older_than > 0 else "any age")
        if keep_last is not None:
            filter_desc.append(f"keeping {keep_last} newest")
        filter_str = (
            f"({', '.join(filter_desc)})"
            if filter_desc
            else "(no filters — nothing matches)"
        )

        return {
            "summary": f"Would remove {len(candidates)} of {len(all_queries)} queries {filter_str}",
            "items": candidates[:20],
            "truncated": len(candidates) > 20,
            "total": len(candidates),
            "warning": "Deleted queries cannot be recovered." if candidates else None,
        }

    def _clean_queries(self, args: dict[str, Any]) -> dict[str, Any]:
        from payp.storage.queries import delete_query, list_queries

        all_flag = args.get("all", False)
        tag = args.get("tag", "") or ""
        older_than = args.get("older_than_days")
        keep_last = args.get("keep_last")

        all_queries = list_queries()
        all_queries.sort(
            key=lambda q: Path(q.get("file", "")).stat().st_mtime
            if q.get("file") and Path(q["file"]).exists()
            else 0,
            reverse=True,
        )

        protected: set[str] = set()
        if keep_last is not None and keep_last > 0:
            for q in all_queries[:keep_last]:
                protected.add(q["name"])

        cutoff = time.time() - older_than * 86400 if older_than is not None else None
        deleted: list[str] = []
        for q in all_queries:
            if q["name"] in protected:
                continue
            match = False
            if all_flag:
                match = True
            if tag:
                tag_lower = tag.lower()
                if (
                    any(tag_lower in t.lower() for t in q.get("tags", []))
                    or tag_lower in q.get("name", "").lower()
                    or tag_lower in q.get("description", "").lower()
                ):
                    match = True
            if cutoff is not None and q.get("file"):
                try:
                    if Path(q["file"]).stat().st_mtime < cutoff:
                        match = True
                except OSError:
                    pass
            if not all_flag and not tag and older_than is None and keep_last is None:
                continue
            if match and delete_query(q["name"]):
                deleted.append(q["name"])

        return {"deleted": deleted, "kept": len(all_queries) - len(deleted), "total_before": len(all_queries)}

    # ------------------------------------------------------------------
    # Cache (T0/T1/T2 schema cache)
    # ------------------------------------------------------------------

    def _preview_cache(self, args: dict[str, Any]) -> dict[str, Any]:
        from payp.config import global_dir

        cache_dir = global_dir() / "cache"
        if not cache_dir.exists():
            return {"summary": "No cache directory.", "items": [], "total": 0}

        connection = (args.get("connection") or "").strip()
        items: list[str] = []
        total_bytes = 0

        if connection:
            # Match files and subdirs prefixed with {connection}_
            for entry in sorted(cache_dir.iterdir()):
                if not entry.name.startswith(f"{connection}_"):
                    continue
                items.append(entry.name)
                total_bytes += self._path_size(entry)
        else:
            for entry in sorted(cache_dir.iterdir()):
                items.append(entry.name)
                total_bytes += self._path_size(entry)

        size_str = self._fmt_bytes(total_bytes)
        scope = f"for '{connection}'" if connection else "for ALL connections"
        return {
            "summary": f"Would remove {len(items)} cache entries {scope} ({size_str})",
            "items": items[:20],
            "truncated": len(items) > 20,
            "total": len(items),
            "warning": (
                "Next query will re-introspect the schema — slower first call."
                if items
                else None
            ),
        }

    def _clean_cache(self, args: dict[str, Any]) -> dict[str, Any]:
        import shutil

        from payp.config import global_dir

        cache_dir = global_dir() / "cache"
        if not cache_dir.exists():
            return {"deleted": [], "kept": 0, "total_before": 0}

        connection = (args.get("connection") or "").strip()
        deleted: list[str] = []
        for entry in list(cache_dir.iterdir()):
            if connection and not entry.name.startswith(f"{connection}_"):
                continue
            try:
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                deleted.append(entry.name)
            except Exception:
                pass
        return {"deleted": deleted, "kept": 0, "total_before": len(deleted)}

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def _preview_snapshots(self, args: dict[str, Any]) -> dict[str, Any]:
        from payp.storage.snapshots import list_snapshots

        snapshots = list_snapshots()
        all_flag = args.get("all", False)
        older_than = args.get("older_than_days")
        keep_last = args.get("keep_last")

        protected: set[str] = set()
        if keep_last is not None and keep_last > 0:
            for s in snapshots[:keep_last]:
                protected.add(s["file"])

        cutoff = time.time() - older_than * 86400 if older_than is not None else None

        candidates: list[str] = []
        for s in snapshots:
            if s["file"] in protected:
                continue
            match = False
            if all_flag:
                match = True
            if cutoff is not None:
                try:
                    if Path(s["file"]).stat().st_mtime < cutoff:
                        match = True
                except OSError:
                    pass
            if not all_flag and older_than is None and keep_last is None:
                # User must specify a filter for snapshots (they're valuable)
                continue
            if match:
                candidates.append(s["filename"])

        filter_desc = []
        if all_flag:
            filter_desc.append("ALL")
        if older_than is not None:
            filter_desc.append(f"older than {older_than}d" if older_than > 0 else "any age")
        if keep_last is not None:
            filter_desc.append(f"keeping {keep_last} newest")
        filter_str = (
            f"({', '.join(filter_desc)})"
            if filter_desc
            else "(no filters — nothing matches; set older_than_days, keep_last, or all)"
        )

        return {
            "summary": f"Would remove {len(candidates)} of {len(snapshots)} snapshots {filter_str}",
            "items": candidates[:20],
            "truncated": len(candidates) > 20,
            "total": len(candidates),
            "warning": (
                "Snapshots are rollback insurance — deleting them removes your ability to restore pre-DML state."
                if candidates
                else None
            ),
        }

    def _clean_snapshots(self, args: dict[str, Any]) -> dict[str, Any]:
        from payp.storage.snapshots import delete_snapshot, list_snapshots

        snapshots = list_snapshots()
        all_flag = args.get("all", False)
        older_than = args.get("older_than_days")
        keep_last = args.get("keep_last")
        if not all_flag and older_than is None and keep_last is None:
            return {"deleted": [], "kept": len(snapshots), "total_before": len(snapshots)}

        protected: set[str] = set()
        if keep_last is not None and keep_last > 0:
            for s in snapshots[:keep_last]:
                protected.add(s["file"])

        cutoff = time.time() - older_than * 86400 if older_than is not None else None
        deleted: list[str] = []
        for s in snapshots:
            if s["file"] in protected:
                continue
            should_delete = False
            if all_flag:
                should_delete = True
            if cutoff is not None:
                try:
                    if Path(s["file"]).stat().st_mtime < cutoff:
                        should_delete = True
                except OSError:
                    pass
            if should_delete and delete_snapshot(s["file"]):
                deleted.append(s["filename"])
        return {"deleted": deleted, "kept": len(snapshots) - len(deleted), "total_before": len(snapshots)}

    # ------------------------------------------------------------------
    # Exports
    # ------------------------------------------------------------------

    def _preview_exports(self, args: dict[str, Any]) -> dict[str, Any]:
        exports_dir = Path("./exports")
        if not exports_dir.exists():
            return {"summary": "No ./exports/ directory.", "items": [], "total": 0}

        all_flag = args.get("all", False)
        older_than = args.get("older_than_days")
        cutoff = time.time() - older_than * 86400 if older_than is not None else None

        candidates: list[str] = []
        total_bytes = 0
        files = sorted(exports_dir.rglob("*"))
        for f in files:
            if not f.is_file():
                continue
            match = False
            if all_flag:
                match = True
            if cutoff is not None and f.stat().st_mtime < cutoff:
                match = True
            if not match:
                continue
            candidates.append(str(f.relative_to(exports_dir)))
            total_bytes += f.stat().st_size

        size_str = self._fmt_bytes(total_bytes)
        if all_flag:
            filter_str = "(ALL)"
        elif older_than is None:
            filter_str = "(set older_than_days or all=true to filter)"
        elif older_than > 0:
            filter_str = f"(older than {older_than}d)"
        else:
            filter_str = "(any age)"
        return {
            "summary": f"Would remove {len(candidates)} export files {filter_str} ({size_str})",
            "items": candidates[:20],
            "truncated": len(candidates) > 20,
            "total": len(candidates),
            "warning": None,
        }

    def _clean_exports(self, args: dict[str, Any]) -> dict[str, Any]:
        exports_dir = Path("./exports")
        if not exports_dir.exists():
            return {"deleted": [], "kept": 0, "total_before": 0}

        all_flag = args.get("all", False)
        older_than = args.get("older_than_days")
        if not all_flag and older_than is None:
            return {"deleted": [], "kept": 0, "total_before": 0}

        cutoff = time.time() - older_than * 86400 if older_than is not None else None
        deleted: list[str] = []
        total = 0
        for f in sorted(exports_dir.rglob("*")):
            if not f.is_file():
                continue
            total += 1
            should_delete = False
            if all_flag:
                should_delete = True
            if cutoff is not None and f.stat().st_mtime < cutoff:
                should_delete = True
            if should_delete:
                try:
                    f.unlink()
                    deleted.append(str(f.relative_to(exports_dir)))
                except OSError:
                    pass
        return {"deleted": deleted, "kept": total - len(deleted), "total_before": total}

    # ------------------------------------------------------------------
    # Legacy knowledge (pre-global-refactor ./payp/knowledge/)
    # ------------------------------------------------------------------

    def _legacy_knowledge_state(self) -> dict[str, Any]:
        """Inspect both legacy and global knowledge dirs. Returns:

            {
              "legacy_dir": Path,
              "legacy_files": [<Path>, ...],
              "global_dir": Path,
              "global_file_count": <int>,
              "migration_done": <bool>,  # True if global has any knowledge
            }
        """
        from payp.storage.knowledge import (
            LEGACY_KNOWLEDGE_DIR,
            get_knowledge_dir,
            list_knowledge_files,
        )

        global_dir_path = get_knowledge_dir()
        legacy_files: list[Path] = []
        if LEGACY_KNOWLEDGE_DIR.exists():
            legacy_files = sorted(LEGACY_KNOWLEDGE_DIR.rglob("*.md"))

        global_files = list_knowledge_files() if global_dir_path.exists() else []

        return {
            "legacy_dir": LEGACY_KNOWLEDGE_DIR,
            "legacy_files": legacy_files,
            "global_dir": global_dir_path,
            "global_file_count": len(global_files),
            "migration_done": len(global_files) > 0,
        }

    def _preview_legacy_knowledge(self, args: dict[str, Any]) -> dict[str, Any]:
        state = self._legacy_knowledge_state()
        legacy_files = state["legacy_files"]

        if not legacy_files:
            return {
                "summary": "No legacy ./payp/knowledge/ directory found — nothing to clean.",
                "items": [],
                "total": 0,
                "warning": None,
            }

        total_bytes = sum(
            f.stat().st_size for f in legacy_files if f.is_file()
        )
        rels = [str(f.relative_to(state["legacy_dir"])) for f in legacy_files]

        warning: str | None = None
        if not state["migration_done"]:
            warning = (
                "Global knowledge dir is EMPTY — you have not migrated yet! "
                "Run /knowledge migrate-legacy FIRST, or you will lose this data."
            )
        else:
            warning = (
                f"Global knowledge already has {state['global_file_count']} file(s). "
                "Deleting the legacy copy is safe."
            )

        return {
            "summary": (
                f"Would remove legacy ./payp/knowledge/ "
                f"({len(legacy_files)} files, {self._fmt_bytes(total_bytes)})"
            ),
            "items": rels[:20],
            "truncated": len(rels) > 20,
            "total": len(rels),
            "warning": warning,
        }

    def _clean_legacy_knowledge(self, args: dict[str, Any]) -> dict[str, Any]:
        import shutil

        state = self._legacy_knowledge_state()
        legacy_dir: Path = state["legacy_dir"]

        if not legacy_dir.exists():
            return {
                "deleted": [],
                "kept": 0,
                "total_before": 0,
                "note": "legacy dir does not exist",
            }

        # Safety: refuse to delete if migration hasn't happened AND user
        # didn't pass force=True.
        if not state["migration_done"] and not args.get("force"):
            return {
                "deleted": [],
                "kept": len(state["legacy_files"]),
                "total_before": len(state["legacy_files"]),
                "error": (
                    "Refusing to delete: global knowledge dir is empty. "
                    "Run /knowledge migrate-legacy first, or pass force=true."
                ),
            }

        deleted = [str(f.relative_to(legacy_dir)) for f in state["legacy_files"]]
        try:
            shutil.rmtree(legacy_dir)
        except Exception as e:
            return {
                "deleted": [],
                "kept": len(deleted),
                "total_before": len(deleted),
                "error": f"rmtree failed: {e}",
            }

        return {
            "deleted": deleted,
            "kept": 0,
            "total_before": len(deleted),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _path_size(p: Path) -> int:
        if p.is_file():
            try:
                return p.stat().st_size
            except OSError:
                return 0
        if p.is_dir():
            total = 0
            for f in p.rglob("*"):
                if f.is_file():
                    try:
                        total += f.stat().st_size
                    except OSError:
                        pass
            return total
        return 0

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        if n < 1024:
            return f"{n}B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f}KB"
        return f"{n / 1024 / 1024:.1f}MB"
