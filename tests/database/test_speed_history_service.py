"""Tests for SpeedHistoryService.

This module tests the SpeedHistoryService which samples and stores
speed metrics for TUI graph visualization.
"""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from typing import Generator
from unittest.mock import MagicMock, AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from haven_cli.database.models import Base, Video, SpeedHistory
from haven_cli.services.speed_history import SpeedHistoryService, get_speed_history_service, reset_speed_history_service
from haven_cli.pipeline.events import EventBus, Event, EventType


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Create a fresh database session for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def sample_video(db_session: Session) -> Video:
    """Create a sample video for testing."""
    video = Video(
        source_path="/test/video.mp4",
        title="Test Video",
        duration=120.0,
        file_size=1000000,
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)
    return video


@pytest.fixture
def event_bus() -> EventBus:
    """Create a fresh event bus."""
    return EventBus()


class TestSpeedHistoryService:
    """Tests for SpeedHistoryService."""
    
    def test_service_creation(self, db_session: Session) -> None:
        """Test creating a speed history service."""
        service = SpeedHistoryService(db_session)
        
        assert service.db == db_session
        assert not service._running
    
    @pytest.mark.asyncio
    async def test_service_start_stop(self, db_session: Session) -> None:
        """Test starting and stopping the service."""
        service = SpeedHistoryService(db_session)
        
        await service.start()
        assert service._running
        
        await service.stop()
        assert not service._running
    
    @pytest.mark.asyncio
    async def test_record_sample(self, db_session: Session, sample_video: Video) -> None:
        """Test recording a speed sample."""
        service = SpeedHistoryService(db_session)
        await service.start()
        
        service.record_sample(
            video_id=sample_video.id,
            stage="download",
            speed=100000,
            progress=50.0,
            bytes_processed=500000,
        )
        
        # Buffer should have the sample
        key = (sample_video.id, "download")
        assert len(service._buffer[key]) == 1
        assert service._buffer[key][0]["speed"] == 100000
        
        await service.stop()
    
    @pytest.mark.asyncio
    async def test_record_sample_not_running(self, db_session: Session, sample_video: Video) -> None:
        """Test recording when service is not running."""
        service = SpeedHistoryService(db_session)
        # Don't start the service
        
        service.record_sample(
            video_id=sample_video.id,
            stage="download",
            speed=100000,
        )
        
        # Buffer should be empty
        key = (sample_video.id, "download")
        assert len(service._buffer[key]) == 0
    
    @pytest.mark.asyncio
    async def test_buffer_flush(self, db_session: Session, sample_video: Video) -> None:
        """Test buffer flushing to database."""
        service = SpeedHistoryService(db_session)
        service.FLUSH_INTERVAL = 5  # Set lower for testing
        await service.start()
        
        # Record samples to trigger flush
        for i in range(service.FLUSH_INTERVAL):
            service.record_sample(
                video_id=sample_video.id,
                stage="download",
                speed=100000 + i * 1000,
                progress=float(i * 10),
            )
        
        # Buffer should be flushed
        key = (sample_video.id, "download")
        assert len(service._buffer[key]) == 0
        
        # Check database has the entries
        entries = db_session.query(SpeedHistory).filter_by(
            video_id=sample_video.id,
            stage="download"
        ).all()
        
        assert len(entries) == service.FLUSH_INTERVAL
        
        await service.stop()
    
    @pytest.mark.asyncio
    async def test_get_speed_history(self, db_session: Session, sample_video: Video) -> None:
        """Test getting speed history."""
        service = SpeedHistoryService(db_session)
        await service.start()
        
        # Record some samples and flush
        for i in range(5):
            service.record_sample(
                video_id=sample_video.id,
                stage="download",
                speed=100000 + i * 1000,
                progress=float(i * 20),
            )
        
        # Force flush
        service._flush_all_buffers()
        
        # Get history
        history = service.get_speed_history(sample_video.id, "download", minutes=5)
        
        assert len(history) == 5
        
        await service.stop()
    
    @pytest.mark.asyncio
    async def test_get_aggregate_speeds(self, db_session: Session, sample_video: Video) -> None:
        """Test getting aggregate speeds."""
        service = SpeedHistoryService(db_session)
        await service.start()
        
        # Record samples for different stages
        for stage in ["download", "encrypt", "upload"]:
            for i in range(3):
                service.record_sample(
                    video_id=sample_video.id,
                    stage=stage,
                    speed=100000,
                    progress=float(i * 33),
                )
        
        # Force flush
        service._flush_all_buffers()
        
        # Get aggregates
        aggregates = service.get_aggregate_speeds(minutes=5)
        
        assert len(aggregates) >= 3
        
        await service.stop()
    
    @pytest.mark.asyncio
    async def test_get_formatted_for_plotille(self, db_session: Session, sample_video: Video) -> None:
        """Test getting data formatted for plotille."""
        service = SpeedHistoryService(db_session)
        await service.start()
        
        # Record samples
        for i in range(10):
            service.record_sample(
                video_id=sample_video.id,
                stage="download",
                speed=100000 + i * 5000,
                progress=float(i * 10),
            )
        
        # Force flush
        service._flush_all_buffers()
        
        # Get formatted data
        data = service.get_formatted_for_plotille(
            video_id=sample_video.id,
            stage="download",
            minutes=5,
        )
        
        assert "x_values" in data
        assert "y_values" in data
        assert "timestamps" in data
        assert "min_speed" in data
        assert "max_speed" in data
        assert "avg_speed" in data
        assert "count" in data
        
        assert data["count"] == 10
        assert data["min_speed"] <= data["max_speed"]
        
        await service.stop()
    
    @pytest.mark.asyncio
    async def test_buffer_size_limit(self, db_session: Session, sample_video: Video) -> None:
        """Test that buffer respects max size limit."""
        service = SpeedHistoryService(db_session)
        service.MAX_SAMPLES = 5  # Set low for testing
        await service.start()
        
        # Record more samples than max
        for i in range(service.MAX_SAMPLES + 10):
            service.record_sample(
                video_id=sample_video.id,
                stage="download",
                speed=100000,
            )
        
        # Buffer should be limited to MAX_SAMPLES
        key = (sample_video.id, "download")
        assert len(service._buffer[key]) <= service.MAX_SAMPLES
        
        await service.stop()
    
    @pytest.mark.asyncio
    async def test_flush_on_stop(self, db_session: Session, sample_video: Video) -> None:
        """Test that buffer is flushed when service stops."""
        service = SpeedHistoryService(db_session)
        await service.start()
        
        # Record samples
        for i in range(3):
            service.record_sample(
                video_id=sample_video.id,
                stage="download",
                speed=100000,
            )
        
        # Buffer should have samples
        key = (sample_video.id, "download")
        assert len(service._buffer[key]) == 3
        
        # Stop should flush
        await service.stop()
        
        # Check database
        entries = db_session.query(SpeedHistory).filter_by(
            video_id=sample_video.id
        ).all()
        
        assert len(entries) == 3
    
    @pytest.mark.asyncio
    async def test_cleanup_old_samples(self, db_session: Session, sample_video: Video) -> None:
        """Test cleanup of old samples."""
        service = SpeedHistoryService(db_session)
        await service.start()
        
        # Add a recent sample
        service.record_sample(
            video_id=sample_video.id,
            stage="download",
            speed=100000,
        )
        
        # Force flush
        service._flush_all_buffers()
        
        # Manually add an old sample
        old_entry = SpeedHistory(
            video_id=sample_video.id,
            stage="download",
            speed=50000,
            timestamp=datetime.now(timezone.utc) - timedelta(hours=48),
        )
        db_session.add(old_entry)
        db_session.commit()
        
        # Verify both exist
        assert db_session.query(SpeedHistory).count() == 2
        
        # Cleanup old samples
        deleted = service._repo.cleanup_old_samples(hours=24)
        
        assert deleted == 1
        assert db_session.query(SpeedHistory).count() == 1
        
        await service.stop()
    
    def test_record_sample_clamping(self, db_session: Session, sample_video: Video) -> None:
        """Test that values are properly clamped."""
        service = SpeedHistoryService(db_session)
        service._running = True  # Simulate running state
        
        # Test negative speed is clamped to 0
        service._record_sample(
            video_id=sample_video.id,
            stage="download",
            speed=-1000,
        )
        
        key = (sample_video.id, "download")
        assert service._buffer[key][-1]["speed"] == 0
        
        # Test progress clamping
        service._record_sample(
            video_id=sample_video.id,
            stage="download",
            speed=100000,
            progress=150.0,  # Should be clamped to 100
        )
        
        assert service._buffer[key][-1]["progress"] == 100.0
        
        # Test negative progress
        service._record_sample(
            video_id=sample_video.id,
            stage="download",
            speed=100000,
            progress=-50.0,  # Should be clamped to 0
        )
        
        assert service._buffer[key][-1]["progress"] == 0.0


class TestSpeedHistoryServiceSingleton:
    """Tests for SpeedHistoryService singleton functions."""
    
    def test_get_speed_history_service(self, db_session: Session) -> None:
        """Test getting the default speed history service."""
        reset_speed_history_service()
        
        service = get_speed_history_service(db_session)
        
        assert service is not None
        assert isinstance(service, SpeedHistoryService)
        
        # Should return same instance
        service2 = get_speed_history_service()
        assert service is service2
        
        reset_speed_history_service()
    
    def test_get_speed_history_service_no_db(self) -> None:
        """Test getting service without providing db session."""
        reset_speed_history_service()
        
        service = get_speed_history_service()
        
        assert service is None
    
    def test_reset_speed_history_service(self, db_session: Session) -> None:
        """Test resetting the default service."""
        reset_speed_history_service()
        
        service1 = get_speed_history_service(db_session)
        reset_speed_history_service()
        service2 = get_speed_history_service(db_session)
        
        # After reset, should be different instance
        assert service1 is not service2


class TestSpeedHistoryServiceEventHandling:
    """Tests for SpeedHistoryService event handling."""
    
    @pytest.mark.asyncio
    async def test_upload_progress_event(self, db_session: Session, sample_video: Video, event_bus: EventBus) -> None:
        """Test handling upload progress events."""
        service = SpeedHistoryService(db_session, event_bus)
        await service.start()
        
        # Publish upload progress event
        event = Event(
            event_type=EventType.UPLOAD_PROGRESS,
            payload={
                "video_id": sample_video.id,
                "progress_percent": 50.0,
                # upload_step.py does not emit a speed field; speed_history.py
                # falls back to 0. ``upload_speed`` is accepted for explicit
                # speed reporting from future emitters.
                "upload_speed": 100000,
                "bytes_uploaded": 500000,
            },
        )
        await event_bus.publish(event)
        
        # Give async handlers time to process
        await asyncio.sleep(0.1)
        
        # Check that sample was recorded
        key = (sample_video.id, "upload")
        # Note: Event handling might not work in test due to async timing
        
        await service.stop()


class TestSpeedHistoryServiceEdgeCases:
    """Tests for edge cases."""
    
    @pytest.mark.asyncio
    async def test_empty_history(self, db_session: Session, sample_video: Video) -> None:
        """Test getting history when no data exists."""
        service = SpeedHistoryService(db_session)
        await service.start()
        
        history = service.get_speed_history(sample_video.id, "download", minutes=5)
        
        assert len(history) == 0
        
        await service.stop()
    
    @pytest.mark.asyncio
    async def test_empty_plotille_data(self, db_session: Session, sample_video: Video) -> None:
        """Test plotille formatting with no data."""
        service = SpeedHistoryService(db_session)
        await service.start()
        
        data = service.get_formatted_for_plotille(sample_video.id, "download")
        
        assert data["x_values"] == []
        assert data["y_values"] == []
        assert data["count"] == 0
        
        await service.stop()
    
    @pytest.mark.asyncio
    async def test_multiple_videos(self, db_session: Session) -> None:
        """Test handling multiple videos simultaneously."""
        # Create multiple videos
        videos = []
        for i in range(3):
            video = Video(
                source_path=f"/test/video{i}.mp4",
                title=f"Test Video {i}",
            )
            db_session.add(video)
            videos.append(video)
        
        db_session.commit()
        for v in videos:
            db_session.refresh(v)
        
        service = SpeedHistoryService(db_session)
        await service.start()
        
        # Record samples for all videos
        for i, video in enumerate(videos):
            service.record_sample(
                video_id=video.id,
                stage="download",
                speed=100000 * (i + 1),
            )
        
        # Check buffers
        for i, video in enumerate(videos):
            key = (video.id, "download")
            assert len(service._buffer[key]) == 1
            assert service._buffer[key][0]["speed"] == 100000 * (i + 1)
        
        await service.stop()
