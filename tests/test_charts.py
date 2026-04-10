"""Tests for advanced chart types.

Runs each render function with sample data, prints output, verifies no errors.
Run with: pytest tests/test_charts.py -s
Or directly: python tests/test_charts.py
"""

from __future__ import annotations

import math
import random

from payp.ui.charts import (
    render_box_plot,
    render_grouped_bar,
    render_heatmap,
    render_multi_line,
    render_stacked_bar,
    sparkline,
)

random.seed(42)


# ────────────────────────── Sample data ──────────────────────────


def _heatmap_data() -> tuple[list[list[float]], list[str], list[str]]:
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    hours = [f"{h:02d}" for h in range(0, 24, 2)]
    grid = []
    for d_idx, _ in enumerate(days):
        row = []
        for h_idx, _ in enumerate(hours):
            # peak around noon, higher on weekdays
            base = 20 + 80 * math.exp(-((h_idx - 6) ** 2) / 6)
            if d_idx < 5:
                base *= 1.4
            row.append(round(base + random.uniform(-10, 10), 1))
        grid.append(row)
    return grid, days, hours


def _multi_line_data() -> dict[str, tuple[list, list]]:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"]
    return {
        "revenue": (months, [100, 120, 115, 140, 165, 180, 200, 225]),
        "orders": (months, [45, 52, 48, 61, 70, 78, 85, 92]),
        "refunds": (months, [5, 7, 6, 8, 9, 10, 12, 13]),
    }


def _stacked_bar_data() -> tuple[list[str], list[list[float]], list[str]]:
    labels = ["Q1", "Q2", "Q3", "Q4"]
    series = ["web", "mobile", "api"]
    stacks = [
        [120.0, 80.0, 40.0],
        [150.0, 110.0, 55.0],
        [180.0, 140.0, 70.0],
        [200.0, 170.0, 90.0],
    ]
    return labels, stacks, series


def _grouped_bar_data() -> tuple[list[str], dict[str, list[float]]]:
    categories = ["North", "South", "East", "West"]
    groups = {
        "2023": [120.0, 90.0, 110.0, 80.0],
        "2024": [140.0, 105.0, 135.0, 95.0],
        "2025": [170.0, 125.0, 160.0, 115.0],
    }
    return categories, groups


def _box_plot_data() -> dict[str, list[float]]:
    return {
        "us-east": [random.gauss(100, 15) for _ in range(50)],
        "us-west": [random.gauss(120, 20) for _ in range(50)],
        "eu-west": [random.gauss(140, 25) for _ in range(50)],
        "ap-south": [random.gauss(180, 30) for _ in range(50)],
    }


# ────────────────────────── Test functions ──────────────────────────


def test_heatmap() -> None:
    grid, rows, cols = _heatmap_data()
    render_heatmap(grid, rows, cols, title="Traffic by day × hour")


def test_multi_line() -> None:
    series = _multi_line_data()
    render_multi_line(
        series,
        title="Key metrics over time",
        x_label="month",
        y_label="value",
    )


def test_stacked_bar() -> None:
    labels, stacks, names = _stacked_bar_data()
    render_stacked_bar(labels, stacks, names, title="Revenue by channel (stacked)")


def test_grouped_bar() -> None:
    cats, groups = _grouped_bar_data()
    render_grouped_bar(cats, groups, title="Sales by region (grouped by year)")


def test_box_plot() -> None:
    data = _box_plot_data()
    render_box_plot(data, title="Latency distribution by region (ms)")


def test_sparkline() -> None:
    values = [random.uniform(10, 100) for _ in range(30)]
    spark = sparkline(values)
    assert len(spark) <= 20
    assert len(spark) > 0
    print(f"\n  Sparkline (30 values → 20 chars): {spark}")

    # Short series (no downsample)
    spark2 = sparkline([1, 3, 2, 8, 5, 4, 9, 7])
    assert len(spark2) == 8
    print(f"  Sparkline (8 values):             {spark2}")

    # Edge cases
    assert sparkline([]) == ""
    assert sparkline([5.0]) != ""


def test_sparkline_monotonic() -> None:
    """Sparkline with monotonic series should show increasing blocks."""
    spark = sparkline(list(range(1, 21)))
    print(f"\n  Monotonic sparkline: {spark}")
    assert len(spark) == 20


# ────────────────────────── Run all when invoked directly ──────────────────────────


if __name__ == "__main__":
    import sys
    print("\n" + "=" * 70)
    print("  payp advanced charts — test suite")
    print("=" * 70)

    tests = [
        ("HEATMAP", test_heatmap),
        ("MULTI-LINE CHART", test_multi_line),
        ("STACKED BAR CHART", test_stacked_bar),
        ("GROUPED BAR CHART", test_grouped_bar),
        ("BOX PLOT", test_box_plot),
        ("SPARKLINE", test_sparkline),
        ("SPARKLINE (monotonic)", test_sparkline_monotonic),
    ]

    failures = 0
    for name, fn in tests:
        print(f"\n{'─' * 70}\n  → {name}\n{'─' * 70}")
        try:
            fn()
            print(f"  [OK] {name}")
        except Exception as e:
            failures += 1
            print(f"  [FAIL] {name}: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{'=' * 70}")
    print(f"  {len(tests) - failures}/{len(tests)} tests passed")
    print("=" * 70)
    sys.exit(0 if failures == 0 else 1)
