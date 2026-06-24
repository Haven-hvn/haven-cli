"""Unit tests for MetricsCollector.

Tests cover:
- MetricsCollector initialization and wrapper functionality
- Speed recording methods
- Per-video speed history queries
- Aggregate speed queries
- Active stage counting
- Visualization helpers (chart data formatting)
- Data bucketing and aggregation
- Cleanup operations
"""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from typing import Generator
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from haven_cli.database.models import Base, Video, SpeedHistory
from haven_cli.services.speed_history import SpeedHistoryService
from haven_tui.core.metrics import MetricsCollector, VALID_STAGES


def dt_now():
    """Get current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


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
def mock_service():
    """Create a mock SpeedHistoryService for unit tests."""
    service = MagicMock(spec=SpeedHistoryService)
    service._repo = MagicMock()
    service._repo.cleanup_old_samples = MagicMock(return_value=10)
    return service


class TestMetricsCollectorInitialization:
    """Test MetricsCollector initialization."""
    
    def test_initialization_with_service(self, mock_service):
        """Test creating MetricsCollector with service."""
        collector = MetricsCollector(mock_service)
        
        assert collector._service is mock_service
        assert collector._max_history == 300  # Default
    
    def test_initialization_with_custom_max_history(self, mock_service):
        """Test creating MetricsCollector with custom max history."""
        collector = MetricsCollector(mock_service, max_history_seconds=600)
        
        assert collector._max_history == 600


class TestMetricsCollectorRecording:
    """Test speed recording methods."""
    
    def test_record_speed(self, mock_service):
        """Test recording speed sample."""
        collector = MetricsCollector(mock_service)
        
        collector.record_speed(1, "download", 1024000.0, 50.0)
        
        mock_service.record_sample.assert_called_once_with(
            video_id=1,
            stage="download",
            speed=1024000,
            progress=50.0,
            bytes_processed=0
        )
    
    def test_record_speed_invalid_stage(self, mock_service):
        """Test recording speed with invalid stage."""
        collector = MetricsCollector(mock_service)
        
        collector.record_speed(1, "invalid_stage", 1024000.0, 50.0)
        
        # Should not call service for invalid stage
        mock_service.record_sample.assert_not_called()
    
    def test_record_speed_zero_progress(self, mock_service):
        """Test recording speed with default progress."""
        collector = MetricsCollector(mock_service)
        
        collector.record_speed(1, "upload", 512000.0)
        
        mock_service.record_sample.assert_called_once_with(
            video_id=1,
            stage="upload",
            speed=512000,
            progress=0.0,
            bytes_processed=0
        )


class TestMetricsCollectorPerVideoQueries:
    """Test per-video speed history queries."""
    
    def test_get_speed_history(self, mock_service):
        """Test getting speed history for video/stage."""
        # Create mock history records
        now = dt_now()
        mock_records = [
            MagicMock(timestamp=now - timedelta(seconds=30), speed=100000),
            MagicMock(timestamp=now - timedelta(seconds=20), speed=150000),
            MagicMock(timestamp=now - timedelta(seconds=10), speed=200000),
        ]
        mock_service.get_speed_history.return_value = mock_records
        
        collector = MetricsCollector(mock_service)
        history = collector.get_speed_history(1, "download", seconds=60)
        
        assert len(history) == 3
        assert history[0] == (mock_records[0].timestamp, 100000.0)
        assert history[1] == (mock_records[1].timestamp, 150000.0)
        assert history[2] == (mock_records[2].timestamp, 200000.0)
    
    def test_get_speed_history_invalid_stage(self, mock_service):
        """Test getting history with invalid stage."""
        collector = MetricsCollector(mock_service)
        history = collector.get_speed_history(1, "invalid", seconds=60)
        
        assert history == []
        mock_service.get_speed_history.assert_not_called()
    
    def test_get_speed_history_time_filtering(self, mock_service):
        """Test that history is filtered by time window."""
        now = dt_now()
        mock_records = [
            MagicMock(timestamp=now - timedelta(seconds=90), speed=50000),  # Too old
            MagicMock(timestamp=now - timedelta(seconds=30), speed=100000),  # In range
            MagicMock(timestamp=now - timedelta(seconds=10), speed=150000),  # In range
        ]
        mock_service.get_speed_history.return_value = mock_records
        
        collector = MetricsCollector(mock_service)
        history = collector.get_speed_history(1, "download", seconds=60)
        
        # Only 2 records within 60 seconds
        assert len(history) == 2
        assert history[0][1] == 100000.0
        assert history[1][1] == 150000.0
    
    def test_get_speed_history_minutes_conversion(self, mock_service):
        """Test that seconds are properly converted to minutes."""
        mock_service.get_speed_history.return_value = []
        
        collector = MetricsCollector(mock_service)
        collector.get_speed_history(1, "download", seconds=120)
        
        # Should call with 2 minutes
        mock_service.get_speed_history.assert_called_once()
        args = mock_service.get_speed_history.call_args
        assert args[0][2] == 2  # minutes argument
    
    def test_get_speed_history_max_history_clamping(self, mock_service):
        """Test that history respects max_history setting."""
        mock_service.get_speed_history.return_value = []
        
        collector = MetricsCollector(mock_service, max_history_seconds=60)
        collector.get_speed_history(1, "download", seconds=300)
        
        # Should clamp to 1 minute (60 seconds / 60)
        args = mock_service.get_speed_history.call_args
        assert args[0][2] == 1  # minutes argument
    
    def test_get_current_speed_with_data(self, mock_service):
        """Test getting current speed when data exists."""
        now = dt_now()
        mock_records = [
            MagicMock(timestamp=now - timedelta(seconds=20), speed=100000),
            MagicMock(timestamp=now - timedelta(seconds=10), speed=150000),
        ]
        mock_service.get_speed_history.return_value = mock_records
        
        collector = MetricsCollector(mock_service)
        current = collector.get_current_speed(1, "download")
        
        assert current == 150000.0
    
    def test_get_current_speed_no_data(self, mock_service):
        """Test getting current speed when no data exists."""
        mock_service.get_speed_history.return_value = []
        
        collector = MetricsCollector(mock_service)
        current = collector.get_current_speed(1, "download")
        
        assert current is None


class TestMetricsCollectorAggregateQueries:
    """Test aggregate speed queries."""
    
    def test_get_aggregate_speeds(self, mock_service):
        """Test getting aggregate speeds across all stages."""
        # Mock aggregate data for each stage
        mock_service.get_aggregate_speeds.side_effect = lambda stage, minutes: [
            {'avg_speed': 100000, 'timestamp': dt_now()},
            {'avg_speed': 150000, 'timestamp': dt_now()},
        ]
        
        collector = MetricsCollector(mock_service)
        aggregates = collector.get_aggregate_speeds(seconds=120)
        
        assert 'download' in aggregates
        assert 'encrypt' in aggregates
        assert 'upload' in aggregates
        assert 'total' in aggregates
        
        # Each stage should have average of [100000, 150000] = 125000
        assert aggregates['download'] == 125000.0
        assert aggregates['encrypt'] == 125000.0
        assert aggregates['upload'] == 125000.0
        assert aggregates['total'] == 375000.0
    
    def test_get_aggregate_speeds_empty_data(self, mock_service):
        """Test aggregate speeds with empty data."""
        mock_service.get_aggregate_speeds.return_value = []
        
        collector = MetricsCollector(mock_service)
        aggregates = collector.get_aggregate_speeds(seconds=60)
        
        assert aggregates['download'] == 0.0
        assert aggregates['encrypt'] == 0.0
        assert aggregates['upload'] == 0.0
        assert aggregates['total'] == 0.0
    
    def test_get_aggregate_speeds_minutes_conversion(self, mock_service):
        """Test that seconds are converted to minutes."""
        mock_service.get_aggregate_speeds.return_value = []
        
        collector = MetricsCollector(mock_service)
        collector.get_aggregate_speeds(seconds=180)
        
        # Should call with 3 minutes
        assert mock_service.get_aggregate_speeds.call_count == 3
        args = mock_service.get_aggregate_speeds.call_args
        assert args[0][1] == 3  # minutes argument


class TestMetricsCollectorActiveStages:
    """Test active stage counting."""
    
    def test_get_active_stages(self, mock_service):
        """Test getting count of active videos per stage."""
        # Mock aggregate data with video IDs
        def mock_aggregate(stage, minutes):
            if stage == "download":
                return [
                    {'avg_speed': 100000, 'video_ids': [1, 2]},
                    {'avg_speed': 150000, 'video_ids': [2, 3]},
                ]
            elif stage == "upload":
                return [
                    {'avg_speed': 50000, 'video_ids': [3]},
                ]
            else:
                return []
        
        mock_service.get_aggregate_speeds.side_effect = mock_aggregate
        
        collector = MetricsCollector(mock_service)
        active = collector.get_active_stages(seconds=60)
        
        # download: unique videos [1, 2, 3] = 3
        # encrypt: no data = 0
        # upload: unique videos [3] = 1
        # total_active: unique videos [1, 2, 3] = 3
        assert active['download'] == 3
        assert active['encrypt'] == 0
        assert active['upload'] == 1
        assert active['total_active'] == 3
    
    def test_get_active_stages_with_video_id_field(self, mock_service):
        """Test active stages with single video_id field."""
        def mock_aggregate(stage, minutes):
            return [
                {'avg_speed': 100000, 'video_id': 1},
                {'avg_speed': 150000, 'video_id': 2},
            ]
        
        mock_service.get_aggregate_speeds.side_effect = mock_aggregate
        
        collector = MetricsCollector(mock_service)
        active = collector.get_active_stages(seconds=60)
        
        assert active['download'] == 2
        assert active['encrypt'] == 2
        assert active['upload'] == 2


class TestMetricsCollectorVisualization:
    """Test visualization helpers."""
    
    def test_get_speed_data_for_chart_per_video(self, mock_service):
        """Test chart data for specific video/stage."""
        now = dt_now()
        mock_records = [
            MagicMock(timestamp=now - timedelta(seconds=30), speed=100000),
            MagicMock(timestamp=now - timedelta(seconds=20), speed=150000),
            MagicMock(timestamp=now - timedelta(seconds=10), speed=200000),
        ]
        mock_service.get_speed_history.return_value = mock_records
        
        collector = MetricsCollector(mock_service)
        data = collector.get_speed_data_for_chart(
            video_id=1,
            stage="download",
            seconds=60
        )
        
        assert 'timestamps' in data
        assert 'speeds' in data
        assert 'avg_speed' in data
        assert 'peak_speed' in data
        assert 'current_speed' in data
        
        assert len(data['timestamps']) == 3
        assert len(data['speeds']) == 3
        assert data['speeds'] == [100000.0, 150000.0, 200000.0]
        assert data['avg_speed'] == 150000.0
        assert data['peak_speed'] == 200000.0
        assert data['current_speed'] == 200000.0
    
    def test_get_speed_data_for_chart_aggregate(self, mock_service):
        """Test chart data for aggregate view."""
        now = dt_now()
        
        def mock_aggregate(stage, minutes):
            # Each stage returns data for the same timestamps
            return [
                {'avg_speed': 100000, 'timestamp': now - timedelta(seconds=30)},
                {'avg_speed': 150000, 'timestamp': now - timedelta(seconds=15)},
            ]
        
        mock_service.get_aggregate_speeds.side_effect = mock_aggregate
        
        collector = MetricsCollector(mock_service)
        data = collector.get_speed_data_for_chart(seconds=60)
        
        # All 3 stages contribute to each timestamp: 100000 + 100000 + 100000 = 300000
        # and 150000 + 150000 + 150000 = 450000, avg = 375000
        assert len(data['timestamps']) == 2
        assert len(data['speeds']) == 2
        assert data['avg_speed'] == 375000.0
    
    def test_get_speed_data_for_chart_empty(self, mock_service):
        """Test chart data when no data exists."""
        mock_service.get_speed_history.return_value = []
        
        collector = MetricsCollector(mock_service)
        data = collector.get_speed_data_for_chart(
            video_id=1,
            stage="download",
            seconds=60
        )
        
        assert data['timestamps'] == []
        assert data['speeds'] == []
        assert data['avg_speed'] == 0.0
        assert data['peak_speed'] == 0.0
        assert data['current_speed'] == 0.0
    
    def test_get_speed_data_for_chart_with_bucketing(self, mock_service):
        """Test chart data with time bucketing."""
        now = dt_now()
        mock_records = [
            MagicMock(timestamp=now - timedelta(seconds=10), speed=100000),
            MagicMock(timestamp=now - timedelta(seconds=8), speed=120000),
            MagicMock(timestamp=now - timedelta(seconds=6), speed=140000),
            MagicMock(timestamp=now - timedelta(seconds=4), speed=160000),
            MagicMock(timestamp=now - timedelta(seconds=2), speed=1800000),
        ]
        mock_service.get_speed_history.return_value = mock_records
        
        collector = MetricsCollector(mock_service)
        data = collector.get_speed_data_for_chart(
            video_id=1,
            stage="download",
            seconds=60,
            bucket_size=5
        )
        
        # With 5-second buckets, 5 samples should be bucketed
        assert 'speeds' in data


class TestMetricsCollectorBucketing:
    """Test data bucketing functionality."""
    
    def test_bucket_data(self, mock_service):
        """Test data bucketing by time."""
        now = dt_now()
        data = [
            (now.replace(second=1), 100000),
            (now.replace(second=2), 110000),
            (now.replace(second=3), 120000),
            (now.replace(second=6), 200000),
            (now.replace(second=7), 210000),
        ]
        
        collector = MetricsCollector(mock_service)
        bucketed = collector._bucket_data(data, bucket_size=5)
        
        assert len(bucketed) == 2
        # First bucket: avg(100000, 110000, 120000) = 110000
        assert bucketed[0][1] == 110000.0
        # Second bucket: avg(200000, 210000) = 205000
        assert bucketed[1][1] == 205000.0
    
    def test_bucket_data_empty(self, mock_service):
        """Test bucketing empty data."""
        collector = MetricsCollector(mock_service)
        bucketed = collector._bucket_data([], bucket_size=5)
        
        assert bucketed == []
    
    def test_bucket_data_single_item(self, mock_service):
        """Test bucketing single item."""
        now = dt_now()
        data = [(now, 100000)]
        
        collector = MetricsCollector(mock_service)
        bucketed = collector._bucket_data(data, bucket_size=5)
        
        assert len(bucketed) == 1
        assert bucketed[0][1] == 100000.0
    
    def test_bucket_data_bucket_size_one(self, mock_service):
        """Test bucketing with bucket_size=1 (no aggregation)."""
        now = dt_now()
        data = [
            (now, 100000),
            (now + timedelta(seconds=1), 110000),
        ]
        
        collector = MetricsCollector(mock_service)
        bucketed = collector._bucket_data(data, bucket_size=1)
        
        assert len(bucketed) == 2


class TestMetricsCollectorAggregateHistory:
    """Test aggregate history retrieval."""
    
    def test_get_aggregate_history_single_stage(self, mock_service):
        """Test aggregate history for single stage."""
        now = dt_now()
        mock_service.get_aggregate_speeds.return_value = [
            {'avg_speed': 100000, 'timestamp': now - timedelta(seconds=30)},
            {'avg_speed': 150000, 'timestamp': now - timedelta(seconds=15)},
        ]
        
        collector = MetricsCollector(mock_service)
        history = collector._get_aggregate_history(stage="download", seconds=60)
        
        assert len(history) == 2
        assert history[0][1] == 100000
        assert history[1][1] == 150000
    
    def test_get_aggregate_history_all_stages(self, mock_service):
        """Test aggregate history for all stages."""
        now = dt_now()
        
        def mock_aggregate(stage, minutes):
            ts = now - timedelta(seconds=30)
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            return [{'avg_speed': 100000, 'timestamp': ts}]
        
        mock_service.get_aggregate_speeds.side_effect = mock_aggregate
        
        collector = MetricsCollector(mock_service)
        history = collector._get_aggregate_history(stage=None, seconds=60)
        
        # All 3 stages, same timestamp -> sum = 300000
        assert len(history) == 1
        assert history[0][1] == 300000
    
    def test_get_aggregate_history_with_string_timestamp(self, mock_service):
        """Test aggregate history with string timestamps."""
        now = dt_now()
        mock_service.get_aggregate_speeds.return_value = [
            {'avg_speed': 100000, 'timestamp': now.isoformat()},
        ]
        
        collector = MetricsCollector(mock_service)
        history = collector._get_aggregate_history(stage="download", seconds=60)
        
        assert len(history) == 1


class TestMetricsCollectorCleanup:
    """Test cleanup operations."""
    
    def test_cleanup_old_data(self, mock_service):
        """Test cleaning up old data."""
        mock_service._repo.cleanup_old_samples.return_value = 42
        
        collector = MetricsCollector(mock_service)
        deleted = collector.cleanup_old_data(hours=24)
        
        assert deleted == 42
        mock_service._repo.cleanup_old_samples.assert_called_once_with(hours=24)
    
    def test_cleanup_old_data_custom_hours(self, mock_service):
        """Test cleanup with custom hours."""
        mock_service._repo.cleanup_old_samples.return_value = 10
        
        collector = MetricsCollector(mock_service)
        deleted = collector.cleanup_old_data(hours=48)
        
        assert deleted == 10
        mock_service._repo.cleanup_old_samples.assert_called_once_with(hours=48)


class TestMetricsCollectorIntegration:
    """Integration tests with real SpeedHistoryService."""
    
    @pytest.mark.asyncio
    async def test_full_workflow(self, db_session: Session, sample_video: Video):
        """Test full workflow with real service."""
        # Create real service
        service = SpeedHistoryService(db_session)
        await service.start()
        
        try:
            # Create collector
            collector = MetricsCollector(service)
            
            # Record some speeds
            collector.record_speed(sample_video.id, "download", 100000.0, 25.0)
            collector.record_speed(sample_video.id, "download", 150000.0, 50.0)
            collector.record_speed(sample_video.id, "download", 200000.0, 75.0)
            
            # Force flush to database
            service._flush_all_buffers()
            
            # Query history
            history = collector.get_speed_history(sample_video.id, "download", seconds=60)
            assert len(history) == 3
            
            # Check current speed
            current = collector.get_current_speed(sample_video.id, "download")
            assert current == 200000.0
            
            # Get chart data (disable bucketing for exact count)
            chart_data = collector.get_speed_data_for_chart(
                video_id=sample_video.id,
                stage="download",
                seconds=60,
                bucket_size=1
            )
            assert len(chart_data['speeds']) == 3
            assert chart_data['peak_speed'] == 200000.0
            
        finally:
            await service.stop()
    
    @pytest.mark.asyncio
    async def test_aggregate_with_real_service(self, db_session: Session):
        """Test aggregate queries with real service."""
        # Create videos
        videos = []
        for i in range(3):
            video = Video(
                source_path=f"/test/video{i}.mp4",
                title=f"Video {i}",
            )
            db_session.add(video)
            videos.append(video)
        db_session.commit()
        for v in videos:
            db_session.refresh(v)
        
        service = SpeedHistoryService(db_session)
        await service.start()
        
        try:
            collector = MetricsCollector(service)
            
            # Record speeds for different videos and stages
            for i, video in enumerate(videos):
                collector.record_speed(video.id, "download", 100000.0 * (i + 1), 50.0)
                collector.record_speed(video.id, "upload", 50000.0 * (i + 1), 50.0)
            
            # Force flush
            service._flush_all_buffers()
            
            # Get aggregates
            aggregates = collector.get_aggregate_speeds(seconds=60)
            assert 'download' in aggregates
            assert 'upload' in aggregates
            assert aggregates['total'] > 0
            
        finally:
            await service.stop()


class TestMetricsCollectorEdgeCases:
    """Test edge cases."""
    
    def test_valid_stages_constant(self):
        """Test VALID_STAGES constant."""
        assert 'download' in VALID_STAGES
        assert 'encrypt' in VALID_STAGES
        assert 'upload' in VALID_STAGES
        assert 'invalid' not in VALID_STAGES
    
    def test_negative_speed_conversion(self, mock_service):
        """Test handling of negative speed values."""
        collector = MetricsCollector(mock_service)
        
        collector.record_speed(1, "download", -1000.0, 50.0)
        
        # Should convert to int (negative gets clamped by service)
        args = mock_service.record_sample.call_args
        assert args[1]['speed'] == -1000
    
    def test_float_speed_conversion(self, mock_service):
        """Test float to int speed conversion."""
        collector = MetricsCollector(mock_service)
        
        collector.record_speed(1, "download", 1024000.5, 50.0)
        
        args = mock_service.record_sample.call_args
        assert args[1]['speed'] == 1024000
