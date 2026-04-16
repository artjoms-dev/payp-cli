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

# ── Accent palette ──
C1 = (168, 0, 111)   # #A8006F
C2 = (180, 224, 76)  # #B4E04C

# ── Banner font ──
# Pyfiglet font used for the "payp" logo.
# Run `python -m payp.ui.dashboard` to preview every candidate with the gradient.
_BANNER_FONT = "doh"

# Logo text. Pyfiglet handles both cases; uppercase reads bolder in `univers`.
_BANNER_TEXT = "payp"

# Internal pyfiglet canvas width (explicit, stable across environments).
# Keeping this at 80 preserves the intended `doh` layout while avoiding
# environment-dependent wrapping behavior.
_BANNER_RENDER_WIDTH = 80

# Soft colored glow rendered behind the banner glyphs.
# Set to 0.0 to disable; 0.18-0.25 gives a clearly-visible tinted canvas.
_BANNER_BACKDROP = 0.20

# Gamma exponent for sRGB-ish blending. 2.2 gives noticeably smoother
# mid-tones than naive linear RGB (the magenta-to-lime middle stops
# being muddy brown and becomes a cleaner desaturated transition).
_GAMMA = 2.2

# Shortlist of fonts that render "payp" at a terminal-friendly size.
# Feel free to prune / extend; the preview script iterates this list.
_CANDIDATE_FONTS: tuple[str, ...] = (
    "ansi_shadow",
    "ansi_regular",
    "slant",
    "small_slant",
    "big",
    "block",
    "banner3",
    "bulbhead",
    "chunky",
    "colossal",
    "doom",
    "epic",
    "isometric1",
    "larry3d",
    "ogre",
    "roman",
    "small",
    "speed",
    "standard",
    "univers",
)

# Ordered fallback chain, wide/tall -> narrow/short. The first entry that
# fits in `console.size.width` AND is <= 1/3 of `console.size.height` wins.
# Used when the configured `_BANNER_FONT` renders larger than the viewport
# (e.g. `doh` is 50 rows tall, far exceeding a typical 24-row terminal —
# without this, the banner scrolls off-screen and the animator's cursor-up
# math lands in the middle of wrapped content instead of the banner top).
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
    A too-tall or too-wide banner would wrap in the terminal — since line
    wrapping doesn't emit `\\n`, the row counter and animator cursor-up
    math would both be off, producing the broken staircase effect seen
    with `doh` on a standard-size terminal.
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
    # Last resort: plain uppercase text as a single row.
    return [text]


BANNER: list[str] = _render_banner_lines(_BANNER_TEXT)


def _lerp(c1: tuple, c2: tuple, t: float) -> tuple:
    # Gamma-corrected blend so mid-tones don't go muddy.
    # Linear-RGB interpolation, then encode back to sRGB-ish output.
    def _blend(a: int, b: int) -> int:
        la = (a / 255.0) ** _GAMMA
        lb = (b / 255.0) ** _GAMMA
        mixed = la + (lb - la) * t
        return max(0, min(255, int(round(mixed ** (1.0 / _GAMMA) * 255))))
    return _blend(c1[0], c2[0]), _blend(c1[1], c2[1]), _blend(c1[2], c2[2])


def _rgb(r: int, g: int, b: int) -> Style:
    return Style(color=RichColor.from_rgb(r, g, b))


def _rgb_bg(fg: tuple[int, int, int], backdrop: float) -> Style:
    # Glyph color plus a dim version of the same hue as background,
    # which produces a soft colored glow behind every cell.
    r, g, b = fg
    br, bg, bb = int(r * backdrop), int(g * backdrop), int(b * backdrop)
    return Style(
        color=RichColor.from_rgb(r, g, b),
        bgcolor=RichColor.from_rgb(br, bg, bb),
    )


# ── Palette LUT ──
# Precompute the gradient once so the per-cell hot path is a single list
# lookup instead of a gamma-corrected lerp + two Style allocations per cell.
# Memory footprint is bounded (`_LUT_SIZE` Style objects, ~10-20 KB total),
# independent of how long the animation runs.
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
    # Ping-pong wave: 0 at x=0, 1 at x=0.5, 0 at x=1, period 1.
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

    # Fast path: default palette / default backdrop can use the precomputed
    # LUT and skip per-cell Style allocation entirely.
    use_lut = c1 is C1 and c2 is C2 and (not use_bg or backdrop == _BANNER_BACKDROP)
    lut = _BG_LUT if use_bg else _FG_LUT
    lut_last = _LUT_SIZE - 1

    result = Text()
    max_cols = max((len(l) for l in lines), default=1)
    max_rows = max(len(lines), 1)
    max_diag = max((max_cols - 1) + (max_rows - 1), 1)
    period = 2 * max_diag
    for row_i, line in enumerate(lines):
        # Pad to max_cols so the backdrop forms a clean rectangle.
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


def _animate_banner(
    console: Console,
    lines: list[str] | None = None,
    *,
    cycle_seconds: float = 2.5,
    cycles: int = 2,
    fps: int = 24,
) -> bool:
    """Slow multi-cycle gradient sweep rendered in place over the banner.

    `cycle_seconds` * `cycles` is the total runtime (default 5 s). The ping-pong
    gradient loops for the full duration and then settles on the static rest
    frame. Ctrl+C breaks out early to the rest frame cleanly.

    Implementation notes:
    - Uses cursor-up ANSI (`\\x1b[nF`) to overwrite the banner block each frame,
      avoiding rich.Live's clear-line flash that was showing through the backdrop.
    - No frame cache — each frame is built, written, and dropped, so memory is
      constant regardless of cycle count. The palette LUT built at import time
      keeps the per-cell hot path allocation-free.

    Returns True when the banner was drawn, False if the terminal can't be
    animated and the caller should fall back to a plain print.
    """
    if (
        cycle_seconds <= 0
        or cycles <= 0
        or fps <= 0
        or not console.is_terminal
        or console.is_dumb_terminal
        or console.color_system is None
    ):
        return False

    lines = lines or BANNER
    n_rows = len(lines)
    total_seconds = cycle_seconds * cycles
    frames = max(int(total_seconds * fps), 1)
    frame_delay = total_seconds / frames
    # Full phase traversal covers `cycles` ping-pongs; triangle period == 2.
    phase_span = 2.0 * cycles
    out = console.file

    # First frame claims the vertical space so cursor-up overwrites stay aligned.
    console.print(_gradient_2d(lines, phase=0.0), highlight=False)

    try:
        for i in range(1, frames):
            time.sleep(frame_delay)
            phase = phase_span * i / frames
            out.write(f"\x1b[{n_rows}F")
            out.flush()
            console.print(_gradient_2d(lines, phase=phase), highlight=False)
    except KeyboardInterrupt:
        # Let the rest frame render so the banner looks intentional, then
        # re-raise so the caller's normal Ctrl+C path still triggers.
        out.write(f"\x1b[{n_rows}F")
        out.flush()
        console.print(_gradient_2d(lines, phase=0.0), highlight=False)
        raise

    # Settle on the static rest frame — matches what `animate=False` would
    # print, so downstream layout doesn't depend on whether animation ran.
    time.sleep(frame_delay)
    out.write(f"\x1b[{n_rows}F")
    out.flush()
    console.print(_gradient_2d(lines, phase=0.0), highlight=False)
    return True


# ── Background animator ──────────────────────────────────────────────

def _banner_frame_payload(lines: list[str], phase: float) -> str:
    """Serialize a banner frame to ANSI, joined by CNL so it doesn't scroll.

    Uses `\\x1b[E` (Cursor Next Line) instead of `\\n` between rows — moving
    the cursor down to column 1 without emitting a linefeed that could cause
    the terminal to scroll the viewport when cursor is already near the bottom.
    """
    frame = _gradient_2d(lines, phase=phase)
    max_cols = max((len(l) for l in lines), default=1)
    tmp = Console(
        file=StringIO(),
        force_terminal=True,
        color_system="truecolor",
        width=max_cols + 4,
        legacy_windows=False,
        emoji=False,
        markup=False,
        highlight=False,
    )
    tmp.print(frame, end="")
    ansi: str = tmp.file.getvalue()  # type: ignore[attr-defined]
    return ansi.rstrip("\n").replace("\n", "\x1b[E")


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

    Memory stays flat — one ANSI string is built and dropped per frame, and
    the palette LUT (built once at import) keeps the per-cell path
    allocation-free. No frame cache.
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
        # Use the same banner that was printed to the terminal so the cursor
        # math lines up. Defaults to `pick_banner_for_console` which applies
        # the width/height fallback — calling with explicit `lines` lets the
        # caller reuse a banner they already chose (avoiding a double pick).
        self.lines = lines if lines is not None else pick_banner_for_console(console)
        self.cycle_seconds = cycle_seconds
        self.fps = fps
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if self._thread is not None or self._stop.is_set():
            return False
        if (
            not self.console.is_terminal
            or self.console.is_dumb_terminal
            or self.console.color_system is None
        ):
            return False
        # Viewport sanity checks: banner must fit in width AND height, and
        # `rows_above` must be plausible. If anything is off, skip animation
        # entirely — better a static banner than a broken redraw.
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
        """Signal the thread to stop and wait briefly for it to finish a frame.

        The last frame drawn stays on screen at whatever phase the loop
        happened to be at. No settling redraw — by the time stop is called
        the prompt has advanced the cursor and our `rows_above` is stale.
        """
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
                # Resize invalidates the cursor-up math — already-printed
                # rows may have been re-wrapped by the terminal (visual
                # rows change without any `\n` being written), so our
                # `rows_above` no longer points at the banner top. Stop
                # cleanly rather than keep painting the wrong place.
                if self._current_size() != start_size:
                    return
                elapsed = time.perf_counter() - start
                phase = (elapsed / self.cycle_seconds) * 2.0
                # Wrap into [0, 2) without drifting via float mod.
                phase = phase - int(phase / 2.0) * 2.0
                self._draw(phase)
                if self._stop.wait(interval):
                    break
        except Exception:
            # Animator is best-effort. Never propagate a thread error into
            # the CLI — just exit the loop and leave the banner static.
            pass

    def _current_size(self) -> tuple[int, int]:
        try:
            size = self.console.size
            return size.width, size.height
        except Exception:
            return 0, 0

    def _draw(self, phase: float) -> None:
        payload = _banner_frame_payload(self.lines, phase)
        seq = (
            "\x1b7"                            # DEC save cursor
            + f"\x1b[{self.rows_above}A"       # cursor up
            + "\r"                              # column 1
            + payload
            + "\x1b8"                           # DEC restore cursor
        )
        try:
            self.console.file.write(seq)
            self.console.file.flush()
        except Exception:
            self._stop.set()


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
    animate: bool = False,
) -> None:
    """Render the welcome screen.

    `animate=False` (the default) prints the banner as a single static frame
    so the whole welcome appears instantly — the continuous shimmer is run
    separately by `BannerAnimator` while the REPL waits for input.
    """

    # ── Banner ──
    # Pick a banner that fits the current terminal — the configured
    # `_BANNER_FONT` may overflow (e.g. `doh` is 50 rows tall); fallbacks
    # step down to progressively smaller fonts until one fits.
    lines = pick_banner_for_console(console)
    drawn = _animate_banner(console, lines=lines) if animate else False
    if not drawn:
        console.print(_gradient_2d(lines), highlight=False)

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


def preview_banner_fonts(console: Console | None = None, text: str = "payp") -> None:
    """Render every candidate font with the brand gradient so you can pick one.

    Change `_BANNER_FONT` at the top of this module to the one you like.
    """
    console = console or Console()
    for font in _CANDIDATE_FONTS:
        try:
            lines = _render_banner_lines(text=text, font=font)
        except Exception as exc:
            console.rule(f"[red]{font} — {exc}[/red]")
            continue
        marker = "  ← current" if font == _BANNER_FONT else ""
        console.rule(f"[bold]{font}[/bold]{marker}")
        console.print(_gradient_2d(lines), highlight=False)
        console.print()


if __name__ == "__main__":
    preview_banner_fonts()
