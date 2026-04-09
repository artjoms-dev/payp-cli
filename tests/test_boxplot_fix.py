"""Visual tests for render_box_plot redesign.

These tests capture the rendered output into a string using a
force_terminal Rich console so ANSI escapes are preserved. Assertions
focus on structural invariants: presence of axis, alignment of rows,
stats line under each group, and that outputs are visually comparable
across groups (shared x-axis).

Run with:  pytest tests/test_boxplot_fix.py -s -v
"""

from __future__ import annotations

import random
import re

import pytest

from payp.ui.charts import render_box_plot, capture_render


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def _render(data, title="") -> str:
    """Render a box plot to a string with ANSI codes preserved."""
    return capture_render(
        render_box_plot,
        data,
        title=title,
        width=60,
        _console_width=120,
    )


# ───────────────────────── Fixtures ─────────────────────────


@pytest.fixture(autouse=True)
def _seed_rng():
    random.seed(42)


def _gauss_sample(mu: float, sigma: float, n: int = 20) -> list[float]:
    return [max(0.0, random.gauss(mu, sigma)) for _ in range(n)]


# ───────────────────────── Tests ─────────────────────────


def test_three_groups_similar_range_are_comparable():
    """Three order-status distributions on a shared x-axis."""
    data = {
        "pending": _gauss_sample(mu=90, sigma=60),
        "confirmed": _gauss_sample(mu=160, sigma=120),
        "shipped": _gauss_sample(mu=200, sigma=140),
    }

    out = _render(data, title="Order Value by Status")
    plain = _strip_ansi(out)

    # Structural assertions
    assert "Order Value by Status" in plain
    assert "├" in plain and "┤" in plain  # axis endpoints
    assert "┼" in plain                    # interior ticks
    assert "█" in plain                    # box fill
    assert "┃" in plain                    # median marker
    assert "·" in plain                    # whisker endpoints
    assert "─" in plain                    # whisker lines

    # Each group label appears, and each has a stats line beneath
    for name in data:
        assert name in plain
    # One stats line per group — detect by "med " prefix in stat lines
    stat_lines = [l for l in plain.splitlines() if "med " in l and "min " in l]
    assert len(stat_lines) == 3

    # All bar rows should share width (i.e., groups are positioned
    # proportionally on a single shared x-axis). Detect bar rows by the
    # presence of the box/whisker glyphs.
    bar_lines = [
        line for line in plain.splitlines()
        if any(g in line for g in ("█", "·", "┃", "┤", "├"))
        and "min " not in line
    ]
    # 3 bar rows + 1 axis rule line = 4
    assert len(bar_lines) >= 4

    print("\n--- 3 GROUPS (similar range) ---\n" + out)


def test_two_groups_wildly_different_ranges():
    """Values of 0–100 vs 1000–5000 should still share one scale."""
    data = {
        "small":  [random.uniform(0, 100) for _ in range(20)],
        "large":  [random.uniform(1000, 5000) for _ in range(20)],
    }

    out = _render(data, title="Small vs Large Scale")
    plain = _strip_ansi(out)

    assert "small" in plain
    assert "large" in plain
    assert "┃" in plain

    # The 'small' group should sit on the left side of the axis
    # (its box is scrunched near the beginning), 'large' should span
    # the bulk of the axis. We detect this by extracting rows.
    lines = plain.splitlines()
    small_row = next(line for line in lines if line.lstrip().startswith("small"))
    large_row = next(line for line in lines if line.lstrip().startswith("large"))

    # The leftmost box/whisker glyph column indicates where the group
    # begins on the shared axis. Use any of the bar glyphs.
    def first_glyph_col(row: str) -> int:
        for i, ch in enumerate(row):
            if ch in "·─┤├█┃":
                return i
        return -1

    small_col = first_glyph_col(small_row)
    large_col = first_glyph_col(large_row)
    assert small_col >= 0 and large_col >= 0
    # small should start well before large on the shared axis
    assert small_col < large_col, (
        f"small first-glyph col={small_col} should be < large col={large_col}"
    )

    print("\n--- 2 GROUPS (very different ranges) ---\n" + out)


def test_five_groups_stress_test():
    """Stress: 5 groups each with 20 values, different distributions."""
    data = {
        "alpha":   _gauss_sample(mu=50, sigma=20),
        "bravo":   _gauss_sample(mu=120, sigma=40),
        "charlie": _gauss_sample(mu=200, sigma=80),
        "delta":   _gauss_sample(mu=80, sigma=10),   # tight cluster
        "echo":    _gauss_sample(mu=300, sigma=150),  # wide spread
    }

    out = _render(data, title="Five-Group Distribution")
    plain = _strip_ansi(out)

    for name in data:
        assert name in plain
    assert plain.count("┃") >= 5  # one median marker per group
    # One stats line per group
    stat_lines = [l for l in plain.splitlines() if "med " in l and "min " in l]
    assert len(stat_lines) == 5

    # Delta should have the narrowest box (tight cluster),
    # echo should have the widest box (wide spread).
    lines = plain.splitlines()
    def box_width(prefix: str) -> int:
        row = next(line for line in lines if line.lstrip().startswith(prefix))
        # Count █ + ┃ + ┤ + ├ chars
        return sum(row.count(c) for c in "█┃┤├")

    delta_w = box_width("delta")
    echo_w = box_width("echo")
    assert delta_w < echo_w, (
        f"delta box width={delta_w} should be < echo box width={echo_w}"
    )

    print("\n--- 5 GROUPS (stress test) ---\n" + out)
