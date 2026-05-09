"""Analytics Dashboard for Haven TUI.

Pipeline analytics view showing performance metrics including:
- Videos processed per day/week
- Average time per stage
- Success/failure rates
- Plugin usage distribution
- Throughput trends

Visual bar charts are rendered in ASCII for terminal display.
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from textual.widgets import Static, DataTable, Header, Footer, Button
from textual.containers import Container, Horizontal, Vertical, Grid
from textual.reactive import reactive
from textual.screen import Screen
from textual.coordinate import Coordinate

from haven_tui.data.repositories import AnalyticsRepository
from haven_tui.config import HavenTUIConfig


@dataclass
class MetricCard:
    """Data for a single metric card."""
    title: str
    value: str
    subtitle: Optional[str] = None
    trend: Optional[str] = None  # "up", "down", "neutral"


class ASCIIBarChart(Static):
    """ASCII bar chart widget for displaying metrics.
    
    Renders horizontal or vertical bar charts using Unicode block characters.
    """
    
    DEFAULT_CSS = """
    ASCIIBarChart {
        height: auto;
        width: 100%;
        padding: 1;
    }
    """
    
    def __init__(
        self,
        title: str = "",
        data: Optional[Dict[str, int]] = None,
        max_bar_width: int = 20,
        unit: str = "",
        show_values: bool = True,
        **kwargs: Any,
    ) -> None:
        """Initialize the bar chart.
        
        Args:
            title: Chart title
            data: Dictionary mapping labels to values
            max_bar_width: Maximum width of bars in characters
            unit: Unit suffix for values
            show_values: Whether to show numeric values
            **kwargs: Additional arguments passed to Static
        """
        super().__init__(**kwargs)
        self.chart_title = title
        self.data = data or {}
        self.max_bar_width = max_bar_width
        self.unit = unit
        self.show_values = show_values
    
    def compose(self):
        """Compose the widget - Static widgets don't yield children."""
        return []
    
    def update_data(self, data: Dict[str, int]) -> None:
        """Update the chart data and refresh.
        
        Args:
            data: New data dictionary
        """
        self.data = data
        self._update_display()
    
    def _update_display(self) -> None:
        """Update the bar chart display."""
        if not self.data:
            self.update(self._render_empty())
            return
        
        lines = []
        
        # Title
        if self.chart_title:
            lines.append(f"[bold]{self.chart_title}[/bold]")
            lines.append("")
        
        # Calculate scale
        max_value = max(self.data.values()) if self.data else 1
        if max_value == 0:
            max_value = 1
        
        # Find longest label for alignment
        max_label_len = max(len(str(k)) for k in self.data.keys()) if self.data else 0
        
        # Render bars
        for label, value in self.data.items():
            bar_len = int((value / max_value) * self.max_bar_width)
            bar = "█" * bar_len
            
            label_str = str(label).ljust(max_label_len)
            
            if self.show_values:
                value_str = f" {value}{self.unit}"
                lines.append(f"  {label_str} │{bar}{value_str}")
            else:
                lines.append(f"  {label_str} │{bar}")
        
        self.update("\n".join(lines))
    
    def render(self) -> str:
        """Render the chart to a string."""
        if not self.data:
            return self._render_empty()

        lines = []

        # Title
        if self.chart_title:
            lines.append(f"[bold]{self.chart_title}[/bold]")
            lines.append("")

        # Calculate scale
        max_value = max(self.data.values()) if self.data else 1
        if max_value == 0:
            max_value = 1

        # Find longest label for alignment
        max_label_len = max(len(str(k)) for k in self.data.keys()) if self.data else 0

        # Render bars
        for label, value in self.data.items():
            bar_len = int((value / max_value) * self.max_bar_width)
            bar = "█" * bar_len

            label_str = str(label).ljust(max_label_len)

            if self.show_values:
                value_str = f" {value}{self.unit}"
                lines.append(f"  {label_str} │{bar}{value_str}")
            else:
                lines.append(f"  {label_str} │{bar}")

        return "\n".join(lines)

    def _render_empty(self) -> str:
        """Render empty state."""
        if self.chart_title:
            return f"[bold]{self.chart_title}[/bold]\n\n  [No data available]"
        return "[No data available]"


class HorizontalBarChart(Static):
    """Horizontal bar chart for displaying multi-column daily data.
    
    Optimized for showing daily video counts across a week.
    """
    
    DEFAULT_CSS = """
    HorizontalBarChart {
        height: auto;
        width: 100%;
        padding: 1;
    }
    """
    
    def __init__(
        self,
        title: str = "",
        data: Optional[Dict[str, int]] = None,
        items_per_row: int = 3,
        **kwargs: Any,
    ) -> None:
        """Initialize the horizontal bar chart.
        
        Args:
            title: Chart title
            data: Dictionary mapping labels to values
            items_per_row: Number of items to show per row
            **kwargs: Additional arguments passed to Static
        """
        super().__init__(**kwargs)
        self.chart_title = title
        self.data = data or {}
        self.items_per_row = items_per_row
    
    def update_data(self, data: Dict[str, int]) -> None:
        """Update the chart data and refresh.
        
        Args:
            data: New data dictionary
        """
        self.data = data
        self._update_display()
    
    def _update_display(self) -> None:
        """Update the horizontal bar chart display."""
        if not self.data:
            self.update(self._render_empty())
            return
        
        lines = []
        
        # Title
        if self.chart_title:
            lines.append(f"[bold]{self.chart_title}[/bold]")
            lines.append("")
        
        # Convert to list and reverse to show oldest first
        items = list(self.data.items())
        
        # Group into rows
        for i in range(0, len(items), self.items_per_row):
            row_items = items[i:i + self.items_per_row]
            
            # Calculate bar lengths for this row
            max_value = max(v for _, v in row_items) if row_items else 1
            if max_value == 0:
                max_value = 1
            
            # Build row lines
            row_lines = []
            for label, value in row_items:
                bar_len = max(1, int((value / max_value) * 12))  # Scale to ~12 chars
                bar = "█" * bar_len
                row_lines.append(f"{label} {bar} {value}")
            
            lines.append("   ".join(row_lines))
        
        self.update("\n".join(lines))
    
    def _render_empty(self) -> str:
        """Render empty state."""
        if self.chart_title:
            return f"[bold]{self.chart_title}[/bold]\n\n  [No data available]"
        return "[No data available]"

    def render(self) -> str:
        """Render the chart to a string."""
        if not self.data:
            return self._render_empty()

        lines = []

        # Title
        if self.chart_title:
            lines.append(f"[bold]{self.chart_title}[/bold]")
            lines.append("")

        # Convert to list and reverse to show oldest first
        items = list(self.data.items())

        # Group into rows
        for i in range(0, len(items), self.items_per_row):
            row_items = items[i:i + self.items_per_row]

            # Calculate bar lengths for this row
            max_value = max(v for _, v in row_items) if row_items else 1
            if max_value == 0:
                max_value = 1

            # Build row lines
            row_lines = []
            for label, value in row_items:
                bar_len = max(1, int((value / max_value) * 12))  # Scale to ~12 chars
                bar = "█" * bar_len
                row_lines.append(f"{label} {bar} {value}")

            lines.append("   ".join(row_lines))

        return "\n".join(lines)


class StageTimingChart(Static):
    """Chart showing average time per pipeline stage."""
    
    DEFAULT_CSS = """
    StageTimingChart {
        height: auto;
        width: 100%;
        padding: 1;
    }
    """
    
    def __init__(
        self,
        title: str = "Average Time Per Stage",
        data: Optional[Dict[str, float]] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the stage timing chart.
        
        Args:
            title: Chart title
            data: Dictionary mapping stage names to time in seconds
            **kwargs: Additional arguments passed to Static
        """
        super().__init__(**kwargs)
        self.chart_title = title
        self.data = data or {}
    
    def update_data(self, data: Dict[str, float]) -> None:
        """Update the chart data.
        
        Args:
            data: New data dictionary with stage times in seconds
        """
        self.data = data
        self._update_display()
    
    def _update_display(self) -> None:
        """Update the stage timing chart display."""
        if not self.data:
            self.update(self._render_empty())
            return
        
        lines = []
        
        # Title
        if self.chart_title:
            lines.append(f"[bold]{self.chart_title}[/bold]")
            lines.append("")
        
        # Calculate scale
        max_value = max(self.data.values()) if self.data else 1
        if max_value == 0:
            max_value = 1
        
        # Stage name mapping for display
        stage_names = {
            "download": "Download",
            "encrypt": "Encrypt",
            "upload": "Upload",
            "analyze": "Analyze",
            "sync": "Sync",
            "ingest": "Ingest",
        }
        
        # Render bars
        max_label_len = 10
        max_bar_width = 25
        
        for stage, seconds in self.data.items():
            bar_len = int((seconds / max_value) * max_bar_width)
            bar = "█" * bar_len
            
            label = stage_names.get(stage, stage.title()).ljust(max_label_len)
            time_str = self._format_duration(seconds)
            
            lines.append(f"  {label} │{bar} {time_str}")
        
        self.update("\n".join(lines))
    
    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable form.
        
        Args:
            seconds: Time in seconds
            
        Returns:
            Formatted string (e.g., "4m 32s")
        """
        if seconds == 0:
            return "-"
        
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        hours = minutes // 60
        minutes = minutes % 60
        
        if hours > 0:
            return f"{hours}h {minutes:02d}m"
        return f"{minutes}m {secs:02d}s"
    
    def _render_empty(self) -> str:
        """Render empty state."""
        if self.chart_title:
            return f"[bold]{self.chart_title}[/bold]\n\n  [No timing data available]"
        return "[No timing data available]"

    def render(self) -> str:
        """Render the chart to a string."""
        if not self.data:
            return self._render_empty()

        lines = []

        # Title
        if self.chart_title:
            lines.append(f"[bold]{self.chart_title}[/bold]")
            lines.append("")

        # Calculate scale
        max_value = max(self.data.values()) if self.data else 1
        if max_value == 0:
            max_value = 1

        # Stage name mapping for display
        stage_names = {
            "download": "Download",
            "encrypt": "Encrypt",
            "upload": "Upload",
            "analyze": "Analyze",
            "sync": "Sync",
            "ingest": "Ingest",
        }

        # Render bars
        max_label_len = 10
        max_bar_width = 25

        for stage, seconds in self.data.items():
            bar_len = int((seconds / max_value) * max_bar_width)
            bar = "█" * bar_len

            label = stage_names.get(stage, stage.title()).ljust(max_label_len)
            time_str = self._format_duration(seconds)

            lines.append(f"  {label} \u2502{bar} {time_str}")

        return "\n".join(lines)


class SuccessRateChart(Static):
    """Chart showing success/failure rates by stage."""
    
    DEFAULT_CSS = """
    SuccessRateChart {
        height: auto;
        width: 100%;
        padding: 1;
    }
    """
    
    def __init__(
        self,
        title: str = "Success Rates",
        data: Optional[Dict[str, Dict[str, float]]] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the success rate chart.
        
        Args:
            title: Chart title
            data: Dictionary mapping stage names to rate info dicts
            **kwargs: Additional arguments passed to Static
        """
        super().__init__(**kwargs)
        self.chart_title = title
        self.data = data or {}
    
    def update_data(self, data: Dict[str, Dict[str, float]]) -> None:
        """Update the chart data.
        
        Args:
            data: New data dictionary with success rate info
        """
        self.data = data
        self._update_display()
    
    def _update_display(self) -> None:
        """Update the success rate chart display."""
        if not self.data:
            self.update(self._render_empty())
            return
        
        lines = []
        
        # Title
        if self.chart_title:
            lines.append(f"[bold]{self.chart_title}[/bold]")
            lines.append("")
        
        # Stage name mapping for display
        stage_names = {
            "download": "Download",
            "encrypt": "Encrypt",
            "upload": "Upload",
            "analyze": "Analyze",
            "sync": "Sync",
            "ingest": "Ingest",
        }
        
        # Render bars (max 100%)
        max_label_len = 10
        max_bar_width = 20
        
        for stage, info in self.data.items():
            success_rate = info.get("success_rate", 0) if isinstance(info, dict) else info
            
            bar_len = int((success_rate / 100) * max_bar_width)
            bar = "█" * bar_len
            
            # Color based on rate
            if success_rate >= 95:
                bar_style = "[success]"
            elif success_rate >= 80:
                bar_style = "[warning]"
            else:
                bar_style = "[error]"
            
            label = stage_names.get(stage, stage.title()).ljust(max_label_len)
            
            lines.append(f"  {label} │{bar_style}{bar}[/] {success_rate:.0f}%")
        
        self.update("\n".join(lines))
    
    def _render_empty(self) -> str:
        """Render empty state."""
        if self.chart_title:
            return f"[bold]{self.chart_title}[/bold]\n\n  [No rate data available]"
        return "[No rate data available]"

    def render(self) -> str:
        """Render the chart to a string."""
        if not self.data:
            return self._render_empty()

        lines = []

        # Title
        if self.chart_title:
            lines.append(f"[bold]{self.chart_title}[/bold]")
            lines.append("")

        # Stage name mapping for display
        stage_names = {
            "download": "Download",
            "encrypt": "Encrypt",
            "upload": "Upload",
            "analyze": "Analyze",
            "sync": "Sync",
            "ingest": "Ingest",
        }

        # Render bars (max 100%)
        max_label_len = 10
        max_bar_width = 20

        for stage, info in self.data.items():
            success_rate = info.get("success_rate", 0) if isinstance(info, dict) else info

            bar_len = int((success_rate / 100) * max_bar_width)
            bar = "█" * bar_len

            # Color based on rate
            if success_rate >= 95:
                bar_style = "[success]"
            elif success_rate >= 80:
                bar_style = "[warning]"
            else:
                bar_style = "[error]"

            label = stage_names.get(stage, stage.title()).ljust(max_label_len)

            lines.append(f"  {label} \u2502{bar_style}{bar}[/] {success_rate:.0f}%")

        return "\n".join(lines)


class PluginUsageChart(Static):
    """Chart showing plugin usage distribution."""
    
    DEFAULT_CSS = """
    PluginUsageChart {
        height: auto;
        width: 100%;
        padding: 1;
    }
    """
    
    def __init__(
        self,
        title: str = "Plugin Usage",
        data: Optional[Dict[str, float]] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the plugin usage chart.
        
        Args:
            title: Chart title
            data: Dictionary mapping plugin names to percentages
            **kwargs: Additional arguments passed to Static
        """
        super().__init__(**kwargs)
        self.chart_title = title
        self.data = data or {}
    
    def update_data(self, data: Dict[str, float]) -> None:
        """Update the chart data.
        
        Args:
            data: New data dictionary with plugin percentages
        """
        self.data = data
        self._update_display()
    
    def _update_display(self) -> None:
        """Update the plugin usage chart display."""
        if not self.data:
            self.update(self._render_empty())
            return
        
        lines = []
        
        # Title
        if self.chart_title:
            lines.append(f"[bold]{self.chart_title}[/bold]")
            lines.append("")
        
        # Render bars (max 100%)
        max_label_len = 12
        max_bar_width = 30
        
        for plugin, percentage in self.data.items():
            bar_len = int((percentage / 100) * max_bar_width)
            bar = "█" * bar_len
            
            label = (plugin or "unknown").title().ljust(max_label_len)
            
            lines.append(f"  {label} │{bar} {percentage:.0f}%")
        
        self.update("\n".join(lines))
    
    def _render_empty(self) -> str:
        """Render empty state."""
        if self.chart_title:
            return f"[bold]{self.chart_title}[/bold]\n\n  [No plugin usage data available]"
        return "[No plugin usage data available]"

    def render(self) -> str:
        """Render the chart to a string."""
        if not self.data:
            return self._render_empty()

        lines = []

        # Title
        if self.chart_title:
            lines.append(f"[bold]{self.chart_title}[/bold]")
            lines.append("")

        # Render bars (max 100%)
        max_label_len = 12
        max_bar_width = 30

        for plugin, percentage in self.data.items():
            bar_len = int((percentage / 100) * max_bar_width)
            bar = "█" * bar_len

            label = (plugin or "unknown").title().ljust(max_label_len)

            lines.append(f"  {label} \u2502{bar} {percentage:.0f}%")

        return "\n".join(lines)


class AnalyticsDashboardWidget(Container):
    """Widget displaying the analytics dashboard.
    
    Shows pipeline performance metrics with ASCII bar charts.
    """
    
    DEFAULT_CSS = """
    AnalyticsDashboardWidget {
        height: 100%;
        width: 100%;
        padding: 1;
        layout: vertical;
        overflow-y: auto;
    }
    
    AnalyticsDashboardWidget > #metrics-grid {
        height: auto;
        layout: grid;
        grid-size: 4;
        grid-columns: 1fr 1fr 1fr 1fr;
        grid-rows: auto;
        margin-bottom: 1;
    }
    
    AnalyticsDashboardWidget > #charts-container {
        height: auto;
        layout: vertical;
    }
    """
    
    def __init__(
        self,
        analytics_repo: Optional[AnalyticsRepository] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the analytics dashboard widget.
        
        Args:
            analytics_repo: Repository for analytics queries
            **kwargs: Additional arguments passed to Container
        """
        super().__init__(**kwargs)
        self.analytics_repo = analytics_repo
        self._last_refresh: Optional[datetime] = None
    
    def compose(self) -> None:
        """Compose the dashboard layout."""
        with Container(id="metrics-grid"):
            yield Static("Total Videos: -", id="metric-total")
            yield Static("Completed: -", id="metric-completed")
            yield Static("Failed: -", id="metric-failed")
            yield Static("Active: -", id="metric-active")
        
        with Container(id="charts-container"):
            yield HorizontalBarChart(
                title="Videos Processed (Last 7 Days)",
                id="daily-chart",
            )
            yield StageTimingChart(
                title="Average Time Per Stage",
                id="timing-chart",
            )
            yield SuccessRateChart(
                title="Success Rates",
                id="success-chart",
            )
            yield PluginUsageChart(
                title="Plugin Usage",
                id="plugin-chart",
            )
    
    def set_repository(self, repo: AnalyticsRepository) -> None:
        """Set the analytics repository.
        
        Args:
            repo: AnalyticsRepository instance
        """
        self.analytics_repo = repo
    
    def refresh_data(self) -> None:
        """Refresh all dashboard data from repository."""
        if self.analytics_repo is None:
            return
        
        try:
            # Get summary stats
            summary = self.analytics_repo.get_pipeline_summary()
            
            # Update metric cards
            self._update_metric_cards(summary)
            
            # Update charts
            self._update_charts(summary)
            
            self._last_refresh = datetime.now(timezone.utc)
            
        except Exception as e:
            # Log error but don't crash
            self.app.notify(f"Analytics refresh failed: {e}", severity="error", timeout=3.0)
    
    def _update_metric_cards(self, summary: Dict[str, Any]) -> None:
        """Update the summary metric cards.
        
        Args:
            summary: Pipeline summary dictionary
        """
        videos = summary.get("videos", {})
        
        try:
            total = self.query_one("#metric-total", Static)
            total.update(f"Total Videos: {videos.get('total', 0)}")
            
            completed = self.query_one("#metric-completed", Static)
            completed.update(f"Completed: {videos.get('completed', 0)}")
            
            failed = self.query_one("#metric-failed", Static)
            failed.update(f"Failed: {videos.get('failed', 0)}")
            
            active = self.query_one("#metric-active", Static)
            active.update(f"Active: {videos.get('active', 0)}")
        except Exception:
            pass  # Widgets may not be mounted yet
    
    def _update_charts(self, summary: Dict[str, Any]) -> None:
        """Update all chart widgets.
        
        Args:
            summary: Pipeline summary dictionary
        """
        try:
            # Daily videos chart
            daily_chart = self.query_one("#daily-chart", HorizontalBarChart)
            daily_data = summary.get("videos_per_day_7d", {})
            # Convert to day names
            daily_formatted = self._format_daily_data(daily_data)
            daily_chart.update_data(daily_formatted)
        except Exception:
            pass
        
        try:
            # Stage timing chart
            timing_chart = self.query_one("#timing-chart", StageTimingChart)
            timing_data = self.analytics_repo.get_avg_time_per_stage(days=30)
            timing_chart.update_data(timing_data)
        except Exception:
            pass
        
        try:
            # Success rate chart
            success_chart = self.query_one("#success-chart", SuccessRateChart)
            success_data = summary.get("success_rates_7d", {})
            success_chart.update_data(success_data)
        except Exception:
            pass
        
        try:
            # Plugin usage chart
            plugin_chart = self.query_one("#plugin-chart", PluginUsageChart)
            plugin_data = self.analytics_repo.get_plugin_usage_percentages(days=30)
            plugin_chart.update_data(plugin_data)
        except Exception:
            pass
    
    def _format_daily_data(self, data: Dict[str, int]) -> Dict[str, int]:
        """Format daily data with day names.
        
        Args:
            data: Dictionary mapping dates to counts
            
        Returns:
            Dictionary with formatted day names
        """
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        formatted = {}
        
        for date_str, count in data.items():
            try:
                # Parse date and get day name
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                day_name = day_names[dt.weekday()]
                formatted[day_name] = count
            except (ValueError, IndexError):
                formatted[date_str] = count
        
        return formatted


class AnalyticsDashboardScreen(Screen):
    """Screen displaying the full analytics dashboard.
    
    This is the main analytics view showing pipeline performance metrics
    with auto-refresh capability.
    """
    
    DEFAULT_CSS = """
    AnalyticsDashboardScreen {
        layout: vertical;
    }
    
    AnalyticsDashboardScreen > #analytics-header {
        height: 3;
        dock: top;
        background: $surface-darken-2;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }
    
    AnalyticsDashboardScreen > #analytics-footer {
        height: 1;
        dock: bottom;
        background: $surface-darken-2;
        color: $text-muted;
        content-align: center middle;
    }
    
    AnalyticsDashboardScreen > #analytics-content {
        height: 1fr;
        width: 100%;
    }
    """
    
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("a", "auto_refresh", "Auto-refresh"),
        ("escape", "back", "Back"),
    ]
    
    auto_refresh: reactive[bool] = reactive(True)
    
    def __init__(
        self,
        analytics_repo: Optional[AnalyticsRepository] = None,
        config: Optional[HavenTUIConfig] = None,
        on_back: Optional[Callable[[], None]] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the analytics dashboard screen.
        
        Args:
            analytics_repo: Repository for analytics queries
            config: TUI configuration
            on_back: Optional callback when user presses back
            **kwargs: Additional arguments passed to Screen
        """
        super().__init__(**kwargs)
        self.analytics_repo = analytics_repo
        self.config = config or HavenTUIConfig()
        self.auto_refresh = True
        self._refresh_timer: Optional[Any] = None
        self.on_back_callback = on_back
    
    def compose(self) -> None:
        """Compose the screen layout."""
        yield Static("Pipeline Analytics Dashboard", id="analytics-header")
        
        with Container(id="analytics-content"):
            yield AnalyticsDashboardWidget(
                analytics_repo=self.analytics_repo,
            )
        
        yield Static(
            "[q] Quit  [r] Refresh  [a] Toggle Auto-refresh  [Esc] Back",
            id="analytics-footer",
        )
    
    def on_mount(self) -> None:
        """Handle mount event - start auto-refresh timer."""
        self._start_refresh_timer()
        
        # Initial data load
        self._refresh_data()
    
    def on_unmount(self) -> None:
        """Handle unmount event - stop timer."""
        self._stop_refresh_timer()
    
    def _start_refresh_timer(self) -> None:
        """Start the auto-refresh timer."""
        if self._refresh_timer is not None:
            return
        
        refresh_interval = self.config.display.refresh_rate if self.config else 5.0
        self._refresh_timer = self.set_interval(refresh_interval, self._auto_refresh)
    
    def _stop_refresh_timer(self) -> None:
        """Stop the auto-refresh timer."""
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None
    
    def _auto_refresh(self) -> None:
        """Perform auto-refresh if enabled."""
        if self.auto_refresh:
            self._refresh_data()
    
    def _refresh_data(self) -> None:
        """Refresh the analytics data."""
        try:
            dashboard = self.query_one(AnalyticsDashboardWidget)
            dashboard.refresh_data()
        except Exception:
            pass  # Widget may not be mounted yet
    
    def action_refresh(self) -> None:
        """Manual refresh action."""
        self._refresh_data()
        self.app.notify("Analytics refreshed", timeout=1.0)
    
    def action_auto_refresh(self) -> None:
        """Toggle auto-refresh on/off."""
        self.auto_refresh = not self.auto_refresh
        status = "ON" if self.auto_refresh else "OFF"
        self.app.notify(f"Auto-refresh: {status}", timeout=2.0)
    
    def action_back(self) -> None:
        """Go back to previous screen."""
        if self.on_back_callback:
            self.on_back_callback()
        else:
            self.app.pop_screen()


class AnalyticsDashboard:
    """High-level interface for the analytics dashboard.
    
    Provides a simple API for showing the analytics dashboard
    and managing its lifecycle.
    
    Example:
        >>> dashboard = AnalyticsDashboard(analytics_repo, config)
        >>> dashboard.show()
    """
    
    def __init__(
        self,
        analytics_repo: AnalyticsRepository,
        config: Optional[HavenTUIConfig] = None,
        on_back: Optional[Callable[[], None]] = None,
    ) -> None:
        """Initialize the analytics dashboard.
        
        Args:
            analytics_repo: Repository for analytics queries
            config: TUI configuration
            on_back: Optional callback when user goes back
        """
        self.analytics_repo = analytics_repo
        self.config = config or HavenTUIConfig()
        self.on_back = on_back
        self.screen: Optional[AnalyticsDashboardScreen] = None
    
    def create_screen(self) -> AnalyticsDashboardScreen:
        """Create the analytics dashboard screen.
        
        Returns:
            The configured AnalyticsDashboardScreen instance
        """
        self.screen = AnalyticsDashboardScreen(
            analytics_repo=self.analytics_repo,
            config=self.config,
            on_back=self.on_back,
        )
        return self.screen
    
    def refresh(self) -> None:
        """Refresh the dashboard data."""
        if self.screen is not None:
            self.screen._refresh_data()
