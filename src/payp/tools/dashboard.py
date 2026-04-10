"""Dashboard tool — execute multiple SQL queries and render charts in a grid TUI."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from payp.tools.base import BaseTool, ToolResult
from payp.tools.chart import (
    _group_series,
    _pivot_to_grid,
    _pivot_to_stacks,
)
from payp.ui.charts import (
    capture_render,
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

VALID_LAYOUTS = {"2x2", "1x3", "2x3", "3x2"}


class DashboardTool(BaseTool):
    name = "dashboard"
    description = (
        "Build an interactive grid dashboard from multiple SQL queries. "
        "Each panel runs its own SQL and renders a chart. "
        "Layout: 2x2 (4 panels), 1x3 (3 panels stacked), 2x3 (6 panels), 3x2 (6 panels)."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "panels": {
                    "type": "array",
                    "description": "List of panel definitions",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sql": {"type": "string"},
                            "chart_type": {
                                "type": "string",
                                "enum": [
                                    "bar", "line", "scatter", "histogram",
                                    "heatmap", "multi_line", "stacked_bar",
                                    "grouped_bar", "box_plot",
                                ],
                            },
                            "title": {"type": "string"},
                        },
                        "required": ["sql", "chart_type", "title"],
                    },
                },
                "layout": {
                    "type": "string",
                    "enum": ["2x2", "1x3", "2x3", "3x2"],
                    "description": "Grid layout (default: 2x2)",
                },
                "title": {
                    "type": "string",
                    "description": "Dashboard title",
                },
            },
            "required": ["panels"],
        }

    async def call(self, args: dict[str, Any], context: dict[str, Any]) -> ToolResult:
        from payp.db.connection import ConnectionManager

        conn: ConnectionManager | None = context.get("connection_manager")
        if not conn or not conn.is_connected:
            return ToolResult(success=False, error="Not connected to a database")

        panels_in = args.get("panels", [])
        layout = args.get("layout", "2x2")
        title = args.get("title", "payp dashboard")

        if layout not in VALID_LAYOUTS:
            layout = "2x2"
        if not panels_in:
            return ToolResult(success=False, error="No panels provided")

        rendered: list[dict[str, Any]] = []
        errors: list[str] = []

        for idx, p in enumerate(panels_in):
            sql = p.get("sql", "").strip()
            chart_type = p.get("chart_type", "bar")
            p_title = p.get("title", f"Panel {idx + 1}")
            if not sql:
                errors.append(f"Panel {idx + 1}: no SQL")
                continue
            try:
                rows = await conn.execute_raw(sql)
            except Exception as e:
                rendered.append({
                    "title": p_title,
                    "content": f"[red]Query failed:[/red]\n{e}",
                })
                errors.append(f"Panel {idx + 1}: {e}")
                continue

            if not rows:
                rendered.append({
                    "title": p_title,
                    "content": "[dim]No data[/dim]",
                })
                continue

            try:
                content = _render_panel_content(rows, chart_type, p_title)
            except Exception as e:
                content = f"[red]Render failed:[/red] {e}"
                errors.append(f"Panel {idx + 1}: render {e}")

            rendered.append({"title": p_title, "content": content})

        # Launch the TUI
        try:
            from payp.ui.dashboard_tui import run_dashboard
            run_dashboard(panels=rendered, layout=layout, title=title)
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Dashboard TUI failed: {e}",
                data={"errors": errors, "panels": len(rendered)},
            )

        return ToolResult(
            success=True,
            data={"panels": len(rendered), "layout": layout, "errors": errors},
            summary=f"Dashboard closed ({len(rendered)} panel{'s' if len(rendered) != 1 else ''})",
        )


def _render_panel_content(
    rows: list[dict[str, Any]],
    chart_type: str,
    title: str,
) -> str:
    """Render a single chart for a dashboard panel as an ANSI string."""
    columns = list(rows[0].keys())

    def _bar() -> None:
        labels = [str(r[columns[0]]) for r in rows]
        values = [float(r[columns[1]]) for r in rows if r[columns[1]] is not None]
        render_bar_chart(labels, values, title="", width=24, _console_width=60)

    def _line() -> None:
        x = [r[columns[0]] for r in rows]
        y = [float(r[columns[1]]) for r in rows if r[columns[1]] is not None]
        render_line_chart(x, y, title="", width=40, height=10)

    def _scatter() -> None:
        x = [float(r[columns[0]]) for r in rows if r[columns[0]] is not None]
        y = [float(r[columns[1]]) for r in rows if r[columns[1]] is not None]
        render_scatter(x, y, title="", width=40, height=10)

    def _hist() -> None:
        values = [float(r[columns[0]]) for r in rows if r[columns[0]] is not None]
        render_histogram(values, title="", x_label=columns[0], width=50)

    def _heatmap() -> None:
        grid, rl, cl = _pivot_to_grid(rows, columns[0], columns[1], columns[2])
        render_heatmap(grid, rl, cl, title="")

    def _ml() -> None:
        series = _group_series(rows, columns[0], columns[1], columns[2])
        render_multi_line(series, title="", width=40, height=10)

    def _sb() -> None:
        labels, stacks, names = _pivot_to_stacks(rows, columns[0], columns[1], columns[2])
        render_stacked_bar(labels, stacks, names, title="", width=24)

    def _gb() -> None:
        labels, stacks, names = _pivot_to_stacks(rows, columns[0], columns[1], columns[2])
        groups: dict[str, list[float]] = OrderedDict((n, []) for n in names)
        for stack in stacks:
            for i, n in enumerate(names):
                groups[n].append(stack[i])
        render_grouped_bar(labels, groups, title="", width=20)

    def _bp() -> None:
        data: dict[str, list[float]] = OrderedDict()
        for r in rows:
            g = str(r[columns[0]])
            v = r[columns[1]]
            if v is not None:
                data.setdefault(g, []).append(float(v))
        render_box_plot(data, title="", width=40)

    dispatch = {
        "bar": _bar, "line": _line, "scatter": _scatter,
        "histogram": _hist, "heatmap": _heatmap, "multi_line": _ml,
        "stacked_bar": _sb, "grouped_bar": _gb, "box_plot": _bp,
    }
    fn = dispatch.get(chart_type, _bar)
    return capture_render(fn)
