"""Async runtime utilities for the payp CLI.

Houses the persistent event loop, the sync-to-async bridge, and the
Esc-cancellable command prompt. All other CLI modules import _run_async
and command_prompt from here.
"""

from __future__ import annotations

import asyncio
from typing import Any

from payp.cli.state import CommandCancelled

_PERSISTENT_LOOP: asyncio.AbstractEventLoop | None = None


def _get_loop() -> asyncio.AbstractEventLoop:
    """Return a single persistent event loop for the entire payp session.

    This prevents 'Event loop is closed' errors when async DB connections
    (aiomysql, oracledb) hold references to a loop that asyncio.run() closed.
    """
    global _PERSISTENT_LOOP
    if _PERSISTENT_LOOP is None or _PERSISTENT_LOOP.is_closed():
        _PERSISTENT_LOOP = asyncio.new_event_loop()
    return _PERSISTENT_LOOP


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from sync code.

    Detects ANY currently-running loop in this thread (prompt_toolkit, click,
    etc.) — not just our persistent loop — and falls back to a worker thread
    when needed. This avoids "Cannot run the event loop while another loop is
    running" inside interactive selectors.
    """
    try:
        asyncio.get_running_loop()
        # We're inside some other loop (e.g. prompt_toolkit inside a selector
        # callback). Execute the coroutine on a dedicated worker thread so it
        # gets its own fresh event loop.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        # No running loop in this thread — use our persistent loop.
        loop = _get_loop()
        return loop.run_until_complete(coro)


def command_prompt(message: str = "", **kwargs: Any) -> str:
    """prompt_toolkit prompt with Esc bound to cancel the current command.

    Drop-in replacement for ``pt_prompt`` inside slash commands.
    Raises ``CommandCancelled`` on Esc.
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    @kb.add("escape")
    def _esc(event: Any) -> None:
        event.app.exit(exception=CommandCancelled())

    session = PromptSession(key_bindings=kb)
    session.app.ttimeoutlen = 0.05
    session.app.timeoutlen = 0.05
    return session.prompt(message, **kwargs)
