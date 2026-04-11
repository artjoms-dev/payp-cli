"""payp CLI package.

This package replaces the monolithic src/payp/cli.py.
All external imports that previously targeted payp.cli are preserved here
via explicit re-exports so nothing outside this package needs to change.
"""

from payp.cli._legacy import (
    CommandCancelled,
    _setup_new_connection,
    _setup_new_provider,
    app,
    app_entry,
    command_prompt,
    console,
    get_config,
    main,
)

__all__ = [
    "app",
    "app_entry",
    "main",
    "CommandCancelled",
    "command_prompt",
    "console",
    "get_config",
    "_setup_new_connection",
    "_setup_new_provider",
]
