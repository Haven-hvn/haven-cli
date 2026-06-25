"""Event Log View for Haven TUI.

Real-time event log view showing pipeline events with filtering,
search, and export capabilities. Similar to a system log viewer.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from textual.widgets import DataTable, Static, Input, Button
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen, ModalScreen
from textual.binding import Binding

from haven_cli.pipeline.events import EventBus, EventType, Event, get_event_bus


class LogLevel(Enum):
    """Log level for event entries."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class LogEntry:
    """Single log entry in the event log.
    
    Attributes:
        timestamp: When the event occurred
        event_type: Type of pipeline event
        message: Human-readable message
        level: Severity level (debug, info, warning, error)
        source: Component that generated the event
        video_id: Optional video ID associated with the event
        metadata: Additional event data
    """
    timestamp: datetime
    event_type: EventType
    message: str
    level: LogLevel = LogLevel.INFO
    source: str = ""
    video_id: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def level_color(self) -> str:
        """Get display color for log level."""
        return {
            LogLevel.DEBUG: "dim",
            LogLevel.INFO: "",
            LogLevel.WARNING: "warning",
            LogLevel.ERROR: "error",
        }.get(self.level, "")


class EventLogWidget(DataTable):
    """Widget displaying real-time event log entries.
    
    This widget shows a scrollable list of pipeline events with
    filtering by event type and search capability.
    
    Attributes:
        event_bus: The EventBus to subscribe to for events
        max_entries: Maximum number of entries to keep in memory
        filter_event_type: Optional filter for specific event type
        filter_video_id: Optional filter for specific video ID
        search_query: Current search query for filtering messages
    """
    
    DEFAULT_CSS = """
    EventLogWidget {
        height: 100%;
        width: 100%;
        border: solid $primary;
    }
    
    EventLogWidget > .datatable--header {
        background: $surface-darken-1;
        color: $text;
        text-style: bold;
    }
    
    EventLogWidget > .datatable--row {
        height: 1;
    }
    
    EventLogWidget > .datatable--row-cursor {
        background: $primary-darken-1;
    }
    
    /* Log level colors */
    .level-debug { color: $text-muted; }
    .level-info { color: $text; }
    .level-warning { color: $warning; }
    .level-error { color: $error; }
    
    /* Event type badges */
    .event-download { color: $accent; }
    .event-encrypt { color: $warning; }
    .event-upload { color: $success; }
    .event-error { color: $error; }
    .event-system { color: $text-muted; }
    """
    
    # Column definitions: (key, label, width)
    COLUMNS: List[tuple[str, str, int]] = [
        ("time", "Time", 12),
        ("level", "Lvl", 6),
        ("type", "Type", 18),
        ("message", "Message", 0),  # 0 = flexible
    ]
    
    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        max_entries: int = 1000,
        **kwargs: Any,
    ) -> None:
        """Initialize the event log widget.
        
        Args:
            event_bus: EventBus to subscribe to (uses default if None)
            max_entries: Maximum number of entries to keep
            **kwargs: Additional arguments passed to DataTable
        """
        super().__init__(**kwargs)
        self.event_bus = event_bus or get_event_bus()
        self.max_entries = max_entries
        self.entries: deque[LogEntry] = deque(maxlen=max_entries)
        self.filter_event_type: Optional[EventType] = None
        self.filter_video_id: Optional[int] = None
        self.search_query: str = ""
        self._unsubscribe: Optional[Callable[[], None]] = None
        self._auto_scroll: bool = True
        
        # Track visible entries for cursor management
        self._visible_indices: List[int] = []
    
    def compose(self):
        """Set up the table columns."""
        self._setup_columns()
        return []
    
    def _setup_columns(self) -> None:
        """Configure table columns."""
        # Clear existing columns
        for key in list(self.columns.keys()):
            self.remove_column(key)
        
        # Add columns
        for key, label, width in self.COLUMNS:
            if width > 0:
                self.add_column(label, key=key, width=width)
            else:
                self.add_column(label, key=key)
    
    def on_mount(self) -> None:
        """Start listening to events when mounted."""
        self.start()
    
    def on_unmount(self) -> None:
        """Stop listening to events when unmounted."""
        self.stop()
    
    def start(self) -> None:
        """Start listening to events from the event bus."""
        if self._unsubscribe is None:
            self._unsubscribe = self.event_bus.subscribe_all(self._on_event)
    
    def stop(self) -> None:
        """Stop listening to events."""
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
    
    async def _on_event(self, event: Event) -> None:
        """Handle incoming event from the event bus.
        
        Args:
            event: The pipeline event
        """
        entry = self._create_log_entry(event)
        self.entries.append(entry)
        
        # Update display if the entry matches current filters
        if self._matches_filters(entry):
            self._refresh_display()
    
    def _create_log_entry(self, event: Event) -> LogEntry:
        """Create a LogEntry from an Event.
        
        Args:
            event: Pipeline event
            
        Returns:
            LogEntry for display
        """
        payload = event.payload
        
        # Determine log level based on event type
        level = self._get_level_for_event(event.event_type)
        
        # Format message based on event type
        message = self._format_event_message(event)
        
        # Extract video_id if present
        video_id = payload.get("video_id")
        
        return LogEntry(
            timestamp=event.timestamp or datetime.now(timezone.utc),
            event_type=event.event_type,
            message=message,
            level=level,
            source=event.source or "pipeline",
            video_id=video_id,
            metadata=payload,
        )
    
    def _get_level_for_event(self, event_type: EventType) -> LogLevel:
        """Determine log level from event type.
        
        Args:
            event_type: Type of event
            
        Returns:
            Appropriate log level
        """
        error_types = {
            EventType.PIPELINE_FAILED,
            EventType.UPLOAD_FAILED,
            EventType.ANALYSIS_FAILED,
            EventType.STEP_FAILED,
        }
        warning_types = {
            EventType.PIPELINE_CANCELLED,
            EventType.STEP_SKIPPED,
        }
        debug_types = {
            EventType.HEALTH_CHECK,
            EventType.WORKER_STATUS,
        }
        
        if event_type in error_types:
            return LogLevel.ERROR
        elif event_type in warning_types:
            return LogLevel.WARNING
        elif event_type in debug_types:
            return LogLevel.DEBUG
        return LogLevel.INFO
    
    def _format_event_message(self, event: Event) -> str:
        """Format a human-readable message for an event.
        
        Args:
            event: Pipeline event
            
        Returns:
            Formatted message string
        """
        et = event.event_type
        p = event.payload
        
        # Progress events
        if et == EventType.DOWNLOAD_PROGRESS:
            video_id = p.get("video_id", "unknown")
            progress = p.get("progress_percent", 0)
            rate = p.get("download_rate", 0)
            rate_str = self._format_speed(rate)
            return f"Video {video_id}: Download {progress:.1f}% at {rate_str}"
        
        elif et == EventType.ENCRYPT_PROGRESS:
            video_id = p.get("video_id", "unknown")
            # Canonical key emitted by encrypt_step's build_encrypt_progress_payload
            # is ``progress_percent``; ``progress`` was the legacy field name
            # and is no longer produced. Always show 0.0% if missing rather
            # than silently dropping the row.
            progress = p.get("progress_percent", 0)
            return f"Video {video_id}: Encryption {progress:.1f}%"
        
        elif et == EventType.UPLOAD_PROGRESS:
            video_id = p.get("video_id", "unknown")
            # Canonical key emitted by upload_step is ``progress_percent``;
            # the legacy ``progress`` field is no longer produced.
            progress = p.get("progress_percent", 0)
            rate = p.get("upload_speed", 0)
            rate_str = self._format_speed(rate)
            return f"Video {video_id}: Upload {progress:.1f}% at {rate_str}"
        
        # Completion events
        elif et == EventType.VIDEO_INGESTED:
            return f"Video {p.get('video_id', 'unknown')}: Ingested"
        elif et == EventType.ENCRYPT_COMPLETE:
            return f"Video {p.get('video_id', 'unknown')}: Encryption complete"
        elif et == EventType.UPLOAD_COMPLETE:
            return f"Video {p.get('video_id', 'unknown')}: Upload complete"
        elif et == EventType.SYNC_COMPLETE:
            return f"Video {p.get('video_id', 'unknown')}: Sync complete"
        elif et == EventType.ANALYSIS_COMPLETE:
            return f"Video {p.get('video_id', 'unknown')}: Analysis complete"
        elif et == EventType.PIPELINE_COMPLETE:
            return f"Video {p.get('video_id', 'unknown')}: Pipeline complete"
        
        # Failure events
        elif et == EventType.PIPELINE_FAILED:
            error = p.get("error", "Unknown error")
            stage = p.get("stage", "unknown")
            return f"Video {p.get('video_id', 'unknown')}: Failed at {stage}: {error}"
        elif et == EventType.UPLOAD_FAILED:
            error = p.get("error", "Unknown error")
            return f"Video {p.get('video_id', 'unknown')}: Upload failed: {error}"
        elif et == EventType.ANALYSIS_FAILED:
            error = p.get("error", "Unknown error")
            return f"Video {p.get('video_id', 'unknown')}: Analysis failed: {error}"
        elif et == EventType.STEP_FAILED:
            error = p.get("error", "Unknown error")
            step = p.get("stage", "unknown")
            return f"Video {p.get('video_id', 'unknown')}: Step '{step}' failed: {error}"
        
        # Lifecycle events
        elif et == EventType.PIPELINE_STARTED:
            return f"Video {p.get('video_id', 'unknown')}: Pipeline started"
        elif et == EventType.STEP_STARTED:
            return f"Video {p.get('video_id', 'unknown')}: Step '{p.get('stage', 'unknown')}' started"
        elif et == EventType.STEP_COMPLETE:
            return f"Video {p.get('video_id', 'unknown')}: Step '{p.get('stage', 'unknown')}' complete"
        elif et == EventType.STEP_SKIPPED:
            return f"Video {p.get('video_id', 'unknown')}: Step '{p.get('stage', 'unknown')}' skipped"
        elif et == EventType.PIPELINE_CANCELLED:
            return f"Video {p.get('video_id', 'unknown')}: Pipeline cancelled"
        
        # System events
        elif et == EventType.SOURCES_DISCOVERED:
            count = p.get("count", 0)
            return f"Discovered {count} source(s)"
        elif et == EventType.ARCHIVE_STARTED:
            return f"Archive started: {p.get('path', 'unknown')}"
        elif et == EventType.ARCHIVE_COMPLETE:
            return f"Archive complete: {p.get('path', 'unknown')}"
        elif et == EventType.CONFIG_UPDATE:
            return f"Configuration updated"
        elif et == EventType.HEALTH_CHECK:
            return f"Health check: {p.get('status', 'ok')}"
        elif et == EventType.WORKER_STATUS:
            return f"Worker status: {p.get('status', 'unknown')}"
        
        # Requested events
        elif et == EventType.DOWNLOAD_PROGRESS:
            return f"Video {p.get('video_id', 'unknown')}: Download requested"
        elif et == EventType.ENCRYPT_REQUESTED:
            return f"Video {p.get('video_id', 'unknown')}: Encryption requested"
        elif et == EventType.UPLOAD_REQUESTED:
            return f"Video {p.get('video_id', 'unknown')}: Upload requested"
        elif et == EventType.SYNC_REQUESTED:
            return f"Video {p.get('video_id', 'unknown')}: Sync requested"
        elif et == EventType.ANALYSIS_REQUESTED:
            return f"Video {p.get('video_id', 'unknown')}: Analysis requested"
        
        # Default
        return f"{et.name}: {str(p)[:50]}"
    
    def _format_speed(self, speed: float) -> str:
        """Format speed in human-readable form.
        
        Args:
            speed: Speed in bytes per second
            
        Returns:
            Formatted speed string
        """
        if speed == 0:
            return "-"
        
        size = float(speed)
        if size < 1024:
            return f"{size:.0f}B/s"
        size /= 1024
        if size < 1024:
            return f"{size:.1f}KB/s"
        size /= 1024
        if size < 1024:
            return f"{size:.1f}MB/s"
        size /= 1024
        return f"{size:.1f}GB/s"
    
    def _setup_columns(self) -> None:
        """Configure table columns."""
        # Clear existing columns
        for key in list(self.columns.keys()):
            self.remove_column(key)
        
        # Add columns - use fixed widths for consistent display
        self.add_column("Time", key="time", width=12)
        self.add_column("Lvl", key="level", width=6)
        self.add_column("Type", key="type", width=20)
        self.add_column("Message", key="message")
    
    def _matches_filters(self, entry: LogEntry) -> bool:
        """Check if an entry matches current filters.
        
        Args:
            entry: Log entry to check
            
        Returns:
            True if entry should be displayed
        """
        # Check video_id filter
        if self.filter_video_id is not None:
            if entry.video_id != self.filter_video_id:
                return False
        
        # Check event type filter
        if self.filter_event_type is not None:
            if entry.event_type != self.filter_event_type:
                return False
        
        # Check search query
        if self.search_query:
            query = self.search_query.lower()
            if query not in entry.message.lower():
                return False
        
        return True
    
    def _get_filtered_entries(self) -> List[LogEntry]:
        """Get entries matching current filters.
        
        Returns:
            List of filtered log entries
        """
        return [e for e in self.entries if self._matches_filters(e)]
    
    def _refresh_display(self) -> None:
        """Refresh the table display with current entries."""
        filtered = self._get_filtered_entries()
        
        # Clear and rebuild
        self.clear()
        self._visible_indices = []
        
        for i, entry in enumerate(filtered):
            self._visible_indices.append(i)
            self._add_entry_row(entry)
        
        # Auto-scroll to bottom if enabled
        if self._auto_scroll and len(filtered) > 0:
            self.move_cursor(row=len(filtered) - 1)
    
    def _add_entry_row(self, entry: LogEntry) -> None:
        """Add a single entry as a row to the table.
        
        Args:
            entry: Log entry to add
        """
        time_str = entry.timestamp.strftime("%H:%M:%S.%f")[:-3]
        level_str = entry.level.value.upper()[:5]
        type_str = entry.event_type.name[:16]
        message = entry.message[:200]  # Truncate long messages
        
        # Apply styling
        level_class = f"level-{entry.level.value}"
        
        cells = [
            time_str,
            f"[{level_class}]{level_str}[/{level_class}]",
            type_str,
            message,
        ]
        
        self.add_row(*cells)
    
    def set_filter(self, event_type: Optional[EventType]) -> None:
        """Set event type filter.
        
        Args:
            event_type: Event type to filter by, or None to clear
        """
        self.filter_event_type = event_type
        self._refresh_display()
    
    def set_video_filter(self, video_id: Optional[int]) -> None:
        """Set video ID filter.
        
        Args:
            video_id: Video ID to filter by, or None to clear
        """
        self.filter_video_id = video_id
        self._refresh_display()
    
    def set_search(self, query: str) -> None:
        """Set search query filter.
        
        Args:
            query: Search string to filter messages
        """
        self.search_query = query
        self._refresh_display()
    
    def clear_log(self) -> None:
        """Clear all log entries."""
        self.entries.clear()
        self._refresh_display()
    
    def export(self, filepath: str) -> None:
        """Export log entries to a file.
        
        Args:
            filepath: Path to export file
            
        Raises:
            IOError: If file cannot be written
        """
        with open(filepath, 'w') as f:
            f.write("# Haven Event Log Export\n")
            f.write(f"# Generated: {datetime.now(timezone.utc).isoformat()}\n")
            f.write("# timestamp | level | event_type | source | video_id | message\n")
            f.write("-" * 80 + "\n")
            
            for entry in self.entries:
                video_id = entry.video_id if entry.video_id else "-"
                f.write(
                    f"{entry.timestamp.isoformat()} | "
                    f"{entry.level.value} | "
                    f"{entry.event_type.name} | "
                    f"{entry.source} | "
                    f"{video_id} | "
                    f"{entry.message}\n"
                )
    
    def get_entry_count(self) -> int:
        """Get total number of entries (before filtering).
        
        Returns:
            Total entry count
        """
        return len(self.entries)
    
    def get_filtered_count(self) -> int:
        """Get number of entries after filtering.
        
        Returns:
            Filtered entry count
        """
        return len(self._get_filtered_entries())
    
    def toggle_auto_scroll(self) -> bool:
        """Toggle auto-scroll to latest entries.
        
        Returns:
            New auto-scroll state
        """
        self._auto_scroll = not self._auto_scroll
        return self._auto_scroll
    
    def refresh_log(self) -> None:
        """Manually refresh the log display."""
        self._refresh_display()


class EventLogHeader(Static):
    """Header widget for event log view."""
    
    DEFAULT_CSS = """
    EventLogHeader {
        height: 3;
        background: $surface-darken-2;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }
    """
    
    def __init__(self, **kwargs: Any) -> None:
        """Initialize the header."""
        super().__init__(**kwargs)
        self._total_entries: int = 0
        self._filtered_entries: int = 0
        self._filter_name: Optional[str] = None
    
    def compose(self):
        """Compose the widget - Static widgets don't yield children."""
        return []
    
    def update_stats(
        self,
        total: int,
        filtered: int,
        filter_name: Optional[str] = None,
    ) -> None:
        """Update header statistics.
        
        Args:
            total: Total number of entries
            filtered: Number of entries after filtering
            filter_name: Name of active filter, if any
        """
        self._total_entries = total
        self._filtered_entries = filtered
        self._filter_name = filter_name
        self._update_content()
    
    def _update_content(self) -> None:
        """Update the header content."""
        if self._filter_name:
            text = f"Event Log [{self._filtered_entries}/{self._total_entries} entries] (filtered: {self._filter_name})"
        else:
            text = f"Event Log [{self._total_entries} entries]"
        self.update(text)


class EventLogFooter(Static):
    """Footer widget for event log view."""
    
    DEFAULT_CSS = """
    EventLogFooter {
        height: 1;
        background: $surface-darken-2;
        color: $text-muted;
        content-align: center middle;
    }
    """
    
    def compose(self):
        """Set up the footer content."""
        self.update(
            "[f] Filter  [/] Search  [e] Export  [c] Clear  [a] Auto-scroll  [q] Back"
        )
        return []


class EventTypeFilterModal(ModalScreen[Optional[EventType]]):
    """Modal dialog for selecting event type filter."""
    
    DEFAULT_CSS = """
    EventTypeFilterModal {
        align: center middle;
    }
    
    EventTypeFilterModal > Container {
        width: 60;
        height: auto;
        max-height: 30;
        background: $surface;
        border: solid $primary;
        padding: 1;
    }
    
    EventTypeFilterModal > Container > Static {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    """
    
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]
    
    # Event types organized by category
    EVENT_CATEGORIES: Dict[str, List[EventType]] = {
        "Progress": [
            EventType.DOWNLOAD_PROGRESS,
            EventType.ENCRYPT_PROGRESS,
            EventType.UPLOAD_PROGRESS,
        ],
        "Completion": [
            EventType.VIDEO_INGESTED,
            EventType.ENCRYPT_COMPLETE,
            EventType.UPLOAD_COMPLETE,
            EventType.SYNC_COMPLETE,
            EventType.ANALYSIS_COMPLETE,
            EventType.PIPELINE_COMPLETE,
        ],
        "Errors": [
            EventType.PIPELINE_FAILED,
            EventType.UPLOAD_FAILED,
            EventType.ANALYSIS_FAILED,
            EventType.STEP_FAILED,
        ],
        "Lifecycle": [
            EventType.PIPELINE_STARTED,
            EventType.STEP_STARTED,
            EventType.STEP_COMPLETE,
            EventType.STEP_SKIPPED,
            EventType.PIPELINE_CANCELLED,
        ],
        "System": [
            EventType.SOURCES_DISCOVERED,
            EventType.CONFIG_UPDATE,
            EventType.HEALTH_CHECK,
        ],
    }
    
    def compose(self) -> None:
        """Compose the modal dialog."""
        with Container():
            yield Static("Select Event Type Filter")
            
            # Add "All Events" option
            btn = Button("All Events (Clear Filter)", variant="primary", id="btn-all")
            yield btn
            
            # Add event type buttons by category
            for category, event_types in self.EVENT_CATEGORIES.items():
                yield Static(f"[bold]{category}[/bold]")
                for et in event_types:
                    btn_id = f"btn-{et.name.lower()}"
                    yield Button(et.name, id=btn_id)
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        button_id = event.button.id
        
        if button_id == "btn-all":
            self.dismiss(None)
            return
        
        # Find the event type from button id
        for et in EventType:
            if button_id == f"btn-{et.name.lower()}":
                self.dismiss(et)
                return
        
        self.dismiss(None)
    
    def action_cancel(self) -> None:
        """Cancel filter selection."""
        self.dismiss(None)


class SearchModal(ModalScreen[str]):
    """Modal dialog for entering search query."""
    
    DEFAULT_CSS = """
    SearchModal {
        align: center middle;
    }
    
    SearchModal > Container {
        width: 60;
        height: auto;
        background: $surface;
        border: solid $primary;
        padding: 1;
    }
    
    SearchModal > Container > Static {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    
    SearchModal Input {
        margin: 1 0;
    }
    """
    
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]
    
    def __init__(self, current_query: str = "", **kwargs: Any) -> None:
        """Initialize the search modal.
        
        Args:
            current_query: Current search query to pre-fill
            **kwargs: Additional arguments
        """
        super().__init__(**kwargs)
        self._current_query = current_query
    
    def compose(self) -> None:
        """Compose the modal dialog."""
        with Container():
            yield Static("Search Log Messages")
            yield Input(
                value=self._current_query,
                placeholder="Enter search term...",
                id="search-input",
            )
            with Horizontal():
                yield Button("Search", variant="primary", id="btn-search")
                yield Button("Clear", variant="error", id="btn-clear")
                yield Button("Cancel", id="btn-cancel")
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        button_id = event.button.id
        
        if button_id == "btn-search":
            input_widget = self.query_one("#search-input", Input)
            self.dismiss(input_widget.value)
        elif button_id == "btn-clear":
            self.dismiss("")
        else:
            self.dismiss(self._current_query)
    
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission."""
        self.dismiss(event.value)
    
    def action_cancel(self) -> None:
        """Cancel search."""
        self.dismiss(self._current_query)


class EventLogScreen(Screen):
    """Screen displaying the event log.
    
    This is the main event log view showing real-time pipeline events
    with filtering, search, and export capabilities.
    
    Attributes:
        event_bus: EventBus to subscribe to
        max_entries: Maximum number of entries to keep
        on_back: Optional callback when user goes back
    """
    
    DEFAULT_CSS = """
    EventLogScreen {
        layout: vertical;
    }
    
    EventLogScreen > #event-log-container {
        height: 100%;
        width: 100%;
        layout: vertical;
    }
    
    EventLogScreen > #event-log-container > #header-container {
        height: auto;
        dock: top;
    }
    
    EventLogScreen > #event-log-container > #content-container {
        height: 1fr;
        width: 100%;
    }
    
    EventLogScreen > #event-log-container > #footer-container {
        height: auto;
        dock: bottom;
    }
    
    EventLogScreen > #event-log-container > #content-container > EventLogWidget {
        height: 100%;
        width: 100%;
    }
    """
    
    BINDINGS = [
        Binding("q", "back", "Back"),
        Binding("f", "filter", "Filter"),
        Binding("slash", "search", "Search"),
        Binding("e", "export", "Export"),
        Binding("c", "clear", "Clear"),
        Binding("a", "toggle_auto_scroll", "Auto-scroll"),
        Binding("r", "refresh", "Refresh"),
    ]
    
    auto_scroll: reactive[bool] = reactive(True)
    
    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        max_entries: int = 1000,
        on_back: Optional[Callable[[], None]] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the event log screen.
        
        Args:
            event_bus: EventBus to subscribe to (uses default if None)
            max_entries: Maximum number of entries to keep
            on_back: Optional callback when user presses back
            **kwargs: Additional arguments passed to Screen
        """
        super().__init__(**kwargs)
        self.event_bus = event_bus
        self.max_entries = max_entries
        self.on_back_callback = on_back
        self._log_widget: Optional[EventLogWidget] = None
    
    def compose(self) -> None:
        """Compose the screen layout."""
        with Container(id="event-log-container"):
            with Container(id="header-container"):
                yield EventLogHeader(id="event-log-header")
            
            with Container(id="content-container"):
                yield EventLogWidget(
                    event_bus=self.event_bus,
                    max_entries=self.max_entries,
                    id="event-log-widget",
                )
            
            with Container(id="footer-container"):
                yield EventLogFooter()
    
    def on_mount(self) -> None:
        """Handle mount event."""
        self._update_header()
    
    def _update_header(self) -> None:
        """Update the header with current stats."""
        try:
            header = self.query_one("#event-log-header", EventLogHeader)
            log_widget = self.query_one("#event-log-widget", EventLogWidget)
            
            filter_name = None
            if log_widget.filter_event_type:
                filter_name = log_widget.filter_event_type.name
            
            header.update_stats(
                total=log_widget.get_entry_count(),
                filtered=log_widget.get_filtered_count(),
                filter_name=filter_name,
            )
        except Exception:
            pass  # Widgets may not be mounted yet
    
    def action_back(self) -> None:
        """Navigate back."""
        if self.on_back_callback:
            self.on_back_callback()
        else:
            self.app.pop_screen()
    
    def action_filter(self) -> None:
        """Open filter dialog."""
        def on_filter_result(event_type: Optional[EventType]) -> None:
            log_widget = self.query_one("#event-log-widget", EventLogWidget)
            log_widget.set_filter(event_type)
            self._update_header()
            
            if event_type:
                self.app.notify(f"Filtered by: {event_type.name}", timeout=2.0)
            else:
                self.app.notify("Filter cleared", timeout=1.0)
        
        self.push_screen(EventTypeFilterModal(), on_filter_result)
    
    def action_search(self) -> None:
        """Open search dialog."""
        log_widget = self.query_one("#event-log-widget", EventLogWidget)
        
        def on_search_result(query: str) -> None:
            log_widget.set_search(query)
            self._update_header()
            
            if query:
                self.app.notify(f"Searching for: {query}", timeout=2.0)
            else:
                self.app.notify("Search cleared", timeout=1.0)
        
        self.push_screen(SearchModal(log_widget.search_query), on_search_result)
    
    def action_export(self) -> None:
        """Export log to file."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        default_path = f"haven_event_log_{timestamp}.log"
        
        def on_export_path(path: str) -> None:
            if not path:
                self.app.notify("Export cancelled", timeout=1.0)
                return
            
            try:
                log_widget = self.query_one("#event-log-widget", EventLogWidget)
                log_widget.export(path)
                self.app.notify(f"Log exported to: {path}", timeout=3.0)
            except Exception as e:
                self.app.notify(f"Export failed: {e}", severity="error", timeout=3.0)
        
        self.push_screen(ExportModal(default_path), on_export_path)
    
    def action_clear(self) -> None:
        """Clear the log."""
        log_widget = self.query_one("#event-log-widget", EventLogWidget)
        log_widget.clear_log()
        self._update_header()
        self.app.notify("Log cleared", timeout=1.0)
    
    def action_toggle_auto_scroll(self) -> None:
        """Toggle auto-scroll."""
        log_widget = self.query_one("#event-log-widget", EventLogWidget)
        new_state = log_widget.toggle_auto_scroll()
        status = "ON" if new_state else "OFF"
        self.app.notify(f"Auto-scroll: {status}", timeout=1.5)
    
    def action_refresh(self) -> None:
        """Manually refresh the log display."""
        log_widget = self.query_one("#event-log-widget", EventLogWidget)
        log_widget.refresh_log()
        self._update_header()
        self.app.notify("Refreshed", timeout=1.0)


class VideoLogsHeader(Static):
    """Header widget for video-specific logs view."""
    
    DEFAULT_CSS = """
    VideoLogsHeader {
        height: 3;
        background: $surface-darken-2;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }
    """
    
    def __init__(self, video_id: Optional[int] = None, video_title: str = "", **kwargs: Any) -> None:
        """Initialize the header.
        
        Args:
            video_id: Video ID being viewed
            video_title: Title of the video
            **kwargs: Additional arguments
        """
        super().__init__(**kwargs)
        self._video_id = video_id
        self._video_title = video_title
        self._total_entries: int = 0
        self._filtered_entries: int = 0
    
    def compose(self):
        """Compose the widget - Static widgets don't yield children."""
        return []
    
    def on_mount(self) -> None:
        """Update display on mount."""
        self._update_content()
    
    def set_video_info(self, video_id: int, video_title: str) -> None:
        """Set video information.
        
        Args:
            video_id: Video ID
            video_title: Video title
        """
        self._video_id = video_id
        self._video_title = video_title
        self._update_content()
    
    def update_stats(self, total: int, filtered: int) -> None:
        """Update header statistics.
        
        Args:
            total: Total number of entries
            filtered: Number of entries after filtering
        """
        self._total_entries = total
        self._filtered_entries = filtered
        self._update_content()
    
    def _update_content(self) -> None:
        """Update the header content."""
        title = self._truncate_title(self._video_title, 40) if self._video_title else f"Video #{self._video_id}"
        text = f"Logs for {title} [{self._filtered_entries} entries]"
        self.update(text)
    
    def _truncate_title(self, title: str, max_length: int) -> str:
        """Truncate title to fit display."""
        if len(title) <= max_length:
            return title
        return title[:max_length - 3] + "..."


class VideoLogsFooter(Static):
    """Footer widget for video logs view."""
    
    DEFAULT_CSS = """
    VideoLogsFooter {
        height: 1;
        background: $surface-darken-2;
        color: $text-muted;
        content-align: center middle;
    }
    """
    
    def compose(self):
        """Set up the footer content."""
        self.update(
            "[f] Filter  [/] Search  [e] Export  [a] Auto-scroll  [r] Refresh  [q] Back"
        )
        return []


class VideoLogsScreen(Screen):
    """Screen displaying logs for a specific video.
    
    This screen shows all pipeline events for a single video,
    filtered by video_id. It's similar to EventLogScreen but
    pre-filtered to show only events for one video.
    
    Attributes:
        video_id: ID of the video to show logs for
        video_title: Title of the video (for display)
        event_bus: EventBus to subscribe to
        max_entries: Maximum number of entries to keep
    """
    
    DEFAULT_CSS = """
    VideoLogsScreen {
        layout: vertical;
    }
    
    VideoLogsScreen > #video-logs-container {
        height: 100%;
        width: 100%;
        layout: vertical;
    }
    
    VideoLogsScreen > #video-logs-container > #header-container {
        height: auto;
        dock: top;
    }
    
    VideoLogsScreen > #video-logs-container > #content-container {
        height: 1fr;
        width: 100%;
    }
    
    VideoLogsScreen > #video-logs-container > #footer-container {
        height: auto;
        dock: bottom;
    }
    
    VideoLogsScreen > #video-logs-container > #content-container > EventLogWidget {
        height: 100%;
        width: 100%;
    }
    """
    
    BINDINGS = [
        Binding("q", "back", "Back"),
        Binding("f", "filter", "Filter"),
        Binding("slash", "search", "Search"),
        Binding("e", "export", "Export"),
        Binding("a", "toggle_auto_scroll", "Auto-scroll"),
        Binding("r", "refresh", "Refresh"),
    ]
    
    def __init__(
        self,
        video_id: int,
        video_title: str = "",
        event_bus: Optional[EventBus] = None,
        max_entries: int = 500,
        **kwargs: Any,
    ) -> None:
        """Initialize the video logs screen.
        
        Args:
            video_id: ID of the video to show logs for
            video_title: Title of the video (for display)
            event_bus: EventBus to subscribe to (uses default if None)
            max_entries: Maximum number of entries to keep
            **kwargs: Additional arguments passed to Screen
        """
        super().__init__(**kwargs)
        self.video_id = video_id
        self.video_title = video_title
        self.event_bus = event_bus
        self.max_entries = max_entries
    
    def compose(self) -> None:
        """Compose the screen layout."""
        with Container(id="video-logs-container"):
            with Container(id="header-container"):
                yield VideoLogsHeader(
                    video_id=self.video_id,
                    video_title=self.video_title,
                    id="video-logs-header",
                )
            
            with Container(id="content-container"):
                log_widget = EventLogWidget(
                    event_bus=self.event_bus,
                    max_entries=self.max_entries,
                    id="video-log-widget",
                )
                # Pre-filter by video_id
                log_widget.filter_video_id = self.video_id
                yield log_widget
            
            with Container(id="footer-container"):
                yield VideoLogsFooter()
    
    def on_mount(self) -> None:
        """Handle mount event."""
        self._update_header()
    
    def _update_header(self) -> None:
        """Update the header with current stats."""
        try:
            header = self.query_one("#video-logs-header", VideoLogsHeader)
            log_widget = self.query_one("#video-log-widget", EventLogWidget)
            
            header.set_video_info(self.video_id, self.video_title)
            header.update_stats(
                total=log_widget.get_entry_count(),
                filtered=log_widget.get_filtered_count(),
            )
        except Exception:
            pass  # Widgets may not be mounted yet
    
    def action_back(self) -> None:
        """Navigate back to the video detail view."""
        self.app.pop_screen()
    
    def action_filter(self) -> None:
        """Open filter dialog."""
        def on_filter_result(event_type: Optional[EventType]) -> None:
            log_widget = self.query_one("#video-log-widget", EventLogWidget)
            log_widget.set_filter(event_type)
            self._update_header()
            
            if event_type:
                self.app.notify(f"Filtered by: {event_type.name}", timeout=2.0)
            else:
                self.app.notify("Filter cleared", timeout=1.0)
        
        self.push_screen(EventTypeFilterModal(), on_filter_result)
    
    def action_search(self) -> None:
        """Open search dialog."""
        log_widget = self.query_one("#video-log-widget", EventLogWidget)
        
        def on_search_result(query: str) -> None:
            log_widget.set_search(query)
            self._update_header()
            
            if query:
                self.app.notify(f"Searching for: {query}", timeout=2.0)
            else:
                self.app.notify("Search cleared", timeout=1.0)
        
        self.push_screen(SearchModal(log_widget.search_query), on_search_result)
    
    def action_export(self) -> None:
        """Export log to file."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        default_path = f"haven_video_{self.video_id}_log_{timestamp}.log"
        
        def on_export_path(path: str) -> None:
            if not path:
                self.app.notify("Export cancelled", timeout=1.0)
                return
            
            try:
                log_widget = self.query_one("#video-log-widget", EventLogWidget)
                log_widget.export(path)
                self.app.notify(f"Log exported to: {path}", timeout=3.0)
            except Exception as e:
                self.app.notify(f"Export failed: {e}", severity="error", timeout=3.0)
        
        self.push_screen(ExportModal(default_path), on_export_path)
    
    def action_toggle_auto_scroll(self) -> None:
        """Toggle auto-scroll."""
        log_widget = self.query_one("#video-log-widget", EventLogWidget)
        new_state = log_widget.toggle_auto_scroll()
        status = "ON" if new_state else "OFF"
        self.app.notify(f"Auto-scroll: {status}", timeout=1.5)
    
    def action_refresh(self) -> None:
        """Manually refresh the log display."""
        log_widget = self.query_one("#video-log-widget", EventLogWidget)
        log_widget.refresh_log()
        self._update_header()
        self.app.notify("Refreshed", timeout=1.0)


class ExportModal(ModalScreen[str]):
    """Modal dialog for entering export file path."""
    
    DEFAULT_CSS = """
    ExportModal {
        align: center middle;
    }
    
    ExportModal > Container {
        width: 60;
        height: auto;
        background: $surface;
        border: solid $primary;
        padding: 1;
    }
    
    ExportModal > Container > Static {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    
    ExportModal Input {
        margin: 1 0;
    }
    """
    
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]
    
    def __init__(self, default_path: str = "", **kwargs: Any) -> None:
        """Initialize the export modal.
        
        Args:
            default_path: Default file path
            **kwargs: Additional arguments
        """
        super().__init__(**kwargs)
        self._default_path = default_path
    
    def compose(self) -> None:
        """Compose the modal dialog."""
        with Container():
            yield Static("Export Event Log")
            yield Input(
                value=self._default_path,
                placeholder="Enter file path...",
                id="export-input",
            )
            with Horizontal():
                yield Button("Export", variant="primary", id="btn-export")
                yield Button("Cancel", id="btn-cancel")
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        button_id = event.button.id
        
        if button_id == "btn-export":
            input_widget = self.query_one("#export-input", Input)
            self.dismiss(input_widget.value)
        else:
            self.dismiss("")
    
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission."""
        self.dismiss(event.value)
    
    def action_cancel(self) -> None:
        """Cancel export."""
        self.dismiss("")


class EventLogView:
    """High-level interface for the event log view.
    
    Provides a simple API for showing the event log and
    managing its lifecycle.
    
    Example:
        >>> view = EventLogView(event_bus)
        >>> view.show()
    
    Attributes:
        event_bus: EventBus to subscribe to
        max_entries: Maximum number of entries to keep
        screen: The EventLogScreen instance
    """
    
    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        max_entries: int = 1000,
        on_back: Optional[Callable[[], None]] = None,
    ) -> None:
        """Initialize the event log view.
        
        Args:
            event_bus: EventBus to subscribe to
            max_entries: Maximum number of entries to keep
            on_back: Optional callback when user goes back
        """
        self.event_bus = event_bus
        self.max_entries = max_entries
        self.on_back = on_back
        self.screen: Optional[EventLogScreen] = None
    
    def create_screen(self) -> EventLogScreen:
        """Create the event log screen.
        
        Returns:
            The configured EventLogScreen instance
        """
        self.screen = EventLogScreen(
            event_bus=self.event_bus,
            max_entries=self.max_entries,
            on_back=self.on_back,
        )
        return self.screen
    
    def refresh(self) -> None:
        """Refresh the event log display."""
        if self.screen is not None:
            self.screen._update_header()
    
    def clear(self) -> None:
        """Clear all log entries."""
        if self.screen is not None:
            log_widget = self.screen.query_one("#event-log-widget", EventLogWidget)
            log_widget.clear_log()
