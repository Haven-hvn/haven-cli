"""Pipeline interface for TUI interaction.

This module provides the PipelineInterface class - the primary bridge between
the TUI and Haven pipeline core. It provides controlled access to pipeline
operations, database queries, and event subscriptions.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional, TypeVar, Union, TYPE_CHECKING

from sqlalchemy import desc
from sqlalchemy.orm import Session

from haven_cli.pipeline.events import (
    Event,
    EventBus,
    EventHandler,
    EventType,
    get_event_bus,
)
from haven_cli.database.connection import get_db_session
from haven_cli.database.models import Video, TorrentDownload, Download, EncryptionJob, UploadJob, SyncJob, AnalysisJob
from haven_cli.database.repositories import (
    VideoRepository,
    TorrentDownloadRepository,
    PipelineSnapshotRepository,
    DownloadRepository,
)
if TYPE_CHECKING:
    from haven_cli.plugins.manager import PluginManager, get_plugin_manager


@dataclass
class UnifiedDownload:
    """Combined view of YouTube and BitTorrent downloads."""
    id: int  # download job ID
    video_id: int
    source_type: str  # "youtube" | "torrent"
    title: str
    
    # Status
    status: str  # "pending" | "active" | "paused" | "completed" | "failed"
    status_message: Optional[str] = None  # Error message or status detail
    
    # Progress
    progress_percent: float = 0.0
    speed: int = 0  # bytes/sec
    eta: Optional[int] = None  # seconds
    
    # Size
    total_bytes: Optional[int] = None
    downloaded_bytes: int = 0
    
    # Timing
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Source-specific - YouTube
    youtube_url: Optional[str] = None
    youtube_format: Optional[str] = None
    
    # Source-specific - BitTorrent
    torrent_magnet: Optional[str] = None
    torrent_info_hash: Optional[str] = None
    torrent_peers: Optional[int] = None
    torrent_seeds: Optional[int] = None
    torrent_ratio: Optional[float] = None


@dataclass
class DownloadStats:
    """Aggregate download statistics."""
    active_count: int = 0
    pending_count: int = 0
    completed_today: int = 0
    failed_count: int = 0
    total_speed: int = 0  # bytes/sec
    
    # Breakdown by source
    youtube_active: int = 0
    torrent_active: int = 0
    
    # Speed by source
    youtube_speed: int = 0
    torrent_speed: int = 0


@dataclass
class RetryResult:
    """Result of a retry operation."""
    success: bool
    message: str
    new_job_id: Optional[int] = None


@dataclass
class BatchResult:
    """Result of batch operation on multiple videos.
    
    Attributes:
        success: List of video IDs that were successfully processed
        failed: List of (video_id, error_message) tuples for failed operations
    """
    success: List[int] = None
    failed: List[tuple] = None
    
    def __post_init__(self):
        """Initialize default empty lists."""
        if self.success is None:
            self.success = []
        if self.failed is None:
            self.failed = []
    
    @property
    def all_succeeded(self) -> bool:
        """Check if all operations succeeded."""
        return len(self.failed) == 0
    
    @property
    def total_count(self) -> int:
        """Get total number of videos processed."""
        return len(self.success) + len(self.failed)
    
    @property
    def success_count(self) -> int:
        """Get number of successful operations."""
        return len(self.success)
    
    @property
    def failed_count(self) -> int:
        """Get number of failed operations."""
        return len(self.failed)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert batch result to dictionary."""
        return {
            "success": self.success,
            "failed": [{"video_id": vid, "error": err} for vid, err in self.failed],
            "all_succeeded": self.all_succeeded,
            "total_count": self.total_count,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
        }


class BatchOperations:
    """Handles multi-select and batch operations on videos.
    
    This class provides batch operations for video processing, allowing
    users to perform actions on multiple videos at once, similar to
    aria2tui's multi-select functionality.
    
    Example:
        >>> batch_ops = BatchOperations(state_manager, pipeline_interface)
        >>> batch_ops.select_all(videos)
        >>> result = await batch_ops.retry_failed()
        >>> print(f"Retried: {result.success_count} videos")
    
    Attributes:
        state_manager: The StateManager for accessing video state
        pipeline: The PipelineInterface for performing operations
        selected: Set of selected video IDs
    """
    
    def __init__(self, state_manager, pipeline):
        """Initialize batch operations handler.
        
        Args:
            state_manager: The StateManager for accessing video state
            pipeline: The PipelineInterface for performing operations
        """
        self.state_manager = state_manager
        self.pipeline = pipeline
        self.selected: set[int] = set()
    
    def toggle_selection(self, video_id: int) -> bool:
        """Toggle video selection.
        
        Args:
            video_id: The video ID to toggle
            
        Returns:
            True if video is now selected, False if unselected
        """
        if video_id in self.selected:
            self.selected.remove(video_id)
            return False
        else:
            self.selected.add(video_id)
            return True
    
    def select_all(self, videos: Optional[List[Any]] = None) -> int:
        """Select all visible videos.
        
        Args:
            videos: Optional list of videos to select. If not provided,
                   uses all videos from the state manager.
                   
        Returns:
            Number of videos selected
        """
        if videos is None:
            videos = self.state_manager.get_all_videos()
        
        self.selected = {v.id for v in videos}
        return len(self.selected)
    
    def clear_selection(self) -> None:
        """Clear all selections."""
        self.selected.clear()
    
    def get_selected(self) -> List[int]:
        """Get list of selected video IDs.
        
        Returns:
            List of selected video IDs
        """
        return list(self.selected)
    
    def is_selected(self, video_id: int) -> bool:
        """Check if a video is selected.
        
        Args:
            video_id: The video ID to check
            
        Returns:
            True if video is selected, False otherwise
        """
        return video_id in self.selected
    
    def get_selected_count(self) -> int:
        """Get the number of selected videos.
        
        Returns:
            Number of selected videos
        """
        return len(self.selected)
    
    def has_selection(self) -> bool:
        """Check if any videos are selected.
        
        Returns:
            True if at least one video is selected
        """
        return len(self.selected) > 0
    
    async def retry_failed(self) -> BatchResult:
        """Retry failed stages for selected videos.
        
        This operation retries only videos that have failed status.
        Videos that are not in failed state are skipped.
        
        Returns:
            BatchResult with success and failure information
        """
        result = BatchResult()
        
        for video_id in self.selected:
            video = self.state_manager.get_video(video_id)
            if video and video.has_failed:
                try:
                    retry_result = await self.pipeline.retry_video(video_id)
                    if retry_result.success:
                        result.success.append(video_id)
                    else:
                        result.failed.append((video_id, retry_result.message))
                except Exception as e:
                    result.failed.append((video_id, str(e)))
            else:
                # Skip videos that aren't failed
                if video:
                    result.failed.append((video_id, "Video is not in failed state"))
                else:
                    result.failed.append((video_id, "Video not found"))
        
        return result
    
    async def remove_from_queue(self) -> BatchResult:
        """Remove selected videos from pipeline.
        
        This cancels all active operations for the selected videos
        and marks them as removed from the queue.
        
        Returns:
            BatchResult with success and failure information
        """
        result = BatchResult()
        
        for video_id in self.selected:
            try:
                success = await self.pipeline.cancel_video(video_id)
                if success:
                    result.success.append(video_id)
                else:
                    result.failed.append((video_id, "Video not found or already cancelled"))
            except Exception as e:
                result.failed.append((video_id, str(e)))
        
        # Clear selection after removal
        self.selected.clear()
        return result
    
    async def force_reprocess(self, stage: Optional[str] = None) -> BatchResult:
        """Force re-process selected videos from given stage.
        
        This operation forces all selected videos to be reprocessed
        from the specified stage, regardless of their current status.
        
        Args:
            stage: Stage to reprocess from (e.g., "download", "encrypt", "upload").
                  If None, uses the current stage or starts from beginning.
                  
        Returns:
            BatchResult with success and failure information
        """
        result = BatchResult()
        
        valid_stages = ["download", "encrypt", "upload", "sync", "analysis", "ingest"]
        if stage is not None and stage not in valid_stages:
            result.failed.append((0, f"Invalid stage: {stage}. Must be one of {valid_stages}"))
            return result
        
        for video_id in self.selected:
            try:
                # Determine which stage to retry from
                retry_stage = stage
                if retry_stage is None:
                    video = self.state_manager.get_video(video_id)
                    if video:
                        retry_stage = video.current_stage
                    else:
                        retry_stage = "download"
                
                retry_result = await self.pipeline.retry_video(video_id, stage=retry_stage)
                if retry_result.success:
                    result.success.append(video_id)
                else:
                    result.failed.append((video_id, retry_result.message))
            except Exception as e:
                result.failed.append((video_id, str(e)))
        
        return result
    
    def export_list(self, filepath: str, videos: Optional[List[Any]] = None) -> Dict[str, Any]:
        """Export selected videos to JSON file.
        
        Args:
            filepath: Path to the output JSON file
            videos: Optional list of video objects with additional metadata.
                   If not provided, uses state manager to get video info.
                   
        Returns:
            Dictionary with export results
        """
        import json
        
        export_data = []
        
        for video_id in self.selected:
            video = self.state_manager.get_video(video_id)
            if video:
                data = {
                    "id": video.id,
                    "title": video.title,
                    "stage": video.current_stage,
                    "progress": video.current_progress,
                    "status": video.overall_status,
                    "is_active": video.is_active,
                    "has_failed": video.has_failed,
                    "is_completed": video.is_completed,
                }
                export_data.append(data)
        
        result = {
            "exported_count": len(export_data),
            "filepath": filepath,
            "videos": export_data,
        }
        
        try:
            with open(filepath, 'w') as f:
                json.dump(result, f, indent=2, default=str)
            result["success"] = True
        except Exception as e:
            result["success"] = False
            result["error"] = str(e)
        
        return result
    
    def get_selected_videos_info(self) -> List[Dict[str, Any]]:
        """Get detailed information about selected videos.
        
        Returns:
            List of dictionaries with video information
        """
        info = []
        
        for video_id in self.selected:
            video = self.state_manager.get_video(video_id)
            if video:
                info.append({
                    "id": video.id,
                    "title": video.title,
                    "stage": video.current_stage,
                    "progress": video.current_progress,
                    "status": video.overall_status,
                })
        
        return info


class PipelineInterface:
    """Primary interface between TUI and Haven pipeline core.
    
    This class provides controlled access to pipeline operations, database
    queries, and event subscriptions. It serves as the main bridge for the
    TUI to interact with the Haven pipeline.
    
    Example:
        async with PipelineInterface() as interface:
            # Get active videos
            videos = interface.get_active_videos()
            
            # Subscribe to events
            interface.on_event(EventType.DOWNLOAD_PROGRESS, handle_download)
            
            # Retry a failed video
            await interface.retry_video(video_id=1, stage="upload")
    """
    
    def __init__(
        self,
        database_path: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
        plugin_manager: Optional[PluginManager] = None,
    ):
        """Initialize the pipeline interface.
        
        Args:
            database_path: Optional path to the database file
            event_bus: Optional event bus instance (uses default if not provided)
            plugin_manager: Optional plugin manager (uses default if not provided)
        """
        self._database_path = database_path
        self._event_bus = event_bus
        self._plugin_manager = plugin_manager
        self._db_session: Optional[Session] = None
        self._subscriptions: Dict[EventType, List[Callable[[Event], None]]] = {}
        self._any_event_handlers: List[Callable[[Event], None]] = []
    
    async def __aenter__(self) -> "PipelineInterface":
        """Context manager entry.
        
        Initializes the database session and event bus.
        
        Returns:
            Self for use in async with statement
        """
        # Initialize database session
        self._db_session = get_db_session()
        if hasattr(self._db_session, '__enter__'):
            self._db_session = self._db_session.__enter__()
        
        # Initialize event bus if not provided
        if self._event_bus is None:
            self._event_bus = get_event_bus()
        
        # Initialize plugin manager if not provided
        if self._plugin_manager is None:
            from haven_cli.plugins.manager import get_plugin_manager
            self._plugin_manager = get_plugin_manager()
        
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit.
        
        Closes the database session and cleans up resources.
        """
        if self._db_session:
            try:
                if exc_type:
                    self._db_session.rollback()
                else:
                    self._db_session.commit()
            finally:
                self._db_session.close()
                self._db_session = None
    
    def _ensure_session(self) -> Session:
        """Ensure database session is available.
        
        Returns:
            Active database session
            
        Raises:
            RuntimeError: If no session is active
        """
        if self._db_session is None:
            raise RuntimeError("No active database session. Use 'async with' context manager.")
        return self._db_session
    
    def _wrap_handler(
        self,
        handler: Callable[[Event], Union[None, Coroutine[Any, Any, None]]]
    ) -> EventHandler:
        """Wrap handler to support both sync and async callbacks.
        
        The event bus expects async handlers, but UI frameworks typically
        need sync callbacks. This wrapper handles both cases.
        
        Args:
            handler: Sync or async event handler
            
        Returns:
            Async handler suitable for the event bus
        """
        if inspect.iscoroutinefunction(handler):
            # Handler is already async
            return handler  # type: ignore
        else:
            # Wrap sync handler
            async def async_wrapper(event: Event) -> None:
                handler(event)
            return async_wrapper
    
    def on_event(
        self,
        event_type: EventType,
        handler: Callable[[Event], Union[None, Coroutine[Any, Any, None]]],
    ) -> None:
        """Subscribe to events with automatic sync/async handling.
        
        Args:
            event_type: The type of event to subscribe to
            handler: Event handler (sync or async)
        """
        if self._event_bus is None:
            raise RuntimeError("Event bus not initialized")
        
        # Track subscription for potential cleanup
        if event_type not in self._subscriptions:
            self._subscriptions[event_type] = []
        self._subscriptions[event_type].append(handler)
        
        # Subscribe to event bus with wrapped handler
        wrapped_handler = self._wrap_handler(handler)
        self._event_bus.subscribe(event_type, wrapped_handler)
    
    def on_any_event(
        self,
        handler: Callable[[Event], Union[None, Coroutine[Any, Any, None]]],
    ) -> None:
        """Subscribe to all events.
        
        Args:
            handler: Event handler (sync or async) that receives all events
        """
        if self._event_bus is None:
            raise RuntimeError("Event bus not initialized")
        
        self._any_event_handlers.append(handler)
        
        # Subscribe to event bus with wrapped handler
        wrapped_handler = self._wrap_handler(handler)
        self._event_bus.subscribe_all(wrapped_handler)
    
    def unsubscribe(
        self,
        event_type: EventType,
        handler: Callable[[Event], Union[None, Coroutine[Any, Any, None]]],
    ) -> bool:
        """Unsubscribe a handler from an event type.
        
        Note: This removes the handler from our tracking. The EventBus
        doesn't support direct unsubscription, so this is a best-effort
        operation that tracks handlers for interface cleanup.
        
        Args:
            event_type: The event type to unsubscribe from
            handler: The handler to remove
            
        Returns:
            True if handler was found and removed, False otherwise
        """
        if event_type in self._subscriptions:
            try:
                self._subscriptions[event_type].remove(handler)
                return True
            except ValueError:
                pass
        return False
    
    def get_video_repository(self) -> VideoRepository:
        """Get video repository for database operations.
        
        Returns:
            VideoRepository instance
        """
        session = self._ensure_session()
        return VideoRepository(session)
    
    def get_active_videos(self, include_completed: bool = False) -> List[Video]:
        """Get videos currently in the pipeline.
        
        Returns videos that are pending, active, or failed.
        Also includes torrent downloads that don't have Video records yet.
        
        Args:
            include_completed: If True, also include videos with overall_status="completed"
        
        Returns:
            List of active videos (or Video-like objects for torrent-only entries)
        """
        session = self._ensure_session()
        repo = VideoRepository(session)
        
        # Get all videos that have pipeline activity
        # This includes videos with downloads, encryption jobs, upload jobs, etc.
        from haven_cli.database.models import (
            PipelineSnapshot, Download, EncryptionJob, UploadJob, SyncJob
        )
        
        # Build status filter
        statuses = ["active", "pending", "failed"]
        if include_completed:
            statuses.append("completed")
        
        # Query videos with active pipeline snapshots
        active_ids = session.query(PipelineSnapshot.video_id).filter(
            PipelineSnapshot.overall_status.in_(statuses)
        ).all()
        
        videos = []
        if active_ids:
            video_ids = [r[0] for r in active_ids]
            videos = session.query(Video).filter(Video.id.in_(video_ids)).all()
        
        # Also get orphaned torrents (torrents without Video records)
        # These are represented as Video-like objects with negative IDs
        from haven_tui.data.repositories import PipelineSnapshotRepository
        snapshot_repo = PipelineSnapshotRepository(session)
        orphan_views = snapshot_repo.get_active_torrents_without_video()
        
        # Convert VideoView objects to placeholder Video objects
        # This maintains backwards compatibility with existing code
        for view in orphan_views:
            # Create a minimal Video-like object
            placeholder = self._create_torrent_placeholder(view)
            if placeholder:
                videos.append(placeholder)
        
        return videos
    
    def get_completed_videos(self, limit: int = 100) -> List[Video]:
        """Get videos that have completed all pipeline stages.
        
        Args:
            limit: Maximum number of completed videos to return
            
        Returns:
            List of completed videos ordered by completion time (newest first)
        """
        session = self._ensure_session()
        
        from haven_cli.database.models import PipelineSnapshot
        
        # Query videos with completed status
        completed_query = session.query(
            PipelineSnapshot.video_id
        ).filter(
            PipelineSnapshot.overall_status == "completed"
        ).order_by(
            desc(PipelineSnapshot.pipeline_completed_at)
        ).limit(limit)
        
        completed_ids = completed_query.all()
        
        if not completed_ids:
            return []
        
        video_ids = [r[0] for r in completed_ids]
        videos = session.query(Video).filter(Video.id.in_(video_ids)).all()
        
        return videos
    
    def _create_torrent_placeholder(self, view) -> Optional[Video]:
        """Create a placeholder Video object from a VideoView for orphaned torrents.
        
        This creates a minimal Video object that can be used by the TUI
        to display torrents that don't have Video records yet.
        
        Args:
            view: VideoView object representing an orphaned torrent
            
        Returns:
            A Video-like object with the necessary attributes for TUI display
        """
        # Create a minimal Video object
        video = Video(
            id=view.id,  # Negative ID to indicate placeholder
            title=view.title,
            source_path=view.source_path,
            file_size=view.file_size,
            plugin_name=view.plugin,
        )
        
        # Store the view data for later use
        video._torrent_view = view
        
        return video
    
    def get_video_detail(self, video_id: int) -> Optional[Video]:
        """Get detailed information about a specific video.
        
        Args:
            video_id: The video ID to look up
            
        Returns:
            Video details if found, None otherwise
        """
        session = self._ensure_session()
        repo = VideoRepository(session)
        return repo.get_by_id(video_id)
    
    def get_pipeline_stats(self) -> Dict[str, Any]:
        """Get aggregate pipeline statistics.
        
        Returns:
            Dictionary with pipeline statistics including:
            - active_count: Number of active videos
            - total_speed: Combined speed across all stages
            - by_stage: Breakdown of videos by current stage
            - total_videos: Total number of videos
            - completed_count: Number of completed videos
            - failed_count: Number of failed videos
        """
        session = self._ensure_session()
        snapshot_repo = PipelineSnapshotRepository(session)
        video_repo = VideoRepository(session)
        
        # Get aggregate stats from pipeline snapshots
        stats = snapshot_repo.get_aggregate_stats()
        
        # Add total counts
        total_videos = video_repo.count()
        
        from haven_cli.database.models import PipelineSnapshot
        
        completed_count = session.query(PipelineSnapshot).filter(
            PipelineSnapshot.overall_status == "completed"
        ).count()
        
        failed_count = session.query(PipelineSnapshot).filter(
            PipelineSnapshot.overall_status == "failed"
        ).count()
        
        return {
            **stats,
            "total_videos": total_videos,
            "completed_count": completed_count,
            "failed_count": failed_count,
        }
    
    def search_videos(self, query: str, limit: int = 50) -> List[Video]:
        """Search videos by title or metadata.
        
        Args:
            query: Search query string
            limit: Maximum number of results
            
        Returns:
            List of matching videos
        """
        session = self._ensure_session()
        
        # Search by title (case-insensitive)
        from sqlalchemy import or_
        
        results = session.query(Video).filter(
            or_(
                Video.title.ilike(f"%{query}%"),
                Video.creator_handle.ilike(f"%{query}%"),
                Video.source_uri.ilike(f"%{query}%"),
            )
        ).limit(limit).all()
        
        return results
    
    def get_plugin_manager(self) -> PluginManager:
        """Get the plugin manager instance.
        
        Returns:
            PluginManager instance
        """
        if self._plugin_manager is None:
            raise RuntimeError("Plugin manager not initialized")
        return self._plugin_manager
    
    def get_active_downloads(self) -> List[UnifiedDownload]:
        """Get unified view of all active downloads.
        
        Combines YouTube and BitTorrent downloads into a single view
        for display in the TUI.
        
        Returns:
            List of unified download objects sorted by created_at desc
        """
        session = self._ensure_session()
        downloads: List[UnifiedDownload] = []
        
        # Get regular downloads (YouTube)
        download_repo = DownloadRepository(session)
        active_downloads = download_repo.get_active_downloads()
        
        for dl in active_downloads:
            # Get video title
            video = session.query(Video).filter(Video.id == dl.video_id).first()
            title = video.title if video else "Unknown"
            
            # Get YouTube URL and format from source metadata
            youtube_url = None
            youtube_format = None
            if dl.source_metadata and isinstance(dl.source_metadata, dict):
                youtube_url = dl.source_metadata.get("url")
                youtube_format = dl.source_metadata.get("format_id")
            
            # Map status to unified status
            status = dl.status
            if status == "downloading":
                status = "active"
            
            unified = UnifiedDownload(
                id=dl.id,
                video_id=dl.video_id,
                source_type=dl.source_type,
                title=title,
                status=status,
                status_message=dl.error_message,
                progress_percent=dl.progress_percent or 0.0,
                speed=dl.download_rate or 0,
                eta=dl.eta_seconds,
                total_bytes=dl.bytes_total,
                downloaded_bytes=dl.bytes_downloaded or 0,
                created_at=dl.created_at,
                started_at=dl.started_at,
                completed_at=dl.completed_at,
                youtube_url=youtube_url,
                youtube_format=youtube_format,
            )
            downloads.append(unified)
        
        # Get BitTorrent downloads
        torrent_repo = TorrentDownloadRepository(session)
        active_torrents = torrent_repo.get_active()
        
        for torrent in active_torrents:
            # Determine status mapping
            if torrent.status == "downloading":
                status = "active"
            elif torrent.status == "paused":
                status = "paused"
            elif torrent.status == "completed":
                status = "completed"
            elif torrent.status == "failed":
                status = "failed"
            else:
                status = "pending"
            
            # Calculate ETA
            eta = None
            if torrent.download_rate and torrent.download_rate > 0:
                remaining = torrent.total_size - torrent.downloaded_size
                eta = int(remaining / torrent.download_rate)
            
            # Try to map torrent to video via source_id lookup
            # source_id format might be "video:{video_id}" or similar
            video_id = -torrent.id  # Default to negative torrent ID
            title = torrent.title or "Unknown"
            
            # Calculate ratio (uploaded/downloaded)
            ratio = None
            if torrent.downloaded_size > 0:
                # We don't track uploaded size for ratio, would need additional field
                ratio = 0.0
            
            unified = UnifiedDownload(
                id=torrent.id,
                video_id=video_id,
                source_type="torrent",
                title=title,
                status=status,
                status_message=torrent.error_message,
                progress_percent=torrent.progress * 100,
                speed=torrent.download_rate,
                eta=eta,
                total_bytes=torrent.total_size if torrent.total_size > 0 else None,
                downloaded_bytes=torrent.downloaded_size,
                created_at=torrent.created_at,
                started_at=torrent.started_at,
                completed_at=torrent.completed_at,
                torrent_magnet=torrent.magnet_uri,
                torrent_info_hash=torrent.infohash,
                torrent_peers=torrent.peers,
                torrent_seeds=torrent.seeds,
                torrent_ratio=ratio,
            )
            downloads.append(unified)
        
        # Sort by created_at desc (most recent first)
        # Normalize all datetimes to offset-aware to avoid comparison errors
        def _get_sort_datetime(x):
            dt = x.created_at
            if dt is None:
                return datetime.min.replace(tzinfo=timezone.utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        downloads.sort(key=_get_sort_datetime, reverse=True)
        
        return downloads
    
    async def retry_video(
        self, video_id: int, stage: Optional[str] = None
    ) -> RetryResult:
        """Retry a video from a specific stage.
        
        Args:
            video_id: The video to retry
            stage: Specific stage to retry ("download", "encrypt", "upload", "sync", "analysis")
                   If None, retries from the failed stage or the beginning
                   
        Returns:
            RetryResult with success status and message
        """
        session = self._ensure_session()
        
        # Get video and check if it exists
        video = session.query(Video).filter(Video.id == video_id).first()
        if not video:
            return RetryResult(
                success=False,
                message=f"Video {video_id} not found"
            )
        
        # Validate stage if provided
        valid_stages = ["download", "encrypt", "upload", "sync", "analysis", "ingest"]
        if stage is not None and stage not in valid_stages:
            return RetryResult(
                success=False,
                message=f"Invalid stage: {stage}. Must be one of {valid_stages}"
            )
        
        # Get pipeline snapshot to determine current state
        snapshot_repo = PipelineSnapshotRepository(session)
        snapshot = snapshot_repo.get_by_video_id(video_id)
        
        # First check if there are any failed stages (regardless of snapshot)
        failed_stage = self._find_failed_stage(video_id)
        
        if not snapshot:
            # No snapshot means video hasn't started pipeline tracking
            # Check for failed jobs first
            if stage is None:
                stage = failed_stage or "ingest"
            
            # If there's a failed stage, reset it and following stages
            if stage != "ingest" and failed_stage:
                await self._reset_stage_and_following(video_id, stage)
            
            await self._emit_retry_event(video_id, stage)
            return RetryResult(
                success=True,
                message=f"Starting video processing from {stage} stage",
                new_job_id=None
            )
        
        # Determine which stage to retry from
        if stage is None:
            # Find the first failed stage or use current stage
            stage = failed_stage or snapshot.current_stage or "download"
        
        # Reset stage and all subsequent stages
        await self._reset_stage_and_following(video_id, stage)
        
        # Clear error state if present
        if snapshot.has_error:
            snapshot.has_error = False
            snapshot.error_stage = None
            snapshot.error_message = None
            snapshot.overall_status = "active"
            session.commit()
        
        # Emit retry event
        await self._emit_retry_event(video_id, stage)
        
        return RetryResult(
            success=True,
            message=f"Retrying video from {stage} stage",
            new_job_id=None
        )
    
    def _find_failed_stage(self, video_id: int) -> Optional[str]:
        """Find the first failed stage for a video.
        
        Args:
            video_id: The video ID to check
            
        Returns:
            Name of the first failed stage, or None if no stages failed
        """
        session = self._ensure_session()
        stage_order = ["download", "encrypt", "upload", "sync", "analysis"]
        
        # Query each job table for failed jobs
        failed_download = session.query(Download).filter(
            Download.video_id == video_id,
            Download.status == "failed"
        ).first()
        if failed_download:
            return "download"
        
        failed_encrypt = session.query(EncryptionJob).filter(
            EncryptionJob.video_id == video_id,
            EncryptionJob.status == "failed"
        ).first()
        if failed_encrypt:
            return "encrypt"
        
        failed_upload = session.query(UploadJob).filter(
            UploadJob.video_id == video_id,
            UploadJob.status == "failed"
        ).first()
        if failed_upload:
            return "upload"
        
        failed_sync = session.query(SyncJob).filter(
            SyncJob.video_id == video_id,
            SyncJob.status == "failed"
        ).first()
        if failed_sync:
            return "sync"
        
        failed_analysis = session.query(AnalysisJob).filter(
            AnalysisJob.video_id == video_id,
            AnalysisJob.status == "failed"
        ).first()
        if failed_analysis:
            return "analysis"
        
        return None
    
    async def _reset_stage_and_following(self, video_id: int, from_stage: str) -> None:
        """Reset stage status to pending for stage and all following stages.
        
        Args:
            video_id: The video ID
            from_stage: Stage to start resetting from
        """
        session = self._ensure_session()
        stage_order = ["download", "encrypt", "upload", "sync", "analysis"]
        
        if from_stage not in stage_order:
            return
        
        start_idx = stage_order.index(from_stage)
        stages_to_reset = stage_order[start_idx:]
        
        for stage in stages_to_reset:
            await self._reset_stage(video_id, stage)
    
    async def _reset_stage(self, video_id: int, stage: str) -> None:
        """Reset a specific stage to pending status.
        
        Args:
            video_id: The video ID
            stage: Stage name to reset
        """
        session = self._ensure_session()
        
        if stage == "download":
            # Reset download jobs
            downloads = session.query(Download).filter(
                Download.video_id == video_id
            ).all()
            for dl in downloads:
                dl.status = "pending"
                dl.error_message = None
                dl.failed_at = None
        
        elif stage == "encrypt":
            # Reset encryption jobs
            jobs = session.query(EncryptionJob).filter(
                EncryptionJob.video_id == video_id
            ).all()
            for job in jobs:
                job.status = "pending"
                job.error_message = None
        
        elif stage == "upload":
            # Reset upload jobs
            jobs = session.query(UploadJob).filter(
                UploadJob.video_id == video_id
            ).all()
            for job in jobs:
                job.status = "pending"
                job.error_message = None
        
        elif stage == "sync":
            # Reset sync jobs
            jobs = session.query(SyncJob).filter(
                SyncJob.video_id == video_id
            ).all()
            for job in jobs:
                job.status = "pending"
                job.error_message = None
        
        elif stage == "analysis":
            # Reset analysis jobs
            jobs = session.query(AnalysisJob).filter(
                AnalysisJob.video_id == video_id
            ).all()
            for job in jobs:
                job.status = "pending"
                job.error_message = None
        
        session.commit()
    
    async def _emit_retry_event(self, video_id: int, stage: str) -> None:
        """Emit a retry event for a video.
        
        Args:
            video_id: Video ID to retry
            stage: Stage to retry from
        """
        if self._event_bus is None:
            return
        
        # Map stage to appropriate event type
        stage_event_map = {
            "download": EventType.DOWNLOAD_PROGRESS,
            "encrypt": EventType.ENCRYPT_REQUESTED,
            "upload": EventType.UPLOAD_REQUESTED,
            "sync": EventType.SYNC_REQUESTED,
            "analyze": EventType.ANALYSIS_REQUESTED,
            "ingest": EventType.VIDEO_INGESTED,
        }
        
        event_type = stage_event_map.get(stage, EventType.PIPELINE_STARTED)
        
        event = Event(
            event_type=event_type,
            payload={
                "video_id": video_id,
                "retry": True,
                "stage": stage,
            },
            source="PipelineInterface",
        )
        
        await self._event_bus.publish(event)
    
    async def cancel_video(self, video_id: int) -> bool:
        """Cancel all operations for a video.
        
        This cancels:
        - Active downloads (both YouTube and torrent)
        - Active encryption jobs
        - Active upload jobs
        - Active sync jobs
        - Active analysis jobs
        
        Args:
            video_id: The video ID to cancel
            
        Returns:
            True if cancellation was initiated, False if video not found
        """
        session = self._ensure_session()
        
        # Get video
        video = session.query(Video).filter(Video.id == video_id).first()
        if not video:
            return False
        
        # Cancel active downloads
        downloads = session.query(Download).filter(
            Download.video_id == video_id,
            Download.status.in_(["downloading", "pending"])
        ).all()
        for dl in downloads:
            dl.status = "cancelled"
        
        # Cancel active encryption jobs
        encrypt_jobs = session.query(EncryptionJob).filter(
            EncryptionJob.video_id == video_id,
            EncryptionJob.status.in_(["encrypting", "pending"])
        ).all()
        for job in encrypt_jobs:
            job.status = "cancelled"
        
        # Cancel active upload jobs
        upload_jobs = session.query(UploadJob).filter(
            UploadJob.video_id == video_id,
            UploadJob.status.in_(["uploading", "pending"])
        ).all()
        for job in upload_jobs:
            job.status = "cancelled"
        
        # Cancel active sync jobs
        sync_jobs = session.query(SyncJob).filter(
            SyncJob.video_id == video_id,
            SyncJob.status.in_(["syncing", "pending"])
        ).all()
        for job in sync_jobs:
            job.status = "cancelled"
        
        # Cancel active analysis jobs
        analysis_jobs = session.query(AnalysisJob).filter(
            AnalysisJob.video_id == video_id,
            AnalysisJob.status.in_(["analyzing", "pending"])
        ).all()
        for job in analysis_jobs:
            job.status = "cancelled"
        
        # Update pipeline snapshot to cancelled state
        snapshot_repo = PipelineSnapshotRepository(session)
        snapshot = snapshot_repo.get_by_video_id(video_id)
        
        if snapshot:
            snapshot.overall_status = "cancelled"
            session.commit()
        
        # Emit cancellation event
        if self._event_bus:
            event = Event(
                event_type=EventType.PIPELINE_CANCELLED,
                payload={"video_id": video_id},
                source="PipelineInterface",
            )
            await self._event_bus.publish(event)
        
        session.commit()
        return True
    
    def pause_download(self, video_id: int) -> bool:
        """Pause an active download.
        
        Pauses both YouTube and torrent downloads for the video.
        
        Args:
            video_id: The video ID to pause
            
        Returns:
            True if pause was successful, False otherwise
        """
        session = self._ensure_session()
        paused = False
        
        # Find active download for this video (YouTube/regular downloads)
        download_repo = DownloadRepository(session)
        downloads = download_repo.get_by_video_id(video_id)
        
        for dl in downloads:
            if dl.status == "downloading":
                download_repo.update_status(dl.id, "paused")
                paused = True
        
        # Also check torrent downloads - look for video reference in source_id
        # Torrent downloads may store video_id in source_id as "video:{id}"
        torrent_repo = TorrentDownloadRepository(session)
        source_id_patterns = [
            f"video:{video_id}",
            str(video_id),
        ]
        
        for pattern in source_id_patterns:
            torrent = torrent_repo.get_by_source_id(pattern)
            if torrent and torrent.status == "downloading":
                torrent_repo.update_status(torrent.infohash, "paused")
                paused = True
        
        return paused
    
    def resume_download(self, video_id: int) -> bool:
        """Resume a paused download.
        
        Resumes both YouTube and torrent downloads for the video.
        
        Args:
            video_id: The video ID to resume
            
        Returns:
            True if resume was successful, False otherwise
        """
        session = self._ensure_session()
        resumed = False
        
        # Find paused download for this video (YouTube/regular downloads)
        download_repo = DownloadRepository(session)
        downloads = download_repo.get_by_video_id(video_id)
        
        for dl in downloads:
            if dl.status == "paused":
                download_repo.update_status(dl.id, "downloading")
                resumed = True
        
        # Also check torrent downloads
        torrent_repo = TorrentDownloadRepository(session)
        source_id_patterns = [
            f"video:{video_id}",
            str(video_id),
        ]
        
        for pattern in source_id_patterns:
            torrent = torrent_repo.get_by_source_id(pattern)
            if torrent and torrent.status == "paused":
                torrent_repo.update_status(torrent.infohash, "downloading")
                resumed = True
        
        return resumed
    
    def get_download_history(self, limit: int = 50) -> List[UnifiedDownload]:
        """Get download history combining YouTube and torrent downloads.
        
        Args:
            limit: Maximum number of results (default: 50)
            
        Returns:
            List of UnifiedDownload objects sorted by created_at desc
        """
        session = self._ensure_session()
        downloads: List[UnifiedDownload] = []
        
        # Get regular downloads (YouTube and other sources)
        download_repo = DownloadRepository(session)
        all_downloads = session.query(Download).order_by(
            desc(Download.created_at)
        ).limit(limit).all()
        
        for dl in all_downloads:
            # Get video title
            video = session.query(Video).filter(Video.id == dl.video_id).first()
            title = video.title if video else "Unknown"
            
            # Get YouTube URL and format from source metadata
            youtube_url = None
            youtube_format = None
            if dl.source_metadata and isinstance(dl.source_metadata, dict):
                youtube_url = dl.source_metadata.get("url")
                youtube_format = dl.source_metadata.get("format_id")
            
            # Map status to unified status
            status = dl.status
            if status == "downloading":
                status = "active"
            
            unified = UnifiedDownload(
                id=dl.id,
                video_id=dl.video_id,
                source_type=dl.source_type,
                title=title,
                status=status,
                status_message=dl.error_message,
                progress_percent=dl.progress_percent or 0.0,
                speed=dl.download_rate or 0,
                eta=dl.eta_seconds,
                total_bytes=dl.bytes_total,
                downloaded_bytes=dl.bytes_downloaded or 0,
                created_at=dl.created_at,
                started_at=dl.started_at,
                completed_at=dl.completed_at,
                youtube_url=youtube_url,
                youtube_format=youtube_format,
            )
            downloads.append(unified)
        
        # Get BitTorrent downloads
        torrent_repo = TorrentDownloadRepository(session)
        all_torrents = session.query(TorrentDownload).order_by(
            desc(TorrentDownload.created_at)
        ).limit(limit).all()
        
        for torrent in all_torrents:
            # Determine status mapping
            if torrent.status == "downloading":
                status = "active"
            elif torrent.status == "paused":
                status = "paused"
            elif torrent.status == "completed":
                status = "completed"
            elif torrent.status == "failed":
                status = "failed"
            elif torrent.status == "skipped":
                status = "skipped"
            elif torrent.status == "cancelled":
                status = "cancelled"
            else:
                status = "pending"
            
            # Calculate ETA
            eta = None
            if torrent.download_rate and torrent.download_rate > 0:
                remaining = torrent.total_size - torrent.downloaded_size
                eta = int(remaining / torrent.download_rate)
            
            # Default video_id to negative torrent ID (no associated video)
            video_id = -torrent.id
            title = torrent.title or "Unknown"
            
            # Calculate ratio (placeholder - would need uploaded bytes)
            ratio = None
            
            unified = UnifiedDownload(
                id=torrent.id,
                video_id=video_id,
                source_type="torrent",
                title=title,
                status=status,
                status_message=torrent.error_message,
                progress_percent=torrent.progress * 100,
                speed=torrent.download_rate,
                eta=eta,
                total_bytes=torrent.total_size if torrent.total_size > 0 else None,
                downloaded_bytes=torrent.downloaded_size,
                created_at=torrent.created_at,
                started_at=torrent.started_at,
                completed_at=torrent.completed_at,
                torrent_magnet=torrent.magnet_uri,
                torrent_info_hash=torrent.infohash,
                torrent_peers=torrent.peers,
                torrent_seeds=torrent.seeds,
                torrent_ratio=ratio,
            )
            downloads.append(unified)
        
        # Sort by created_at desc and apply limit
        # Normalize all datetimes to offset-aware to avoid comparison errors
        def _get_sort_datetime(x):
            dt = x.created_at
            if dt is None:
                return datetime.min.replace(tzinfo=timezone.utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        downloads.sort(key=_get_sort_datetime, reverse=True)
        return downloads[:limit]
    
    def get_download_stats(self) -> DownloadStats:
        """Get aggregate download statistics.
        
        Returns:
            DownloadStats with counts and speeds by source type
        """
        session = self._ensure_session()
        from sqlalchemy import func
        from datetime import timedelta
        
        stats = DownloadStats()
        
        # YouTube/other downloads stats
        youtube_active = session.query(Download).filter(
            Download.source_type == "youtube",
            Download.status == "downloading"
        )
        stats.youtube_active = youtube_active.count()
        stats.youtube_speed = youtube_active.with_entities(
            func.sum(Download.download_rate)
        ).scalar() or 0
        
        youtube_pending = session.query(Download).filter(
            Download.source_type == "youtube",
            Download.status == "pending"
        ).count()
        
        youtube_failed = session.query(Download).filter(
            Download.source_type == "youtube",
            Download.status == "failed"
        ).count()
        
        # Torrent downloads stats
        torrent_active = session.query(TorrentDownload).filter(
            TorrentDownload.status == "downloading"
        )
        stats.torrent_active = torrent_active.count()
        stats.torrent_speed = torrent_active.with_entities(
            func.sum(TorrentDownload.download_rate)
        ).scalar() or 0
        
        torrent_pending = session.query(TorrentDownload).filter(
            TorrentDownload.status == "pending"
        ).count()
        
        torrent_failed = session.query(TorrentDownload).filter(
            TorrentDownload.status == "failed"
        ).count()
        
        # Aggregate stats
        stats.active_count = stats.youtube_active + stats.torrent_active
        stats.pending_count = youtube_pending + torrent_pending
        stats.failed_count = youtube_failed + torrent_failed
        stats.total_speed = stats.youtube_speed + stats.torrent_speed
        
        # Completed today
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        youtube_completed_today = session.query(Download).filter(
            Download.source_type == "youtube",
            Download.status == "completed",
            Download.completed_at >= today_start
        ).count()
        
        torrent_completed_today = session.query(TorrentDownload).filter(
            TorrentDownload.status == "completed",
            TorrentDownload.completed_at >= today_start
        ).count()
        
        stats.completed_today = youtube_completed_today + torrent_completed_today
        
        return stats
