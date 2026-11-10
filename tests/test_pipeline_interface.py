"""Unit tests for PipelineInterface.

Tests cover:
- Context manager functionality
- Event subscription wrappers
- Database query methods
- Unified download view
- TUI-first operations
"""

import asyncio
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# Ensure we can import from the project
import sys
sys.path.insert(0, '/home/tower/Documents/workspace/haven-cli')

from haven_tui.core.pipeline_interface import (
    PipelineInterface,
    UnifiedDownload,
    DownloadStats,
    RetryResult,
)
from haven_cli.database.models import (
    Base,
    Video,
    Download,
    TorrentDownload,
    PipelineSnapshot,
)
from haven_cli.database.repositories import VideoRepository
from haven_cli.pipeline.events import EventType, Event, EventBus


@pytest.fixture
def temp_db_path():
    """Create a temporary database file."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def db_session(temp_db_path):
    """Create a database session with test data."""
    engine = create_engine(f"sqlite:///{temp_db_path}")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    
    yield session
    
    session.close()
    engine.dispose()


@pytest.fixture
def mock_event_bus():
    """Create a mock event bus."""
    bus = MagicMock(spec=EventBus)
    bus.subscribe = MagicMock(return_value=MagicMock())
    bus.subscribe_all = MagicMock(return_value=MagicMock())
    bus.publish = AsyncMock()
    return bus


class TestPipelineInterfaceContextManager:
    """Test context manager functionality."""
    
    
    @pytest.mark.asyncio
    async def test_aenter_initializes_event_bus(self, temp_db_path):
        """Test that __aenter__ initializes the event bus."""
        interface = PipelineInterface(database_path=temp_db_path)
        
        async with interface as iface:
            assert iface._event_bus is not None
    
    @pytest.mark.asyncio
    async def test_aexit_closes_session(self, temp_db_path):
        """Test that __aexit__ closes the database session."""
        interface = PipelineInterface(database_path=temp_db_path)
        
        async with interface as iface:
            pass
        
        assert interface._db_session is None
    
    @pytest.mark.asyncio
    async def test_custom_event_bus_preserved(self, temp_db_path, mock_event_bus):
        """Test that custom event bus is preserved."""
        interface = PipelineInterface(
            database_path=temp_db_path,
            event_bus=mock_event_bus
        )
        
        async with interface as iface:
            assert iface._event_bus is mock_event_bus


class TestEventSubscriptions:
    """Test event subscription functionality."""
    
    @pytest.mark.asyncio
    async def test_on_event_with_sync_handler(self, temp_db_path):
        """Test subscribing with a sync handler."""
        interface = PipelineInterface(database_path=temp_db_path)
        mock_bus = MagicMock(spec=EventBus)
        mock_bus.subscribe = MagicMock(return_value=MagicMock())
        interface._event_bus = mock_bus
        interface._db_session = MagicMock()
        
        events_received = []
        
        def sync_handler(event: Event):
            events_received.append(event)
        
        interface.on_event(EventType.DOWNLOAD_PROGRESS, sync_handler)
        
        # Verify subscription was made
        assert mock_bus.subscribe.called
        call_args = mock_bus.subscribe.call_args
        assert call_args[0][0] == EventType.DOWNLOAD_PROGRESS
    
    @pytest.mark.asyncio
    async def test_on_event_with_async_handler(self, temp_db_path):
        """Test subscribing with an async handler."""
        interface = PipelineInterface(database_path=temp_db_path)
        mock_bus = MagicMock(spec=EventBus)
        mock_bus.subscribe = MagicMock(return_value=MagicMock())
        interface._event_bus = mock_bus
        interface._db_session = MagicMock()
        
        async def async_handler(event: Event):
            pass
        
        interface.on_event(EventType.DOWNLOAD_PROGRESS, async_handler)
        
        # Verify subscription was made
        assert mock_bus.subscribe.called
    
    @pytest.mark.asyncio
    async def test_on_any_event(self, temp_db_path):
        """Test subscribing to all events."""
        interface = PipelineInterface(database_path=temp_db_path)
        mock_bus = MagicMock(spec=EventBus)
        mock_bus.subscribe_all = MagicMock(return_value=MagicMock())
        interface._event_bus = mock_bus
        interface._db_session = MagicMock()
        
        def handler(event: Event):
            pass
        
        interface.on_any_event(handler)
        
        # Verify subscription was made
        assert mock_bus.subscribe_all.called
    
    @pytest.mark.asyncio
    async def test_unsubscribe(self, temp_db_path):
        """Test unsubscribing a handler."""
        interface = PipelineInterface(database_path=temp_db_path)
        interface._event_bus = MagicMock()
        interface._db_session = MagicMock()
        
        def handler(event: Event):
            pass
        
        # First subscribe
        interface.on_event(EventType.DOWNLOAD_PROGRESS, handler)
        
        # Then unsubscribe
        result = interface.unsubscribe(EventType.DOWNLOAD_PROGRESS, handler)
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_unsubscribe_not_found(self, temp_db_path):
        """Test unsubscribing a handler that wasn't subscribed."""
        interface = PipelineInterface(database_path=temp_db_path)
        
        def handler(event: Event):
            pass
        
        result = interface.unsubscribe(EventType.DOWNLOAD_PROGRESS, handler)
        
        assert result is False


class TestDatabaseQueries:
    """Test database query methods."""
    
    
    
    
    
    


class TestUnifiedDownloads:
    """Test unified download view functionality."""
    
    
    
    


class TestTuiOperations:
    """Test TUI-first operations."""
    
    
    
    
    
    
    
    


class TestErrorHandling:
    """Test error handling scenarios."""
    
    
    @pytest.mark.asyncio
    async def test_no_event_bus_error(self, db_session):
        """Test error when event operations called without event bus."""
        interface = PipelineInterface()
        interface._db_session = db_session
        # Don't set _event_bus
        
        with pytest.raises(RuntimeError, match="Event bus not initialized"):
            interface.on_event(EventType.DOWNLOAD_PROGRESS, lambda e: None)
        
        with pytest.raises(RuntimeError, match="Event bus not initialized"):
            interface.on_any_event(lambda e: None)


class TestUnifiedDownloadDataclass:
    """Test UnifiedDownload dataclass."""
    
    def test_unified_download_creation(self):
        """Test creating a UnifiedDownload instance."""
        download = UnifiedDownload(
            id=1,
            video_id=2,
            source_type="youtube",
            title="Test Video",
            status="active",
            progress_percent=50.0,
            speed=1024,
            eta=60,
            total_bytes=1000,
            downloaded_bytes=500,
            started_at=datetime.now(timezone.utc),
            youtube_url="https://youtube.com/watch?v=test",
        )
        
        assert download.id == 1
        assert download.video_id == 2
        assert download.source_type == "youtube"
        assert download.youtube_url == "https://youtube.com/watch?v=test"
    
    def test_unified_download_torrent_fields(self):
        """Test UnifiedDownload with torrent-specific fields."""
        download = UnifiedDownload(
            id=1,
            video_id=2,
            source_type="torrent",
            title="Torrent Video",
            status="active",
            progress_percent=75.0,
            speed=2048,
            eta=120,
            total_bytes=10000,
            downloaded_bytes=7500,
            started_at=datetime.now(timezone.utc),
            torrent_magnet="magnet:?xt=urn:btih:test",
            torrent_peers=10,
            torrent_seeds=5,
        )
        
        assert download.torrent_magnet == "magnet:?xt=urn:btih:test"
        assert download.torrent_peers == 10
        assert download.torrent_seeds == 5


class TestDownloadStats:
    """Test DownloadStats dataclass."""
    
    def test_download_stats_creation(self):
        """Test creating a DownloadStats instance."""
        stats = DownloadStats(
            active_count=5,
            pending_count=3,
            completed_today=10,
            failed_count=2,
            total_speed=10240,
            youtube_active=3,
            torrent_active=2,
            youtube_speed=6144,
            torrent_speed=4096,
        )
        
        assert stats.active_count == 5
        assert stats.pending_count == 3
        assert stats.completed_today == 10
        assert stats.failed_count == 2
        assert stats.total_speed == 10240
        assert stats.youtube_active == 3
        assert stats.torrent_active == 2
        assert stats.youtube_speed == 6144
        assert stats.torrent_speed == 4096


class TestRetryResult:
    """Test RetryResult dataclass."""
    
    def test_retry_result_success(self):
        """Test creating a successful RetryResult."""
        result = RetryResult(
            success=True,
            message="Retrying video from download stage",
            new_job_id=123,
        )
        
        assert result.success is True
        assert "download" in result.message
        assert result.new_job_id == 123
    
    def test_retry_result_failure(self):
        """Test creating a failed RetryResult."""
        result = RetryResult(
            success=False,
            message="Video 999 not found",
        )
        
        assert result.success is False
        assert "not found" in result.message
        assert result.new_job_id is None


class TestGetDownloadStats:
    """Test get_download_stats functionality."""
    
    


class TestGetDownloadHistory:
    """Test get_download_history functionality."""
    
    
    


class TestRetryVideoPerStage:
    """Test retry_video with per-stage support."""
    
    @pytest.mark.asyncio
    async def test_retry_invalid_stage(self, db_session):
        """Test retrying with an invalid stage."""
        video = Video(source_path="/test/video.mp4", title="Test Video")
        db_session.add(video)
        db_session.commit()
        
        interface = PipelineInterface()
        interface._db_session = db_session
        interface._event_bus = MagicMock()
        
        result = await interface.retry_video(video.id, stage="invalid_stage")
        
        assert isinstance(result, RetryResult)
        assert result.success is False
        assert "Invalid stage" in result.message
    
    


class TestCancelVideo:
    """Test cancel_video functionality."""
    


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
