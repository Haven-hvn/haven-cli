"""Tests for Event Log View.

This module tests the event log components:
- LogEntry dataclass
- EventLogWidget
- EventLogScreen
- EventLogView
- Modal dialogs (EventTypeFilterModal, SearchModal, ExportModal)
"""

import asyncio
import os
import tempfile
from datetime import datetime
from typing import Generator
from unittest.mock import Mock, MagicMock

import pytest

from haven_cli.pipeline.events import EventBus, EventType, Event, reset_event_bus
from haven_tui.ui.views.event_log import (
    LogEntry,
    LogLevel,
    EventLogWidget,
    EventLogHeader,
    EventLogFooter,
    EventLogScreen,
    EventLogView,
    EventTypeFilterModal,
    SearchModal,
    ExportModal,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def event_bus() -> Generator[EventBus, None, None]:
    """Create a fresh event bus for testing."""
    reset_event_bus()
    bus = EventBus()
    yield bus
    reset_event_bus()


@pytest.fixture
def sample_events() -> list[Event]:
    """Create sample events for testing."""
    return [
        Event(
            event_type=EventType.PIPELINE_STARTED,
            payload={"video_id": 1, "source": "test"},
            timestamp=datetime(2024, 1, 1, 12, 0, 0),
            source="test",
        ),
        Event(
            event_type=EventType.DOWNLOAD_PROGRESS,
            payload={"video_id": 1, "progress_percent": 50, "download_rate": 1024000},
            timestamp=datetime(2024, 1, 1, 12, 0, 1),
            source="test",
        ),
        Event(
            event_type=EventType.VIDEO_INGESTED,
            payload={"video_id": 1},
            timestamp=datetime(2024, 1, 1, 12, 0, 2),
            source="test",
        ),
        Event(
            event_type=EventType.ENCRYPT_PROGRESS,
            payload={"video_id": 1, "progress": 75},
            timestamp=datetime(2024, 1, 1, 12, 0, 3),
            source="test",
        ),
        Event(
            event_type=EventType.PIPELINE_FAILED,
            payload={"video_id": 1, "stage": "upload", "error": "Network error"},
            timestamp=datetime(2024, 1, 1, 12, 0, 4),
            source="test",
        ),
    ]


@pytest.fixture
def sample_log_entries() -> list[LogEntry]:
    """Create sample log entries for testing."""
    return [
        LogEntry(
            timestamp=datetime(2024, 1, 1, 12, 0, 0),
            event_type=EventType.PIPELINE_STARTED,
            message="Video 1: Pipeline started",
            level=LogLevel.INFO,
            source="test",
            video_id=1,
        ),
        LogEntry(
            timestamp=datetime(2024, 1, 1, 12, 0, 1),
            event_type=EventType.DOWNLOAD_PROGRESS,
            message="Video 1: Download 50.0% at 1.0MB/s",
            level=LogLevel.INFO,
            source="test",
            video_id=1,
        ),
        LogEntry(
            timestamp=datetime(2024, 1, 1, 12, 0, 2),
            event_type=EventType.VIDEO_INGESTED,
            message="Video 1: Ingested",
            level=LogLevel.INFO,
            source="test",
            video_id=1,
        ),
        LogEntry(
            timestamp=datetime(2024, 1, 1, 12, 0, 3),
            event_type=EventType.ENCRYPT_PROGRESS,
            message="Video 1: Encryption 75.0%",
            level=LogLevel.INFO,
            source="test",
            video_id=1,
        ),
        LogEntry(
            timestamp=datetime(2024, 1, 1, 12, 0, 4),
            event_type=EventType.PIPELINE_FAILED,
            message="Video 1: Failed at upload: Network error",
            level=LogLevel.ERROR,
            source="test",
            video_id=1,
        ),
    ]


# =============================================================================
# LogEntry Tests
# =============================================================================

class TestLogEntry:
    """Tests for LogEntry dataclass."""

    def test_basic_creation(self) -> None:
        """Test creating a basic log entry."""
        entry = LogEntry(
            timestamp=datetime(2024, 1, 1, 12, 0, 0),
            event_type=EventType.PIPELINE_STARTED,
            message="Test message",
        )
        
        assert entry.timestamp == datetime(2024, 1, 1, 12, 0, 0)
        assert entry.event_type == EventType.PIPELINE_STARTED
        assert entry.message == "Test message"
        assert entry.level == LogLevel.INFO  # Default
        assert entry.source == ""  # Default
        assert entry.video_id is None  # Default

    def test_level_colors(self) -> None:
        """Test level color mapping."""
        debug_entry = LogEntry(
            timestamp=datetime.now(),
            event_type=EventType.HEALTH_CHECK,
            message="Debug",
            level=LogLevel.DEBUG,
        )
        assert debug_entry.level_color == "dim"
        
        info_entry = LogEntry(
            timestamp=datetime.now(),
            event_type=EventType.PIPELINE_STARTED,
            message="Info",
            level=LogLevel.INFO,
        )
        assert info_entry.level_color == ""
        
        warning_entry = LogEntry(
            timestamp=datetime.now(),
            event_type=EventType.STEP_SKIPPED,
            message="Warning",
            level=LogLevel.WARNING,
        )
        assert warning_entry.level_color == "warning"
        
        error_entry = LogEntry(
            timestamp=datetime.now(),
            event_type=EventType.PIPELINE_FAILED,
            message="Error",
            level=LogLevel.ERROR,
        )
        assert error_entry.level_color == "error"

    def test_full_creation(self) -> None:
        """Test creating log entry with all fields."""
        entry = LogEntry(
            timestamp=datetime(2024, 1, 1, 12, 0, 0),
            event_type=EventType.DOWNLOAD_PROGRESS,
            message="Download progress",
            level=LogLevel.INFO,
            source="downloader",
            video_id=42,
            metadata={"progress": 50, "rate": 1024},
        )
        
        assert entry.video_id == 42
        assert entry.source == "downloader"
        assert entry.metadata == {"progress": 50, "rate": 1024}


# =============================================================================
# EventLogWidget Tests
# =============================================================================

class TestEventLogWidget:
    """Tests for EventLogWidget."""

    def test_basic_creation(self, event_bus: EventBus) -> None:
        """Test creating the widget."""
        widget = EventLogWidget(event_bus=event_bus, max_entries=500)
        
        assert widget.event_bus is event_bus
        assert widget.max_entries == 500
        assert widget.filter_event_type is None
        assert widget.search_query == ""
        assert len(widget.entries) == 0

    def test_start_stop_listening(self, event_bus: EventBus) -> None:
        """Test starting and stopping event listening."""
        widget = EventLogWidget(event_bus=event_bus)
        
        # Should not be subscribed initially
        assert widget._unsubscribe is None
        
        # Start listening
        widget.start()
        assert widget._unsubscribe is not None
        
        # Stop listening
        widget.stop()
        assert widget._unsubscribe is None

    def test_create_log_entry(self, event_bus: EventBus) -> None:
        """Test creating log entry from event."""
        widget = EventLogWidget(event_bus=event_bus)
        
        event = Event(
            event_type=EventType.PIPELINE_STARTED,
            payload={"video_id": 1, "source": "test"},
            timestamp=datetime(2024, 1, 1, 12, 0, 0),
            source="test",
        )
        
        entry = widget._create_log_entry(event)
        
        assert entry.event_type == EventType.PIPELINE_STARTED
        assert entry.video_id == 1
        assert entry.level == LogLevel.INFO
        assert "Video 1" in entry.message

    def test_get_level_for_event(self, event_bus: EventBus) -> None:
        """Test level determination for different event types."""
        widget = EventLogWidget(event_bus=event_bus)
        
        # Error types
        assert widget._get_level_for_event(EventType.PIPELINE_FAILED) == LogLevel.ERROR
        assert widget._get_level_for_event(EventType.UPLOAD_FAILED) == LogLevel.ERROR
        assert widget._get_level_for_event(EventType.STEP_FAILED) == LogLevel.ERROR
        
        # Warning types
        assert widget._get_level_for_event(EventType.PIPELINE_CANCELLED) == LogLevel.WARNING
        assert widget._get_level_for_event(EventType.STEP_SKIPPED) == LogLevel.WARNING
        
        # Debug types
        assert widget._get_level_for_event(EventType.HEALTH_CHECK) == LogLevel.DEBUG
        assert widget._get_level_for_event(EventType.WORKER_STATUS) == LogLevel.DEBUG
        
        # Info types
        assert widget._get_level_for_event(EventType.PIPELINE_STARTED) == LogLevel.INFO
        assert widget._get_level_for_event(EventType.DOWNLOAD_PROGRESS) == LogLevel.INFO


    def test_format_speed(self, event_bus: EventBus) -> None:
        """Test speed formatting."""
        widget = EventLogWidget(event_bus=event_bus)
        
        assert widget._format_speed(0) == "-"
        assert widget._format_speed(500) == "500B/s"
        assert widget._format_speed(1024) == "1.0KB/s"
        assert widget._format_speed(1048576) == "1.0MB/s"  # 1024*1024 = 1MB
        assert widget._format_speed(1073741824) == "1.0GB/s"  # 1024^3 = 1GB

    def test_matches_filters_no_filter(self, event_bus: EventBus, sample_log_entries: list[LogEntry]) -> None:
        """Test matching with no filters applied."""
        widget = EventLogWidget(event_bus=event_bus)
        
        entry = sample_log_entries[0]
        assert widget._matches_filters(entry) is True

    def test_matches_filters_event_type(self, event_bus: EventBus, sample_log_entries: list[LogEntry]) -> None:
        """Test matching with event type filter."""
        widget = EventLogWidget(event_bus=event_bus)
        widget.filter_event_type = EventType.PIPELINE_STARTED
        
        # Entry matching the filter
        started_entry = sample_log_entries[0]
        assert widget._matches_filters(started_entry) is True
        
        # Entry not matching the filter
        download_entry = sample_log_entries[1]
        assert widget._matches_filters(download_entry) is False

    def test_matches_filters_search_query(self, event_bus: EventBus, sample_log_entries: list[LogEntry]) -> None:
        """Test matching with search query."""
        widget = EventLogWidget(event_bus=event_bus)
        widget.search_query = "download"
        
        # Entry matching the search
        download_entry = sample_log_entries[1]
        assert widget._matches_filters(download_entry) is True
        
        # Entry not matching the search
        started_entry = sample_log_entries[0]
        assert widget._matches_filters(started_entry) is False

    def test_matches_filters_case_insensitive(self, event_bus: EventBus, sample_log_entries: list[LogEntry]) -> None:
        """Test search query is case insensitive."""
        widget = EventLogWidget(event_bus=event_bus)
        widget.search_query = "DOWNLOAD"
        
        download_entry = sample_log_entries[1]
        assert widget._matches_filters(download_entry) is True

    def test_get_filtered_entries(self, event_bus: EventBus, sample_log_entries: list[LogEntry]) -> None:
        """Test getting filtered entries."""
        widget = EventLogWidget(event_bus=event_bus)
        widget.entries.extend(sample_log_entries)
        
        # No filter - all entries
        filtered = widget._get_filtered_entries()
        assert len(filtered) == 5
        
        # Filter by event type
        widget.filter_event_type = EventType.PIPELINE_STARTED
        filtered = widget._get_filtered_entries()
        assert len(filtered) == 1
        assert filtered[0].event_type == EventType.PIPELINE_STARTED
        
        # Filter by search query
        widget.filter_event_type = None
        widget.search_query = "Video 1:"
        filtered = widget._get_filtered_entries()
        assert len(filtered) == 5

    def test_clear_log(self, event_bus: EventBus, sample_log_entries: list[LogEntry]) -> None:
        """Test clearing the log."""
        widget = EventLogWidget(event_bus=event_bus)
        widget.entries.extend(sample_log_entries)
        
        assert len(widget.entries) == 5
        
        widget.clear_log()
        
        assert len(widget.entries) == 0

    def test_export(self, event_bus: EventBus, sample_log_entries: list[LogEntry]) -> None:
        """Test exporting log to file."""
        widget = EventLogWidget(event_bus=event_bus)
        widget.entries.extend(sample_log_entries)
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log') as f:
            temp_path = f.name
        
        try:
            widget.export(temp_path)
            
            # Read and verify export
            with open(temp_path, 'r') as f:
                content = f.read()
            
            assert "# Haven Event Log Export" in content
            assert "Video 1: Pipeline started" in content
            assert "Video 1: Download 50.0%" in content
            assert "Video 1: Failed at upload" in content
            
            # Check header format
            assert "timestamp | level | event_type" in content
        finally:
            os.unlink(temp_path)

    def test_max_entries_limit(self, event_bus: EventBus) -> None:
        """Test that max entries limit is enforced."""
        widget = EventLogWidget(event_bus=event_bus, max_entries=3)
        
        # Add more entries than max
        for i in range(5):
            entry = LogEntry(
                timestamp=datetime.now(),
                event_type=EventType.PIPELINE_STARTED,
                message=f"Entry {i}",
            )
            widget.entries.append(entry)
        
        # Only max_entries should be kept
        assert len(widget.entries) == 3
        
        # Should be the most recent ones
        messages = [e.message for e in widget.entries]
        assert "Entry 2" in messages
        assert "Entry 3" in messages
        assert "Entry 4" in messages

    def test_get_entry_count(self, event_bus: EventBus, sample_log_entries: list[LogEntry]) -> None:
        """Test getting entry counts."""
        widget = EventLogWidget(event_bus=event_bus)
        widget.entries.extend(sample_log_entries)
        
        assert widget.get_entry_count() == 5
        
        # Test filtered count
        widget.filter_event_type = EventType.PIPELINE_STARTED
        assert widget.get_filtered_count() == 1

    def test_toggle_auto_scroll(self, event_bus: EventBus) -> None:
        """Test toggling auto-scroll."""
        widget = EventLogWidget(event_bus=event_bus)
        
        # Default is True
        assert widget._auto_scroll is True
        
        # Toggle off
        result = widget.toggle_auto_scroll()
        assert result is False
        assert widget._auto_scroll is False
        
        # Toggle on
        result = widget.toggle_auto_scroll()
        assert result is True
        assert widget._auto_scroll is True


# =============================================================================
# EventLogHeader Tests
# =============================================================================

class TestEventLogHeader:
    """Tests for EventLogHeader widget."""

    def test_basic_creation(self) -> None:
        """Test creating the header."""
        header = EventLogHeader()
        
        assert header._total_entries == 0
        assert header._filtered_entries == 0
        assert header._filter_name is None

    def test_update_stats_no_filter(self) -> None:
        """Test updating stats without filter."""
        header = EventLogHeader()
        
        header.update_stats(total=100, filtered=100)
        
        assert header._total_entries == 100
        assert header._filtered_entries == 100
        assert header._filter_name is None

    def test_update_stats_with_filter(self) -> None:
        """Test updating stats with filter."""
        header = EventLogHeader()
        
        header.update_stats(total=100, filtered=25, filter_name="DOWNLOAD_PROGRESS")
        
        assert header._total_entries == 100
        assert header._filtered_entries == 25
        assert header._filter_name == "DOWNLOAD_PROGRESS"


# =============================================================================
# EventLogView Tests
# =============================================================================

class TestEventLogView:
    """Tests for EventLogView high-level interface."""

    def test_basic_creation(self, event_bus: EventBus) -> None:
        """Test creating the view."""
        view = EventLogView(event_bus=event_bus, max_entries=500)
        
        assert view.event_bus is event_bus
        assert view.max_entries == 500
        assert view.screen is None

    def test_create_screen(self, event_bus: EventBus) -> None:
        """Test creating the screen."""
        view = EventLogView(event_bus=event_bus, max_entries=500)
        
        screen = view.create_screen()
        
        assert screen is not None
        assert view.screen is screen
        assert screen.event_bus is event_bus
        assert screen.max_entries == 500

    def test_create_screen_with_callback(self, event_bus: EventBus) -> None:
        """Test creating screen with back callback."""
        callback = Mock()
        view = EventLogView(event_bus=event_bus, on_back=callback)
        
        screen = view.create_screen()
        
        assert screen.on_back_callback is callback


# =============================================================================
# Modal Dialog Tests
# =============================================================================

class TestEventTypeFilterModal:
    """Tests for EventTypeFilterModal."""

    def test_basic_creation(self) -> None:
        """Test creating the modal."""
        modal = EventTypeFilterModal()
        
        assert modal is not None
        # Check that categories are defined
        assert "Progress" in modal.EVENT_CATEGORIES
        assert "Completion" in modal.EVENT_CATEGORIES
        assert EventType.DOWNLOAD_PROGRESS in modal.EVENT_CATEGORIES["Progress"]


class TestSearchModal:
    """Tests for SearchModal."""

    def test_basic_creation(self) -> None:
        """Test creating the modal."""
        modal = SearchModal()
        
        assert modal._current_query == ""

    def test_creation_with_query(self) -> None:
        """Test creating modal with existing query."""
        modal = SearchModal(current_query="test query")
        
        assert modal._current_query == "test query"


class TestExportModal:
    """Tests for ExportModal."""

    def test_basic_creation(self) -> None:
        """Test creating the modal."""
        modal = ExportModal()
        
        assert modal._default_path == ""

    def test_creation_with_path(self) -> None:
        """Test creating modal with default path."""
        modal = ExportModal(default_path="/path/to/log.txt")
        
        assert modal._default_path == "/path/to/log.txt"


# =============================================================================
# Integration Tests
# =============================================================================

class TestEventLogIntegration:
    """Integration tests for event log components."""

    @pytest.mark.asyncio
    async def test_event_subscribing_and_processing(self, event_bus: EventBus) -> None:
        """Test subscribing to events and processing them."""
        widget = EventLogWidget(event_bus=event_bus, max_entries=100)
        
        # Start listening
        widget.start()
        
        # Publish some events
        events = [
            Event(event_type=EventType.PIPELINE_STARTED, payload={"video_id": 1}),
            Event(event_type=EventType.DOWNLOAD_PROGRESS, payload={"video_id": 1, "progress_percent": 50}),
            Event(event_type=EventType.VIDEO_INGESTED, payload={"video_id": 1}),
        ]
        
        for event in events:
            await event_bus.publish(event)
        
        # Give async operations time to complete
        await asyncio.sleep(0.1)
        
        # Check that entries were created
        assert len(widget.entries) == 3
        
        # Stop listening
        widget.stop()

    def test_filter_and_search_combined(self, event_bus: EventBus, sample_log_entries: list[LogEntry]) -> None:
        """Test combining event type filter with search."""
        widget = EventLogWidget(event_bus=event_bus)
        widget.entries.extend(sample_log_entries)
        
        # Apply both filters
        widget.filter_event_type = EventType.PIPELINE_STARTED
        widget.search_query = "Video 1"
        
        filtered = widget._get_filtered_entries()
        
        # Should find the PIPELINE_STARTED entry
        assert len(filtered) == 1
        assert filtered[0].event_type == EventType.PIPELINE_STARTED
        
        # Change search to something that won't match
        widget.search_query = "download"
        filtered = widget._get_filtered_entries()
        
        # Should find nothing (PIPELINE_STARTED doesn't contain "download")
        assert len(filtered) == 0

    def test_multiple_filter_operations(self, event_bus: EventBus, sample_log_entries: list[LogEntry]) -> None:
        """Test setting and clearing filters."""
        widget = EventLogWidget(event_bus=event_bus)
        widget.entries.extend(sample_log_entries)
        
        # Set filter directly without calling _refresh_display (which needs mounted widget)
        widget.filter_event_type = EventType.DOWNLOAD_PROGRESS
        assert widget.filter_event_type == EventType.DOWNLOAD_PROGRESS
        
        # Clear filter
        widget.filter_event_type = None
        assert widget.filter_event_type is None
        
        # Set search
        widget.search_query = "test"
        assert widget.search_query == "test"
        
        # Clear search
        widget.search_query = ""
        assert widget.search_query == ""

    def test_export_empty_log(self, event_bus: EventBus) -> None:
        """Test exporting an empty log."""
        widget = EventLogWidget(event_bus=event_bus)
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log') as f:
            temp_path = f.name
        
        try:
            widget.export(temp_path)
            
            with open(temp_path, 'r') as f:
                content = f.read()
            
            # Should still have header
            assert "# Haven Event Log Export" in content
            
            # No entry lines after header
            lines = [l for l in content.split('\n') if l and not l.startswith('#') and not l.startswith('-')]
            assert len(lines) == 0
        finally:
            os.unlink(temp_path)
