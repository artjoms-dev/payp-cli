"""payp visual design system — colors, icons, message helpers.

Color grammar:
  green   — success, ready state, active/connected
  cyan    — information, commands, highlighted values
  yellow  — warnings, attention needed, empty state
  red     — errors, destructive ops, hard blocks
  magenta — brand, titles
  dim     — meta info, secondary text, hints
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel


# Status icons
class Icon:
    READY = "●"         # green: ready/active
    INACTIVE = "○"      # gray: not set/disconnected
    WARNING = "⚠"       # yellow: attention
    ERROR = "✗"         # red: error
    SUCCESS = "✓"       # green: success
    ARROW = "→"
    BULLET = "•"
    PIN = "📌"


# Colors (rich style strings)
class Color:
    BRAND = "bold rgb(168,0,111)"      # #A8006F
    BRAND_ALT = "bold rgb(180,224,76)"  # #B4E04C
    SUCCESS = "green"
    INFO = "cyan"
    WARN = "yellow"
    ERROR = "red"
    DIM = "dim"
    DIM_GRAY = "fg:gray"
    ACCENT = "bold rgb(168,0,111)"


# Colors (prompt_toolkit style strings — for selector / prompt_toolkit UIs)
class PTColor:
    BRAND = "bold fg:#A8006F"       # accent magenta
    BRAND_ALT = "bold fg:#B4E04C"   # accent lime
    SUCCESS = "fg:ansigreen"
    INFO = "fg:ansicyan"
    WARN = "fg:ansiyellow"
    ERROR = "fg:ansired"
    DIM = "fg:ansigray"


def success(console: Console, msg: str) -> None:
    """Print a success message."""
    console.print(f"  [{Color.SUCCESS}]{Icon.SUCCESS}[/{Color.SUCCESS}] {msg}")


def error(console: Console, msg: str) -> None:
    """Print an error message."""
    console.print(f"  [{Color.ERROR}]{Icon.ERROR}[/{Color.ERROR}] {msg}")


def warn(console: Console, msg: str) -> None:
    """Print a warning message."""
    console.print(f"  [{Color.WARN}]{Icon.WARNING}[/{Color.WARN}] {msg}")


def info(console: Console, msg: str) -> None:
    """Print an info message."""
    console.print(f"  [{Color.INFO}]{Icon.BULLET}[/{Color.INFO}] {msg}")


def hint(console: Console, msg: str) -> None:
    """Print a hint/meta message in dim style."""
    console.print(f"  [{Color.DIM}]{msg}[/{Color.DIM}]")


def section(console: Console, title: str) -> None:
    """Print a section header."""
    console.print(f"\n[{Color.ACCENT}]{title}[/{Color.ACCENT}]")
    console.print(f"[{Color.DIM}]{'─' * 50}[/{Color.DIM}]")


def brand_title() -> str:
    """Return the brand title styling."""
    return f"[{Color.BRAND}]payp[/{Color.BRAND}]"
