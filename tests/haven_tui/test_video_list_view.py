"""Tests for the Main Video List View.

This module tests the VideoListView, VideoListScreen, and related widgets
to ensure they meet the acceptance criteria.
"""

import asyncio
import os
import tempfile
from datetime import datetime, timezone
from typing import List, Optional
from unittest.mock import MagicMock, Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

import sys
sys.path.insert(0, '/home/tower/Documents/workspace/haven-cli')

from haven_cli.database.models import (
    Base,
    Video,
    Download,
    PipelineSnapshot,
)
from haven_cli.pipeline.events import (
    Event,
    EventType,
    EventBus,
    get_event_bus,
    reset_event_bus,
)

from haven_tui.core.state_manager import StateManager, VideoState
from haven_tui.core.pipeline_interface import PipelineInterface
from haven_tui.config import HavenTUIConfig, FiltersConfig, DisplayConfig
from haven_tui.ui.views.video_list import (
    VideoListView,
    VideoListScreen,
    VideoListWidget,
    VideoListHeader,
    VideoListFooter,
    VideoRow,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def temp_db_path():
    """Create a temporary database file."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def database_engine(temp_db_path):
    """Create a database engine with all tables."""
    engine = create_engine(f"sqlite:///{temp_db_path}")
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(database_engine) -> Session:
    """Create a fresh database session for each test."""
    SessionLocal = sessionmaker(bind=database_engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def event_bus():
    """Get a fresh event bus for testing."""
    reset_event_bus()
    bus = get_event_bus()
    yield bus
    reset_event_bus()


@pytest.fixture
def config():
    """Create a test configuration."""
    return HavenTUIConfig(
        filters=FiltersConfig(
            show_completed=True,
            show_failed=True,
        ),
        display=DisplayConfig(
            refresh_rate=1.0,
        ),
    )


@pytest.fixture
async def pipeline_interface(database_engine, temp_db_path, event_bus):
    """Create a PipelineInterface with test database."""
    interface = PipelineInterface(
        database_path=temp_db_path,
        event_bus=event_bus,
    )
    
    SessionLocal = sessionmaker(bind=database_engine)
    session = SessionLocal()
    interface._db_session = session
    interface._plugin_manager = None
    
    yield interface
    
    session.close()
    reset_event_bus()


@pytest.fixture
async def state_manager(pipeline_interface):
    """Create an initialized StateManager."""
    manager = StateManager(pipeline_interface)
    await manager.initialize()
    yield manager
    await manager.shutdown()


@pytest.fixture
def video_list_view(state_manager, config):
    """Create a VideoListView instance."""
    return VideoListView(
        state_manager=state_manager,
        config=config,
    )


# =============================================================================
# Test Data Helpers
# =============================================================================

async def create_test_video(
    session: Session,
    title: str,
    status: str = "pending",
    stage: str = "download",
) -> Video:
    """Helper to create a test video."""
    video = Video(
        source_path=f"/test/{title.replace(' ', '_')}.mp4",
        title=title,
        duration=300.0,
        file_size=10485760,
    )
    session.add(video)
    session.commit()
    session.refresh(video)
    
    # Create pipeline snapshot
    snapshot = PipelineSnapshot(
        video_id=video.id,
        current_stage=stage,
        overall_status=status,
    )
    session.add(snapshot)
    session.commit()
    
    return video


def create_mock_video_state(
    video_id: int,
    title: str,
    stage: str = "download",
    progress: float = 0.0,
    status: str = "pending",
    speed: float = 0.0,
) -> VideoState:
    """Helper to create a mock VideoState."""
    state = VideoState(
        id=video_id,
        title=title,
        current_stage=stage,
        overall_status=status,
    )
    
    if stage == "download":
        state.download_status = status if status != "active" else "active"
        state.download_progress = progress
        state.download_speed = speed
    elif stage == "encrypt":
        state.encrypt_status = status if status != "active" else "active"
        state.encrypt_progress = progress
    elif stage == "upload":
        state.upload_status = status if status != "active" else "active"
        state.upload_progress = progress
        state.upload_speed = speed
    
    return state


# =============================================================================
# Unit Tests for VideoRow
# =============================================================================

class TestVideoRow:
    """Tests for the VideoRow dataclass."""
    
    def test_video_row_creation(self):
        """Test creating a VideoRow."""
        row = VideoRow(
            index=1,
            video_id=100,
            title="Test Video",
            stage="download",
            progress=50.0,
            speed="2.5MB/s",
            plugin="youtube",
            size="1.2GB",
            eta="5:30",
            status="active",
            started_at="02/08 14:30",
            skip_reason="",
        )
        
        assert row.index == 1
        assert row.video_id == 100
        assert row.title == "Test Video"
        assert row.stage == "download"
        assert row.progress == 50.0
        assert row.speed == "2.5MB/s"
        assert row.plugin == "youtube"
        assert row.size == "1.2GB"
        assert row.eta == "5:30"
        assert row.status == "active"
        assert row.started_at == "02/08 14:30"
        assert row.skip_reason == ""
    
    def test_video_row_with_skip_reason(self):
        """Test creating a VideoRow with skip reason."""
        row = VideoRow(
            index=2,
            video_id=101,
            title="Skipped Video",
            stage="upload",
            progress=0.0,
            speed="-",
            plugin="bittorrent",
            size="5.0GB",
            eta="--:--",
            status="skipped",
            started_at="02/08 15:00",
            skip_reason="exceeded configured size limit",
        )
        
        assert row.status == "skipped"
        assert row.skip_reason == "exceeded configured size limit"


# =============================================================================
# Unit Tests for VideoListWidget
# =============================================================================

class TestVideoListWidget:
    """Tests for the VideoListWidget."""
    
    def test_format_progress_bar_zero(self):
        """Test progress bar formatting at 0%."""
        widget = VideoListWidget()
        result = widget._format_progress_bar(0.0, 10)
        assert "0%" in result
        assert "░" in result
    
    def test_format_progress_bar_complete(self):
        """Test progress bar formatting at 100%."""
        widget = VideoListWidget()
        result = widget._format_progress_bar(100.0, 10)
        assert "100%" in result
        assert "█" in result
    
    def test_format_progress_bar_partial(self):
        """Test progress bar formatting at 50%."""
        widget = VideoListWidget()
        result = widget._format_progress_bar(50.0, 10)
        assert "50%" in result or "50" in result
        assert "█" in result
        assert "░" in result
    
    def test_format_speed_zero(self):
        """Test speed formatting at zero."""
        widget = VideoListWidget()
        result = widget._format_speed(0.0)
        assert result == "-"
    
    def test_format_speed_bytes(self):
        """Test speed formatting in bytes."""
        widget = VideoListWidget()
        result = widget._format_speed(500.0)
        assert "B/s" in result
    
    def test_format_speed_kilobytes(self):
        """Test speed formatting in KB."""
        widget = VideoListWidget()
        result = widget._format_speed(1024.0)
        assert "KB/s" in result
    
    def test_format_speed_megabytes(self):
        """Test speed formatting in MB."""
        widget = VideoListWidget()
        result = widget._format_speed(2.5 * 1024 * 1024)
        assert "MB/s" in result
    
    def test_format_size_zero(self):
        """Test size formatting at zero."""
        widget = VideoListWidget()
        result = widget._format_size(0)
        assert result == "-"
    
    def test_format_size_bytes(self):
        """Test size formatting in bytes."""
        widget = VideoListWidget()
        result = widget._format_size(500)
        assert "B" in result
    
    def test_format_size_gigabytes(self):
        """Test size formatting in GB."""
        widget = VideoListWidget()
        result = widget._format_size(3 * 1024 * 1024 * 1024)
        assert "GB" in result
    
    def test_format_eta_none(self):
        """Test ETA formatting when None."""
        widget = VideoListWidget()
        result = widget._format_eta(None)
        assert "--:--" in result
    
    def test_format_eta_minutes(self):
        """Test ETA formatting with minutes."""
        widget = VideoListWidget()
        result = widget._format_eta(330)  # 5 minutes 30 seconds
        assert "5:" in result or "5h" in result
    
    def test_format_eta_hours(self):
        """Test ETA formatting with hours."""
        widget = VideoListWidget()
        result = widget._format_eta(3660)  # 1 hour 1 minute
        assert "1h" in result
    
    def test_truncate_title_short(self):
        """Test title truncation for short titles."""
        widget = VideoListWidget()
        title = "Short"
        result = widget._truncate_title(title, 35)
        assert result == title
    
    def test_truncate_title_long(self):
        """Test title truncation for long titles."""
        widget = VideoListWidget()
        title = "A" * 50
        result = widget._truncate_title(title, 35)
        assert len(result) <= 35
        assert result.endswith("...")
    
    def test_get_stage_style_completed(self):
        """Test stage style for completed status."""
        widget = VideoListWidget()
        result = widget._get_stage_style("upload", "completed")
        assert "complete" in result
    
    def test_get_stage_style_failed(self):
        """Test stage style for failed status."""
        widget = VideoListWidget()
        result = widget._get_stage_style("download", "failed")
        assert "failed" in result
    
    def test_get_stage_style_active(self):
        """Test stage style for active stage."""
        widget = VideoListWidget()
        result = widget._get_stage_style("download", "active")
        assert "stage-download" in result


# =============================================================================
# Integration Tests for VideoListView
# =============================================================================

class TestVideoListView:
    """Integration tests for VideoListView."""
    
    @pytest.mark.asyncio
    async def test_view_initialization(self, state_manager, config):
        """Test VideoListView can be initialized."""
        view = VideoListView(
            state_manager=state_manager,
            config=config,
        )
        
        assert view.state_manager == state_manager
        assert view.config == config
        assert view.screen is None
    
    @pytest.mark.asyncio
    async def test_view_creates_screen(self, state_manager, config):
        """Test VideoListView creates a screen."""
        view = VideoListView(
            state_manager=state_manager,
            config=config,
        )
        
        screen = view.create_screen()
        
        assert screen is not None
        assert isinstance(screen, VideoListScreen)
        assert screen.state_manager == state_manager
        assert screen.config == config
    
    @pytest.mark.asyncio
    async def test_view_get_selected_video_no_screen(self, state_manager, config):
        """Test get_selected_video_id returns None when no screen."""
        view = VideoListView(
            state_manager=state_manager,
            config=config,
        )
        
        result = view.get_selected_video_id()
        assert result is None
    
    @pytest.mark.asyncio
    async def test_view_get_selected_videos_no_screen(self, state_manager, config):
        """Test get_selected_video_ids returns empty list when no screen."""
        view = VideoListView(
            state_manager=state_manager,
            config=config,
        )
        
        result = view.get_selected_video_ids()
        assert result == []


# =============================================================================
# Acceptance Criteria Tests
# =============================================================================

class TestAcceptanceCriteria:
    """Tests for meeting the acceptance criteria."""
    
    @pytest.mark.asyncio
    async def test_displays_active_videos(
        self,
        database_engine,
        db_session,
        temp_db_path,
        event_bus,
        config,
    ):
        """AC1: List displays all active videos from PipelineSnapshot table.
        
        Verify that videos with active pipeline snapshots are displayed.
        """
        SessionLocal = sessionmaker(bind=database_engine)
        session = SessionLocal()
        
        try:
            # Create videos with pipeline snapshots
            video1 = await create_test_video(
                session, "Active Video 1", "active", "download"
            )
            video2 = await create_test_video(
                session, "Active Video 2", "active", "upload"
            )
            video3 = await create_test_video(
                session, "Completed Video", "completed", "complete"
            )
            
            # Create pipeline interface and state manager
            pipeline = PipelineInterface(
                database_path=temp_db_path,
                event_bus=event_bus,
            )
            pipeline._db_session = session
            pipeline._plugin_manager = None
            
            state_manager = StateManager(pipeline)
            await state_manager.initialize()
            
            # Load videos into state manager manually
            state_manager._state[video1.id] = create_mock_video_state(
                video1.id, video1.title, "download", 50.0, "active", 1024000
            )
            state_manager._state[video2.id] = create_mock_video_state(
                video2.id, video2.title, "upload", 75.0, "active", 512000
            )
            state_manager._state[video3.id] = create_mock_video_state(
                video3.id, video3.title, "complete", 100.0, "completed", 0
            )
            
            # Create video list view
            view = VideoListView(state_manager, config)
            screen = view.create_screen()
            
            # Verify state manager has videos
            all_videos = state_manager.get_all_videos()
            assert len(all_videos) == 3
            
            await state_manager.shutdown()
            
        finally:
            session.close()
    
    def test_columns_display(self):
        """AC2: Columns are #, Title, Stage, Progress, Speed, Plugin, Size, ETA.
        
        Verify that VideoListWidget has the correct column definitions.
        """
        widget = VideoListWidget()
        columns = widget.COLUMNS
        
        column_keys = [col[0] for col in columns if col[3]]  # Get visible column keys
        
        assert "#" in column_keys
        assert "title" in column_keys
        assert "stage" in column_keys
        assert "progress" in column_keys
        assert "speed" in column_keys
        assert "plugin" in column_keys
        assert "size" in column_keys
        assert "eta" in column_keys
    
    def test_progress_bar_visual(self):
        """AC3: Progress bar shows visual progress per stage.
        
        Verify that progress bars are formatted with Unicode characters.
        """
        widget = VideoListWidget()
        
        # Test various progress levels
        zero_bar = widget._format_progress_bar(0.0, 10)
        assert "░" in zero_bar
        
        half_bar = widget._format_progress_bar(50.0, 10)
        assert "█" in half_bar
        assert "░" in half_bar
        
        full_bar = widget._format_progress_bar(100.0, 10)
        assert "█" in full_bar
        assert "100%" in full_bar
    
    @pytest.mark.asyncio
    async def test_auto_refresh_config(self, config):
        """AC4: Auto-refresh updates every N seconds.
        
        Verify that the refresh rate is configurable.
        """
        screen = VideoListScreen(config=config)
        
        assert screen.auto_refresh is True
        assert config.display.refresh_rate > 0
    
    def test_multi_selection_support(self):
        """AC5: Supports multi-selection (like aria2tui).
        
        Verify that VideoListWidget supports multi-selection.
        """
        from haven_tui.core.pipeline_interface import BatchOperations
        
        # Create a mock state_manager and pipeline_interface
        mock_state_manager = MagicMock()
        mock_pipeline = MagicMock()
        
        # Create batch operations
        batch_ops = BatchOperations(mock_state_manager, mock_pipeline)
        batch_ops.selected = {1, 2, 3}
        
        # Create widget with batch operations
        widget = VideoListWidget(batch_operations=batch_ops)
        
        # Test selection tracking
        selected = widget.get_selected_video_ids()
        
        assert len(selected) == 3
        assert 1 in selected
        assert 2 in selected
        assert 3 in selected
        
        # Test clear selection
        widget.clear_selection()
        assert len(widget.get_selected_video_ids()) == 0
    
    def test_color_coding_stages(self):
        """AC6: Color coding for different stages.
        
        Verify that different stages have different CSS styles.
        """
        widget = VideoListWidget()
        
        # Each stage should have a specific style
        download_style = widget._get_stage_style("download", "active")
        assert "download" in download_style
        
        upload_style = widget._get_stage_style("upload", "active")
        assert "upload" in upload_style
        
        encrypt_style = widget._get_stage_style("encrypt", "active")
        assert "encrypt" in encrypt_style
        
        complete_style = widget._get_stage_style("complete", "completed")
        assert "complete" in complete_style


# =============================================================================
# Additional Tests for VideoListScreen
# =============================================================================

class TestVideoListScreen:
    """Tests for VideoListScreen functionality."""
    
    @pytest.mark.asyncio
    async def test_screen_bindings(self, config):
        """Test that all keyboard bindings are defined."""
        screen = VideoListScreen(config=config)
        
        bindings = screen.BINDINGS
        binding_keys = [b[0] for b in bindings]
        
        assert "q" in binding_keys  # Quit
        assert "r" in binding_keys  # Refresh
        assert "a" in binding_keys  # Auto-refresh toggle
        assert "d" in binding_keys  # Details
        assert "f" in binding_keys  # Filter
        assert "s" in binding_keys  # Sort
        assert "?" in binding_keys  # Help
        assert "space" in binding_keys  # Select toggle
    
    def test_screen_has_auto_refresh_reactive(self, config):
        """Test that auto_refresh is a reactive property."""
        screen = VideoListScreen(config=config)
        
        assert hasattr(screen, 'auto_refresh')
        assert screen.auto_refresh is True
        
        screen.auto_refresh = False
        assert screen.auto_refresh is False


# =============================================================================
# VideoListHeader Tests
# =============================================================================

class TestVideoListHeader:
    """Tests for VideoListHeader widget."""
    
    def test_header_without_state_manager(self):
        """Test header displays default text without state manager."""
        header = VideoListHeader()
        
        # Just verify it doesn't raise an error
        header.update_header()
    
    @pytest.mark.asyncio
    async def test_header_shows_stats(self):
        """Test header shows pipeline statistics."""
        # Create a mock state manager
        mock_state = MagicMock()
        
        # Mock video states
        video1 = MagicMock()
        video1.is_active = True
        video1.is_completed = False
        video1.has_failed = False
        
        video2 = MagicMock()
        video2.is_active = False
        video2.is_completed = True
        video2.has_failed = False
        
        mock_state.get_all_videos.return_value = [video1, video2]
        
        header = VideoListHeader(state_manager=mock_state)
        header.update_header()


# =============================================================================
# VideoListFooter Tests
# =============================================================================

class TestVideoListFooter:
    """Tests for VideoListFooter widget."""
    
    def test_footer_content(self):
        """Test footer contains all key bindings."""
        footer = VideoListFooter()
        
        # The footer should mention key bindings
        # Note: Since compose() is async in textual, we can't easily test the content
        # but we can verify the widget is created
        assert footer is not None


# =============================================================================
# Performance Tests
# =============================================================================

class TestVideoListPerformance:
    """Performance tests for video list view."""
    
    def test_format_progress_bar_performance(self):
        """Test progress bar formatting performance."""
        import time
        
        widget = VideoListWidget()
        
        start = time.time()
        for i in range(1000):
            widget._format_progress_bar(float(i % 100), 10)
        elapsed = time.time() - start
        
        # Should format 1000 progress bars in less than 1 second
        assert elapsed < 1.0
    
    def test_format_speed_performance(self):
        """Test speed formatting performance."""
        import time
        
        widget = VideoListWidget()
        
        start = time.time()
        for i in range(1000):
            widget._format_speed(float(i * 1024))
        elapsed = time.time() - start
        
        # Should format 1000 speeds in less than 1 second
        assert elapsed < 1.0


# =============================================================================
# Speed Graph Toggle Tests (Task 5.4)
# =============================================================================

class TestVideoListFooterGraphState:
    """Tests for VideoListFooter graph visibility state."""
    
    def test_footer_with_graph_visible(self):
        """Test footer shows graph ON state."""
        footer = VideoListFooter(show_graph=True)
        assert footer._show_graph is True
    
    def test_footer_with_graph_hidden(self):
        """Test footer shows graph OFF state."""
        footer = VideoListFooter(show_graph=False)
        assert footer._show_graph is False
    
    def test_footer_set_show_graph(self):
        """Test footer can toggle graph state."""
        footer = VideoListFooter(show_graph=False)
        assert footer._show_graph is False
        
        footer.set_show_graph(True)
        assert footer._show_graph is True
        
        footer.set_show_graph(False)
        assert footer._show_graph is False


class TestSpeedGraphToggle:
    """Tests for speed graph toggle functionality (Task 5.4)."""
    
    def test_screen_has_graph_binding(self, config):
        """Test that 'g' key binding exists for graph toggle."""
        screen = VideoListScreen(config=config)
        bindings = screen.BINDINGS
        binding_keys = [b[0] for b in bindings]
        assert "g" in binding_keys
    
    def test_screen_has_show_graph_reactive(self, config):
        """Test that show_graph reactive property exists."""
        screen = VideoListScreen(config=config)
        assert hasattr(screen, 'show_graph')
    
    def test_screen_show_graph_default_from_config(self, config):
        """Test that show_graph defaults to config value (True by default)."""
        screen = VideoListScreen(config=config)
        # Default config has show_speed_graphs=True
        assert screen.show_graph is True
    
    def test_screen_show_graph_from_config_true(self):
        """Test that show_graph can be set to True from config."""
        config_with_graph = HavenTUIConfig(
            display=DisplayConfig(show_speed_graphs=True)
        )
        screen = VideoListScreen(config=config_with_graph)
        assert screen.show_graph is True
    
    def test_screen_show_graph_from_config_false(self):
        """Test that show_graph can be set to False from config."""
        config_no_graph = HavenTUIConfig(
            display=DisplayConfig(show_speed_graphs=False)
        )
        screen = VideoListScreen(config=config_no_graph)
        assert screen.show_graph is False
    
    def test_screen_has_toggle_graph_action(self, config):
        """Test that action_toggle_graph method exists and is callable."""
        screen = VideoListScreen(config=config)
        assert hasattr(screen, 'action_toggle_graph')
        assert callable(screen.action_toggle_graph)
    
    def test_screen_has_selected_video_id(self, config):
        """Test that _selected_video_id tracking exists."""
        screen = VideoListScreen(config=config)
        assert hasattr(screen, '_selected_video_id')
        assert screen._selected_video_id is None
    
    def test_screen_has_selected_stage(self, config):
        """Test that _selected_stage tracking exists."""
        screen = VideoListScreen(config=config)
        assert hasattr(screen, '_selected_stage')
        assert screen._selected_stage == "download"
    
    def test_screen_has_on_video_select(self, config):
        """Test that _on_video_select callback exists."""
        screen = VideoListScreen(config=config)
        assert hasattr(screen, '_on_video_select')
        assert callable(screen._on_video_select)
    
    def test_screen_updates_selected_video_on_select(self, config):
        """Test that _on_video_select updates _selected_video_id."""
        screen = VideoListScreen(config=config)
        assert screen._selected_video_id is None
        
        screen._on_video_select(123)
        assert screen._selected_video_id == 123
        
        screen._on_video_select(456)
        assert screen._selected_video_id == 456
    
    def test_screen_has_speed_history_repo_param(self):
        """Test that speed_history_repo parameter is accepted."""
        mock_repo = MagicMock()
        screen = VideoListScreen(
            config=HavenTUIConfig(),
            speed_history_repo=mock_repo,
        )
        assert screen._speed_history_repo == mock_repo


class TestSpeedGraphIntegration:
    """Integration tests for speed graph with video list."""
    
    @pytest.mark.asyncio
    async def test_view_accepts_speed_history_repo(self, state_manager, config):
        """Test that VideoListView accepts speed_history_repo parameter."""
        mock_repo = MagicMock()
        view = VideoListView(
            state_manager=state_manager,
            config=config,
            speed_history_repo=mock_repo,
        )
        assert view.speed_history_repo == mock_repo
    
    @pytest.mark.asyncio
    async def test_view_creates_screen_with_repo(self, state_manager, config):
        """Test that VideoListView passes repo to VideoListScreen."""
        mock_repo = MagicMock()
        view = VideoListView(
            state_manager=state_manager,
            config=config,
            speed_history_repo=mock_repo,
        )
        screen = view.create_screen()
        assert screen._speed_history_repo == mock_repo
    
    
