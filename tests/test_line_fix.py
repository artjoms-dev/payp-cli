"""Tests for smart axis formatting on line charts.

Verifies render_line_chart / render_multi_line produce scannable axes:
- Y values auto-format by magnitude (no 3093.72267 noise)
- $ prefix when title/y_label hints at money
- X-axis categorical labels render at integer positions only
"""

from __future__ import annotations

import io
import contextlib

from payp.ui.charts import (
    capture_render,
    render_line_chart,
    render_multi_line,
)


def _run(render_fn, **kwargs) -> str:
    """Render with a fixed console width, capturing BOTH rich and raw stdout."""
    stdout_buf = io.StringIO()
    with contextlib.redirect_stdout(stdout_buf):
        rich_part = capture_render(render_fn, _console_width=120, **kwargs)
    # Rich output + raw braille stdout, interleaved loosely
    return rich_part + "\n" + stdout_buf.getvalue()


def test_currency_data_with_month_labels() -> None:
    """1K–3.5K revenue series with 7 months → $ prefix + K suffix + real month names."""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul"]
    values = [1000, 1400, 1850, 2200, 2700, 3100, 3500]

    out = _run(
        render_line_chart,
        x=months,
        y=values,
        title="Revenue Over Time",
        y_label="revenue",
        width=50,
        height=10,
    )

    # Money prefix applied
    assert "$" in out, "expected $ prefix when title contains 'Revenue'"
    # K suffix for values >= 1000 (since abs_max=3500 is < 10K, uses thousands separator)
    # abs_max < 10_000 → comma-format, so expect "3,500" not "3.5K"
    assert "3,500" in out or "3.5K" in out
    assert "1,000" in out or "1.0K" in out
    # No garbage fractional decimals
    assert "3093.7" not in out
    assert "885.3" not in out
    # At least 3 month labels should appear on the x-axis
    visible_months = [m for m in months if m in out]
    assert len(visible_months) >= 3, f"expected several month labels, got {visible_months}"
    # First and last must always appear
    assert "Jan" in out and "Jul" in out
    # No fractional index positions like "1.33" or "2.67"
    assert "1.33" not in out
    assert "2.67" not in out
    print("\n[currency + months]\n" + out)


def test_small_ints_numeric_x() -> None:
    """0–50 integer series with numeric x → bare integers on Y, ints on X, no $."""
    x = list(range(10))
    y = [0, 5, 12, 18, 25, 30, 38, 42, 48, 50]

    out = _run(
        render_line_chart,
        x=x,
        y=y,
        title="Count Over Steps",
        y_label="count",
        width=50,
        height=10,
    )

    # No money prefix for generic counts
    assert "$" not in out
    # Values 10–999 → bare integers (no decimals)
    assert "50" in out
    assert "25" in out or "24" in out or "26" in out  # mid-range tick
    # No useless decimals like "50.00"
    assert "50.00" not in out
    assert "25.00" not in out
    # X-axis should show integer positions
    assert " 0" in out or "0 " in out
    assert "9" in out
    print("\n[small ints numeric]\n" + out)


def test_huge_values_millions() -> None:
    """1M–5M range → M suffix."""
    x = ["Q1", "Q2", "Q3", "Q4"]
    y = [1_000_000, 2_200_000, 3_500_000, 5_000_000]

    out = _run(
        render_line_chart,
        x=x,
        y=y,
        title="Annual Sales",
        y_label="sales",
        width=50,
        height=10,
    )

    # M suffix applied since abs_max >= 1M
    assert "M" in out
    assert "5.0M" in out or "5M" in out
    assert "1.0M" in out or "1M" in out
    # $ prefix (sales is a money keyword)
    assert "$" in out
    # Quarter labels visible
    assert "Q1" in out and "Q4" in out
    print("\n[millions]\n" + out)


def test_multi_line_two_series() -> None:
    """Two series sharing categorical x labels → smart axes apply to both."""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
    series = {
        "Revenue": (months, [1200.0, 1500.0, 1800.0, 2100.0, 2500.0, 3000.0]),
        "Costs": (months, [800.0, 900.0, 1100.0, 1300.0, 1500.0, 1700.0]),
    }

    out = _run(
        render_multi_line,
        series=series,
        title="Revenue vs Costs",
        y_label="amount",
        width=55,
        height=10,
    )

    # Money prefix (Revenue/Costs in title)
    assert "$" in out
    # Month labels visible
    assert "Jan" in out and "Jun" in out
    # No fractional index garbage
    assert "1.33" not in out
    assert "2.67" not in out
    # Legend present
    assert "Revenue" in out and "Costs" in out
    # Y values look clean
    assert "3,000" in out or "3.0K" in out or "3K" in out
    print("\n[multi-line]\n" + out)


if __name__ == "__main__":
    test_currency_data_with_month_labels()
    test_small_ints_numeric_x()
    test_huge_values_millions()
    test_multi_line_two_series()
    print("\nAll 4 line-chart tests passed.")
