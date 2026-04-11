"""payp CLI package.

This package replaces the monolithic src/payp/cli.py.
All external imports that previously targeted payp.cli are preserved here
via explicit re-exports so nothing outside this package needs to change.
"""

from payp.cli.app import app, app_entry, main
from payp.cli.commands.db import _setup_new_connection
from payp.cli.commands.models import _setup_new_provider
from payp.cli.runtime import command_prompt
from payp.cli.state import CommandCancelled, console, get_config

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
