"""Esc-to-abort helper for long-running async operations.

Watches stdin in a non-blocking way; when the user presses Esc or 'q',
a shared asyncio.Event is set. Callers check/await the event to abort.

On Windows `termios`/`tty` don't exist and `select` can't poll stdin, so
the whole watcher downgrades to a no-op context manager. Ctrl+C remains
the cancellation path, matching the native Windows convention.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from contextlib import contextmanager

_IS_WINDOWS = sys.platform == "win32"
_POSIX = not _IS_WINDOWS

if _POSIX:
    import select
    import termios
    import tty


def _can_raw_stdin() -> bool:
    return _POSIX and sys.stdin.isatty()


@contextmanager
def _raw_stdin() -> Iterator[None]:
    """Temporarily put stdin in raw mode so we can read single keypresses."""
    if not _can_raw_stdin():
        yield
        return
    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)


class AbortWatcher:
    """Watches stdin for Esc/q during an operation. Sets an asyncio event on trigger.

    Supports pause()/resume() so nested UIs (approval prompts, etc.) can own stdin
    temporarily without fighting the watcher.
    """

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stop_flag = False
        self._paused = False
        self._raw_ctx = None
        self._enabled = _can_raw_stdin()

    def pause(self) -> None:
        """Pause stdin polling + restore cooked mode so nested UIs can read input."""
        if not self._enabled:
            return
        self._paused = True
        # Restore cooked mode so prompt_toolkit apps can take over stdin cleanly
        if self._raw_ctx is not None:
            try:
                self._raw_ctx.__exit__(None, None, None)
            except Exception:
                pass
            self._raw_ctx = None

    def resume(self) -> None:
        """Resume stdin polling + drain any buffered bytes to avoid stale triggers."""
        if not self._enabled:
            return
        # Drain stdin buffer (clear any stray bytes from nested UI)
        if sys.stdin.isatty():
            if _IS_WINDOWS:
                import msvcrt
                try:
                    while msvcrt.kbhit():
                        msvcrt.getch()
                except Exception:
                    pass
            else:
                try:
                    while True:
                        r, _, _ = select.select([sys.stdin], [], [], 0)
                        if not r:
                            break
                        sys.stdin.read(1)
                except Exception:
                    pass
        # Re-enter raw mode
        self._raw_ctx = _raw_stdin()
        self._raw_ctx.__enter__()
        self._paused = False

    async def _watch(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._stop_flag and not self.event.is_set():
            if self._paused:
                await asyncio.sleep(0.1)
                continue
            ready = await loop.run_in_executor(None, self._poll_stdin)
            if ready:
                self.event.set()
                return

    def _poll_stdin(self) -> bool:
        """Return True if Esc/q was pressed."""
        if not self._enabled or not sys.stdin.isatty() or self._paused:
            return False
        if _IS_WINDOWS:
            import msvcrt
            if msvcrt.kbhit():
                try:
                    ch = msvcrt.getch().decode("utf-8", errors="ignore")
                except Exception:
                    return False
                return ch in ("\x1b", "q", "Q")
            return False
        r, _, _ = select.select([sys.stdin], [], [], 0.2)
        if r and not self._paused:
            try:
                ch = sys.stdin.read(1)
            except Exception:
                return False
            # Esc = '\x1b', q = 'q', Q = 'Q' — Enter is '\r'/'\n', ignored
            return ch in ("\x1b", "q", "Q")
        return False

    async def __aenter__(self) -> AbortWatcher:
        self._stop_flag = False
        if not self._enabled:
            # Windows or non-tty: the watcher is inert. Ctrl+C stays the
            # only cancellation path, and the surrounding chat flow keeps
            # working without a background stdin poller.
            return self
        self._raw_ctx = _raw_stdin()
        self._raw_ctx.__enter__()
        self._task = asyncio.create_task(self._watch())
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self._stop_flag = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self._raw_ctx is not None:
            self._raw_ctx.__exit__(None, None, None)
            self._raw_ctx = None

    @property
    def aborted(self) -> bool:
        return self.event.is_set()
