"""Async runtime utilities for the payp CLI.

Houses the persistent event loop, the sync-to-async bridge, and the
Esc-cancellable command prompt. All other CLI modules import _run_async
and command_prompt from here.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from typing import Any, TypeVar

from payp.cli.state import CommandCancelled, console

T = TypeVar("T")

# psycopg3 async refuses to run under Windows' default ProactorEventLoop.
# Force the Selector policy *before* any loop is created so every code
# path (our persistent loop, asyncio.run in worker threads, etc.) gets
# a compatible loop. See psycopg docs: https://www.psycopg.org/psycopg3/docs/advanced/async.html#async-on-windows
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

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


def _error(msg: str) -> None:
    console.print(f"[red]{msg}[/red]")


def prompt_choice(
    message: str,
    options: Mapping[str, T],
    *,
    default: str | None = None,
    case_sensitive: bool = False,
) -> T:
    """Loop until the user picks a valid key from ``options``.

    Keys in ``options`` are the valid user inputs (e.g. ``"1"``, ``"2"``
    or ``"postgres"``, ``"mysql"``). The mapped value is returned. On Esc
    the command is cancelled via ``CommandCancelled``. Empty input reuses
    ``default`` if provided.
    """
    normalized = {
        k if case_sensitive else k.lower(): v for k, v in options.items()
    }
    valid_keys = ", ".join(options.keys())
    while True:
        raw = command_prompt(message).strip()
        if not raw and default is not None:
            raw = default
        key = raw if case_sensitive else raw.lower()
        if key in normalized:
            return normalized[key]
        _error(f"Invalid choice '{raw}'. Valid options: {valid_keys}")


def prompt_required(
    message: str,
    *,
    default: str | None = None,
    is_password: bool = False,
) -> str:
    """Loop until the user enters a non-empty value (or accepts ``default``)."""
    while True:
        value = command_prompt(message, is_password=is_password).strip()
        if value:
            return value
        if default is not None:
            return default
        _error("Value is required.")


def prompt_int(
    message: str,
    *,
    default: int | None = None,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    """Loop until the user enters a valid integer within optional bounds."""
    while True:
        raw = command_prompt(message).strip()
        if not raw and default is not None:
            return default
        try:
            value = int(raw)
        except ValueError:
            _error(f"'{raw}' is not a valid integer.")
            continue
        if min_val is not None and value < min_val:
            _error(f"Value must be >= {min_val}.")
            continue
        if max_val is not None and value > max_val:
            _error(f"Value must be <= {max_val}.")
            continue
        return value


def prompt_confirm(message: str, *, default: bool) -> bool:
    """Strict y/n prompt. Loops on invalid input.

    ``default`` is used only when the user submits an empty line. The
    prompt suffix makes the default explicit so pressing Enter has an
    obvious meaning: ``[y/n] (Enter = yes)`` or ``[y/n] (Enter = no)``.
    """
    hint = "yes" if default else "no"
    full_message = f"{message.rstrip()} [y/n] (Enter = {hint}): "
    while True:
        raw = command_prompt(full_message).strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        _error("Please answer 'y' or 'n'.")
