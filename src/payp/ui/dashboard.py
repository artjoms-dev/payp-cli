"""Status dashboard - welcome screen using Rich only.

Accent colors: #A8006F (magenta-pink) -> #B4E04C (lime green).
"""

from __future__ import annotations

import threading
import time
from io import StringIO

import pyfiglet
from rich import box
from rich.color import Color as RichColor
from rich.console import Console
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

C1 = (168, 0, 111)
C2 = (180, 224, 76)

_BANNER_FONT = "doh"
_BANNER_TEXT = "payp"
_BANNER_RENDER_WIDTH = 80
_BANNER_BACKDROP = 0.20

# sRGB-ish gamma for blending; 2.2 keeps magenta-to-lime midtones from going muddy brown.
_GAMMA = 2.2

# Ordered fallback chain when the configured font would overflow the viewport.
# doh is 50 rows tall; on a standard terminal it would scroll off-screen and
# break the animator's cursor-up math (line wrapping emits no newlines).
_BANNER_FALLBACKS: tuple[str, ...] = (
    "univers",
    "ansi_regular",
    "ansi_shadow",
    "standard",
    "small",
)


def _render_banner_lines(text: str = "payp", font: str = _BANNER_FONT) -> list[str]:
    """Render ASCII banner via pyfiglet with leading / trailing blank rows trimmed."""
    try:
        raw = pyfiglet.figlet_format(text, font=font, width=_BANNER_RENDER_WIDTH)
    except pyfiglet.FontNotFound:
        raw = pyfiglet.figlet_format(text, font="standard", width=_BANNER_RENDER_WIDTH)
    lines = [line.rstrip() for line in raw.rstrip("\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines or [text]


def _fits_viewport(lines: list[str], max_w: int, max_h: int) -> bool:
    if not lines:
        return False
    return (
        len(lines) <= max_h
        and max(len(l) for l in lines) <= max_w
    )


def pick_banner_for_console(
    console: Console, text: str = _BANNER_TEXT
) -> list[str]:
    """Return the widest banner that fits the current terminal.

    Tries `_BANNER_FONT` first, then walks `_BANNER_FALLBACKS` until it
    finds one that fits within the console width and a third of the height.
    A too-tall or too-wide banner would wrap in the terminal; since line
    wrapping doesn't emit `\\n`, the row counter and animator cursor-up
    math would both be off, producing a broken staircase effect.
    """
    try:
        max_w = console.size.width
        max_h = max(console.size.height // 3, 4)
    except Exception:
        max_w, max_h = 80, 10
    # Dedup preserving order so the configured font is tried first.
    seen: set[str] = set()
    for font in (_BANNER_FONT, *_BANNER_FALLBACKS):
        if font in seen:
            continue
        seen.add(font)
        try:
            lines = _render_banner_lines(text, font=font)
        except Exception:
            continue
        if _fits_viewport(lines, max_w, max_h):
            return lines
    return [text]


def _lerp(c1: tuple, c2: tuple, t: float) -> tuple:
    # Linear-RGB interpolation, encoded back to sRGB so mid-tones don't go muddy.
    def _blend(a: int, b: int) -> int:
        la = (a / 255.0) ** _GAMMA
        lb = (b / 255.0) ** _GAMMA
        mixed = la + (lb - la) * t
        return max(0, min(255, int(round(mixed ** (1.0 / _GAMMA) * 255))))
    return _blend(c1[0], c2[0]), _blend(c1[1], c2[1]), _blend(c1[2], c2[2])


def _rgb(r: int, g: int, b: int) -> Style:
    return Style(color=RichColor.from_rgb(r, g, b))


def _rgb_bg(fg: tuple[int, int, int], backdrop: float) -> Style:
    r, g, b = fg
    br, bg, bb = int(r * backdrop), int(g * backdrop), int(b * backdrop)
    return Style(
        color=RichColor.from_rgb(r, g, b),
        bgcolor=RichColor.from_rgb(br, bg, bb),
    )


# Precomputed gradient palette so per-cell animation avoids a gamma-corrected
# lerp + two Style allocations every frame.
_LUT_SIZE = 256
_FG_LUT: list[Style] = []
_BG_LUT: list[Style] = []


def _build_palette_lut() -> None:
    _FG_LUT.clear()
    _BG_LUT.clear()
    last = _LUT_SIZE - 1
    for i in range(_LUT_SIZE):
        fg = _lerp(C1, C2, i / last)
        _FG_LUT.append(_rgb(*fg))
        _BG_LUT.append(_rgb_bg(fg, _BANNER_BACKDROP))


_build_palette_lut()


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


def _triangle(x: float) -> float:
    x = x - int(x)
    return 1.0 - abs(2.0 * x - 1.0)


def _gradient_2d(
    lines: list[str],
    c1: tuple = C1,
    c2: tuple = C2,
    phase: float = 0.0,
    backdrop: float | None = None,
) -> Text:
    """Diagonal gradient with optional phase shift and colored backdrop.

    phase == 0 renders the static rest frame (top-left C1, bottom-right C2).
    Non-zero phase slides a triangle-wave gradient along the diagonal so
    animation sweeps are seamless and loop back to phase 0 without a seam.
    `backdrop` is the dim-bg intensity (0.0-1.0); `None` uses the module default.
    """
    if backdrop is None:
        backdrop = _BANNER_BACKDROP
    use_bg = backdrop > 0.0

    # Default palette and backdrop hit the precomputed LUT path, skipping per-cell Style allocation.
    use_lut = c1 is C1 and c2 is C2 and (not use_bg or backdrop == _BANNER_BACKDROP)
    lut = _BG_LUT if use_bg else _FG_LUT
    lut_last = _LUT_SIZE - 1

    result = Text()
    max_cols = max((len(l) for l in lines), default=1)
    max_rows = max(len(lines), 1)
    max_diag = max((max_cols - 1) + (max_rows - 1), 1)
    period = 2 * max_diag
    for row_i, line in enumerate(lines):
        padded = line.ljust(max_cols) if use_bg else line
        for col_i, ch in enumerate(padded):
            x = (col_i + row_i + phase * max_diag) / period
            t = _triangle(x)
            if use_lut:
                if use_bg or ch != " ":
                    result.append(ch, style=lut[int(t * lut_last)])
                else:
                    result.append(ch)
            else:
                fg = _lerp(c1, c2, t)
                if use_bg:
                    result.append(ch, style=_rgb_bg(fg, backdrop))
                elif ch == " ":
                    result.append(ch)
                else:
                    result.append(ch, style=_rgb(*fg))
        if row_i < len(lines) - 1:
            result.append("\n")
    return result


def _build_frame_console(width: int) -> tuple[Console, StringIO]:
    """Build a Rich console that writes into a StringIO buffer.

    Uses `\\x1b[E` (Cursor Next Line) instead of `\\n` so emitted frames
    don't scroll the viewport when the cursor is near the bottom.
    """
    buf = StringIO()
    return (
        Console(
            file=buf,
            force_terminal=True,
            color_system="truecolor",
            width=width,
            legacy_windows=False,
            emoji=False,
            markup=False,
            highlight=False,
        ),
        buf,
    )


class BannerAnimator:
    """Keeps the banner shimmering in place while the REPL waits for input.

    Runs a daemon thread that every ~1/fps seconds writes one frame to
    `console.file` as a single atomic sequence:

        \\x1b7  save cursor
        \\x1b[{N}A  move up N rows (N = rows_above, counted from the prompt line)
        \\r  column 1
        <banner ansi>  rendered via CNL separators, no linefeeds
        \\x1b8  restore cursor

    Coexists with prompt_toolkit because the save/restore pair leaves the
    cursor where the prompt put it, and writes land on rows above the prompt
    that prompt_toolkit doesn't touch.
    """

    def __init__(
        self,
        console: Console,
        rows_above: int,
        *,
        lines: list[str] | None = None,
        cycle_seconds: float = 3.0,
        fps: int = 20,
    ) -> None:
        self.console = console
        self.rows_above = rows_above
        self.lines = lines if lines is not None else pick_banner_for_console(console)
        self.cycle_seconds = cycle_seconds
        self.fps = fps
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        max_cols = max((len(l) for l in self.lines), default=1)
        self._frame_console, self._frame_buf = _build_frame_console(max_cols + 4)
        self._last_payload: str | None = None

    def start(self) -> bool:
        if self._thread is not None or self._stop.is_set():
            return False
        if (
            not self.console.is_terminal
            or self.console.is_dumb_terminal
            or self.console.color_system is None
        ):
            return False
        try:
            size = self.console.size
            height, width = size.height, size.width
        except Exception:
            height, width = 24, 80
        banner_h = len(self.lines)
        banner_w = max((len(l) for l in self.lines), default=0)
        if banner_w > width or banner_h >= height:
            return False
        if self.rows_above < banner_h or self.rows_above >= height - 1:
            return False
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="payp-banner-anim"
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=0.3)
        self._thread = None

    def _run(self) -> None:
        try:
            start_size = self._current_size()
            start = time.perf_counter()
            interval = 1.0 / self.fps
            while not self._stop.is_set():
                if self._current_size() != start_size:
                    return
                elapsed = time.perf_counter() - start
                phase = (elapsed / self.cycle_seconds) * 2.0
                phase = phase - int(phase / 2.0) * 2.0
                self._draw(phase)
                if self._stop.wait(interval):
                    break
        except Exception:
            pass

    def _current_size(self) -> tuple[int, int]:
        try:
            size = self.console.size
            return size.width, size.height
        except Exception:
            return 0, 0

    def _build_payload(self, phase: float) -> str:
        frame = _gradient_2d(self.lines, phase=phase)
        self._frame_buf.seek(0)
        self._frame_buf.truncate(0)
        self._frame_console.print(frame, end="")
        ansi = self._frame_buf.getvalue()
        return ansi.rstrip("\n").replace("\n", "\x1b[E")

    def _draw(self, phase: float) -> None:
        payload = self._build_payload(phase)
        if payload == self._last_payload:
            return
        self._last_payload = payload
        seq = (
            "\x1b7"
            + f"\x1b[{self.rows_above}A"
            + "\r"
            + payload
            + "\x1b8"
        )
        try:
            self.console.file.write(seq)
            self.console.file.flush()
        except Exception:
            self._stop.set()


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
) -> None:
    """Render the welcome screen as a single static frame.

    The continuous shimmer is driven separately by `BannerAnimator` while
    the REPL waits for input.
    """
    lines = pick_banner_for_console(console)
    console.print(_gradient_2d(lines), highlight=False)

    tagline = Text()
    tagline.append(f"v{version}", style="dim")
    tagline.append("  ")
    tagline.append_text(_gradient("AI CLI for Data Engineers"))
    console.print(tagline, highlight=False)
    console.print()

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

    if connection_name and connection_status:
        r, g, b = C2
        db_text = Text()
        db_text.append(connection_name, style=f"rgb({r},{g},{b})")
        db_text.append(f"  ({connection_status})", style="dim")
        grid.add_row("Database", db_text)
    elif connection_name:
        grid.add_row("Database", Text(f"{connection_name} - not connected", style="dim"))

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


