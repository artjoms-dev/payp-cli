"""Session persistence — save conversation history as JSONL files.

Each session is one JSONL file in ~/.payp/sessions/.
Format: one JSON object per line with timestamp, role, content, etc.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
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

    def _write_event(self, event: dict[str, Any]) -> None:
        """Append one event to the JSONL file."""
        event.setdefault("ts", datetime.now(timezone.utc).isoformat())
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
    """List all saved sessions, newest first."""
    d = sessions_dir()
    sessions = []
    for f in sorted(d.glob("*.jsonl"), reverse=True):
        try:
            # Count events and find first/last timestamps
            with open(f) as fp:
                lines = fp.readlines()
            event_count = len(lines)
            query_count = sum(1 for line in lines if '"event": "query_executed"' in line)

            # Parse filename for metadata
            parts = f.stem.split("_")
            date_part = parts[0] if len(parts) >= 1 else "?"
            conn_part = parts[1] if len(parts) >= 2 else "?"

            sessions.append({
                "file": str(f),
                "filename": f.name,
                "date": date_part,
                "connection": conn_part,
                "events": event_count,
                "queries": query_count,
                "size": f.stat().st_size,
            })
        except Exception:
            pass
    return sessions


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
                    if ev.get("connection") and not connection:
                        connection = ev["connection"]
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
