"""Chart tool — render SQL query results as terminal charts."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from payp.tools.base import BaseTool, ToolResult
from payp.ui.charts import (
    render_bar_chart,
    render_box_plot,
    render_grouped_bar,
    render_heatmap,
    render_histogram,
    render_line_chart,
    render_multi_line,
    render_scatter,
    render_stacked_bar,
)


class ChartTool(BaseTool):
    name = "chart"
    description = (
        "Render a SQL query result as a terminal chart. "
        "Use for visualizing data: revenue over time, counts by category, distributions, "
        "heatmaps, multi-series, stacked/grouped comparisons, box plots. "
        "SQL column requirements by chart type: "
        "bar/line (label, value); "
        "scatter (x, y); "
        "histogram (single numeric column); "
        "heatmap (row_label, col_label, value); "
        "multi_line (x, series_name, y); "
        "stacked_bar / grouped_bar (category, series_name, value); "
        "box_plot (group_name, value)."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "SELECT query returning columns for the chart",
                },
                "chart_type": {
                    "type": "string",
                    "enum": [
                        "bar", "line", "scatter", "histogram",
                        "heatmap", "multi_line", "stacked_bar",
                        "grouped_bar", "box_plot",
                    ],
                    "description": (
                        "Chart type: bar, line, scatter, histogram, "
                        "heatmap (3 cols), multi_line (x,series,y), "
                        "stacked_bar/grouped_bar (cat,series,val), box_plot (group,val)"
                    ),
                },
                "title": {"type": "string", "description": "Chart title"},
                "x_label": {"type": "string", "description": "X-axis label"},
                "y_label": {"type": "string", "description": "Y-axis label"},
            },
            "required": ["sql", "chart_type"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.db.connection import ConnectionManager

        conn: ConnectionManager | None = context.get("connection_manager")
        if not conn or not conn.is_connected:
            return ToolResult(success=False, error="Not connected to a database")

        sql = args.get("sql", "").strip()
        chart_type = args.get("chart_type", "bar")
        title = args.get("title", "")
        x_label = args.get("x_label", "")
        y_label = args.get("y_label", "")

        if not sql:
            return ToolResult(success=False, error="No SQL provided")

        try:
            rows = await conn.execute_raw(sql)
        except Exception as e:
            import logging
            logging.getLogger("payp.tools.chart").exception("ChartTool query failed")
            return ToolResult(success=False, error=f"Query failed: {e}")

        if not rows:
            return ToolResult(success=True, data={"rows": 0}, summary="No data to chart")

        columns = list(rows[0].keys())

        try:
            if chart_type == "histogram":
                if len(columns) < 1:
                    return ToolResult(success=False, error="Histogram needs 1 column")
                values = [float(r[columns[0]]) for r in rows if r[columns[0]] is not None]
                render_histogram(
                    values,
                    title=title or f"Distribution of {columns[0]}",
                    x_label=x_label or columns[0],
                )

            elif chart_type == "scatter":
                if len(columns) < 2:
                    return ToolResult(success=False, error="Scatter needs 2 columns")
                x = [float(r[columns[0]]) for r in rows if r[columns[0]] is not None]
                y = [float(r[columns[1]]) for r in rows if r[columns[1]] is not None]
                render_scatter(
                    x, y,
                    title=title or f"{columns[1]} vs {columns[0]}",
                    x_label=x_label or columns[0],
                    y_label=y_label or columns[1],
                )

            elif chart_type == "line":
                if len(columns) < 2:
                    return ToolResult(success=False, error="Line chart needs 2 columns")
                x = [r[columns[0]] for r in rows]
                y = [float(r[columns[1]]) for r in rows if r[columns[1]] is not None]
                render_line_chart(
                    x, y,
                    title=title or f"{columns[1]} over {columns[0]}",
                    x_label=x_label or columns[0],
                    y_label=y_label or columns[1],
                )

            elif chart_type == "heatmap":
                if len(columns) < 3:
                    return ToolResult(
                        success=False,
                        error="Heatmap needs 3 columns (row_label, col_label, value)",
                    )
                grid, row_labels, col_labels = _pivot_to_grid(
                    rows, columns[0], columns[1], columns[2]
                )
                render_heatmap(
                    grid, row_labels, col_labels,
                    title=title or f"{columns[2]} by {columns[0]} × {columns[1]}",
                )

            elif chart_type == "multi_line":
                if len(columns) < 3:
                    return ToolResult(
                        success=False,
                        error="Multi-line needs 3 columns (x, series_name, y)",
                    )
                series = _group_series(rows, columns[0], columns[1], columns[2])
                render_multi_line(
                    series,
                    title=title or f"{columns[2]} over {columns[0]} by {columns[1]}",
                    x_label=x_label or columns[0],
                    y_label=y_label or columns[2],
                )

            elif chart_type == "stacked_bar":
                if len(columns) < 3:
                    return ToolResult(
                        success=False,
                        error="Stacked bar needs 3 columns (category, series_name, value)",
                    )
                labels, stacks, series_names = _pivot_to_stacks(
                    rows, columns[0], columns[1], columns[2]
                )
                render_stacked_bar(
                    labels, stacks, series_names,
                    title=title or f"{columns[2]} by {columns[0]} (stacked by {columns[1]})",
                )

            elif chart_type == "grouped_bar":
                if len(columns) < 3:
                    return ToolResult(
                        success=False,
                        error="Grouped bar needs 3 columns (category, series_name, value)",
                    )
                labels, stacks, series_names = _pivot_to_stacks(
                    rows, columns[0], columns[1], columns[2]
                )
                # Convert stacks (per-category lists) to groups (per-series lists)
                groups: dict[str, list[float]] = {name: [] for name in series_names}
                for stack in stacks:
                    for i, name in enumerate(series_names):
                        groups[name].append(stack[i])
                render_grouped_bar(
                    labels, groups,
                    title=title or f"{columns[2]} by {columns[0]} (grouped by {columns[1]})",
                )

            elif chart_type == "box_plot":
                if len(columns) < 2:
                    return ToolResult(
                        success=False,
                        error="Box plot needs 2 columns (group_name, value)",
                    )
                groups_data: dict[str, list[float]] = OrderedDict()
                for r in rows:
                    g = str(r[columns[0]])
                    v = r[columns[1]]
                    if v is None:
                        continue
                    groups_data.setdefault(g, []).append(float(v))
                render_box_plot(
                    groups_data,
                    title=title or f"Distribution of {columns[1]} by {columns[0]}",
                )

            else:  # bar
                if len(columns) < 2:
                    return ToolResult(success=False, error="Bar chart needs 2 columns")
                labels = [str(r[columns[0]]) for r in rows]
                values = [float(r[columns[1]]) for r in rows if r[columns[1]] is not None]
                render_bar_chart(
                    labels, values,
                    title=title or f"{columns[1]} by {columns[0]}",
                    x_label=x_label or columns[0],
                    y_label=y_label or columns[1],
                )

            return ToolResult(
                success=True,
                data={"chart_type": chart_type, "rows": len(rows)},
                summary=f"{chart_type} chart rendered ({len(rows)} points)",
            )

        except Exception as e:
            import logging
            logging.getLogger("payp.tools.chart").exception("ChartTool render failed")
            return ToolResult(success=False, error=f"Chart failed: {e}")


# ────────────────────────── Pivot helpers ──────────────────────────


def _pivot_to_grid(
    rows: list[dict[str, Any]],
    row_col: str,
    col_col: str,
    val_col: str,
) -> tuple[list[list[float]], list[str], list[str]]:
    """Pivot rows into a 2D grid.

    Returns (grid, row_labels, col_labels). Missing cells are 0.0.
    """
    row_labels: list[str] = []
    col_labels: list[str] = []
    row_seen: set[str] = set()
    col_seen: set[str] = set()

    for r in rows:
        rl = str(r[row_col])
        cl = str(r[col_col])
        if rl not in row_seen:
            row_seen.add(rl)
            row_labels.append(rl)
        if cl not in col_seen:
            col_seen.add(cl)
            col_labels.append(cl)

    row_idx = {l: i for i, l in enumerate(row_labels)}
    col_idx = {l: i for i, l in enumerate(col_labels)}

    grid: list[list[float]] = [
        [0.0 for _ in col_labels] for _ in row_labels
    ]
    for r in rows:
        v = r[val_col]
        if v is None:
            continue
        grid[row_idx[str(r[row_col])]][col_idx[str(r[col_col])]] = float(v)

    return grid, row_labels, col_labels


def _group_series(
    rows: list[dict[str, Any]],
    x_col: str,
    series_col: str,
    y_col: str,
) -> dict[str, tuple[list[Any], list[float]]]:
    """Group rows into {series_name: (x_list, y_list)}."""
    series: dict[str, tuple[list[Any], list[float]]] = OrderedDict()
    for r in rows:
        name = str(r[series_col])
        x = r[x_col]
        y = r[y_col]
        if y is None:
            continue
        if name not in series:
            series[name] = ([], [])
        series[name][0].append(x)
        series[name][1].append(float(y))
    return series


def _pivot_to_stacks(
    rows: list[dict[str, Any]],
    cat_col: str,
    series_col: str,
    val_col: str,
) -> tuple[list[str], list[list[float]], list[str]]:
    """Pivot rows into (categories, stacks, series_names).

    stacks[i][j] = value of series_names[j] for categories[i]. Missing = 0.0.
    """
    categories: list[str] = []
    series_names: list[str] = []
    cat_seen: set[str] = set()
    series_seen: set[str] = set()

    for r in rows:
        c = str(r[cat_col])
        s = str(r[series_col])
        if c not in cat_seen:
            cat_seen.add(c)
            categories.append(c)
        if s not in series_seen:
            series_seen.add(s)
            series_names.append(s)

    cat_idx = {c: i for i, c in enumerate(categories)}
    series_idx = {s: i for i, s in enumerate(series_names)}

    stacks: list[list[float]] = [
        [0.0 for _ in series_names] for _ in categories
    ]
    for r in rows:
        v = r[val_col]
        if v is None:
            continue
        stacks[cat_idx[str(r[cat_col])]][series_idx[str(r[series_col])]] = float(v)

    return categories, stacks, series_names
