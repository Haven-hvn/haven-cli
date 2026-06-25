"""Integration tests for StateManager with mock events.

Tests cover:
- Full pipeline lifecycle through events
- Multiple videos being processed concurrently
- Event sequences and state transitions
- Performance with 100+ videos
"""

import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure we can import from the project
import sys
sys.path.insert(0, '/home/tower/Documents/workspace/haven-cli')

from haven_tui.core.state_manager import VideoState, StateManager
from haven_cli.pipeline.events import Event, EventType, EventBus


def dt_now():
    """Get current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


@pytest.fixture
def event_bus():
    """Create a real event bus for testing."""
    return EventBus()


@pytest.fixture
def mock_pipeline_with_event_bus(event_bus):
    """Create a mock pipeline that uses a real event bus."""
    pipeline = MagicMock()
    pipeline.get_active_videos = MagicMock(return_value=[])
    
    # Track subscriptions
    subscriptions = []
    
    def on_event(event_type, handler):
        unsub = event_bus.subscribe(event_type, handler)
        subscriptions.append(unsub)
        return unsub
    
    pipeline.on_event = MagicMock(side_effect=on_event)
    pipeline._event_bus = event_bus
    pipeline._subscriptions = subscriptions
    
    return pipeline


class TestStateManagerLifecycle:
    """Test full lifecycle through state manager."""
    
    @pytest.mark.asyncio
    async def test_full_pipeline_lifecycle(self, mock_pipeline_with_event_bus, event_bus):
        """Test a complete video pipeline from start to finish."""
        pipeline = mock_pipeline_with_event_bus
        
        manager = StateManager(pipeline)
        await manager.initialize()
        
        changes = []
        manager.on_change(lambda vid, field, val: changes.append((vid, field, val)))
        
        # Video ingested
        await event_bus.publish(Event(
            event_type=EventType.VIDEO_INGESTED,
            payload={'video_id': 1},
        ))
        await asyncio.sleep(0.01)  # Allow event to process
        
        # Pipeline started
        await event_bus.publish(Event(
            event_type=EventType.PIPELINE_STARTED,
            payload={'video_id': 1},
        ))
        await asyncio.sleep(0.01)
        
        # Download progress
        for i in range(0, 101, 25):
            await event_bus.publish(Event(
                event_type=EventType.DOWNLOAD_PROGRESS,
                payload={
                    'video_id': 1,
                    'progress_percent': float(i),
                    'download_rate': 1024.0,
                },
            ))
            await asyncio.sleep(0.001)
        
        # Download complete
        await event_bus.publish(Event(
            event_type=EventType.STEP_COMPLETE,
            payload={'video_id': 1, 'stage': 'download'},
        ))
        await asyncio.sleep(0.01)
        
        # Encryption complete
        await event_bus.publish(Event(
            event_type=EventType.ENCRYPT_COMPLETE,
            payload={'video_id': 1},
        ))
        await asyncio.sleep(0.01)
        
        # Upload progress
        await event_bus.publish(Event(
            event_type=EventType.UPLOAD_PROGRESS,
            payload={
                'video_id': 1,
                'progress_percent': 50.0,
                'download_rate': 512.0,
            },
        ))
        await asyncio.sleep(0.01)
        
        # Upload complete
        await event_bus.publish(Event(
            event_type=EventType.UPLOAD_COMPLETE,
            payload={'video_id': 1},
        ))
        await asyncio.sleep(0.01)
        
        # Sync complete
        await event_bus.publish(Event(
            event_type=EventType.SYNC_COMPLETE,
            payload={'video_id': 1},
        ))
        await asyncio.sleep(0.01)
        
        # Analysis complete
        await event_bus.publish(Event(
            event_type=EventType.ANALYSIS_COMPLETE,
            payload={'video_id': 1},
        ))
        await asyncio.sleep(0.01)
        
        # Check final state
        state = manager.get_video(1)
        assert state is not None
        assert state.download_status == "completed"
        assert state.download_progress == 100.0
        assert state.encrypt_status == "completed"
        assert state.upload_status == "completed"
        assert state.sync_status == "completed"
        assert state.analysis_status == "completed"
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_pipeline_failure_recovery(self, mock_pipeline_with_event_bus, event_bus):
        """Test pipeline failure and retry."""
        pipeline = mock_pipeline_with_event_bus
        
        manager = StateManager(pipeline)
        await manager.initialize()
        
        # Setup video
        manager._state[1] = VideoState(
            id=1,
            title="Test Video",
            download_status="active",
            overall_status="active",
        )
        
        # Simulate failure
        await event_bus.publish(Event(
            event_type=EventType.PIPELINE_FAILED,
            payload={
                'video_id': 1,
                'stage': 'upload',
                'error': 'Network error',
            },
        ))
        await asyncio.sleep(0.01)
        
        state = manager.get_video(1)
        assert state.overall_status == "failed"
        assert state.upload_status == "failed"
        
        # Retry
        await event_bus.publish(Event(
            event_type=EventType.PIPELINE_STARTED,
            payload={'video_id': 1},
        ))
        await asyncio.sleep(0.01)
        
        state = manager.get_video(1)
        assert state.overall_status == "active"
        
        # Complete successfully
        await event_bus.publish(Event(
            event_type=EventType.UPLOAD_COMPLETE,
            payload={'video_id': 1},
        ))
        await asyncio.sleep(0.01)
        
        state = manager.get_video(1)
        assert state.upload_status == "completed"
        
        await manager.shutdown()


class TestStateManagerConcurrentVideos:
    """Test StateManager with multiple concurrent videos."""
    
    @pytest.mark.asyncio
    async def test_multiple_videos_in_pipeline(self, mock_pipeline_with_event_bus, event_bus):
        """Test managing state for multiple videos simultaneously."""
        pipeline = mock_pipeline_with_event_bus
        
        manager = StateManager(pipeline)
        await manager.initialize()
        
        # Add multiple videos
        for i in range(1, 6):
            manager._state[i] = VideoState(
                id=i,
                title=f"Video {i}",
                download_status="active",
                overall_status="active",
            )
        
        # Send progress events for all videos
        for i in range(1, 6):
            await event_bus.publish(Event(
                event_type=EventType.DOWNLOAD_PROGRESS,
                payload={
                    'video_id': i,
                    'progress_percent': float(i * 20),
                    'download_rate': float(i * 1000),
                },
            ))
        
        await asyncio.sleep(0.05)
        
        # Verify all videos updated
        for i in range(1, 6):
            state = manager.get_video(i)
            assert state.download_progress == float(i * 20)
            assert state.download_speed == float(i * 1000)
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_mixed_video_states(self, mock_pipeline_with_event_bus, event_bus):
        """Test videos at different pipeline stages simultaneously."""
        pipeline = mock_pipeline_with_event_bus
        
        manager = StateManager(pipeline)
        await manager.initialize()
        
        # Video 1: Downloading
        manager._state[1] = VideoState(
            id=1,
            title="Video 1",
            current_stage="download",
            download_status="active",
        )
        
        # Video 2: Encrypting
        manager._state[2] = VideoState(
            id=2,
            title="Video 2",
            current_stage="encrypt",
            download_status="completed",
            encrypt_status="active",
        )
        
        # Video 3: Uploading
        manager._state[3] = VideoState(
            id=3,
            title="Video 3",
            current_stage="upload",
            download_status="completed",
            encrypt_status="completed",
            upload_status="active",
        )
        
        # Video 4: Failed
        manager._state[4] = VideoState(
            id=4,
            title="Video 4",
            overall_status="failed",
            download_status="failed",
        )
        
        # Video 5: Completed
        manager._state[5] = VideoState(
            id=5,
            title="Video 5",
            overall_status="completed",
            download_status="completed",
            encrypt_status="completed",
            upload_status="completed",
            sync_status="completed",
            analysis_status="completed",
        )
        
        # Test filtering
        active = manager.get_active()
        assert len(active) == 3  # Videos 1, 2, 3
        
        failed = manager.get_by_status("failed")
        assert len(failed) == 1
        assert failed[0].id == 4
        
        completed = [v for v in manager.get_all_videos() if v.is_completed]
        assert len(completed) == 1
        assert completed[0].id == 5
        
        upload_stage = manager.get_by_stage("upload")
        assert len(upload_stage) == 1
        assert upload_stage[0].id == 3
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_concurrent_event_processing(self, mock_pipeline_with_event_bus, event_bus):
        """Test processing many events concurrently."""
        pipeline = mock_pipeline_with_event_bus
        
        manager = StateManager(pipeline)
        await manager.initialize()
        
        # Add 10 videos
        for i in range(1, 11):
            manager._state[i] = VideoState(
                id=i,
                title=f"Video {i}",
                download_status="active",
            )
        
        # Generate many events
        async def generate_events(video_id):
            for progress in range(0, 101, 10):
                await event_bus.publish(Event(
                    event_type=EventType.DOWNLOAD_PROGRESS,
                    payload={
                        'video_id': video_id,
                        'progress_percent': float(progress),
                        'download_rate': 1024.0,
                    },
                ))
        
        # Process events for all videos concurrently
        await asyncio.gather(*[generate_events(i) for i in range(1, 11)])
        await asyncio.sleep(0.1)
        
        # Verify all videos have progress
        for i in range(1, 11):
            state = manager.get_video(i)
            assert state.download_progress > 0
            assert len(state.speed_history) > 0
        
        await manager.shutdown()


class TestStateManagerPerformance:
    """Performance tests for StateManager."""
    
    @pytest.mark.asyncio
    async def test_performance_with_100_videos(self, mock_pipeline_with_event_bus, event_bus):
        """Test performance with 100 videos."""
        pipeline = mock_pipeline_with_event_bus
        
        manager = StateManager(pipeline)
        await manager.initialize()
        
        # Add 100 videos
        for i in range(1, 101):
            manager._state[i] = VideoState(
                id=i,
                title=f"Video {i}",
                download_status="active",
            )
        
        # Measure time to process events
        start = asyncio.get_event_loop().time()
        
        # Send progress for all videos
        for i in range(1, 101):
            await event_bus.publish(Event(
                event_type=EventType.DOWNLOAD_PROGRESS,
                payload={
                    'video_id': i,
                    'progress_percent': 50.0,
                    'download_rate': 1024.0,
                },
            ))
        
        await asyncio.sleep(0.1)
        
        elapsed = asyncio.get_event_loop().time() - start
        
        # Should process 100 events in less than 1 second
        assert elapsed < 1.0
        
        # Verify all videos updated
        all_videos = manager.get_all_videos()
        assert len(all_videos) == 100
        
        for v in all_videos:
            assert v.download_progress == 50.0
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_performance_with_rapid_updates(self, mock_pipeline_with_event_bus, event_bus):
        """Test performance with rapid state updates."""
        pipeline = mock_pipeline_with_event_bus
        
        manager = StateManager(pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test")
        
        # Send 1000 rapid updates
        start = asyncio.get_event_loop().time()
        
        for i in range(1000):
            await event_bus.publish(Event(
                event_type=EventType.DOWNLOAD_PROGRESS,
                payload={
                    'video_id': 1,
                    'progress_percent': float(i % 101),
                    'download_rate': float(i),
                },
            ))
        
        await asyncio.sleep(0.2)
        
        elapsed = asyncio.get_event_loop().time() - start
        
        # Should process 1000 events in less than 2 seconds
        assert elapsed < 2.0
        
        state = manager.get_video(1)
        assert len(state.speed_history) == 300  # Max capacity
        
        await manager.shutdown()


class TestStateManagerCallbacks:
    """Test change notification callbacks."""
    
    @pytest.mark.asyncio
    async def test_callback_fires_for_all_relevant_fields(self, mock_pipeline_with_event_bus, event_bus):
        """Test callbacks fire for all field changes."""
        pipeline = mock_pipeline_with_event_bus
        
        manager = StateManager(pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test")
        
        changes = []
        manager.on_change(lambda vid, field, val: changes.append((vid, field, val)))
        
        # Download progress
        await event_bus.publish(Event(
            event_type=EventType.DOWNLOAD_PROGRESS,
            payload={'video_id': 1, 'progress_percent': 50.0, 'download_rate': 1024.0},
        ))
        await asyncio.sleep(0.01)
        
        # Encryption complete
        await event_bus.publish(Event(
            event_type=EventType.ENCRYPT_COMPLETE,
            payload={'video_id': 1},
        ))
        await asyncio.sleep(0.01)
        
        # Upload progress — speed is derived from successive bytes_uploaded
        # deltas (upload_step.py does not emit an explicit speed field), so
        # publish two events and rewind the tracker baseline to deterministic
        # produce a non-zero ``upload_speed`` change notification.
        await event_bus.publish(Event(
            event_type=EventType.UPLOAD_PROGRESS,
            payload={
                'video_id': 1,
                'stage': 'uploading',
                'progress_percent': 50.0,
                'bytes_uploaded': 0,
                'total_bytes': 1024,
            },
        ))
        await asyncio.sleep(0.01)
        import time as _time
        prev_bytes, _prev_ts = manager._upload_byte_tracker[1]
        manager._upload_byte_tracker[1] = (prev_bytes, _time.monotonic() - 1.0)
        await event_bus.publish(Event(
            event_type=EventType.UPLOAD_PROGRESS,
            payload={
                'video_id': 1,
                'stage': 'uploading',
                'progress_percent': 75.0,
                'bytes_uploaded': 512,
                'total_bytes': 1024,
            },
        ))
        await asyncio.sleep(0.01)
        
        # Check callbacks were fired
        fields_changed = [c[1] for c in changes]
        assert 'download_progress' in fields_changed
        assert 'download_speed' in fields_changed
        assert 'encrypt_status' in fields_changed
        assert 'encrypt_progress' in fields_changed
        assert 'upload_progress' in fields_changed
        assert 'upload_speed' in fields_changed
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_multiple_callbacks(self, mock_pipeline_with_event_bus, event_bus):
        """Test multiple callbacks all receive notifications."""
        pipeline = mock_pipeline_with_event_bus
        
        manager = StateManager(pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test")
        
        callback1 = MagicMock()
        callback2 = MagicMock()
        
        manager.on_change(callback1)
        manager.on_change(callback2)
        
        await event_bus.publish(Event(
            event_type=EventType.DOWNLOAD_PROGRESS,
            payload={'video_id': 1, 'progress_percent': 50.0},
        ))
        await asyncio.sleep(0.01)
        
        callback1.assert_called()
        callback2.assert_called()
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_callback_isolation(self, mock_pipeline_with_event_bus, event_bus):
        """Test that one failing callback doesn't affect others."""
        pipeline = mock_pipeline_with_event_bus
        
        manager = StateManager(pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test")
        
        bad_callback = MagicMock(side_effect=Exception("Test error"))
        good_callback = MagicMock()
        
        manager.on_change(bad_callback)
        manager.on_change(good_callback)
        
        # Should not raise despite bad callback
        await event_bus.publish(Event(
            event_type=EventType.DOWNLOAD_PROGRESS,
            payload={'video_id': 1, 'progress_percent': 50.0},
        ))
        await asyncio.sleep(0.01)
        
        good_callback.assert_called()
        
        await manager.shutdown()


class TestStateManagerEdgeCases:
    """Test edge cases and error conditions."""
    
    @pytest.mark.asyncio
    async def test_event_with_invalid_video_id(self, mock_pipeline_with_event_bus, event_bus):
        """Test handling events with invalid video IDs."""
        pipeline = mock_pipeline_with_event_bus
        
        # Setup get_video_detail to return None for unknown videos
        pipeline.get_video_detail = MagicMock(return_value=None)
        
        manager = StateManager(pipeline)
        await manager.initialize()
        
        # Event for non-existent video
        await event_bus.publish(Event(
            event_type=EventType.DOWNLOAD_PROGRESS,
            payload={'video_id': 99999, 'progress_percent': 50.0},
        ))
        await asyncio.sleep(0.01)
        
        # Should not crash and should not add video
        assert manager.get_video(99999) is None
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_event_without_required_fields(self, mock_pipeline_with_event_bus, event_bus):
        """Test handling events with missing required fields."""
        pipeline = mock_pipeline_with_event_bus
        
        manager = StateManager(pipeline)
        await manager.initialize()
        
        manager._state[1] = VideoState(id=1, title="Test")
        
        # Event with missing video_id
        await event_bus.publish(Event(
            event_type=EventType.DOWNLOAD_PROGRESS,
            payload={'progress_percent': 50.0},  # No video_id
        ))
        await asyncio.sleep(0.01)
        
        # State should be unchanged
        state = manager.get_video(1)
        assert state.download_progress == 0.0
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_shutdown_without_initialize(self, mock_pipeline_with_event_bus):
        """Test shutdown without initialization."""
        pipeline = mock_pipeline_with_event_bus
        
        manager = StateManager(pipeline)
        
        # Should not raise
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_initialize_with_database_error(self, mock_pipeline_with_event_bus):
        """Test initialization when database query fails."""
        pipeline = mock_pipeline_with_event_bus
        pipeline.get_active_videos = MagicMock(side_effect=Exception("DB Error"))
        
        manager = StateManager(pipeline)
        
        # Should not raise, just log error
        await manager.initialize()
        
        # State should be empty but manager functional
        assert manager.get_all_videos() == []
        
        await manager.shutdown()
    
    @pytest.mark.asyncio
    async def test_load_video_with_partial_data(self, mock_pipeline_with_event_bus):
        """Test loading video with incomplete pipeline data."""
        pipeline = mock_pipeline_with_event_bus
        
        # Create video with partial data
        mock_video = MagicMock()
        mock_video.id = 1
        mock_video.title = "Test"
        mock_video.created_at = dt_now()
        mock_video.updated_at = dt_now()
        
        # Partial snapshot
        mock_snapshot = MagicMock()
        mock_snapshot.overall_status = "active"
        mock_snapshot.current_stage = "download"
        mock_snapshot.stage_progress_percent = 50.0
        mock_video.pipeline_snapshot = mock_snapshot
        
        # Video flags
        mock_video.encrypted = True
        mock_video.cid = "test-cid"
        mock_video.arkiv_entity_key = None
        mock_video.has_ai_data = False
        
        pipeline.get_video_detail = MagicMock(return_value=mock_video)
        
        manager = StateManager(pipeline)
        await manager.initialize()
        
        state = await manager._load_video(1)
        
        assert state.overall_status == "active"
        assert state.current_stage == "download"
        assert state.download_progress == 50.0
        assert state.encrypt_status == "completed"  # From encrypted flag
        assert state.upload_status == "completed"   # From cid
        assert state.sync_status == "pending"       # No arkiv_entity_key
        assert state.analysis_status == "pending"   # No has_ai_data
        
        await manager.shutdown()
