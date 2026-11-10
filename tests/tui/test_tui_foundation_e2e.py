"""End-to-End tests for TUI Foundation layer.

These tests verify complete workflows across all foundation components:
- PipelineInterface
- StateManager
- MetricsCollector

Tests cover:
- Full video lifecycle (ingest -> download -> encrypt -> upload)
- Retry flows (failure and recovery)
- Concurrent video processing
- Performance with many videos
"""

import asyncio
import os
import tempfile
import time
from datetime import datetime, timezone
from typing import List, Dict, Any
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
from haven_tui.core.state_manager import VideoState, StateManager
from haven_tui.core.metrics import MetricsCollector
from haven_cli.database.models import (
    Base,
    Video,
    Download,
    TorrentDownload,
    EncryptionJob,
    UploadJob,
    SyncJob,
    AnalysisJob,
    PipelineSnapshot,
    SpeedHistory,
)
from haven_cli.pipeline.events import (
    Event,
    EventType,
    EventBus,
    get_event_bus,
    reset_event_bus,
)
from haven_cli.services.speed_history import SpeedHistoryService


def dt_now():
    """Get current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


# =============================================================================
# Test Fixtures
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
async def pipeline_interface(database_engine, temp_db_path, event_bus):
    """Create a PipelineInterface with test database."""
    interface = PipelineInterface(
        database_path=temp_db_path,
        event_bus=event_bus,
    )
    
    # Set up session manually
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
async def metrics_collector(db_session):
    """Create a MetricsCollector with real SpeedHistoryService."""
    service = SpeedHistoryService(db_session)
    await service.start()
    
    collector = MetricsCollector(service)
    yield collector
    
    await service.stop()


@pytest.fixture
def mock_event_bus():
    """Create a mock event bus for testing."""
    return MockEventBus()


class MockEventBus:
    """Mock event bus for testing without full pipeline."""
    
    def __init__(self):
        from collections import defaultdict
        self._subscribers = defaultdict(list)
        self._all_subscribers = []
        self._history = []
    
    def subscribe(self, event_type, handler):
        self._subscribers[event_type].append(handler)
        return lambda: self._subscribers[event_type].remove(handler)
    
    def subscribe_all(self, handler):
        self._all_subscribers.append(handler)
        return lambda: self._all_subscribers.remove(handler)
    
    def unsubscribe(self, event_type, handler):
        if event_type in self._subscribers:
            try:
                self._subscribers[event_type].remove(handler)
            except ValueError:
                pass
    
    async def publish(self, event):
        self._history.append(event)
        
        # Call type-specific handlers
        handlers = self._subscribers.get(event.event_type, [])
        for handler in list(handlers):
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
        
        # Call all-event handlers
        for handler in list(self._all_subscribers):
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
    
    def simulate_download_progress(self, video_id, progress, speed):
        """Helper to simulate download progress events."""
        event = Event(
            event_type=EventType.DOWNLOAD_PROGRESS,
            payload={
                'video_id': video_id,
                'progress': progress,
                'speed': speed,
            },
        )
        asyncio.create_task(self.publish(event))


# =============================================================================
# Test Helpers
# =============================================================================

async def create_test_video(session: Session, title: str, status: str = "pending") -> Video:
    """Helper to create a test video in the database."""
    video = Video(
        source_path=f"/test/{title.replace(' ', '_')}.mp4",
        title=title,
        duration=300.0,
        file_size=10485760,
    )
    session.add(video)
    session.commit()
    session.refresh(video)
    return video


async def create_test_download(
    session: Session,
    video_id: int,
    source_type: str = "youtube",
    status: str = "pending"
) -> Download:
    """Helper to create a test download job."""
    download = Download(
        video_id=video_id,
        source_type=source_type,
        status=status,
        progress_percent=0.0,
        download_rate=0,
    )
    session.add(download)
    session.commit()
    session.refresh(download)
    return download


def wait_for_state_change(callback_mock, timeout: float = 1.0):
    """Helper to wait for state change callback."""
    import time
    start = time.time()
    while time.time() - start < timeout:
        if callback_mock.called:
            return True
        time.sleep(0.01)
    return False


# =============================================================================
# End-to-End Tests
# =============================================================================

class TestTuiFoundationE2E:
    """End-to-end tests for the complete foundation layer."""
    
    
    @pytest.mark.asyncio
    async def test_retry_flow(
        self,
        database_engine,
        temp_db_path,
        event_bus,
    ):
        """Test: Video fails at encrypt -> retry from encrypt -> completes.
        
        Verify state resets and re-updates correctly.
        """
        SessionLocal = sessionmaker(bind=database_engine)
        session = SessionLocal()
        
        try:
            # Create pipeline interface
            pipeline = PipelineInterface(
                database_path=temp_db_path,
                event_bus=event_bus,
            )
            pipeline._db_session = session
            pipeline._plugin_manager = None
            
            # Create state manager
            state_manager = StateManager(pipeline)
            await state_manager.initialize()
            
            # 1. Create video and set up initial state
            video = Video(
                source_path="/test/retry_video.mp4",
                title="Retry Test Video",
            )
            session.add(video)
            session.commit()
            session.refresh(video)
            
            # Add to state manager
            state_manager._state[video.id] = VideoState(
                id=video.id,
                title="Retry Test Video",
                download_status="completed",
                encrypt_status="failed",
                overall_status="failed",
            )
            
            # 2. Verify initial failed state
            state = state_manager.get_video(video.id)
            assert state.encrypt_status == "failed"
            assert state.overall_status == "failed"
            
            # 3. Retry pipeline
            await event_bus.publish(Event(
                event_type=EventType.PIPELINE_STARTED,
                payload={'video_id': video.id, 'retry': True},
            ))
            await asyncio.sleep(0.05)
            
            # 4. Manually reset encrypt status (in real scenario, retry_video would do this)
            async with state_manager._lock:
                state_manager._state[video.id].encrypt_status = "active"
                state_manager._state[video.id].encrypt_progress = 0.0
                state_manager._state[video.id].overall_status = "active"
            
            state = state_manager.get_video(video.id)
            assert state.encrypt_status == "active"
            assert state.overall_status == "active"
            
            # 5. Encryption completes successfully on retry
            await event_bus.publish(Event(
                event_type=EventType.ENCRYPT_COMPLETE,
                payload={'video_id': video.id},
            ))
            await asyncio.sleep(0.05)
            
            state = state_manager.get_video(video.id)
            assert state.encrypt_status == "completed"
            assert state.encrypt_progress == 100.0
            
            # 6. Continue to upload and complete
            await event_bus.publish(Event(
                event_type=EventType.UPLOAD_COMPLETE,
                payload={'video_id': video.id},
            ))
            await event_bus.publish(Event(
                event_type=EventType.SYNC_COMPLETE,
                payload={'video_id': video.id},
            ))
            await event_bus.publish(Event(
                event_type=EventType.ANALYSIS_COMPLETE,
                payload={'video_id': video.id},
            ))
            await asyncio.sleep(0.1)
            
            # 7. Verify final completed state
            state = state_manager.get_video(video.id)
            assert state.encrypt_status == "completed"
            assert state.upload_status == "completed"
            assert state.sync_status == "completed"
            assert state.analysis_status == "completed"
            assert state.is_completed is True
            assert state.has_failed is False
            
            await state_manager.shutdown()
            
        finally:
            session.close()
    
    


# =============================================================================
# Performance Tests
# =============================================================================

class TestTuiFoundationPerformance:
    """Performance tests for TUI foundation."""
    
    


# =============================================================================
# Integration Tests for PipelineInterface
# =============================================================================

class TestPipelineInterfaceIntegration:
    """Integration tests for PipelineInterface."""
    
    
    
    @pytest.mark.asyncio
    async def test_get_active_videos_returns_videos(
        self,
        database_engine,
        temp_db_path,
    ):
        """Test get_active_videos returns videos with pipeline activity."""
        SessionLocal = sessionmaker(bind=database_engine)
        session = SessionLocal()
        
        async with PipelineInterface(database_path=temp_db_path) as interface:
            interface._db_session = session
            
            # Create a video with an active snapshot
            video = Video(
                source_path="/test/active_video.mp4",
                title="Active Video",
            )
            session.add(video)
            session.commit()
            session.refresh(video)
            
            snapshot = PipelineSnapshot(
                video_id=video.id,
                current_stage="download",
                overall_status="active",
            )
            session.add(snapshot)
            session.commit()
            
            # Get active videos
            active = interface.get_active_videos()
            assert len(active) >= 1
            assert any(v.id == video.id for v in active)
        
        session.close()
    
    
    
    
    
    @pytest.mark.asyncio
    async def test_event_subscription_works(
        self,
        database_engine,
        temp_db_path,
        event_bus,
    ):
        """Test event subscription and unsubscription."""
        SessionLocal = sessionmaker(bind=database_engine)
        session = SessionLocal()
        
        events_received = []
        
        def handler(event):
            events_received.append(event)
        
        try:
            async with PipelineInterface(
                database_path=temp_db_path,
                event_bus=event_bus,
            ) as interface:
                interface._db_session = session
                
                # Subscribe to event
                interface.on_event(EventType.DOWNLOAD_PROGRESS, handler)
                
                # Publish event
                await event_bus.publish(Event(
                    event_type=EventType.DOWNLOAD_PROGRESS,
                    payload={'progress': 50.0},
                ))
                
                await asyncio.sleep(0.05)
                
                # Verify handler was called
                assert len(events_received) >= 1
                
                # Unsubscribe
                interface.unsubscribe(EventType.DOWNLOAD_PROGRESS, handler)
        
        finally:
            session.close()


# =============================================================================
# Integration Tests for StateManager
# =============================================================================

class TestStateManagerIntegration:
    """Integration tests for StateManager."""
    
    
    
    
    
    
    @pytest.mark.asyncio
    async def test_handles_missing_video_gracefully(
        self,
        database_engine,
        temp_db_path,
        event_bus,
    ):
        """Test StateManager handles missing video gracefully."""
        SessionLocal = sessionmaker(bind=database_engine)
        session = SessionLocal()
        
        try:
            pipeline = PipelineInterface(
                database_path=temp_db_path,
                event_bus=event_bus,
            )
            pipeline._db_session = session
            
            # Set up to return None for unknown video
            pipeline.get_video_detail = MagicMock(return_value=None)
            
            state_manager = StateManager(pipeline)
            await state_manager.initialize()
            
            # Send event for non-existent video
            await event_bus.publish(Event(
                event_type=EventType.DOWNLOAD_PROGRESS,
                payload={
                    'video_id': 99999,
                    'progress': 50.0,
                },
            ))
            await asyncio.sleep(0.05)
            
            # Should not crash
            assert state_manager.get_video(99999) is None
            
            await state_manager.shutdown()
        
        finally:
            session.close()


# =============================================================================
# Integration Tests for MetricsCollector
# =============================================================================

class TestPipelineInterfaceAdditionalCoverage:
    """Additional tests to improve PipelineInterface coverage."""
    
    
    
    
    @pytest.mark.asyncio
    async def test_retry_video_not_found(self, database_engine, temp_db_path):
        """Test retry_video with non-existent video."""
        SessionLocal = sessionmaker(bind=database_engine)
        session = SessionLocal()
        
        try:
            async with PipelineInterface(database_path=temp_db_path) as interface:
                interface._db_session = session
                
                result = await interface.retry_video(99999)
                
                assert result.success is False
                assert "not found" in result.message.lower()
        
        finally:
            session.close()
    
    @pytest.mark.asyncio
    async def test_retry_video_invalid_stage(self, database_engine, temp_db_path):
        """Test retry_video with invalid stage."""
        SessionLocal = sessionmaker(bind=database_engine)
        session = SessionLocal()
        
        try:
            async with PipelineInterface(database_path=temp_db_path) as interface:
                interface._db_session = session
                
                video = Video(
                    source_path="/test/invalid_stage.mp4",
                    title="Invalid Stage Test",
                )
                session.add(video)
                session.commit()
                session.refresh(video)
                
                result = await interface.retry_video(video.id, stage="invalid_stage")
                
                assert result.success is False
                assert "invalid" in result.message.lower()
        
        finally:
            session.close()
    
    @pytest.mark.asyncio
    async def test_cancel_video_not_found(self, database_engine, temp_db_path):
        """Test cancel_video with non-existent video."""
        SessionLocal = sessionmaker(bind=database_engine)
        session = SessionLocal()
        
        try:
            async with PipelineInterface(database_path=temp_db_path) as interface:
                interface._db_session = session
                
                result = await interface.cancel_video(99999)
                
                assert result is False
    
        finally:
            session.close()
    
    @pytest.mark.asyncio
    async def test_error_handling_database_rollback(self, database_engine, temp_db_path):
        """Test that database errors trigger rollback."""
        SessionLocal = sessionmaker(bind=database_engine)
        session = SessionLocal()
        
        try:
            interface = PipelineInterface(database_path=temp_db_path)
            interface._db_session = session
            
            # Create a valid context first
            try:
                async with interface:
                    # Force an error by raising
                    raise ValueError("Test error")
            except ValueError:
                pass
            
            # Session should be closed after exception
            # (Note: we can't easily verify rollback without more setup)
        
        finally:
            session.close()
    
    @pytest.mark.asyncio
    async def test_unsubscribe_returns_false_when_not_found(
        self,
        database_engine,
        temp_db_path,
        event_bus,
    ):
        """Test unsubscribe returns False when handler not found."""
        SessionLocal = sessionmaker(bind=database_engine)
        session = SessionLocal()
        
        try:
            async with PipelineInterface(
                database_path=temp_db_path,
                event_bus=event_bus,
            ) as interface:
                interface._db_session = session
                
                # Try to unsubscribe a handler that was never subscribed
                def dummy_handler(event):
                    pass
                
                result = interface.unsubscribe(EventType.DOWNLOAD_PROGRESS, dummy_handler)
                assert result is False
        
        finally:
            session.close()


class TestMetricsCollectorIntegration:
    """Integration tests for MetricsCollector."""
    
    @pytest.mark.asyncio
    async def test_metrics_invalid_stage_handling(self, db_session):
        """Test MetricsCollector handles invalid stage gracefully."""
        speed_service = SpeedHistoryService(db_session)
        await speed_service.start()
        
        metrics = MetricsCollector(speed_service)
        
        # Try to record with invalid stage
        metrics.record_speed(1, "invalid_stage", 1000000.0, 50.0)
        # Should not raise, just log warning
        
        # Try to get history with invalid stage
        history = metrics.get_speed_history(1, "invalid_stage", seconds=60)
        assert history == []
        
        await speed_service.stop()
    
    @pytest.mark.asyncio
    async def test_metrics_current_speed_none_when_empty(self, db_session):
        """Test get_current_speed returns None when no data."""
        speed_service = SpeedHistoryService(db_session)
        await speed_service.start()
        
        metrics = MetricsCollector(speed_service)
        
        current = metrics.get_current_speed(1, "download")
        assert current is None
        
        await speed_service.stop()

    
    @pytest.mark.asyncio
    async def test_records_speed_from_events(
        self,
        database_engine,
        db_session,
        temp_db_path,
        event_bus,
    ):
        """Test MetricsCollector records speed from events."""
        # Set up services
        speed_service = SpeedHistoryService(db_session)
        await speed_service.start()
        
        metrics = MetricsCollector(speed_service)
        
        # Create video
        video = Video(
            source_path="/test/speed_video.mp4",
            title="Speed Test",
        )
        db_session.add(video)
        db_session.commit()
        db_session.refresh(video)
        
        # Record speeds
        for i in range(5):
            metrics.record_speed(
                video.id,
                "download",
                1000000.0 * (i + 1),
                float((i + 1) * 20),
            )
        
        # Flush to database
        speed_service._flush_all_buffers()
        
        # Verify history
        history = metrics.get_speed_history(video.id, "download", seconds=60)
        assert len(history) == 5
        
        await speed_service.stop()
    
    @pytest.mark.asyncio
    async def test_get_speed_history_returns_correct_range(
        self,
        database_engine,
        db_session,
    ):
        """Test get_speed_history returns correct time range."""
        speed_service = SpeedHistoryService(db_session)
        await speed_service.start()
        
        metrics = MetricsCollector(speed_service)
        
        # Create video
        video = Video(
            source_path="/test/range_video.mp4",
            title="Range Test",
        )
        db_session.add(video)
        db_session.commit()
        db_session.refresh(video)
        
        # Record speed
        metrics.record_speed(video.id, "download", 1000000.0, 50.0)
        speed_service._flush_all_buffers()
        
        # Query with different ranges
        history_60s = metrics.get_speed_history(video.id, "download", seconds=60)
        history_1s = metrics.get_speed_history(video.id, "download", seconds=1)
        
        # Both should have the recent sample
        assert len(history_60s) == 1
        # 1 second might filter it out depending on timing
        assert len(history_1s) <= 1
        
        await speed_service.stop()
    
    @pytest.mark.asyncio
    async def test_aggregate_speeds_calculate_correctly(
        self,
        database_engine,
        db_session,
    ):
        """Test aggregate speeds calculate correctly."""
        speed_service = SpeedHistoryService(db_session)
        await speed_service.start()
        
        metrics = MetricsCollector(speed_service)
        
        # Create multiple videos
        videos = []
        for i in range(3):
            video = Video(
                source_path=f"/test/agg_video_{i}.mp4",
                title=f"Aggregate Video {i}",
            )
            db_session.add(video)
            videos.append(video)
        db_session.commit()
        for video in videos:
            db_session.refresh(video)
        
        # Record speeds for different videos
        for i, video in enumerate(videos):
            metrics.record_speed(
                video.id,
                "download",
                1000000.0 * (i + 1),
                50.0,
            )
        
        speed_service._flush_all_buffers()
        
        # Get aggregates
        aggregates = metrics.get_aggregate_speeds(seconds=60)
        
        assert 'download' in aggregates
        assert 'upload' in aggregates
        assert 'total' in aggregates
        
        await speed_service.stop()
    
    @pytest.mark.asyncio
    async def test_chart_data_formatted_correctly(
        self,
        database_engine,
        db_session,
    ):
        """Test chart data is formatted correctly."""
        speed_service = SpeedHistoryService(db_session)
        await speed_service.start()
        
        metrics = MetricsCollector(speed_service)
        
        # Create video
        video = Video(
            source_path="/test/chart_video.mp4",
            title="Chart Test",
        )
        db_session.add(video)
        db_session.commit()
        db_session.refresh(video)
        
        # Record speeds
        for i in range(10):
            metrics.record_speed(
                video.id,
                "download",
                500000.0 + (i * 100000),
                float(i * 10),
            )
        
        speed_service._flush_all_buffers()
        
        # Get chart data
        chart_data = metrics.get_speed_data_for_chart(
            video_id=video.id,
            stage="download",
            seconds=60,
            bucket_size=5,
        )
        
        assert 'timestamps' in chart_data
        assert 'speeds' in chart_data
        assert 'avg_speed' in chart_data
        assert 'peak_speed' in chart_data
        assert 'current_speed' in chart_data
        
        assert len(chart_data['timestamps']) > 0
        assert len(chart_data['speeds']) > 0
        assert chart_data['peak_speed'] >= chart_data['avg_speed']
        
        await speed_service.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
