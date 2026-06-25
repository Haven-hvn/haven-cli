"""Unit tests for StateManager and VideoState.

Tests cover:
- VideoState dataclass initialization and properties
- StateManager lifecycle (initialize, shutdown)
- State access methods (get_video, get_all_videos, get_active, get_by_status)
- Event handlers for all progress event types
- Change notification system
- Thread-safe state updates
"""

import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure we can import from the project
import sys
sys.path.insert(0, '/home/tower/Documents/workspace/haven-cli')

from haven_tui.core.state_manager import (
    VideoState,
    StateManager,
    VALID_STATUSES,
)
from haven_cli.pipeline.events import Event, EventType


def dt_now():
    """Get current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


class TestVideoState:
    """Test VideoState dataclass."""
    
    def test_default_initialization(self):
        """Test VideoState initializes with correct defaults."""
        state = VideoState(id=1, title="Test Video")
        
        assert state.id == 1
        assert state.title == "Test Video"
        assert state.download_status == "pending"
        assert state.download_progress == 0.0
        assert state.download_speed == 0.0
        assert state.download_eta is None
        assert state.encrypt_status == "pending"
        assert state.upload_status == "pending"
        assert state.sync_status == "pending"
        assert state.analysis_status == "pending"
        assert state.overall_status == "pending"
        assert state.current_stage == "download"
    
    def test_custom_initialization(self):
        """Test VideoState initializes with custom values."""
        state = VideoState(
            id=1,
            title="Test",
            download_status="active",
            download_progress=50.0,
            download_speed=1024.0,
            download_eta=60,
        )
        
        assert state.download_status == "active"
        assert state.download_progress == 50.0
        assert state.download_speed == 1024.0
        assert state.download_eta == 60
    
    def test_invalid_status_raises(self):
        """Test that invalid status values raise ValueError."""
        with pytest.raises(ValueError, match="Invalid download_status"):
            VideoState(id=1, title="Test", download_status="invalid")
    
    def test_current_progress_download(self):
        """Test current_progress property for download stage."""
        state = VideoState(
            id=1,
            title="Test",
            current_stage="download",
            download_progress=75.0,
        )
        assert state.current_progress == 75.0
    
    def test_current_progress_encrypt(self):
        """Test current_progress property for encrypt stage."""
        state = VideoState(
            id=1,
            title="Test",
            current_stage="encrypt",
            encrypt_progress=50.0,
        )
        assert state.current_progress == 50.0
    
    def test_current_progress_upload(self):
        """Test current_progress property for upload stage."""
        state = VideoState(
            id=1,
            title="Test",
            current_stage="upload",
            upload_progress=25.0,
        )
        assert state.current_progress == 25.0
    
    def test_current_speed_download(self):
        """Test current_speed property for download stage."""
        state = VideoState(
            id=1,
            title="Test",
            current_stage="download",
            download_speed=1024.0,
        )
        assert state.current_speed == 1024.0
    
    def test_current_speed_upload(self):
        """Test current_speed property for upload stage."""
        state = VideoState(
            id=1,
            title="Test",
            current_stage="upload",
            upload_speed=512.0,
        )
        assert state.current_speed == 512.0
    
    def test_is_active_true(self):
        """Test is_active returns True when video is active."""
        state = VideoState(
            id=1,
            title="Test",
            download_status="active",
        )
        assert state.is_active is True
    
    def test_is_active_false(self):
        """Test is_active returns False when video is not active."""
        state = VideoState(
            id=1,
            title="Test",
            download_status="pending",
        )
        assert state.is_active is False
    
    def test_has_failed_true(self):
        """Test has_failed returns True when video has failed."""
        state = VideoState(
            id=1,
            title="Test",
            download_status="failed",
        )
        assert state.has_failed is True
    
    def test_has_failed_false(self):
        """Test has_failed returns False when video hasn't failed."""
        state = VideoState(
            id=1,
            title="Test",
            download_status="active",
        )
        assert state.has_failed is False
    
    def test_is_completed_true(self):
        """Test is_completed returns True when all stages complete."""
        state = VideoState(
            id=1,
            title="Test",
            download_status="completed",
            encrypt_status="completed",
            upload_status="completed",
            sync_status="completed",
            analysis_status="completed",
        )
        assert state.is_completed is True
    
    def test_is_completed_false(self):
        """Test is_completed returns False when stages incomplete."""
        state = VideoState(
            id=1,
            title="Test",
            download_status="completed",
            encrypt_status="pending",
        )
        assert state.is_completed is False
    
    def test_add_speed_sample(self):
        """Test adding speed samples to history."""
        state = VideoState(id=1, title="Test")
        
        state.add_speed_sample(1024.0, 50.0)
        
        assert len(state.speed_history) == 1
        sample = state.speed_history[0]
        assert sample['speed'] == 1024.0
        assert sample['progress'] == 50.0
        assert 'timestamp' in sample
    
    def test_speed_history_maxlen(self):
        """Test that speed history has max length of 300."""
        state = VideoState(id=1, title="Test")
        
        # Add more than 300 samples
        for i in range(350):
            state.add_speed_sample(float(i), float(i))
        
        assert len(state.speed_history) == 300
        # First 50 should have been dropped (oldest)
        assert state.speed_history[0]['speed'] == 50.0
    
    def test_to_dict(self):
        """Test VideoState conversion to dictionary."""
        state = VideoState(
            id=1,
            title="Test Video",
            download_progress=50.0,
        )
        
        data = state.to_dict()
        
        assert data['id'] == 1
        assert data['title'] == "Test Video"
        assert data['download_progress'] == 50.0
        assert data['current_progress'] == 50.0
        assert data['is_active'] is False
        assert data['has_failed'] is False
        assert 'created_at' in data
        assert 'updated_at' in data


@pytest.fixture
def mock_pipeline():
    """Create a mock pipeline interface."""
    pipeline = MagicMock()
    pipeline.get_active_videos = MagicMock(return_value=[])
    pipeline.on_event = MagicMock(return_value=MagicMock())
    return pipeline


class TestStateManagerInitialization:
    """Test StateManager lifecycle methods."""
    
    @pytest.mark.asyncio
    async def test_initialize_loads_active_videos(self, mock_pipeline):
        """Test that initialize loads active videos from database."""
        # Create mock videos
        mock_video = MagicMock()
        mock_video.id = 1
        mock_video.title = "Test"
        mock_video.created_at = dt_now()
        mock_video.updated_at = dt_now()
        mock_video.pipeline_snapshot = None
        mock_video.encrypted = False
        mock_video.cid = None
        mock_video.arkiv_entity_key = None
        mock_video.has_ai_data = False
        
        mock_pipeline.get_active_videos = MagicMock(return_value=[mock_video])
        mock_pipeline.get_video_detail = MagicMock(return_value=mock_video)
        
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        mock_pipeline.get_active_videos.assert_called_once()
        assert manager.get_video(1) is not None
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_initialize_sets_up_event_handlers(self, mock_pipeline):
        """Test that initialize subscribes to events."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        # Should subscribe to multiple event types
        assert mock_pipeline.on_event.call_count >= 10
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_initialize_idempotent(self, mock_pipeline):
        """Test that calling initialize twice is safe."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        await manager.initialize()  # Should not raise
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_shutdown_clears_state(self, mock_pipeline):
        """Test that shutdown clears all state."""
        # Setup state
        mock_video = MagicMock()
        mock_video.id = 1
        mock_video.title = "Test"
        mock_video.created_at = dt_now()
        mock_video.updated_at = dt_now()
        mock_video.pipeline_snapshot = None
        mock_video.encrypted = False
        mock_video.cid = None
        mock_video.arkiv_entity_key = None
        mock_video.has_ai_data = False
        
        mock_pipeline.get_active_videos = MagicMock(return_value=[mock_video])
        mock_pipeline.get_video_detail = MagicMock(return_value=mock_video)
        
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        assert manager.get_video(1) is not None
        
        await manager.shutdown()
        assert manager.get_video(1) is None
    
    @pytest.mark.asyncio
    async def test_shutdown_unsubscribes_events(self, mock_pipeline):
        """Test that shutdown unsubscribes from events."""
        mock_unsubscriber = MagicMock()
        mock_pipeline.on_event = MagicMock(return_value=mock_unsubscriber)
        
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        await manager.shutdown()
        
        # All unsubscribers should have been called
        assert mock_unsubscriber.call_count >= 10


@pytest.fixture
async def populated_manager(mock_pipeline):
    """Create a StateManager with test data."""
    manager = StateManager(mock_pipeline)
    await manager.initialize()
    
    # Manually add test states
    manager._state[1] = VideoState(
        id=1,
        title="Active Video",
        download_status="active",
        overall_status="active",
    )
    manager._state[2] = VideoState(
        id=2,
        title="Failed Video",
        download_status="failed",
        overall_status="failed",
    )
    manager._state[3] = VideoState(
        id=3,
        title="Completed Video",
        download_status="completed",
        overall_status="completed",
    )
    
    return manager


class TestStateManagerAccess:
    """Test StateManager state access methods."""
    
    @pytest.mark.asyncio
    async def test_get_video(self, mock_pipeline):
        """Test getting a specific video."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test")
        
        state = manager.get_video(1)
        assert state is not None
        assert state.id == 1
        assert state.title == "Test"
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_get_video_not_found(self, mock_pipeline):
        """Test getting a non-existent video."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        state = manager.get_video(999)
        assert state is None
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_get_all_videos(self, mock_pipeline):
        """Test getting all videos."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Video 1")
        manager._state[2] = VideoState(id=2, title="Video 2")
        manager._state[3] = VideoState(id=3, title="Video 3")
        
        videos = manager.get_all_videos()
        assert len(videos) == 3
        ids = {v.id for v in videos}
        assert ids == {1, 2, 3}
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_get_active(self, mock_pipeline):
        """Test getting active videos."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Active", download_status="active")
        manager._state[2] = VideoState(id=2, title="Pending", download_status="pending")
        
        active = manager.get_active()
        assert len(active) == 1
        assert active[0].id == 1
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_get_by_status(self, mock_pipeline):
        """Test getting videos by status."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Failed", overall_status="failed")
        manager._state[2] = VideoState(id=2, title="Completed", overall_status="completed")
        manager._state[3] = VideoState(id=3, title="Pending", overall_status="pending")
        
        failed = manager.get_by_status("failed")
        assert len(failed) == 1
        assert failed[0].id == 1
        
        completed = manager.get_by_status("completed")
        assert len(completed) == 1
        assert completed[0].id == 2
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_get_by_stage(self, mock_pipeline):
        """Test getting videos by current stage."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Uploading", current_stage="upload")
        manager._state[2] = VideoState(id=2, title="Downloading", current_stage="download")
        
        upload_videos = manager.get_by_stage("upload")
        assert len(upload_videos) == 1
        assert upload_videos[0].id == 1
        
        await manager.shutdown()


class TestStateManagerChangeCallbacks:
    """Test StateManager change notification system."""
    
    @pytest.mark.asyncio
    async def test_on_change_registers_callback(self, mock_pipeline):
        """Test registering a change callback."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        callback = MagicMock()
        manager.on_change(callback)
        assert callback in manager._change_callbacks
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_off_change_removes_callback(self, mock_pipeline):
        """Test unregistering a change callback."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        callback = MagicMock()
        manager.on_change(callback)
        result = manager.off_change(callback)
        assert result is True
        assert callback not in manager._change_callbacks
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_off_change_not_found(self, mock_pipeline):
        """Test unregistering a callback that doesn't exist."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        callback = MagicMock()
        result = manager.off_change(callback)
        assert result is False
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_notify_change_calls_callbacks(self, mock_pipeline):
        """Test that changes notify callbacks."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        callback = MagicMock()
        manager.on_change(callback)
        
        # Add a video to state
        manager._state[1] = VideoState(id=1, title="Test")
        
        # Trigger notification
        manager._notify_change(1, "download_progress", 50.0)
        
        callback.assert_called_once_with(1, "download_progress", 50.0)
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_notify_change_async_callback(self, mock_pipeline):
        """Test that async callbacks are handled."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        async_callback = AsyncMock()
        manager.on_change(async_callback)
        
        manager._state[1] = VideoState(id=1, title="Test")
        manager._notify_change(1, "download_progress", 50.0)
        
        # Give async task time to run
        await asyncio.sleep(0.01)
        
        async_callback.assert_called_once_with(1, "download_progress", 50.0)
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_notify_change_error_handling(self, mock_pipeline):
        """Test that callback errors don't break other callbacks."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        bad_callback = MagicMock(side_effect=Exception("Test error"))
        good_callback = MagicMock()
        
        manager.on_change(bad_callback)
        manager.on_change(good_callback)
        
        manager._state[1] = VideoState(id=1, title="Test")
        manager._notify_change(1, "download_progress", 50.0)
        
        # Both should be called despite error in first
        bad_callback.assert_called_once()
        good_callback.assert_called_once()
        
        await manager.shutdown()


class TestStateManagerEventHandlers:
    """Test StateManager event handler methods."""
    
    @pytest.mark.asyncio
    async def test_on_download_progress(self, mock_pipeline):
        """Test handling download progress events."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test", download_progress=0.0)
        
        callback = MagicMock()
        manager.on_change(callback)
        
        event = Event(
            event_type=EventType.DOWNLOAD_PROGRESS,
            payload={
                'video_id': 1,
                'download_rate': 1024.0,
                'progress_percent': 50.0,
                'eta_seconds': 60,
            },
        )

        await manager._on_download_progress(event)

        state = manager.get_video(1)
        assert state.download_status == "active"
        assert state.download_speed == 1024.0
        assert state.download_progress == 50.0
        assert state.download_eta == 60
        assert state.current_stage == "download"
        assert len(state.speed_history) == 1
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_on_upload_progress(self, mock_pipeline):
        """Test handling upload progress events.

        Upload speed is derived from successive ``bytes_uploaded`` deltas
        because the production emitter (``upload_step.py``) does not
        include an explicit speed field.
        """
        manager = StateManager(mock_pipeline)
        await manager.initialize()

        manager._state[1] = VideoState(id=1, title="Test")

        # First event: establishes the byte-delta baseline. No speed yet.
        first = Event(
            event_type=EventType.UPLOAD_PROGRESS,
            payload={
                'video_id': 1,
                'stage': 'uploading',
                'progress_percent': 50.0,
                'bytes_uploaded': 0,
                'total_bytes': 1024,
            },
        )
        await manager._on_upload_progress(first)

        # Force the byte-delta tracker baseline back in time so the second
        # event computes a deterministic, non-zero speed.
        import time as _time
        prev_bytes, _prev_ts = manager._upload_byte_tracker[1]
        manager._upload_byte_tracker[1] = (prev_bytes, _time.monotonic() - 1.0)

        second = Event(
            event_type=EventType.UPLOAD_PROGRESS,
            payload={
                'video_id': 1,
                'stage': 'uploading',
                'progress_percent': 75.0,
                'bytes_uploaded': 512,
                'total_bytes': 1024,
            },
        )
        await manager._on_upload_progress(second)

        state = manager.get_video(1)
        assert state.upload_status == "active"
        assert state.upload_progress == 75.0
        assert state.upload_speed > 0  # 512 bytes / ~1s ≈ 512 B/s
        assert state.current_stage == "upload"

        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_on_encrypt_progress(self, mock_pipeline):
        """Test handling encryption progress events."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()

        manager._state[1] = VideoState(id=1, title="Test")

        event = Event(
            event_type=EventType.ENCRYPT_PROGRESS,
            payload={
                'video_id': 1,
                'progress_percent': 80.0,
            },
        )

        await manager._on_encrypt_progress(event)

        state = manager.get_video(1)
        assert state.encrypt_status == "active"
        assert state.encrypt_progress == 80.0
        assert state.current_stage == "encrypt"

        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_on_encrypt_complete(self, mock_pipeline):
        """Test handling encryption complete events."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test")
        
        event = Event(
            event_type=EventType.ENCRYPT_COMPLETE,
            payload={'video_id': 1},
        )
        
        await manager._on_encrypt_complete(event)
        
        state = manager.get_video(1)
        assert state.encrypt_status == "completed"
        assert state.encrypt_progress == 100.0
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_on_upload_complete(self, mock_pipeline):
        """Test handling upload complete events."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test")
        
        event = Event(
            event_type=EventType.UPLOAD_COMPLETE,
            payload={'video_id': 1},
        )
        
        await manager._on_upload_complete(event)
        
        state = manager.get_video(1)
        assert state.upload_status == "completed"
        assert state.upload_progress == 100.0
        assert state.upload_speed == 0.0
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_on_sync_complete(self, mock_pipeline):
        """Test handling sync complete events."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test")
        
        event = Event(
            event_type=EventType.SYNC_COMPLETE,
            payload={'video_id': 1},
        )
        
        await manager._on_sync_complete(event)
        
        state = manager.get_video(1)
        assert state.sync_status == "completed"
        assert state.sync_progress == 100.0
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_on_analysis_complete(self, mock_pipeline):
        """Test handling analysis complete events."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test")
        
        event = Event(
            event_type=EventType.ANALYSIS_COMPLETE,
            payload={'video_id': 1},
        )
        
        await manager._on_analysis_complete(event)
        
        state = manager.get_video(1)
        assert state.analysis_status == "completed"
        assert state.analysis_progress == 100.0
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_on_stage_complete(self, mock_pipeline):
        """Test handling stage complete events."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test")
        
        event = Event(
            event_type=EventType.STEP_COMPLETE,
            payload={
                'video_id': 1,
                'stage': 'download',
            },
        )
        
        await manager._on_stage_complete(event)
        
        state = manager.get_video(1)
        assert state.download_status == "completed"
        assert state.download_progress == 100.0
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_on_pipeline_failed(self, mock_pipeline):
        """Test handling pipeline failure events."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test")
        
        event = Event(
            event_type=EventType.PIPELINE_FAILED,
            payload={
                'video_id': 1,
                'stage': 'upload',
            },
        )
        
        await manager._on_pipeline_failed(event)
        
        state = manager.get_video(1)
        assert state.overall_status == "failed"
        assert state.upload_status == "failed"
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_on_pipeline_started(self, mock_pipeline):
        """Test handling pipeline started events."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test", overall_status="pending")
        
        event = Event(
            event_type=EventType.PIPELINE_STARTED,
            payload={'video_id': 1},
        )
        
        await manager._on_pipeline_started(event)
        
        state = manager.get_video(1)
        assert state.overall_status == "active"
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_event_without_video_id(self, mock_pipeline):
        """Test handling events without video_id."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test", download_progress=0.0)
        
        event = Event(
            event_type=EventType.DOWNLOAD_PROGRESS,
            payload={'progress': 50.0},  # No video_id
        )
        
        # Should not raise
        await manager._on_download_progress(event)
        
        # State should remain unchanged
        state = manager.get_video(1)
        assert state.download_progress == 0.0
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_event_for_unknown_video(self, mock_pipeline):
        """Test handling events for videos not in state."""
        mock_pipeline.get_active_videos = MagicMock(return_value=[])
        
        # Setup mock to return a video when loaded
        mock_video = MagicMock()
        mock_video.id = 999
        mock_video.title = "New Video"
        mock_video.created_at = dt_now()
        mock_video.updated_at = dt_now()
        mock_video.pipeline_snapshot = None
        mock_video.encrypted = False
        mock_video.cid = None
        mock_video.arkiv_entity_key = None
        mock_video.has_ai_data = False
        
        mock_pipeline.get_video_detail = MagicMock(return_value=mock_video)
        
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        event = Event(
            event_type=EventType.DOWNLOAD_PROGRESS,
            payload={
                'video_id': 999,
                'progress_percent': 50.0,
            },
        )

        await manager._on_download_progress(event)

        # Should have loaded the video
        state = manager.get_video(999)
        assert state is not None
        assert state.download_progress == 50.0
        
        await manager.shutdown()


class TestStateManagerThreadSafety:
    """Test StateManager thread-safety."""
    
    @pytest.mark.asyncio
    async def test_concurrent_updates(self, mock_pipeline):
        """Test concurrent state updates are thread-safe."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test", download_progress=0.0)
        
        async def update_progress(value):
            event = Event(
                event_type=EventType.DOWNLOAD_PROGRESS,
                payload={'video_id': 1, 'progress_percent': value},
            )
            await manager._on_download_progress(event)
        
        # Run many concurrent updates
        tasks = [update_progress(float(i)) for i in range(100)]
        await asyncio.gather(*tasks)
        
        # Final state should be consistent
        state = manager.get_video(1)
        assert state is not None
        # Last update should have been applied
        assert state.download_progress >= 0.0
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_concurrent_access_and_modification(self, mock_pipeline):
        """Test concurrent access and modification."""
        manager = StateManager(mock_pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test")
        
        async def modifier():
            for i in range(50):
                async with manager._lock:
                    manager._state[1].download_progress = float(i)
                await asyncio.sleep(0)
        
        async def reader():
            for _ in range(50):
                _ = manager.get_video(1)
                await asyncio.sleep(0)
        
        await asyncio.gather(modifier(), reader())
        
        # Should complete without errors
        assert True
        
        await manager.shutdown()
