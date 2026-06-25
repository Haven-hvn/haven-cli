"""State management for TUI.

This module provides the StateManager class and VideoState dataclass for
thread-safe, real-time state management in the TUI. It maintains an in-memory
cache of pipeline state with change notifications.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


from haven_cli.pipeline.events import (
    Event,
    EventType,
)

# Set up logging
logger = logging.getLogger(__name__)


# Valid status values for each stage
VALID_STATUSES = {"pending", "active", "paused", "completed", "failed"}


@dataclass
class VideoState:
    """Complete state for a single video in the TUI.
    
    This dataclass holds all state information for a video as it progresses
    through the pipeline stages: download, encrypt, upload, sync, and analysis.
    
    Attributes:
        id: Unique video identifier
        title: Video title for display
        file_size: File size in bytes
        plugin: Plugin name that downloaded this video (e.g., "youtube", "bittorrent")
        download_status: Current download state
        download_progress: Download progress (0.0 - 100.0)
        download_speed: Current download speed in bytes/sec
        download_eta: Estimated time to completion in seconds
        encrypt_status: Current encryption state
        encrypt_progress: Encryption progress (0.0 - 100.0)
        upload_status: Current upload state
        upload_progress: Upload progress (0.0 - 100.0)
        upload_speed: Current upload speed in bytes/sec
        sync_status: Current sync state
        sync_progress: Sync progress (0.0 - 100.0)
        analysis_status: Current analysis state
        analysis_progress: Analysis progress (0.0 - 100.0)
        overall_status: Computed overall pipeline state
        current_stage: Name of the currently active stage
        speed_history: Circular buffer of speed measurements for graphing
        created_at: When this state record was created
        updated_at: When this state record was last updated
    """
    
    # Identity
    id: int
    title: str
    file_size: int = 0  # File size in bytes
    plugin: str = "unknown"  # Plugin name (youtube, bittorrent, etc.)
    
    # Download state
    download_status: str = "pending"  # "pending" | "active" | "paused" | "completed" | "failed"
    download_progress: float = 0.0  # 0.0 - 100.0
    download_speed: float = 0.0  # bytes/sec
    download_eta: Optional[int] = None  # seconds
    
    # Pipeline stages
    encrypt_status: str = "pending"
    encrypt_progress: float = 0.0
    
    upload_status: str = "pending"
    upload_progress: float = 0.0
    upload_speed: float = 0.0
    
    sync_status: str = "pending"
    sync_progress: float = 0.0
    
    analysis_status: str = "pending"
    analysis_progress: float = 0.0
    
    # Computed overall state
    overall_status: str = "pending"  # "pending" | "active" | "completed" | "failed"
    current_stage: str = "download"
    
    # Skip tracking
    skip_reason: Optional[str] = None  # Reason for being skipped (e.g., "exceeded configured size limit")
    
    # Speed history for graphing (circular buffer - 300 points = 5 minutes at 1 sample/sec)
    speed_history: deque = field(default_factory=lambda: deque(maxlen=300))
    
    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None  # When processing started (download began)
    
    def __post_init__(self):
        """Validate status values after initialization."""
        self._validate_status("download_status", self.download_status)
        self._validate_status("encrypt_status", self.encrypt_status)
        self._validate_status("upload_status", self.upload_status)
        self._validate_status("sync_status", self.sync_status)
        self._validate_status("analysis_status", self.analysis_status)
        self._validate_status("overall_status", self.overall_status)
    
    def _validate_status(self, field_name: str, value: str) -> None:
        """Validate that a status value is valid."""
        if value not in VALID_STATUSES:
            raise ValueError(
                f"Invalid {field_name}: {value}. Must be one of {VALID_STATUSES}"
            )
    
    @property
    def current_progress(self) -> float:
        """Get progress for the current active stage."""
        stage_progress_map = {
            "download": self.download_progress,
            "encrypt": self.encrypt_progress,
            "upload": self.upload_progress,
            "sync": self.sync_progress,
            "analysis": self.analysis_progress,
        }
        return stage_progress_map.get(self.current_stage, 0.0)
    
    @property
    def current_speed(self) -> float:
        """Get speed for the current active stage."""
        stage_speed_map = {
            "download": self.download_speed,
            "encrypt": 0.0,  # Encryption typically doesn't report speed
            "upload": self.upload_speed,
            "sync": 0.0,  # Sync typically doesn't report speed
            "analysis": 0.0,  # Analysis typically doesn't report speed
        }
        return stage_speed_map.get(self.current_stage, 0.0)
    
    @property
    def is_active(self) -> bool:
        """Check if video has any active operations."""
        return (
            self.download_status == "active"
            or self.encrypt_status == "active"
            or self.upload_status == "active"
            or self.sync_status == "active"
            or self.analysis_status == "active"
            or self.overall_status == "active"
        )
    
    @property
    def has_failed(self) -> bool:
        """Check if video has any failed stages."""
        return (
            self.download_status == "failed"
            or self.encrypt_status == "failed"
            or self.upload_status == "failed"
            or self.sync_status == "failed"
            or self.analysis_status == "failed"
            or self.overall_status == "failed"
        )
    
    @property
    def is_completed(self) -> bool:
        """Check if video has completed all stages.
        
        Returns True if either:
        - All individual stage statuses are "completed", OR
        - The overall_status is "completed" (for backward compatibility)
        """
        # First check overall status as a quick shortcut
        if self.overall_status == "completed":
            return True
        
        # Also check all individual stages
        return (
            self.download_status == "completed"
            and self.encrypt_status == "completed"
            and self.upload_status == "completed"
            and self.sync_status == "completed"
            and self.analysis_status == "completed"
        )
    
    def update_timestamp(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = datetime.now(timezone.utc)
    
    def add_speed_sample(self, speed: float, progress: float) -> None:
        """Add a speed sample to the history buffer.
        
        Args:
            speed: Current speed in bytes/sec
            progress: Current progress percentage
        """
        self.speed_history.append({
            'timestamp': datetime.now(timezone.utc),
            'speed': speed,
            'progress': progress
        })
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert VideoState to dictionary representation."""
        return {
            'id': self.id,
            'title': self.title,
            'file_size': self.file_size,
            'plugin': self.plugin,
            'download_status': self.download_status,
            'download_progress': self.download_progress,
            'download_speed': self.download_speed,
            'download_eta': self.download_eta,
            'encrypt_status': self.encrypt_status,
            'encrypt_progress': self.encrypt_progress,
            'upload_status': self.upload_status,
            'upload_progress': self.upload_progress,
            'upload_speed': self.upload_speed,
            'sync_status': self.sync_status,
            'sync_progress': self.sync_progress,
            'analysis_status': self.analysis_status,
            'analysis_progress': self.analysis_progress,
            'overall_status': self.overall_status,
            'current_stage': self.current_stage,
            'current_progress': self.current_progress,
            'current_speed': self.current_speed,
            'is_active': self.is_active,
            'has_failed': self.has_failed,
            'is_completed': self.is_completed,
            'skip_reason': self.skip_reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
        }


class StateManager:
    """Thread-safe state manager for TUI.
    
    Maintains an in-memory cache of video states with thread-safe access
    and change notifications. Subscribes to pipeline events to keep state
    synchronized with the actual pipeline.
    
    Example:
        async with PipelineInterface() as pipeline:
            state_manager = StateManager(pipeline)
            await state_manager.initialize()
            
            # Register for state changes
            state_manager.on_change(lambda vid, field, val: print(f"{vid}.{field} = {val}"))
            
            # Access state
            video = state_manager.get_video(1)
            all_active = state_manager.get_active()
            
            await state_manager.shutdown()
    """
    
    def __init__(self, pipeline):
        """Initialize the state manager.
        
        Args:
            pipeline: PipelineInterface instance for database access and event subscription
        """
        self._pipeline = pipeline
        self._state: Dict[int, VideoState] = {}
        self._lock = asyncio.Lock()
        self._change_callbacks: List[Callable[[int, str, Any], None]] = []
        self._event_unsubscribers: List[Callable[[], None]] = []
        self._initialized = False
        # Byte-delta tracking for computing upload speed when the emitter
        # only sends ``bytes_uploaded`` / ``total_bytes`` (no speed field).
        # Maps video_id -> (last_bytes, last_monotonic_ts).
        self._upload_byte_tracker: Dict[int, tuple[int, float]] = {}
    
    async def initialize(self) -> None:
        """Load initial state from database and setup event handlers.
        
        This method should be called before using the state manager.
        It loads all active videos from the database and subscribes to
        pipeline events. Also loads orphaned torrents (torrents without
        Video records) so they appear in the TUI.
        """
        if self._initialized:
            logger.warning("StateManager already initialized")
            return
        
        logger.debug("Initializing StateManager")
        
        # Load all active videos
        try:
            active_videos = self._pipeline.get_active_videos()
            async with self._lock:
                for video in active_videos:
                    await self._load_video(video.id)
            logger.debug(f"Loaded {len(active_videos)} active videos")
        except Exception as e:
            logger.error(f"Failed to load active videos: {e}")
        
        # Load orphaned torrents (torrents without Video records)
        try:
            from haven_tui.data.repositories import PipelineSnapshotRepository
            from haven_cli.database.connection import get_db_session
            
            session = get_db_session()
            if hasattr(session, '__enter__'):
                with session as db_session:
                    snapshot_repo = PipelineSnapshotRepository(db_session)
                    orphan_views = snapshot_repo.get_active_torrents_without_video()
                    
                    async with self._lock:
                        for view in orphan_views:
                            await self._load_torrent_view(view)
                    
                    logger.debug(f"Loaded {len(orphan_views)} orphaned torrents")
        except Exception as e:
            logger.error(f"Failed to load orphaned torrents: {e}")
        
        # Setup event handlers
        self._setup_event_handlers()
        
        self._initialized = True
        logger.debug("StateManager initialization complete")
    
    async def shutdown(self) -> None:
        """Cleanup and unsubscribe from events.
        
        This method should be called when the state manager is no longer needed.
        It unsubscribes from all pipeline events and clears the state cache.
        """
        if not self._initialized:
            return
        
        logger.debug("Shutting down StateManager")
        
        # Unsubscribe from all events
        for unsubscriber in self._event_unsubscribers:
            if unsubscriber is None:
                continue
            try:
                unsubscriber()
            except Exception as e:
                logger.error(f"Error unsubscribing from event: {e}")
        
        self._event_unsubscribers.clear()
        
        # Clear state
        async with self._lock:
            self._state.clear()
        
        self._change_callbacks.clear()
        self._initialized = False
        logger.debug("StateManager shutdown complete")
    
    async def _load_video(self, video_id: int) -> Optional[VideoState]:
        """Load a video's state from the database.
        
        Args:
            video_id: The video ID to load
            
        Returns:
            VideoState if found, None otherwise
            
        Note:
            This method does NOT acquire the lock. Callers must ensure proper
            locking when calling this method from within locked contexts.
        """
        # Handle negative IDs (torrent-only placeholders)
        if video_id < 0:
            # This is a torrent placeholder - load from the torrent view
            return await self._load_torrent_by_id(video_id)
        
        try:
            video = self._pipeline.get_video_detail(video_id)
            if not video:
                return None
            
            # Try to get pipeline snapshot for additional state
            stats = self._pipeline.get_pipeline_stats()
            
            # Create initial state from video
            state = VideoState(
                id=video.id,
                title=video.title or f"Video {video.id}",
                file_size=video.file_size or 0,
                plugin=video.plugin_name or "unknown",
                created_at=video.created_at or datetime.now(timezone.utc),
                updated_at=video.updated_at or datetime.now(timezone.utc),
            )
            
            # Update from pipeline snapshot if available
            if video.pipeline_snapshot:
                snapshot = video.pipeline_snapshot
                state.overall_status = snapshot.overall_status
                state.current_stage = snapshot.current_stage

                # Set stage progress if available
                if snapshot.stage_progress_percent is not None:
                    progress = snapshot.stage_progress_percent
                    if snapshot.current_stage == "download":
                        state.download_progress = progress
                    elif snapshot.current_stage == "encrypt":
                        state.encrypt_progress = progress
                    elif snapshot.current_stage == "upload":
                        state.upload_progress = progress
                    elif snapshot.current_stage == "sync":
                        state.sync_progress = progress
                    elif snapshot.current_stage == "analyze":
                        state.analysis_progress = progress

                # Sync stage_speed from the snapshot so the 5 s polling
                # refresh re-syncs upload/download speed even if a few
                # progress events were missed (e.g. daemon ↔ TUI process
                # boundary). The snapshot only carries the *current* stage's
                # speed, so map it onto the matching attribute.
                stage_speed = getattr(snapshot, "stage_speed", None)
                if stage_speed is not None:
                    try:
                        stage_speed_f = float(stage_speed)
                    except (TypeError, ValueError):
                        stage_speed_f = 0.0
                    if snapshot.current_stage == "download":
                        state.download_speed = stage_speed_f
                    elif snapshot.current_stage == "upload":
                        state.upload_speed = stage_speed_f

                # Surface ETA from the snapshot if available (download stage).
                stage_eta = getattr(snapshot, "stage_eta", None)
                if stage_eta is not None and snapshot.current_stage == "download":
                    try:
                        state.download_eta = int(stage_eta)
                    except (TypeError, ValueError):
                        pass
            
            # Check video status flags
            if video.encrypted:
                state.encrypt_status = "completed"
                state.encrypt_progress = 100.0
            
            if video.cid:
                state.upload_status = "completed"
                state.upload_progress = 100.0
            
            if video.arkiv_entity_key:
                state.sync_status = "completed"
                state.sync_progress = 100.0
            
            if video.has_ai_data:
                state.analysis_status = "completed"
                state.analysis_progress = 100.0
            
            self._state[video_id] = state
            
            return state
            
        except Exception as e:
            logger.error(f"Error loading video {video_id}: {e}")
            return None
    
    async def _load_torrent_view(self, view) -> Optional[VideoState]:
        """Load a torrent view (orphaned torrent) into state.
        
        Creates a VideoState for a torrent that doesn't have a Video record.
        These torrents use negative IDs to distinguish them from real videos.
        
        Args:
            view: VideoView object representing an orphaned torrent
            
        Returns:
            VideoState if created, None otherwise
            
        Note:
            This method does NOT acquire the lock. Callers must ensure proper
            locking when calling this method from within locked contexts.
        """
        try:
            # Map overall status
            status_map = {
                "active": "active",
                "pending": "pending",
                "completed": "completed",
                "failed": "failed",
            }
            overall_status = status_map.get(view.overall_status, "pending")
            
            # Map stage string to status
            stage_status = "pending"
            if overall_status == "active":
                stage_status = "active"
            elif overall_status == "failed":
                stage_status = "failed"
            
            # Create VideoState for the torrent
            state = VideoState(
                id=view.id,  # Negative ID
                title=view.title or f"Torrent {abs(view.id)}",
                file_size=view.file_size or 0,
                plugin="bittorrent",
                download_status=stage_status,
                download_progress=view.stage_progress,
                download_speed=float(view.stage_speed),
                download_eta=view.stage_eta,
                overall_status=overall_status,
                current_stage="download",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            
            self._state[view.id] = state
            
            return state
            
        except Exception as e:
            logger.error(f"Error loading torrent view {view.id}: {e}")
            return None
    
    async def _load_torrent_by_id(self, video_id: int) -> Optional[VideoState]:
        """Load a torrent by its negative ID.
        
        Args:
            video_id: Negative ID representing a torrent (-torrent_id)
            
        Returns:
            VideoState if found, None otherwise
        """
        try:
            from haven_tui.data.repositories import PipelineSnapshotRepository
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.models import TorrentDownload
            
            # Convert negative ID to torrent ID
            torrent_id = abs(video_id)
            
            session = get_db_session()
            if hasattr(session, '__enter__'):
                with session as db_session:
                    # Get the torrent directly
                    torrent = db_session.query(TorrentDownload).filter_by(
                        id=torrent_id
                    ).first()
                    
                    if not torrent:
                        return None
                    
                    # Convert to view and load
                    snapshot_repo = PipelineSnapshotRepository(db_session)
                    view = snapshot_repo._torrent_to_view(torrent)
                    return await self._load_torrent_view(view)
            
            return None
            
        except Exception as e:
            logger.error(f"Error loading torrent by ID {video_id}: {e}")
            return None
    
    def _setup_event_handlers(self) -> None:
        """Setup event handlers for pipeline events."""
        event_handlers = [
            (EventType.DOWNLOAD_PROGRESS, self._on_download_progress),
            (EventType.UPLOAD_PROGRESS, self._on_upload_progress),
            (EventType.ENCRYPT_PROGRESS, self._on_encrypt_progress),
            (EventType.ENCRYPT_COMPLETE, self._on_encrypt_complete),
            (EventType.UPLOAD_COMPLETE, self._on_upload_complete),
            (EventType.SYNC_COMPLETE, self._on_sync_complete),
            (EventType.ANALYSIS_COMPLETE, self._on_analysis_complete),
            (EventType.STEP_COMPLETE, self._on_stage_complete),
            (EventType.PIPELINE_COMPLETE, self._on_pipeline_complete),
            (EventType.PIPELINE_FAILED, self._on_pipeline_failed),
            (EventType.STEP_FAILED, self._on_step_failed),
            (EventType.PIPELINE_STARTED, self._on_pipeline_started),
            (EventType.VIDEO_INGESTED, self._on_video_ingested),
            (EventType.STEP_SKIPPED, self._on_step_skipped),
        ]
        
        for event_type, handler in event_handlers:
            unsubscriber = self._pipeline.on_event(event_type, handler)
            if unsubscriber is not None:
                self._event_unsubscribers.append(unsubscriber)
    
    def on_change(self, callback: Callable[[int, str, Any], None]) -> None:
        """Register callback for state changes.
        
        The callback receives: (video_id, field_name, new_value)
        
        Args:
            callback: Function to call when state changes
        """
        self._change_callbacks.append(callback)
    
    def off_change(self, callback: Callable[[int, str, Any], None]) -> bool:
        """Unregister a state change callback.
        
        Args:
            callback: The callback to remove
            
        Returns:
            True if callback was found and removed, False otherwise
        """
        try:
            self._change_callbacks.remove(callback)
            return True
        except ValueError:
            return False
    
    def _notify_change(self, video_id: int, field: str, value: Any) -> None:
        """Notify all registered callbacks of a state change.
        
        Args:
            video_id: The video that changed
            field: The name of the field that changed
            value: The new value
        """
        for callback in self._change_callbacks:
            try:
                if inspect.iscoroutinefunction(callback):
                    asyncio.create_task(callback(video_id, field, value))
                else:
                    callback(video_id, field, value)
            except Exception as e:
                logger.error(f"Change callback error: {e}")
    
    def get_video(self, video_id: int) -> Optional[VideoState]:
        """Get the state for a specific video.
        
        Args:
            video_id: The video ID to look up
            
        Returns:
            VideoState if found, None otherwise
        """
        return self._state.get(video_id)
    
    def get_all_videos(self) -> List[VideoState]:
        """Get all video states.
        
        Returns:
            List of all VideoState objects
        """
        return list(self._state.values())
    
    def get_active(self) -> List[VideoState]:
        """Get all active video states.
        
        Returns:
            List of VideoState objects with is_active=True
        """
        return [v for v in self._state.values() if v.is_active]
    
    def get_by_status(self, status: str) -> List[VideoState]:
        """Get videos by overall status.
        
        Args:
            status: The status to filter by ("pending", "active", "completed", "failed")
            
        Returns:
            List of VideoState objects with matching overall_status
        """
        return [v for v in self._state.values() if v.overall_status == status]
    
    def get_by_stage(self, stage: str) -> List[VideoState]:
        """Get videos by current stage.
        
        Args:
            stage: The stage to filter by ("download", "encrypt", "upload", "sync", "analysis")
            
        Returns:
            List of VideoState objects with matching current_stage
        """
        return [v for v in self._state.values() if v.current_stage == stage]
    
    async def refresh_from_database(self, include_completed: bool = True) -> int:
        """Refresh state by fetching active videos from the database.
        
        This method polls the database for current active videos and updates
        the in-memory state. It adds new videos, updates existing ones.
        Completed videos are kept in state so they can be displayed.
        
        Args:
            include_completed: If True, also fetch completed videos from database
        
        Returns:
            Number of videos refreshed/updated.
        """
        if not self._initialized:
            return 0
        
        try:
            # Fetch active videos from the database
            active_videos = self._pipeline.get_active_videos(include_completed=include_completed)
            refreshed_count = 0
            current_ids = set()
            
            async with self._lock:
                for video in active_videos:
                    video_id = video.id
                    current_ids.add(video_id)
                    
                    # Load or update each video
                    try:
                        # Check if this is a new video or needs refresh
                        if video_id not in self._state:
                            # New video - load it
                            await self._load_video(video_id)
                            refreshed_count += 1
                        else:
                            # Existing video - check if it needs update
                            state = self._state[video_id]
                            # Update if video has newer timestamp
                            video_updated = getattr(video, 'updated_at', None)
                            if video_updated and video_updated > state.updated_at:
                                await self._load_video(video_id)
                                refreshed_count += 1
                    except Exception as e:
                        logger.error(f"Error refreshing video {video_id}: {e}")
                
                # Mark videos that are no longer in active list but are in state
                # We keep them in state but mark them as needing re-check
                # Only remove videos that are truly gone (deleted from database)
                existing_ids = set(self._state.keys())
                removed_ids = existing_ids - current_ids
                
                for video_id in list(removed_ids):
                    video = self._state.get(video_id)
                    # Only remove if the video is not completed/failed
                    # (completed/failed videos should stay in state for display)
                    if video and video.overall_status not in ("completed", "failed"):
                        # Video is no longer active and wasn't completed - might be deleted
                        del self._state[video_id]
                        logger.debug(f"Removed stale video {video_id} from state")
            
            return refreshed_count
            
        except Exception as e:
            logger.error(f"Error refreshing from database: {e}")
            return 0
    
    def get_speed_history(
        self,
        video_id: int,
        stage: str = "download",
        seconds: int = 60
    ) -> List[tuple[float, float, float]]:
        """Get speed history for a video from in-memory state.
        
        This provides speed data for graphing without requiring database queries.
        
        Args:
            video_id: The video ID to look up
            stage: The pipeline stage ("download", "encrypt", "upload")
            seconds: Time window in seconds
            
        Returns:
            List of (timestamp, speed, progress) tuples where timestamp is Unix time
        """
        state = self._state.get(video_id)
        if not state:
            return []
        
        # Get the appropriate speed history based on stage
        if stage == "download":
            history = state.speed_history
        elif stage == "upload":
            # Upload speed history would be stored separately if needed
            # For now, return empty list
            return []
        elif stage == "encrypt":
            # Encryption typically doesn't have speed tracking
            return []
        else:
            return []
        
        # Filter by time window and convert to expected format
        cutoff = time.time() - seconds
        result = []
        for sample in history:
            ts = sample.get('timestamp')
            if ts:
                # Convert datetime to Unix timestamp
                if isinstance(ts, datetime):
                    ts_float = ts.timestamp()
                else:
                    ts_float = float(ts)
                
                if ts_float >= cutoff:
                    result.append((
                        ts_float,
                        float(sample.get('speed', 0)),
                        float(sample.get('progress', 0))
                    ))
        
        return result
    
    # Event handlers
    
    async def _on_download_progress(self, event: Event) -> None:
        """Handle download progress events.
        
        Args:
            event: The download progress event
        """
        payload = event.payload
        video_id = payload.get('video_id')
        
        if not video_id:
            return
        
        # Handle synthetic IDs for orphaned torrents (negative IDs)
        is_orphaned_torrent = video_id < 0
        
        # Load video first if needed (outside lock to avoid reentrancy issues)
        if video_id not in self._state:
            if is_orphaned_torrent:
                # For orphaned torrents, create state from the event data
                await self._create_torrent_state_from_event(video_id, payload)
            else:
                await self._load_video(video_id)
        
        if video_id not in self._state:
            return
        
        async with self._lock:
            state = self._state[video_id]
            
            # Update download state
            old_speed = state.download_speed
            old_progress = state.download_progress
            
            state.download_status = "active"
            state.current_stage = "download"
            state.overall_status = "active"
            
            # Set started_at when download first becomes active
            if state.started_at is None:
                state.started_at = datetime.now(timezone.utc)
            
            # Canonical payload keys emitted by
            # ``haven_tui/data/download_tracker.py``:
            #   - ``progress_percent`` (float, 0.0 - 100.0)
            #   - ``download_rate``   (bytes/sec)
            #   - ``eta_seconds``     (Optional[int])
            speed_present = 'download_rate' in payload and payload['download_rate'] is not None
            progress_present = 'progress_percent' in payload and payload['progress_percent'] is not None

            if speed_present:
                state.download_speed = float(payload['download_rate'])
            if progress_present:
                state.download_progress = float(payload['progress_percent'])

            eta_val = payload.get('eta_seconds')
            if eta_val is not None:
                state.download_eta = int(eta_val)

            # Update speed history (only sample when we actually got a value
            # to avoid flooding the buffer with zeros on no-op updates).
            if speed_present or progress_present:
                state.add_speed_sample(state.download_speed, state.download_progress)
            state.update_timestamp()
        
        # Notify outside lock to prevent deadlocks
        if speed_present and state.download_speed != old_speed:
            self._notify_change(video_id, 'download_speed', state.download_speed)
        if progress_present and state.download_progress != old_progress:
            self._notify_change(video_id, 'download_progress', state.download_progress)
    
    async def _create_torrent_state_from_event(self, video_id: int, payload: Dict[str, Any]) -> Optional[VideoState]:
        """Create VideoState for an orphaned torrent from event data.
        
        This is used when progress events arrive for torrents that don't have
        Video records in the database yet.
        
        Args:
            video_id: Synthetic negative video ID
            payload: Event payload with torrent data
            
        Returns:
            VideoState if created, None otherwise
        """
        try:
            # Create VideoState from event payload
            state = VideoState(
                id=video_id,
                title=payload.get('video_path', f"Torrent {abs(video_id)}"),
                file_size=payload.get('total_bytes', 0),
                plugin="bittorrent",
                download_status="active",
                download_progress=payload.get('progress_percent', 0.0),
                download_speed=float(payload.get('download_rate', 0)),
                download_eta=payload.get('eta_seconds'),
                overall_status="active",
                current_stage="download",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            
            # Set started_at
            state.started_at = datetime.now(timezone.utc)
            
            self._state[video_id] = state
            logger.debug(f"Created state for orphaned torrent: {state.title}")
            
            return state
            
        except Exception as e:
            logger.error(f"Error creating torrent state from event: {e}")
            return None
    
    async def _on_upload_progress(self, event: Event) -> None:
        """Handle upload progress events.

        The canonical payload emitted by ``haven_cli/pipeline/steps/upload_step.py``
        is::

            {
                "video_id": int,
                "job_id": int,
                "stage": "preparing" | "encoding_car" | "uploading"
                         | "verifying" | "creating_pieces" | "completed",
                "progress_percent": float,           # 0.0 - 100.0
                "bytes_uploaded": Optional[int],     # byte counter from synapse-sdk
                "total_bytes": Optional[int],
                "upload_speed": int,                 # bytes/sec, 0 during prep
            }

        We prefer ``upload_speed`` when it's a positive integer (it is the
        authoritative value derived from the synapse network-upload window
        inside ``upload_step``). When it's 0 (preparation phase) or missing
        we fall back to a per-video byte-delta computed from
        ``bytes_uploaded`` via ``self._upload_byte_tracker`` — this keeps
        the in-memory ring graph alive on older daemons that haven't been
        restarted with the explicit field yet.
        """
        payload = event.payload
        video_id = payload.get('video_id')

        if not video_id:
            return

        # Load video first if needed (outside lock to avoid reentrancy issues)
        if video_id not in self._state:
            await self._load_video(video_id)

        if video_id not in self._state:
            return

        async with self._lock:
            state = self._state[video_id]

            old_speed = state.upload_speed
            old_progress = state.upload_progress

            state.upload_status = "active"
            state.current_stage = "upload"
            state.overall_status = "active"

            # --- Progress ---------------------------------------------------
            progress_present = 'progress_percent' in payload and payload['progress_percent'] is not None
            if progress_present:
                state.upload_progress = float(payload['progress_percent'])

            # --- Speed: prefer explicit upload_speed, else byte-delta -------
            speed_present = False
            raw_speed = payload.get('upload_speed')
            if raw_speed is not None:
                try:
                    explicit_speed = float(raw_speed)
                except (TypeError, ValueError):
                    explicit_speed = 0.0
                if explicit_speed > 0:
                    state.upload_speed = explicit_speed
                    speed_present = True

            # upload_step emits ``bytes_uploaded`` only during the actual
            # network upload phase (otherwise it is ``None``); we still
            # advance the byte tracker so the fallback path stays primed
            # in case a later event drops the explicit speed field.
            raw_bytes = payload.get('bytes_uploaded')
            if raw_bytes is not None:
                try:
                    cur_bytes = int(raw_bytes)
                except (TypeError, ValueError):
                    cur_bytes = 0
                now = time.monotonic()
                prev = self._upload_byte_tracker.get(video_id)
                if prev is not None and not speed_present:
                    prev_bytes, prev_ts = prev
                    dt = now - prev_ts
                    if dt > 0 and cur_bytes >= prev_bytes:
                        state.upload_speed = float(cur_bytes - prev_bytes) / dt
                        speed_present = True
                self._upload_byte_tracker[video_id] = (cur_bytes, now)

            state.update_timestamp()

        if speed_present and state.upload_speed != old_speed:
            self._notify_change(video_id, 'upload_speed', state.upload_speed)
        if progress_present and state.upload_progress != old_progress:
            self._notify_change(video_id, 'upload_progress', state.upload_progress)
    
    async def _on_encrypt_progress(self, event: Event) -> None:
        """Handle encryption progress events.
        
        Args:
            event: The encryption progress event
        """
        payload = event.payload
        video_id = payload.get('video_id')
        
        if not video_id:
            return
        
        # Load video first if needed (outside lock to avoid reentrancy issues)
        if video_id not in self._state:
            await self._load_video(video_id)
        
        if video_id not in self._state:
            return
        
        async with self._lock:
            state = self._state[video_id]
            old_progress = state.encrypt_progress

            state.encrypt_status = "active"
            state.current_stage = "encrypt"
            state.overall_status = "active"

            # Canonical key from ``build_encrypt_progress_payload``.
            progress_present = 'progress_percent' in payload and payload['progress_percent'] is not None
            if progress_present:
                state.encrypt_progress = float(payload['progress_percent'])

            state.update_timestamp()

        if progress_present and state.encrypt_progress != old_progress:
            self._notify_change(video_id, 'encrypt_progress', state.encrypt_progress)
    
    async def _on_encrypt_complete(self, event: Event) -> None:
        """Handle encryption complete events.
        
        Args:
            event: The encryption complete event
        """
        payload = event.payload
        video_id = payload.get('video_id')
        
        if not video_id or video_id not in self._state:
            return
        
        async with self._lock:
            state = self._state[video_id]
            state.encrypt_status = "completed"
            state.encrypt_progress = 100.0
            state.update_timestamp()
        
        self._notify_change(video_id, 'encrypt_status', 'completed')
        self._notify_change(video_id, 'encrypt_progress', 100.0)
    
    async def _on_upload_complete(self, event: Event) -> None:
        """Handle upload complete events.
        
        Args:
            event: The upload complete event
        """
        payload = event.payload
        video_id = payload.get('video_id')
        
        if not video_id or video_id not in self._state:
            return
        
        async with self._lock:
            state = self._state[video_id]
            state.upload_status = "completed"
            state.upload_progress = 100.0
            state.upload_speed = 0.0
            state.update_timestamp()
        
        self._notify_change(video_id, 'upload_status', 'completed')
        self._notify_change(video_id, 'upload_progress', 100.0)
        self._notify_change(video_id, 'upload_speed', 0.0)
    
    async def _on_sync_complete(self, event: Event) -> None:
        """Handle sync complete events.
        
        Args:
            event: The sync complete event
        """
        payload = event.payload
        video_id = payload.get('video_id')
        
        if not video_id or video_id not in self._state:
            return
        
        async with self._lock:
            state = self._state[video_id]
            state.sync_status = "completed"
            state.sync_progress = 100.0
            state.update_timestamp()
        
        self._notify_change(video_id, 'sync_status', 'completed')
        self._notify_change(video_id, 'sync_progress', 100.0)
    
    async def _on_analysis_complete(self, event: Event) -> None:
        """Handle analysis complete events.
        
        Args:
            event: The analysis complete event
        """
        payload = event.payload
        video_id = payload.get('video_id')
        
        if not video_id or video_id not in self._state:
            return
        
        async with self._lock:
            state = self._state[video_id]
            state.analysis_status = "completed"
            state.analysis_progress = 100.0
            state.update_timestamp()
        
        self._notify_change(video_id, 'analysis_status', 'completed')
        self._notify_change(video_id, 'analysis_progress', 100.0)
    
    async def _on_pipeline_complete(self, event: Event) -> None:
        """Handle pipeline complete events.
        
        Marks all stages as completed and sets overall status to completed.
        This ensures the video shows as fully completed in the TUI.
        
        Args:
            event: The pipeline complete event
        """
        payload = event.payload
        video_id = payload.get('video_id')
        
        if not video_id or video_id not in self._state:
            return
        
        async with self._lock:
            state = self._state[video_id]
            # Mark all stages as completed
            state.download_status = "completed"
            state.download_progress = 100.0
            state.encrypt_status = "completed"
            state.encrypt_progress = 100.0
            state.upload_status = "completed"
            state.upload_progress = 100.0
            state.sync_status = "completed"
            state.sync_progress = 100.0
            state.analysis_status = "completed"
            state.analysis_progress = 100.0
            # Update overall status
            state.overall_status = "completed"
            state.current_stage = "complete"
            state.update_timestamp()
        
        # Notify all changes
        self._notify_change(video_id, 'overall_status', 'completed')
        self._notify_change(video_id, 'current_stage', 'complete')
        self._notify_change(video_id, 'download_status', 'completed')
        self._notify_change(video_id, 'encrypt_status', 'completed')
        self._notify_change(video_id, 'upload_status', 'completed')
        self._notify_change(video_id, 'sync_status', 'completed')
        self._notify_change(video_id, 'analysis_status', 'completed')
    
    async def _on_stage_complete(self, event: Event) -> None:
        """Handle stage/step complete events.
        
        Args:
            event: The stage complete event
        """
        payload = event.payload
        video_id = payload.get('video_id')
        stage = payload.get('stage')
        
        if not video_id or not stage or video_id not in self._state:
            return
        
        async with self._lock:
            state = self._state[video_id]
            
            # Update the appropriate stage
            if stage == "download":
                state.download_status = "completed"
                state.download_progress = 100.0
            elif stage == "encrypt":
                state.encrypt_status = "completed"
                state.encrypt_progress = 100.0
            elif stage == "upload":
                state.upload_status = "completed"
                state.upload_progress = 100.0
            elif stage == "sync":
                state.sync_status = "completed"
                state.sync_progress = 100.0
            elif stage == "analysis":
                state.analysis_status = "completed"
                state.analysis_progress = 100.0
            
            state.update_timestamp()
        
        self._notify_change(video_id, f'{stage}_status', 'completed')
        self._notify_change(video_id, f'{stage}_progress', 100.0)
    
    async def _on_pipeline_failed(self, event: Event) -> None:
        """Handle pipeline failure events.
        
        Args:
            event: The pipeline failed event
        """
        payload = event.payload
        video_id = payload.get('video_id')
        failed_stage = payload.get('stage', 'unknown')
        
        if not video_id or video_id not in self._state:
            return
        
        async with self._lock:
            state = self._state[video_id]
            state.overall_status = "failed"
            state.update_timestamp()
            
            # Mark the failed stage
            if failed_stage == "download":
                state.download_status = "failed"
            elif failed_stage == "encrypt":
                state.encrypt_status = "failed"
            elif failed_stage == "upload":
                state.upload_status = "failed"
            elif failed_stage == "sync":
                state.sync_status = "failed"
            elif failed_stage == "analysis":
                state.analysis_status = "failed"
        
        self._notify_change(video_id, 'overall_status', 'failed')
        self._notify_change(video_id, f'{failed_stage}_status', 'failed')
    
    async def _on_step_failed(self, event: Event) -> None:
        """Handle step failure events.
        
        Args:
            event: The step failed event
        """
        await self._on_pipeline_failed(event)
    
    async def _on_pipeline_started(self, event: Event) -> None:
        """Handle pipeline started events.
        
        Args:
            event: The pipeline started event
        """
        payload = event.payload
        video_id = payload.get('video_id')
        
        if not video_id:
            return
        
        # Load the video if not in state (outside lock to avoid reentrancy issues)
        if video_id not in self._state:
            await self._load_video(video_id)
        
        if video_id in self._state:
            async with self._lock:
                state = self._state[video_id]
                state.overall_status = "active"
                state.update_timestamp()
            
            self._notify_change(video_id, 'overall_status', 'active')
    
    async def _on_video_ingested(self, event: Event) -> None:
        """Handle video ingested events.
        
        Args:
            event: The video ingested event
        """
        payload = event.payload
        video_id = payload.get('video_id')
        
        if not video_id:
            return
        
        # Load the newly ingested video
        await self._load_video(video_id)
        
        if video_id in self._state:
            self._notify_change(video_id, 'overall_status', 'pending')
    
    async def _on_step_skipped(self, event: Event) -> None:
        """Handle step skipped events.
        
        Args:
            event: The step skipped event
        """
        payload = event.payload
        video_id = payload.get('video_id')
        reason = payload.get('reason', '')
        step_name = payload.get('step_name', '')
        
        if not video_id:
            return
        
        # Load video first if needed (outside lock to avoid reentrancy issues)
        if video_id not in self._state:
            await self._load_video(video_id)
        
        if video_id not in self._state:
            return
        
        async with self._lock:
            state = self._state[video_id]
            
            # Store the skip reason
            if reason:
                # If there's already a skip reason, append the new one
                if state.skip_reason:
                    state.skip_reason = f"{state.skip_reason}; {reason}"
                else:
                    state.skip_reason = reason
            
            state.update_timestamp()
        
        if reason:
            self._notify_change(video_id, 'skip_reason', reason)
