"""Status dashboard — welcome screen using Rich only.

Accent colors: #A8006F (magenta-pink) → #B4E04C (lime green).
"""

from __future__ import annotations

import time

from rich import box
from rich.color import Color as RichColor
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.style import Style
from rich.table import Table
from rich.text import Text

# ── Accent palette ──────────────────────────────────────────────────────
C1 = (168, 0, 111)   # #A8006F
C2 = (180, 224, 76)   # #B4E04C


def _lerp(c1: tuple, c2: tuple, t: float) -> tuple:
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def _rgb(r: int, g: int, b: int) -> Style:
    return Style(color=RichColor.from_rgb(r, g, b))


def _gradient(s: str, c1: tuple = C1, c2: tuple = C2) -> Text:
    """Per-character horizontal gradient."""
    t = Text()
    n = max(len(s) - 1, 1)
    for i, ch in enumerate(s):
        if ch == " ":
            t.append(ch)
        else:
            r, g, b = _lerp(c1, c2, i / n)
            t.append(ch, style=_rgb(r, g, b))
    return t


def _gradient_2d(lines: list[str], c1: tuple = C1, c2: tuple = C2) -> Text:
    """Per-character diagonal gradient — each cell right or down shifts color equally."""
    result = Text()
    max_cols = max(len(l) for l in lines)
    max_rows = len(lines)
    # Diagonal distance: top-left (0,0) = 0, bottom-right = max_cols + max_rows
    max_diag = max((max_cols - 1) + (max_rows - 1), 1)
    for row_i, line in enumerate(lines):
        for col_i, ch in enumerate(line):
            if ch == " ":
                result.append(ch)
            else:
                t = (col_i + row_i) / max_diag
                r, g, b = _lerp(c1, c2, t)
                result.append(ch, style=_rgb(r, g, b))
        if row_i < len(lines) - 1:
            result.append("\n")
    return result


# ── Banner ──────────────────────────────────────────────────────────────
BANNER = [
    "██████╗  █████╗ ██╗   ██╗██████╗ ",
    "██╔══██╗██╔══██╗╚██╗ ██╔╝██╔══██╗",
    "██████╔╝███████║ ╚████╔╝ ██████╔╝",
    "██╔═══╝ ██╔══██║  ╚██╔╝  ██╔═══╝ ",
    "██║     ██║  ██║   ██║   ██║     ",
    "╚═╝     ╚═╝  ╚═╝   ╚═╝   ╚═╝     ",
]


# ── Loading animation ──────────────────────────────────────────────────

def _loading_animation(console: Console) -> None:
    """Quick gradient progress bar using Rich Progress."""
    # Build a custom bar style — Rich BarColumn supports style + complete_style
    r1, g1, b1 = C1
    r2, g2, b2 = C2
    with Progress(
        TextColumn("  "),
        BarColumn(
            bar_width=36,
            style=f"rgb({r1},{g1},{b1})",
            complete_style=f"rgb({r2},{g2},{b2})",
            finished_style=f"rgb({r2},{g2},{b2})",
        ),
        console=console,
        transient=True,  # disappears when done
    ) as progress:
        task = progress.add_task("", total=100)
        for _ in range(100):
            progress.advance(task, 1)
            time.sleep(0.008)
    time.sleep(0.15)


# ── Public API ─────────────────────────────────────────────────────────

def render_status_dashboard(
    console: Console,
    version: str,
    model_name: str | None,
    model_provider: str | None,
    reviewer_name: str | None,
    connection_name: str | None,
    connection_status: str | None,
    mode: str,
    snapshot_count: int,
    session_count: int | None = None,
    animate: bool = True,
) -> None:
    """Render the welcome screen."""

    if animate:
        _loading_animation(console)

    console.print()

    # ── Banner ──
    banner_text = _gradient_2d(BANNER)
    console.print(banner_text, highlight=False)

    # ── Tagline ──
    tagline = Text()
    tagline.append(f"v{version}", style="dim")
    tagline.append("  ")
    tagline.append_text(_gradient("AI CLI for Data Engineers"))
    console.print(tagline, highlight=False)
    console.print()

    # ── Info panel ──
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", no_wrap=True, width=12, style="dim")
    grid.add_column(justify="left")

    # Model
    if model_name:
        model_text = Text()
        model_text.append_text(_gradient(model_name))
        if model_provider:
            model_text.append(f"  via {model_provider}", style="dim")
        grid.add_row("Model", model_text)
        if reviewer_name:
            grid.add_row("Reviewer", Text(reviewer_name, style="dim"))
    else:
        r, g, b = C1
        grid.add_row(
            "Model",
            Text.assemble(
                ("not set", f"rgb({r},{g},{b})"),
                ("  /models to set up", "dim"),
            ),
        )

    # Database — only when connected
    if connection_name and connection_status:
        r, g, b = C2
        db_text = Text()
        db_text.append(connection_name, style=f"rgb({r},{g},{b})")
        db_text.append(f"  ({connection_status})", style="dim")
        grid.add_row("Database", db_text)
    elif connection_name:
        grid.add_row("Database", Text(f"{connection_name} — not connected", style="dim"))

    # Mode
    mode_colors = {
        "manual": C2, "secure": C2, "secure-auto": C1, "yolo": (255, 180, 50),
    }
    mode_notes = {
        "manual": "approve each write",
        "yolo": "auto-execute all",
        "secure": "reviewer + approval",
        "secure-auto": "reviewer decides",
    }
    mc = mode_colors.get(mode, C2)
    mode_text = Text()
    mode_text.append(mode, style=f"bold rgb({mc[0]},{mc[1]},{mc[2]})")
    if note := mode_notes.get(mode, ""):
        mode_text.append(f"  {note}", style="dim")
    grid.add_row("Mode", mode_text)

    # Snapshots
    if snapshot_count > 0:
        r, g, b = C1
        snap = Text()
        snap.append(str(snapshot_count), style=f"rgb({r},{g},{b})")
        snap.append("  /snapshots to manage", style="dim")
        grid.add_row("Snapshots", snap)

    # Panel border uses a blend of the two accents
    br, bg, bb = _lerp(C1, C2, 0.35)
    console.print(Panel(
        grid,
        border_style=Style(color=RichColor.from_rgb(br, bg, bb), dim=True),
        box=box.ROUNDED,
        padding=(1, 3),
    ))


def render_compact_hint(console: Console) -> None:
    """Hint footer."""
    r1, g1, b1 = C1
    r2, g2, b2 = C2
    hint = Text("  ")
    hint.append("Type naturally to chat", style="dim")
    hint.append("  •  ", style="dim")
    hint.append("/help", style=f"dim rgb({r1},{g1},{b1})")
    hint.append(" for commands", style="dim")
    hint.append("  •  ", style="dim")
    hint.append("Ctrl+D", style=f"dim rgb({r2},{g2},{b2})")
    hint.append(" to exit", style="dim")
    console.print(hint)
    console.print()
