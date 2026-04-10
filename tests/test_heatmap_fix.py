"""Visual test harness for the refactored render_heatmap.

Prints 3 scenarios. Run directly:
    python -m tests.test_heatmap_fix
or via pytest (it will print output and always pass).
"""

from __future__ import annotations

import random

from payp.ui.charts import render_heatmap

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
HOURS_24 = [f"{h:02d}" for h in range(24)]
HOURS_12 = [f"{h:02d}" for h in range(0, 24, 2)]  # 12 bins of 2 hours


def _dense_7x24() -> list[list[float]]:
    """Order volume by day-of-week × hour with realistic peaks."""
    random.seed(42)
    grid: list[list[float]] = []
    for d_idx, _ in enumerate(DAYS):
        row: list[float] = []
        for h in range(24):
            # Business-hours peak (10-18), tapering at night, weekend dip.
            base = 0.0
            if 10 <= h <= 18:
                base = 20 + (h - 10) * 3
            elif 8 <= h < 10 or 18 < h <= 21:
                base = 8 + random.random() * 5
            elif 0 <= h < 6:
                base = 0  # nightly zeros
            else:
                base = 2 + random.random() * 3
            # Weekend dampening.
            if d_idx >= 5:
                base *= 0.25
            # Friday peak boost.
            if d_idx == 4 and 14 <= h <= 18:
                base *= 1.4
            row.append(round(base + random.random() * 3, 0))
        grid.append(row)
    return grid


def _sparse_5x5() -> list[list[float]]:
    """Mostly zeros, 3 hotspots."""
    grid = [[0.0 for _ in range(5)] for _ in range(5)]
    grid[0][2] = 15
    grid[2][4] = 42
    grid[4][0] = 8
    return grid


def _medium_7x12() -> list[list[float]]:
    """Order volume by day × 2-hour bin."""
    random.seed(7)
    grid: list[list[float]] = []
    for d_idx, _ in enumerate(DAYS):
        row: list[float] = []
        for b in range(12):
            hour = b * 2
            if 10 <= hour <= 18:
                val = 15 + random.random() * 20
            elif 0 <= hour < 6:
                val = 0
            else:
                val = random.random() * 8
            if d_idx >= 5:
                val *= 0.3
            row.append(round(val, 0))
        grid.append(row)
    return grid


def test_dense_7x24() -> None:
    render_heatmap(
        grid=_dense_7x24(),
        row_labels=DAYS,
        col_labels=HOURS_24,
        title="Order Volume by Hour × Day (7×24 dense)",
    )


def test_sparse_5x5() -> None:
    render_heatmap(
        grid=_sparse_5x5(),
        row_labels=["Region A", "Region B", "Region C", "Region D", "Region E"],
        col_labels=["Q1", "Q2", "Q3", "Q4", "Q5"],
        title="Sparse Signals (5×5, mostly zero)",
    )


def test_medium_7x12() -> None:
    render_heatmap(
        grid=_medium_7x12(),
        row_labels=DAYS,
        col_labels=HOURS_12,
        title="Order Volume by 2h Bin × Day (7×12 medium)",
    )


if __name__ == "__main__":
    print("\n" + "=" * 72)
    print("TEST 1: Dense 7×24 (day × hour)")
    print("=" * 72)
    test_dense_7x24()

    print("\n" + "=" * 72)
    print("TEST 2: Sparse 5×5 (mostly zeros)")
    print("=" * 72)
    test_sparse_5x5()

    print("\n" + "=" * 72)
    print("TEST 3: Medium 7×12 (day × 2h bin)")
    print("=" * 72)
    test_medium_7x12()
