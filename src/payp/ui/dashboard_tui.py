"""Textual-based dashboard TUI — grid of charts, interactive.

Displays multiple pre-rendered chart panels in a grid layout.
Press 'q' to exit.
"""

from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid
from textual.widgets import Footer, Header, Static

# Grid layouts: (columns, rows)
GRID_LAYOUTS: dict[str, tuple[int, int]] = {
    "1x3": (1, 3),
    "2x2": (2, 2),
    "2x3": (3, 2),
    "3x2": (2, 3),
}


class ChartPanel(Static):
    """A single chart panel in the dashboard grid."""

    DEFAULT_CSS = """
    ChartPanel {
        border: round cyan;
        padding: 1 1;
        background: $surface;
        content-align: left top;
        overflow: auto;
    }
    ChartPanel:focus {
        border: round magenta;
    }
    """

    def __init__(self, title: str, content: str, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self.border_title = title


class DashboardApp(App[None]):
    """Textual dashboard app — shows a grid of pre-rendered charts."""

    CSS = """
    Screen {
        background: $background;
    }

    #dashboard-grid {
        grid-gutter: 1 1;
        padding: 1 1;
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("escape", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(
        self,
        panels: list[dict[str, Any]],
        layout: str = "2x2",
        title: str = "payp dashboard",
    ) -> None:
        super().__init__()
        self.panels_data = panels
        self.layout_name = layout if layout in GRID_LAYOUTS else "2x2"
        self.title = title

    def compose(self) -> ComposeResult:
        cols, rows = GRID_LAYOUTS[self.layout_name]
        grid = Grid(id="dashboard-grid")
        # Set grid size dynamically
        grid.styles.grid_size_columns = cols
        grid.styles.grid_size_rows = rows

        yield Header(show_clock=True)
        with grid:
            max_panels = cols * rows
            for panel in self.panels_data[:max_panels]:
                title = panel.get("title", "Chart")
                content = panel.get("content", "(empty)")
                yield ChartPanel(title=title, content=content)
            # Fill with empty panels if needed
            for _ in range(max_panels - len(self.panels_data)):
                yield ChartPanel(title="—", content="[dim](empty panel)[/dim]")
        yield Footer()

    def action_refresh(self) -> None:
        """Refresh bindings action — placeholder for future live refresh."""
        self.refresh()


def run_dashboard(
    panels: list[dict[str, Any]],
    layout: str = "2x2",
    title: str = "payp dashboard",
) -> None:
    """Launch the dashboard TUI.

    Args:
        panels: list of {"title": str, "content": str (rich markup or ANSI)}
        layout: "2x2", "1x3", "2x3", "3x2"
        title: dashboard window title
    """
    app = DashboardApp(panels=panels, layout=layout, title=title)
    app.run()
