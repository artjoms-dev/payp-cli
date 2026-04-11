"""Session persistence — save conversation history as JSONL files.

Each session is one JSONL file in ~/.payp/sessions/.
Format: one JSON object per line with timestamp, role, content, etc.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex
from typing import Any

from payp.config import global_dir


def sessions_dir() -> Path:
    """Return the sessions directory, creating it if needed."""
    d = global_dir() / "sessions"
    d.mkdir(exist_ok=True)
    return d


class SessionWriter:
    """Writes session events to a JSONL file."""

    def __init__(self, connection_name: str = "no-db") -> None:
        date_str = datetime.now().strftime("%Y-%m-%d")
        hex_str = token_hex(2)  # 4 hex chars
        self._connection_name = connection_name
        self.filename = f"{date_str}_{connection_name}_{hex_str}.jsonl"
        self.path = sessions_dir() / self.filename
        self.session_id = f"{date_str}_{hex_str}"
        self._ensure_file()

    def _ensure_file(self) -> None:
        """Create the session file with initial metadata."""
        if not self.path.exists():
            self._write_event({
                "event": "session_start",
                "role": "system",
                "session_id": self.session_id,
            })
            # Persist the active connection explicitly so /resume can
            # auto-reconnect even if the session never executed a query.
            if self._connection_name and self._connection_name != "no-db":
                self._write_event({
                    "event": "db_connected",
                    "role": "system",
                    "connection": self._connection_name,
                })

    def log_db_connected(self, connection_name: str) -> None:
        """Record that the user connected (or switched) to a DB.

        Called when the active connection changes mid-session so /resume
        can land on the most recent connection.
        """
        if not connection_name or connection_name == "no-db":
            return
        self._write_event({
            "event": "db_connected",
            "role": "system",
            "connection": connection_name,
        })

    def log_memory_backend(self, backend_name: str) -> None:
        """Record which memory backend is active (native, mempalace, ...).

        Called at session start and every /memory switch so /resume can
        restore the same backend the user was using previously.
        """
        if not backend_name:
            return
        self._write_event({
            "event": "memory_backend",
            "role": "system",
            "backend": backend_name,
        })

    def _write_event(self, event: dict[str, Any]) -> None:
        """Append one event to the JSONL file."""
        event.setdefault("ts", datetime.now(UTC).isoformat())
        with open(self.path, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")

    def log_user(self, content: str) -> None:
        """Log a user message."""
        self._write_event({"role": "user", "content": content})

    def log_assistant(self, content: str, model: str | None = None) -> None:
        """Log an assistant message."""
        event = {"role": "assistant", "content": content}
        if model:
            event["model"] = model
        self._write_event(event)

    def log_sql(
        self,
        sql: str,
        connection: str,
        mode: str,
        approved: bool,
        reverse_sql: str | None = None,
    ) -> None:
        """Log a SQL statement that was generated/approved."""
        event: dict[str, Any] = {
            "event": "sql_generated",
            "role": "system",
            "sql": sql,
            "connection": connection,
            "mode": mode,
            "approved": approved,
        }
        if reverse_sql:
            event["reverse_sql"] = reverse_sql
        self._write_event(event)

    def log_query_executed(
        self,
        connection: str,
        rows: int,
        ms: int,
        status: str = "success",
        error: str | None = None,
    ) -> None:
        """Log a query execution result."""
        event: dict[str, Any] = {
            "event": "query_executed",
            "role": "system",
            "connection": connection,
            "rows": rows,
            "ms": ms,
            "status": status,
        }
        if error:
            event["error"] = error
        self._write_event(event)

    def log_tool_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        success: bool,
        summary: str,
    ) -> None:
        """Log a tool call."""
        self._write_event({
            "event": "tool_call",
            "role": "system",
            "tool": tool_name,
            "args": args,
            "success": success,
            "summary": summary,
        })


def list_sessions() -> list[dict[str, Any]]:
    """List all saved sessions, newest first.

    Each entry includes a cheap "user_messages" count so callers can filter
    stubs/empty sessions without a second file-read pass.
    """
    d = sessions_dir()
    sessions = []
    for f in sorted(d.glob("*.jsonl"), reverse=True):
        try:
            with open(f) as fp:
                lines = fp.readlines()
            event_count = len(lines)
            query_count = sum(1 for line in lines if '"event": "query_executed"' in line)
            # Substring check is enough — role:user lines always contain it.
            user_msg_count = sum(
                1 for line in lines if '"role": "user"' in line or '"role":"user"' in line
            )

            parts = f.stem.split("_")
            date_part = parts[0] if len(parts) >= 1 else "?"
            conn_part = parts[1] if len(parts) >= 2 else "?"

            stat = f.stat()
            sessions.append({
                "file": str(f),
                "filename": f.name,
                "date": date_part,
                "connection": conn_part,
                "events": event_count,
                "queries": query_count,
                "user_messages": user_msg_count,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
        except Exception:
            pass
    return sessions


def is_empty_session(session: dict[str, Any]) -> bool:
    """A session is 'empty' if no user messages AND no executed queries.

    This catches launch-then-quit stubs regardless of how many internal
    events (startup, connect, etc.) they contain.
    """
    return (session.get("user_messages", 0) == 0
            and session.get("queries", 0) == 0)


def clean_sessions(
    *,
    empty: bool = True,
    older_than_days: int | None = None,
    keep_last: int | None = None,
    keep_current: str | None = None,
) -> dict[str, Any]:
    """Delete sessions matching cleanup criteria.

    Criteria are combined with OR semantics — a session is removed if it
    matches ANY of the active filters. If `keep_last` is set, the N most
    recent sessions are ALWAYS preserved regardless of other filters.

    Args:
        empty: If True, remove sessions with no user messages and no queries.
        older_than_days: Remove sessions whose mtime is older than N days.
        keep_last: Always keep the N most-recent sessions.
        keep_current: Absolute path of a session to always preserve.

    Returns:
        {"deleted": [<filenames>], "kept": <n>, "total_before": <n>}
    """
    import time

    all_sessions = list_sessions()  # already sorted newest-first
    total_before = len(all_sessions)

    protected: set[str] = set()
    if keep_last is not None and keep_last > 0:
        for s in all_sessions[:keep_last]:
            protected.add(s["file"])
    if keep_current:
        protected.add(keep_current)

    now = time.time()
    cutoff = (
        now - older_than_days * 86400 if older_than_days is not None else None
    )

    deleted: list[str] = []
    for s in all_sessions:
        if s["file"] in protected:
            continue

        should_delete = False
        if empty and is_empty_session(s):
            should_delete = True
        if cutoff is not None and s.get("mtime", now) < cutoff:
            should_delete = True

        if should_delete:
            if delete_session(s["file"]):
                deleted.append(s["filename"])

    return {
        "deleted": deleted,
        "kept": total_before - len(deleted),
        "total_before": total_before,
    }


def delete_session(filepath: str) -> bool:
    """Delete a session file."""
    p = Path(filepath)
    if p.exists() and p.suffix == ".jsonl":
        p.unlink()
        return True
    return False


def read_session(filepath: str) -> dict[str, Any]:
    """Read a session file and return events + summary."""
    import json as _json
    p = Path(filepath)
    if not p.exists():
        return {"events": [], "user_messages": [], "summary": "", "connection": None}

    events = []
    user_messages = []
    assistant_messages = []
    connection = None
    memory_backend: str | None = None
    query_count = 0
    last_ts = ""

    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = _json.loads(line)
                events.append(ev)
                if ev.get("ts"):
                    last_ts = ev["ts"]
                if ev.get("role") == "user":
                    user_messages.append(ev.get("content", ""))
                elif ev.get("role") == "assistant":
                    assistant_messages.append(ev.get("content", ""))
                elif ev.get("event") == "query_executed":
                    query_count += 1
                    # Prefer the most recent connection seen, so sessions
                    # that switched DBs resume on the last one.
                    if ev.get("connection"):
                        connection = ev["connection"]
                elif ev.get("event") == "db_connected":
                    if ev.get("connection"):
                        connection = ev["connection"]
                elif ev.get("event") == "memory_backend":
                    # Most recent wins, so mid-session /memory switch
                    # is respected on /resume.
                    if ev.get("backend"):
                        memory_backend = ev["backend"]
            except Exception:
                pass

    # Build summary from first user message
    summary = ""
    if user_messages:
        summary = user_messages[0][:80]

    return {
        "events": events,
        "user_messages": user_messages,
        "assistant_messages": assistant_messages,
        "summary": summary,
        "connection": connection,
        "memory_backend": memory_backend,
        "query_count": query_count,
        "last_ts": last_ts,
    }


def build_chat_messages_from_session(filepath: str) -> list[dict[str, Any]]:
    """Reconstruct chat messages list from a session file.

    Returns a list of {role, content} dicts suitable for loading into ChatSession.messages.
    Skips tool_call details (only rebuilds user/assistant text).
    """
    import json as _json
    p = Path(filepath)
    if not p.exists():
        return []

    messages: list[dict[str, Any]] = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = _json.loads(line)
                role = ev.get("role")
                content = ev.get("content")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
            except Exception:
                pass
    return messages
