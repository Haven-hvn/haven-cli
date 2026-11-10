"""Extended tests for Haven TUI State Manager.

This module provides additional tests for StateManager and VideoState classes
to increase test coverage.
"""

import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch
from collections import deque

import sys
sys.path.insert(0, '/home/tower/Documents/workspace/haven-cli')

from haven_cli.pipeline.events import Event, EventType
from haven_tui.core.state_manager import StateManager, VideoState, VALID_STATUSES


# =============================================================================
# VideoState Tests
# =============================================================================

class TestVideoState:
    """Tests for VideoState dataclass."""
    
    def test_basic_creation(self):
        """Test creating VideoState with default values."""
        state = VideoState(
            id=1,
            title="Test Video",
        )
        
        assert state.id == 1
        assert state.title == "Test Video"
        assert state.download_status == "pending"
        assert state.download_progress == 0.0
        assert state.overall_status == "pending"
        assert state.current_stage == "download"
    
    def test_creation_with_custom_values(self):
        """Test creating VideoState with custom values."""
        now = datetime.now(timezone.utc)
        state = VideoState(
            id=1,
            title="Test Video",
            download_status="active",
            download_progress=50.0,
            download_speed=1024000.0,
            download_eta=60,
            encrypt_status="completed",
            encrypt_progress=100.0,
            overall_status="active",
            current_stage="upload",
        )
        
        assert state.download_status == "active"
        assert state.download_progress == 50.0
        assert state.download_speed == 1024000.0
        assert state.download_eta == 60
        assert state.encrypt_status == "completed"
        assert state.encrypt_progress == 100.0
        assert state.overall_status == "active"
        assert state.current_stage == "upload"
    
    def test_invalid_status_raises_error(self):
        """Test that invalid status raises ValueError."""
        with pytest.raises(ValueError):
            VideoState(
                id=1,
                title="Test",
                download_status="invalid_status",
            )
    
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
            encrypt_progress=80.0,
        )
        
        assert state.current_progress == 80.0
    
    def test_current_progress_upload(self):
        """Test current_progress property for upload stage."""
        state = VideoState(
            id=1,
            title="Test",
            current_stage="upload",
            upload_progress=90.0,
        )
        
        assert state.current_progress == 90.0
    
    def test_current_speed_download(self):
        """Test current_speed property for download stage."""
        state = VideoState(
            id=1,
            title="Test",
            current_stage="download",
            download_speed=1024000.0,
        )
        
        assert state.current_speed == 1024000.0
    
    def test_current_speed_upload(self):
        """Test current_speed property for upload stage."""
        state = VideoState(
            id=1,
            title="Test",
            current_stage="upload",
            upload_speed=512000.0,
        )
        
        assert state.current_speed == 512000.0
    
    def test_is_active_true(self):
        """Test is_active property when video is active."""
        state = VideoState(
            id=1,
            title="Test",
            download_status="active",
        )
        
        assert state.is_active is True
    
    def test_is_active_false(self):
        """Test is_active property when video is not active."""
        state = VideoState(
            id=1,
            title="Test",
            download_status="pending",
            encrypt_status="pending",
            upload_status="pending",
            sync_status="pending",
            analysis_status="pending",
            overall_status="pending",
        )
        
        assert state.is_active is False
    
    def test_has_failed_true(self):
        """Test has_failed property when video has failed."""
        state = VideoState(
            id=1,
            title="Test",
            download_status="failed",
        )
        
        assert state.has_failed is True
    
    def test_has_failed_false(self):
        """Test has_failed property when video has not failed."""
        state = VideoState(
            id=1,
            title="Test",
            download_status="active",
            encrypt_status="completed",
            upload_status="pending",
        )
        
        assert state.has_failed is False
    
    def test_is_completed_true(self):
        """Test is_completed property when all stages completed."""
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
        """Test is_completed property when not all stages completed."""
        state = VideoState(
            id=1,
            title="Test",
            download_status="completed",
            encrypt_status="completed",
            upload_status="active",
        )
        
        assert state.is_completed is False
    
    def test_update_timestamp(self):
        """Test update_timestamp method."""
        state = VideoState(
            id=1,
            title="Test",
        )
        old_timestamp = state.updated_at
        
        # Small delay to ensure different timestamp
        import time
        time.sleep(0.01)
        
        state.update_timestamp()
        
        assert state.updated_at > old_timestamp
    
    def test_add_speed_sample(self):
        """Test add_speed_sample method."""
        state = VideoState(
            id=1,
            title="Test",
        )
        
        state.add_speed_sample(1000.0, 50.0)
        
        assert len(state.speed_history) == 1
        assert state.speed_history[0]["speed"] == 1000.0
        assert state.speed_history[0]["progress"] == 50.0
        assert "timestamp" in state.speed_history[0]
    
    def test_speed_history_maxlen(self):
        """Test that speed_history respects maxlen."""
        state = VideoState(
            id=1,
            title="Test",
            speed_history=deque(maxlen=5),
        )
        
        # Add more samples than maxlen
        for i in range(10):
            state.add_speed_sample(float(i * 100), float(i * 10))
        
        assert len(state.speed_history) == 5
        # Should keep most recent samples
        assert state.speed_history[0]["speed"] == 500.0
        assert state.speed_history[-1]["speed"] == 900.0
    
    def test_to_dict(self):
        """Test to_dict method."""
        state = VideoState(
            id=1,
            title="Test Video",
            download_status="active",
            download_progress=50.0,
        )
        
        d = state.to_dict()
        
        assert d["id"] == 1
        assert d["title"] == "Test Video"
        assert d["download_status"] == "active"
        assert d["download_progress"] == 50.0
        assert "current_progress" in d
        assert "current_speed" in d
        assert "is_active" in d
        assert "has_failed" in d
        assert "is_completed" in d
        assert "created_at" in d
        assert "updated_at" in d


# =============================================================================
# StateManager Tests
# =============================================================================

class TestStateManagerInitialization:
    """Tests for StateManager initialization."""
    
    def test_initialization(self):
        """Test StateManager initialization."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        
        assert manager._pipeline == mock_pipeline
        assert manager._state == {}
        assert manager._initialized is False
    
    @pytest.mark.asyncio
    async def test_initialize(self):
        """Test StateManager initialize."""
        mock_pipeline = MagicMock()
        mock_pipeline.get_active_videos = AsyncMock(return_value=[])
        mock_pipeline.on_event = MagicMock(return_value=MagicMock())
        
        manager = StateManager(pipeline=mock_pipeline)
        await manager.initialize()
        
        assert manager._initialized is True
        mock_pipeline.get_active_videos.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_initialize_already_initialized(self):
        """Test initialize when already initialized."""
        mock_pipeline = MagicMock()
        mock_pipeline.get_active_videos = AsyncMock(return_value=[])
        mock_pipeline.on_event = MagicMock(return_value=MagicMock())
        
        manager = StateManager(pipeline=mock_pipeline)
        await manager.initialize()
        
        # Second initialize should be a no-op
        await manager.initialize()
        
        # get_active_videos should only be called once
        mock_pipeline.get_active_videos.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_initialize_with_active_videos(self):
        """Test initialize with active videos."""
        mock_video = MagicMock()
        mock_video.id = 1
        mock_video.title = "Test Video"
        mock_video.created_at = datetime.now(timezone.utc)
        mock_video.updated_at = datetime.now(timezone.utc)
        mock_video.pipeline_snapshot = None
        mock_video.encrypted = False
        mock_video.cid = None
        mock_video.arkiv_entity_key = None
        mock_video.has_ai_data = False
        
        mock_pipeline = MagicMock()
        # Need to use a coroutine that returns the list properly
        async def get_active_videos():
            return [mock_video]
        async def get_video_detail(vid):
            return mock_video
        async def get_pipeline_stats():
            return {}
        
        mock_pipeline.get_active_videos = get_active_videos
        mock_pipeline.get_video_detail = get_video_detail
        mock_pipeline.get_pipeline_stats = get_pipeline_stats
        mock_pipeline.on_event = MagicMock(return_value=MagicMock())
        
        manager = StateManager(pipeline=mock_pipeline)
        await manager.initialize()
        
        assert manager._initialized is True
        # The video should be loaded during initialization
        # Note: The actual loading happens in _load_video which may succeed or fail
    
    @pytest.mark.asyncio
    async def test_shutdown(self):
        """Test StateManager shutdown."""
        mock_pipeline = MagicMock()
        mock_pipeline.get_active_videos = AsyncMock(return_value=[])
        mock_pipeline.on_event = MagicMock(return_value=MagicMock())
        
        manager = StateManager(pipeline=mock_pipeline)
        await manager.initialize()
        
        # Add some state
        manager._state[1] = VideoState(id=1, title="Test")
        
        await manager.shutdown()
        
        assert manager._initialized is False
        assert manager._state == {}
    
    @pytest.mark.asyncio
    async def test_shutdown_not_initialized(self):
        """Test shutdown when not initialized."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        
        # Should not raise
        await manager.shutdown()


class TestStateManagerCallbacks:
    """Tests for StateManager callback system."""
    
    def test_on_change(self):
        """Test registering change callback."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        
        callback = MagicMock()
        manager.on_change(callback)
        
        assert callback in manager._change_callbacks
    
    def test_off_change(self):
        """Test unregistering change callback."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        
        callback = MagicMock()
        manager.on_change(callback)
        result = manager.off_change(callback)
        
        assert result is True
        assert callback not in manager._change_callbacks
    
    def test_off_change_not_found(self):
        """Test unregistering callback that doesn't exist."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        
        callback = MagicMock()
        result = manager.off_change(callback)
        
        assert result is False
    
    def test_notify_change(self):
        """Test notifying change callbacks."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        
        callback = MagicMock()
        manager.on_change(callback)
        
        manager._notify_change(1, "test_field", "test_value")
        
        callback.assert_called_once_with(1, "test_field", "test_value")
    
    @pytest.mark.asyncio
    async def test_notify_change_async(self):
        """Test notifying async change callbacks."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        
        async_callback = AsyncMock()
        manager.on_change(async_callback)
        
        manager._notify_change(1, "test_field", "test_value")
        
        # Give async task time to run
        await asyncio.sleep(0.01)
        
        async_callback.assert_called_once_with(1, "test_field", "test_value")


class TestStateManagerQueries:
    """Tests for StateManager query methods."""
    
    def test_get_video(self):
        """Test getting video by ID."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        
        state = VideoState(id=1, title="Test")
        manager._state[1] = state
        
        result = manager.get_video(1)
        
        assert result == state
    
    def test_get_video_not_found(self):
        """Test getting video that doesn't exist."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        
        result = manager.get_video(999)
        
        assert result is None
    
    def test_get_all_videos(self):
        """Test getting all videos."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        
        manager._state[1] = VideoState(id=1, title="Test 1")
        manager._state[2] = VideoState(id=2, title="Test 2")
        
        videos = manager.get_all_videos()
        
        assert len(videos) == 2
    
    def test_get_active(self):
        """Test getting active videos."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        
        manager._state[1] = VideoState(id=1, title="Active", download_status="active")
        manager._state[2] = VideoState(id=2, title="Pending", download_status="pending")
        
        active = manager.get_active()
        
        assert len(active) == 1
        assert active[0].id == 1
    
    def test_get_by_status(self):
        """Test getting videos by status."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        
        manager._state[1] = VideoState(id=1, title="Active", overall_status="active")
        manager._state[2] = VideoState(id=2, title="Pending", overall_status="pending")
        manager._state[3] = VideoState(id=3, title="Failed", overall_status="failed")
        
        failed = manager.get_by_status("failed")
        
        assert len(failed) == 1
        assert failed[0].id == 3
    
    def test_get_by_stage(self):
        """Test getting videos by stage."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        
        manager._state[1] = VideoState(id=1, title="Download", current_stage="download")
        manager._state[2] = VideoState(id=2, title="Upload", current_stage="upload")
        
        download = manager.get_by_stage("download")
        
        assert len(download) == 1
        assert download[0].id == 1


class TestStateManagerEventHandlers:
    """Tests for StateManager event handlers."""
    
    
    
    
    @pytest.mark.asyncio
    async def test_on_encrypt_complete(self):
        """Test handling encrypt complete event."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        manager._state[1] = VideoState(id=1, title="Test")
        
        event = Event(
            event_type=EventType.ENCRYPT_COMPLETE,
            payload={"video_id": 1},
        )
        
        await manager._on_encrypt_complete(event)
        
        state = manager.get_video(1)
        assert state.encrypt_status == "completed"
        assert state.encrypt_progress == 100.0
    
    @pytest.mark.asyncio
    async def test_on_upload_complete(self):
        """Test handling upload complete event."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        manager._state[1] = VideoState(id=1, title="Test")
        
        event = Event(
            event_type=EventType.UPLOAD_COMPLETE,
            payload={"video_id": 1},
        )
        
        await manager._on_upload_complete(event)
        
        state = manager.get_video(1)
        assert state.upload_status == "completed"
        assert state.upload_progress == 100.0
        assert state.upload_speed == 0.0
    
    @pytest.mark.asyncio
    async def test_on_sync_complete(self):
        """Test handling sync complete event."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        manager._state[1] = VideoState(id=1, title="Test")
        
        event = Event(
            event_type=EventType.SYNC_COMPLETE,
            payload={"video_id": 1},
        )
        
        await manager._on_sync_complete(event)
        
        state = manager.get_video(1)
        assert state.sync_status == "completed"
        assert state.sync_progress == 100.0
    
    @pytest.mark.asyncio
    async def test_on_analysis_complete(self):
        """Test handling analysis complete event."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        manager._state[1] = VideoState(id=1, title="Test")
        
        event = Event(
            event_type=EventType.ANALYSIS_COMPLETE,
            payload={"video_id": 1},
        )
        
        await manager._on_analysis_complete(event)
        
        state = manager.get_video(1)
        assert state.analysis_status == "completed"
        assert state.analysis_progress == 100.0
    
    @pytest.mark.asyncio
    async def test_on_stage_complete_download(self):
        """Test handling stage complete for download."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        manager._state[1] = VideoState(id=1, title="Test")
        
        event = Event(
            event_type=EventType.STEP_COMPLETE,
            payload={"video_id": 1, "stage": "download"},
        )
        
        await manager._on_stage_complete(event)
        
        state = manager.get_video(1)
        assert state.download_status == "completed"
        assert state.download_progress == 100.0
    
    @pytest.mark.asyncio
    async def test_on_stage_complete_encrypt(self):
        """Test handling stage complete for encrypt."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        manager._state[1] = VideoState(id=1, title="Test")
        
        event = Event(
            event_type=EventType.STEP_COMPLETE,
            payload={"video_id": 1, "stage": "encrypt"},
        )
        
        await manager._on_stage_complete(event)
        
        state = manager.get_video(1)
        assert state.encrypt_status == "completed"
        assert state.encrypt_progress == 100.0
    
    @pytest.mark.asyncio
    async def test_on_pipeline_failed(self):
        """Test handling pipeline failed event."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        manager._state[1] = VideoState(id=1, title="Test")
        
        event = Event(
            event_type=EventType.PIPELINE_FAILED,
            payload={"video_id": 1, "stage": "upload"},
        )
        
        await manager._on_pipeline_failed(event)
        
        state = manager.get_video(1)
        assert state.overall_status == "failed"
        assert state.upload_status == "failed"
    
    @pytest.mark.asyncio
    async def test_on_step_failed(self):
        """Test handling step failed event."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        manager._state[1] = VideoState(id=1, title="Test")
        
        event = Event(
            event_type=EventType.STEP_FAILED,
            payload={"video_id": 1, "stage": "encrypt"},
        )
        
        await manager._on_step_failed(event)
        
        state = manager.get_video(1)
        assert state.overall_status == "failed"
        assert state.encrypt_status == "failed"
    
    @pytest.mark.asyncio
    async def test_on_pipeline_started(self):
        """Test handling pipeline started event."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        manager._state[1] = VideoState(id=1, title="Test", overall_status="pending")
        
        event = Event(
            event_type=EventType.PIPELINE_STARTED,
            payload={"video_id": 1},
        )
        
        await manager._on_pipeline_started(event)
        
        state = manager.get_video(1)
        assert state.overall_status == "active"
    
    @pytest.mark.asyncio
    async def test_event_handler_missing_video_id(self):
        """Test event handler with missing video_id."""
        mock_pipeline = MagicMock()
        manager = StateManager(pipeline=mock_pipeline)
        
        event = Event(
            event_type=EventType.DOWNLOAD_PROGRESS,
            payload={"speed": 1000.0},  # Missing video_id
        )
        
        # Should not raise
        await manager._on_download_progress(event)


# =============================================================================
# Valid Statuses Tests
# =============================================================================

class TestValidStatuses:
    """Tests for VALID_STATUSES constant."""
    
    def test_valid_statuses(self):
        """Test that valid statuses are correct."""
        assert "pending" in VALID_STATUSES
        assert "active" in VALID_STATUSES
        assert "paused" in VALID_STATUSES
        assert "completed" in VALID_STATUSES
        assert "failed" in VALID_STATUSES
        assert len(VALID_STATUSES) == 5
