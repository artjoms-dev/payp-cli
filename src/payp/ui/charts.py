"""Terminal chart rendering — hybrid approach.

- Bar charts: custom rich-based horizontal bars (clean, themed)
- Line / scatter: plotille (braille for high resolution)
- Histogram: plotext (works well for this)
"""

from __future__ import annotations

import sys
from typing import Any

from rich.console import Console

_console = Console()


# ────────────────────────── Bar Chart ──────────────────────────

BAR_FULL = "█"
BAR_PARTIAL = ["", "▏", "▎", "▍", "▌", "▋", "▊", "▉"]


def render_bar_chart(
    labels: list[str],
    values: list[float],
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    width: int = 40,
    **kwargs: Any,
) -> None:
    """Render a clean horizontal bar chart using unicode blocks."""
    if not values:
        _console.print("[dim]  No data to chart.[/dim]")
        return

    max_val = max(values)
    if max_val <= 0:
        _console.print("[dim]  All values are zero.[/dim]")
        return

    # Fixed label width (align bars)
    max_label_len = max(len(str(l)) for l in labels)
    label_w = min(max_label_len, 20)

    _console.print()
    if title:
        _console.print(f"  [bold cyan]{title}[/bold cyan]")
        _console.print()

    # Compute scaled bars with 1/8 precision using partial block chars
    for label, value in zip(labels, values):
        label_str = str(label)[:label_w].ljust(label_w)

        ratio = value / max_val
        total_eighths = int(ratio * width * 8)
        full_blocks = total_eighths // 8
        partial = total_eighths % 8

        bar = BAR_FULL * full_blocks
        if partial > 0:
            bar += BAR_PARTIAL[partial]

        # Format value compactly
        if isinstance(value, float) and not value.is_integer():
            val_str = f"{value:,.2f}"
        else:
            val_str = f"{int(value):,}"

        _console.print(
            f"  [dim]{label_str}[/dim]  [bold rgb(180,224,76)]{bar}[/bold rgb(180,224,76)] [bold]{val_str}[/bold]"
        )
    _console.print()


# ────────────────────────── Line / Scatter (plotille) ──────────────────────────


def _is_money_context(*texts: str) -> bool:
    """Detect if any of the given text hints suggest monetary values."""
    haystack = " ".join(texts).lower()
    for token in ("revenue", "cost", "price", "sales", "profit", "$", "usd", "eur", "gbp"):
        if token in haystack:
            return True
    return False


def _make_y_formatter(values: list[float], is_money: bool):
    """Build a smart y-axis tick formatter based on the magnitude of values."""
    prefix = "$" if is_money else ""
    abs_max = max((abs(v) for v in values), default=0.0)

    def fmt(val: float, _next: float = 0.0) -> str:
        sign = "-" if val < 0 else ""
        v = abs(val)
        if abs_max >= 1_000_000:
            s = f"{v / 1_000_000:.1f}M"
        elif abs_max >= 10_000:
            s = f"{v / 1_000:.1f}K"
        elif abs_max >= 1_000:
            # 1000–9999 → thousands separator with 0 decimals
            s = f"{v:,.0f}"
        elif abs_max >= 10:
            # 10–999 → max 1 decimal, strip trailing zero
            if v == int(v):
                s = f"{int(v)}"
            else:
                s = f"{v:.1f}"
        else:
            # < 10 → 2 decimals
            s = f"{v:.2f}"
        return f"{sign}{prefix}{s}"

    return fmt


def _make_x_formatter_categorical(labels: list[str]):
    """X-axis formatter for non-numeric labels.

    plotille emits ticks every 10 char columns which rarely align with our
    integer label indices. We snap each tick to the nearest integer within
    half a tick-spacing and dedupe across calls so each label only prints once.
    """
    used: set[int] = set()

    def fmt(val: float, next_val: float = 0.0) -> str:
        spacing = abs(next_val - val)
        half = spacing / 2 if spacing > 0 else 0.5
        i = int(round(val))
        # Only snap if we're actually close enough to an integer tick
        if abs(val - i) > max(half, 0.5):
            return ""
        if i in used or not (0 <= i < len(labels)):
            return ""
        used.add(i)
        text = labels[i]
        return text[:9] if len(text) > 9 else text

    return fmt


def _make_x_formatter_numeric(values: list[float]):
    """X-axis formatter for numeric values — prefer integers, compact decimals."""
    all_int = all(float(v).is_integer() for v in values)

    def fmt(val: float, _next: float = 0.0) -> str:
        if all_int:
            return f"{int(round(val))}"
        if abs(val) >= 1000:
            return f"{val:,.0f}"
        if abs(val) >= 10:
            return f"{val:.1f}"
        return f"{val:.2f}"

    return fmt


def render_line_chart(
    x: list[Any],
    y: list[float],
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    width: int = 60,
    height: int = 15,
    **kwargs: Any,
) -> None:
    """Render a line chart with braille characters (high resolution).

    Smart axes: y-values auto-format by magnitude ($1.2K, 3.1M, 850); x-axis
    labels for categorical/date data render only at integer tick positions,
    mapped back to their actual label strings.
    """
    import plotille

    if len(y) < 2:
        _console.print(f"  [yellow]⚠ Line chart needs ≥2 data points ({len(y)} given)[/yellow]")
        return

    # Convert non-numeric x to indices; keep labels for tick mapping
    if x and not isinstance(x[0], (int, float)):
        x_values = [float(i) for i in range(len(x))]
        x_labels_str: list[str] | None = [str(v) for v in x]
    else:
        x_values = [float(v) for v in x]
        x_labels_str = None

    y_values = [float(v) for v in y]
    is_money = _is_money_context(title, y_label)

    fig = plotille.Figure()
    fig.width = width
    fig.height = height
    fig.color_mode = "byte"
    fig.x_label = x_label or ""
    fig.y_label = y_label or ""
    fig.set_x_limits(min_=min(x_values), max_=max(x_values))
    fig.set_y_limits(min_=min(y_values), max_=max(y_values))

    # Smart axis formatters — the headline fix
    fig.y_ticks_fkt = _make_y_formatter(y_values, is_money)
    if x_labels_str is not None:
        fig.x_ticks_fkt = _make_x_formatter_categorical(x_labels_str)
    else:
        fig.x_ticks_fkt = _make_x_formatter_numeric(x_values)

    fig.plot(x_values, y_values, lc=51, label="")  # cyan-ish

    _console.print()
    if title:
        _console.print(f"  [bold cyan]{title}[/bold cyan]")
    _print_raw(fig.show(legend=False))
    _console.print()


def render_scatter(
    x: list[float],
    y: list[float],
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    width: int = 60,
    height: int = 15,
    **kwargs: Any,
) -> None:
    """Render a scatter plot with braille characters."""
    import plotille

    if not x or not y:
        _console.print("[dim]  No data to chart.[/dim]")
        return

    fig = plotille.Figure()
    fig.width = width
    fig.height = height
    fig.color_mode = "byte"
    fig.x_label = x_label or ""
    fig.y_label = y_label or ""
    fig.set_x_limits(min_=min(x), max_=max(x))
    fig.set_y_limits(min_=min(y), max_=max(y))
    fig.scatter(x, y, lc=51)

    _console.print()
    if title:
        _console.print(f"  [bold cyan]{title}[/bold cyan]")
    _print_raw(fig.show(legend=False))
    _console.print()


# ────────────────────────── Histogram (plotext) ──────────────────────────


def render_histogram(
    values: list[float],
    bins: int = 10,
    title: str = "",
    x_label: str = "",
    width: int = 60,
    height: int = 12,
    **kwargs: Any,
) -> None:
    """Render a histogram using custom rich implementation."""
    if not values:
        _console.print("[dim]  No data to chart.[/dim]")
        return

    # Build bins manually
    min_v, max_v = min(values), max(values)
    if min_v == max_v:
        _console.print(f"  [dim]All values are {min_v}[/dim]")
        return

    bin_width = (max_v - min_v) / bins
    counts = [0] * bins
    for v in values:
        idx = min(int((v - min_v) / bin_width), bins - 1)
        counts[idx] += 1

    # Build labels as ranges
    labels = []
    for i in range(bins):
        lo = min_v + i * bin_width
        hi = lo + bin_width
        if isinstance(min_v, float) and not min_v.is_integer():
            labels.append(f"{lo:.1f}–{hi:.1f}")
        else:
            labels.append(f"{int(lo)}–{int(hi)}")

    # Render as horizontal bars
    render_bar_chart(
        labels=labels,
        values=[float(c) for c in counts],
        title=title or f"Distribution of {x_label}" if x_label else "Distribution",
        x_label="count",
        y_label="range",
        width=width - 25,
    )


# ────────────────────────── Heatmap ──────────────────────────

HEATMAP_SHADES = [" ", "░", "░", "▒", "▒", "▓", "▓", "█", "█"]


def render_heatmap(
    grid: list[list[float]],
    row_labels: list[str] | None = None,
    col_labels: list[str] | None = None,
    title: str = "",
    **kwargs: Any,
) -> None:
    """Render a fast-read unicode heatmap.

    Each cell is 3 chars wide (2 shade chars + 1 space) for alignment with
    2-digit column labels. Empty/zero cells show a faint dot. Row totals
    shown on the right for quick scanning. Uses a 5-stop color gradient
    (blue → cyan → yellow → magenta → bright_red) to make patterns pop.
    """
    if not grid or not grid[0]:
        _console.print("[dim]  No data to chart.[/dim]")
        return

    flat = [v for row in grid for v in row if v is not None]
    if not flat:
        _console.print("[dim]  No data to chart.[/dim]")
        return

    max_v = max(flat)
    # Normalize from zero so "cold" cells read as cold, not as mid-scale.
    span = max_v if max_v > 0 else 1.0

    n_rows = len(grid)
    n_cols = len(grid[0])

    row_labels = row_labels or [str(i) for i in range(n_rows)]
    col_labels = col_labels or [str(i) for i in range(n_cols)]

    # Row label column width (cap at 10 for compactness).
    label_w = max((len(str(l)) for l in row_labels), default=0)
    label_w = min(label_w, 10)

    # Pre-compute row totals for the Σ column.
    row_totals: list[float] = [
        sum(v for v in row if v is not None) for row in grid
    ]
    max_total_str = max(
        (f"{int(t):,}" if float(t).is_integer() else f"{t:,.1f}")
        for t in row_totals
    ) if row_totals else "0"
    total_w = max(len(max_total_str), 3)

    # Column header: show every Nth label when > 12 cols.
    # Each cell takes 3 chars (2 shade + 1 space separator).
    cell_w = 3
    if n_cols > 12:
        # Aim for ~6-8 header labels.
        step = max(1, round(n_cols / 6))
    else:
        step = 1

    _console.print()
    if title:
        _console.print(f"  [bold cyan]{title}[/bold cyan]")
        _console.print()

    # Build header row.
    indent = "  "
    header = indent + " " * (label_w + 2)
    for j in range(n_cols):
        if j % step == 0:
            lbl = str(col_labels[j])[:2].rjust(2)
            header += f"{lbl} "
        else:
            header += "   "
    # Σ column header
    header += f" {'Σ'.rjust(total_w)}"
    _console.print(f"[dim]{header}[/dim]")

    # Color stops for 5-band gradient.
    def _cell_style(ratio: float) -> tuple[str, str]:
        """Return (shade_char, color) for a given 0..1 ratio."""
        if ratio <= 0:
            return ("·", "grey39")
        if ratio < 0.2:
            return ("░", "blue")
        if ratio < 0.4:
            return ("░", "cyan")
        if ratio < 0.6:
            return ("▒", "yellow")
        if ratio < 0.8:
            return ("▓", "magenta")
        return ("█", "bright_red")

    # Each data row.
    for i, row in enumerate(grid):
        label_str = str(row_labels[i])[:label_w].ljust(label_w)
        line = f"{indent}[bold]{label_str}[/bold]  "
        for v in row:
            if v is None or v == 0:
                # Empty/zero cell: faint dot so user knows the cell exists.
                line += "[grey39] · [/grey39]"
                continue
            ratio = v / span
            ch, color = _cell_style(ratio)
            line += f"[{color}]{ch}{ch}[/{color}] "

        # Row total.
        total = row_totals[i]
        if float(total).is_integer():
            total_str = f"{int(total):,}"
        else:
            total_str = f"{total:,.1f}"
        line += f" [bold dim]{total_str.rjust(total_w)}[/bold dim]"
        _console.print(line)

    # Compact legend + peak value.
    if float(max_v).is_integer():
        peak_str = f"{int(max_v):,}"
    else:
        peak_str = f"{max_v:,.1f}"
    _console.print()
    _console.print(
        f"{indent}[dim]0[/dim] "
        f"[blue]░[/blue] [dim]cold[/dim]  "
        f"[cyan]░[/cyan] [dim]cool[/dim]  "
        f"[yellow]▒[/yellow] [dim]mild[/dim]  "
        f"[magenta]▓[/magenta] [dim]warm[/dim]  "
        f"[bright_red]█[/bright_red] [dim]hot[/dim]  "
        f"[dim]peak={peak_str}[/dim]"
    )
    _console.print()


# ────────────────────────── Multi-series Line Chart ──────────────────────────

# palette of plotille byte colors (cyan, magenta, yellow, green, red, blue, orange, pink)
_SERIES_COLORS = [51, 201, 226, 46, 196, 33, 208, 213]


def render_multi_line(
    series: dict[str, tuple[list[Any], list[float]]],
    title: str = "",
    x_label: str = "",
    y_label: str = "",
    width: int = 60,
    height: int = 15,
    **kwargs: Any,
) -> None:
    """Render multiple series on the same line chart with a legend."""
    import plotille

    if not series:
        _console.print("[dim]  No data to chart.[/dim]")
        return

    # Validate: need at least one series with 2+ points
    valid = {name: (x, y) for name, (x, y) in series.items() if len(y) >= 2}
    if not valid:
        _console.print("  [yellow]⚠ Multi-line chart needs ≥2 points per series[/yellow]")
        return

    # Collect all x & y for range determination; convert non-numeric x to indices.
    # We keep the first non-numeric series's labels as the canonical x-axis labels
    # so every integer tick maps to a real category/date string.
    all_x_numeric: list[float] = []
    all_y: list[float] = []
    processed: dict[str, tuple[list[float], list[float]]] = {}
    shared_labels: list[str] | None = None

    for name, (x, y) in valid.items():
        if x and not isinstance(x[0], (int, float)):
            x_values = [float(i) for i in range(len(x))]
            if shared_labels is None:
                shared_labels = [str(v) for v in x]
        else:
            x_values = [float(v) for v in x]
        processed[name] = (x_values, [float(v) for v in y])
        all_x_numeric.extend(x_values)
        all_y.extend(float(v) for v in y)

    is_money = _is_money_context(title, y_label)

    fig = plotille.Figure()
    fig.width = width
    fig.height = height
    fig.color_mode = "byte"
    fig.x_label = x_label or ""
    fig.y_label = y_label or ""
    fig.set_x_limits(min_=min(all_x_numeric), max_=max(all_x_numeric))
    fig.set_y_limits(min_=min(all_y), max_=max(all_y))

    # Smart axis formatters — readable tick values
    fig.y_ticks_fkt = _make_y_formatter(all_y, is_money)
    if shared_labels is not None:
        fig.x_ticks_fkt = _make_x_formatter_categorical(shared_labels)
    else:
        fig.x_ticks_fkt = _make_x_formatter_numeric(all_x_numeric)

    colors: dict[str, int] = {}
    for i, (name, (xv, yv)) in enumerate(processed.items()):
        color = _SERIES_COLORS[i % len(_SERIES_COLORS)]
        colors[name] = color
        fig.plot(xv, yv, lc=color, label=name)

    _console.print()
    if title:
        _console.print(f"  [bold cyan]{title}[/bold cyan]")
    _print_raw(fig.show(legend=False))

    # Our own legend (rich-rendered)
    legend_parts = []
    for name, color in colors.items():
        # Map byte color to rough rich color name for legend
        rich_color = _byte_to_rich_name(color)
        legend_parts.append(f"[{rich_color}]━━[/{rich_color}] {name}")
    _console.print("  " + "   ".join(legend_parts))
    _console.print()


def _byte_to_rich_name(byte_color: int) -> str:
    """Map a plotille byte color to a rich color name for legends."""
    mapping = {
        51: "cyan",
        201: "magenta",
        226: "yellow",
        46: "green",
        196: "red",
        33: "blue",
        208: "bright_yellow",
        213: "bright_magenta",
    }
    return mapping.get(byte_color, "white")


# ────────────────────────── Stacked Bar Chart ──────────────────────────

# Rich color names for series segments
_SERIES_RICH_COLORS = [
    "cyan", "magenta", "yellow", "green",
    "blue", "red", "bright_cyan", "bright_magenta",
]


def render_stacked_bar(
    labels: list[str],
    stacks: list[list[float]],
    series_names: list[str],
    title: str = "",
    width: int = 40,
    **kwargs: Any,
) -> None:
    """Render a horizontal stacked bar chart.

    Args:
        labels: one label per bar
        stacks: list of stack values per bar — each inner list is the segments
                for one bar, aligned with series_names
        series_names: name of each stacked series (legend)
    """
    if not labels or not stacks:
        _console.print("[dim]  No data to chart.[/dim]")
        return

    totals = [sum(s) for s in stacks]
    max_total = max(totals) if totals else 0
    if max_total <= 0:
        _console.print("[dim]  All values are zero.[/dim]")
        return

    max_label_len = max(len(str(l)) for l in labels)
    label_w = min(max_label_len, 20)

    _console.print()
    if title:
        _console.print(f"  [bold cyan]{title}[/bold cyan]")
        _console.print()

    for label, stack in zip(labels, stacks):
        label_str = str(label)[:label_w].ljust(label_w)
        total = sum(stack)
        bar = ""
        for i, segment in enumerate(stack):
            if segment <= 0:
                continue
            seg_width = int(round((segment / max_total) * width))
            if seg_width <= 0 and segment > 0:
                seg_width = 1
            color = _SERIES_RICH_COLORS[i % len(_SERIES_RICH_COLORS)]
            bar += f"[{color}]{BAR_FULL * seg_width}[/{color}]"

        if isinstance(total, float) and not float(total).is_integer():
            total_str = f"{total:,.2f}"
        else:
            total_str = f"{int(total):,}"

        _console.print(f"  [dim]{label_str}[/dim]  {bar} [bold]{total_str}[/bold]")

    # Legend
    legend_parts = []
    for i, name in enumerate(series_names):
        color = _SERIES_RICH_COLORS[i % len(_SERIES_RICH_COLORS)]
        legend_parts.append(f"[{color}]█[/{color}] {name}")
    _console.print("\n  " + "   ".join(legend_parts))
    _console.print()


# ────────────────────────── Grouped (Clustered) Bar Chart ──────────────────────────


def render_grouped_bar(
    categories: list[str],
    groups: dict[str, list[float]],
    title: str = "",
    width: int = 30,
    **kwargs: Any,
) -> None:
    """Render side-by-side grouped bars per category.

    Args:
        categories: the category labels (outer groups)
        groups: mapping of series_name → list of values (one per category)
    """
    if not categories or not groups:
        _console.print("[dim]  No data to chart.[/dim]")
        return

    # Find global max value across all series
    all_values = [v for vals in groups.values() for v in vals]
    if not all_values:
        _console.print("[dim]  No data to chart.[/dim]")
        return
    max_val = max(all_values)
    if max_val <= 0:
        _console.print("[dim]  All values are zero.[/dim]")
        return

    series_names = list(groups.keys())
    max_cat_len = max(len(str(c)) for c in categories)
    cat_w = min(max_cat_len, 20)
    max_name_len = max(len(n) for n in series_names)
    name_w = min(max_name_len, 14)

    _console.print()
    if title:
        _console.print(f"  [bold cyan]{title}[/bold cyan]")
        _console.print()

    for ci, cat in enumerate(categories):
        _console.print(f"  [bold]{str(cat)[:cat_w]}[/bold]")
        for si, name in enumerate(series_names):
            value = groups[name][ci] if ci < len(groups[name]) else 0.0
            ratio = value / max_val
            bar_len = int(ratio * width)
            color = _SERIES_RICH_COLORS[si % len(_SERIES_RICH_COLORS)]
            bar = BAR_FULL * bar_len

            if isinstance(value, float) and not float(value).is_integer():
                val_str = f"{value:,.2f}"
            else:
                val_str = f"{int(value):,}"

            name_str = name[:name_w].ljust(name_w)
            _console.print(
                f"    [dim]{name_str}[/dim] [{color}]{bar}[/{color}] {val_str}"
            )
        _console.print()


# ────────────────────────── Sparkline ──────────────────────────

_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float], max_width: int = 20) -> str:
    """Return a tiny inline sparkline string (up to max_width chars)."""
    if not values:
        return ""

    # Downsample if too long
    if len(values) > max_width:
        step = len(values) / max_width
        sampled = [values[int(i * step)] for i in range(max_width)]
    else:
        sampled = list(values)

    min_v = min(sampled)
    max_v = max(sampled)
    span = max_v - min_v if max_v > min_v else 1.0
    n = len(_SPARK_CHARS)

    out = ""
    for v in sampled:
        ratio = (v - min_v) / span
        idx = min(int(ratio * (n - 1)), n - 1)
        out += _SPARK_CHARS[idx]
    return out


# ────────────────────────── Box Plot ──────────────────────────


def _quantile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated quantile."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def render_box_plot(
    data: dict[str, list[float]],
    title: str = "",
    width: int = 60,
    **kwargs: Any,
) -> None:
    """Render a horizontal box-and-whisker plot for each group.

    Design goals:
    - Shared x-axis across all groups for proportional visual comparison
    - Dim whiskers (min→Q1, Q3→max), bright cyan box (Q1→Q3), magenta median
    - Compact stats below each row in consistent alignment
    - Value-scale ticks at the top for reference
    """
    if not data:
        _console.print("[dim]  No data to chart.[/dim]")
        return

    # Compute stats per group
    stats: dict[str, dict[str, float]] = {}
    all_vals: list[float] = []
    for name, vals in data.items():
        if not vals:
            continue
        sv = sorted(float(v) for v in vals)
        s = {
            "min": sv[0],
            "q1": _quantile(sv, 0.25),
            "median": _quantile(sv, 0.5),
            "q3": _quantile(sv, 0.75),
            "max": sv[-1],
        }
        stats[name] = s
        all_vals.extend(sv)

    if not stats:
        _console.print("[dim]  No data to chart.[/dim]")
        return

    global_min = min(all_vals)
    global_max = max(all_vals)
    span = global_max - global_min if global_max > global_min else 1.0

    max_name_len = max(len(n) for n in stats.keys())
    name_w = min(max_name_len, 16)

    # Clamp width to a usable range
    W = max(20, min(width, 120))

    def pos(v: float) -> int:
        """Map value to column in [0, W-1]."""
        return max(0, min(W - 1, int(round(((v - global_min) / span) * (W - 1)))))

    def fmt(v: float) -> str:
        """Compact human-readable number formatting."""
        av = abs(v)
        if av >= 1_000_000:
            return f"{v / 1_000_000:.1f}M"
        if av >= 10_000:
            return f"{v / 1000:.0f}k"
        if av >= 1000:
            return f"{v / 1000:.1f}k"
        if av >= 100 or float(v).is_integer():
            return f"{int(round(v))}"
        return f"{v:.1f}"

    _console.print()
    if title:
        _console.print(f"  [bold cyan]{title}[/bold cyan]")
        _console.print()

    # ─── Top value-scale (axis) ───
    n_ticks = 7
    tick_positions = [int(round(i * (W - 1) / (n_ticks - 1))) for i in range(n_ticks)]
    tick_values = [global_min + (i / (n_ticks - 1)) * span for i in range(n_ticks)]

    # Label row: center each tick label at its position
    label_row = [" "] * W
    for tp, tv in zip(tick_positions, tick_values):
        lbl = fmt(tv)
        start = tp - len(lbl) // 2
        start = max(0, min(W - len(lbl), start))
        for i, ch in enumerate(lbl):
            if 0 <= start + i < W:
                label_row[start + i] = ch
    label_line = "".join(label_row)

    # Axis rule: ├───┼───┼───┼───┼───┼───┤
    axis_chars = ["─"] * W
    for i, tp in enumerate(tick_positions):
        if i == 0:
            axis_chars[tp] = "├"
        elif i == n_ticks - 1:
            axis_chars[tp] = "┤"
        else:
            axis_chars[tp] = "┼"
    axis_line = "".join(axis_chars)

    indent = "  "
    pad = " " * (name_w + 2)
    _console.print(f"{indent}{pad}[dim]{label_line}[/dim]")
    _console.print(f"{indent}{pad}[dim]{axis_line}[/dim]")

    # ─── Token styles for the bar ───
    # DWE=dim whisker endpoint, DWH=dim whisker line, BL/BR=box edges,
    # BX=box fill (bright cyan), MED=median marker (magenta)
    token_style = {
        "DWE": ("dim", "·"),
        "DWH": ("dim", "─"),
        "BL": ("cyan", "┤"),
        "BR": ("cyan", "├"),
        "BX": ("bold cyan", "█"),
        "MED": ("bold magenta", "┃"),
    }

    # ─── Render each group row ───
    for name, s in stats.items():
        p_min = pos(s["min"])
        p_q1 = pos(s["q1"])
        p_med = pos(s["median"])
        p_q3 = pos(s["q3"])
        p_max = pos(s["max"])

        # Enforce ordering so the box never visually inverts
        if p_q3 < p_q1:
            p_q1, p_q3 = p_q3, p_q1
        p_med = max(p_q1, min(p_q3, p_med))
        p_min = min(p_min, p_q1)
        p_max = max(p_max, p_q3)

        row: list[str] = [" "] * W

        # Left whisker: min .. q1 (exclusive of q1)
        for i in range(p_min, p_q1):
            row[i] = "DWH"
        if p_min < p_q1:
            row[p_min] = "DWE"

        # Box fill: q1 .. q3 (inclusive)
        for i in range(p_q1, p_q3 + 1):
            row[i] = "BX"
        row[p_q1] = "BL"
        row[p_q3] = "BR"

        # Median overlay: always draw so the magenta marker is visible,
        # even when the box is narrow (q1≈q3) and the median collides
        # with a box edge.
        row[p_med] = "MED"

        # Right whisker: q3+1 .. max
        for i in range(p_q3 + 1, p_max):
            row[i] = "DWH"
        if p_max > p_q3:
            row[p_max] = "DWE"

        # Convert tokens to rich spans (batch consecutive identical tokens)
        parts: list[str] = []
        i = 0
        while i < W:
            tok = row[i]
            if tok == " ":
                parts.append(" ")
                i += 1
                continue
            style = token_style[tok][0]
            run_chars = ""
            j = i
            while j < W and row[j] == tok:
                run_chars += token_style[row[j]][1]
                j += 1
            parts.append(f"[{style}]{run_chars}[/{style}]")
            i = j

        bar = "".join(parts)
        name_str = name[:name_w].ljust(name_w)
        _console.print(f"{indent}[bold]{name_str}[/bold]  {bar}")

        # Compact stats line beneath, aligned under the bar
        stat_line = (
            f"[dim]min[/dim] [bold]{fmt(s['min'])}[/bold]  "
            f"[dim]q1[/dim] {fmt(s['q1'])}  "
            f"[magenta]med[/magenta] [bold magenta]{fmt(s['median'])}[/bold magenta]  "
            f"[dim]q3[/dim] {fmt(s['q3'])}  "
            f"[dim]max[/dim] [bold]{fmt(s['max'])}[/bold]"
        )
        _console.print(f"{indent}{' ' * name_w}  {stat_line}")

    _console.print()


# ────────────────────────── Helpers ──────────────────────────


def _print_raw(text: str) -> None:
    """Write text directly to stdout preserving ANSI codes."""
    sys.stdout.write(text)
    sys.stdout.write("\n")
    sys.stdout.flush()


def capture_render(render_fn, *args, **kwargs) -> str:
    """Capture the console output of a render function as a string.

    Useful for embedding charts in dashboard panels.
    """
    from io import StringIO

    from rich.console import Console as _C

    buf = StringIO()
    tmp_console = _C(file=buf, force_terminal=True, width=kwargs.pop("_console_width", 100))
    global _console
    original = _console
    _console = tmp_console
    try:
        render_fn(*args, **kwargs)
    finally:
        _console = original
    return buf.getvalue()
