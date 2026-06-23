"""Pipeline interface for TUI interaction.

This module provides the PipelineInterface class - the primary bridge between
the TUI and Haven pipeline core. It provides controlled access to pipeline
operations, database queries, and event subscriptions.

Concurrency / freshness contract
--------------------------------
Historically this class held a single SQLAlchemy ``Session`` open for the
entire lifetime of the TUI process. Combined with SQLite's default rollback
journal, the implicit reader transaction stayed open and pinned the TUI to a
frozen snapshot of the database — meaning every poll returned the same data
the TUI saw at startup. The user-visible symptom was: "I have to close and
reopen the TUI to see updates." See ``docs/TUI_IMPROVEMENTS_PROPOSAL.md`` R1.

The fix is two-fold:

1. The engine now runs in WAL mode with a busy-timeout (set in
   ``haven_cli/database/connection.py``). WAL allows readers to see committed
   writes without blocking the writer.
2. ``PipelineInterface`` no longer holds a long-lived ``Session``. Instead it
   holds a ``sessionmaker`` factory and every public method opens a
   short-lived session via :meth:`_session_scope`. The session is closed
   before the method returns, so the next call always sees the latest
   committed data.

ORM objects that escape the session must therefore be **detached**. We use
``session.expunge_all()`` after eager-loading any relationships the caller
needs (``Video.pipeline_snapshot`` is the main one). Callers MUST NOT trigger
lazy-loads on returned ORM objects; if they need extra fields, they should
either ask this interface for them or convert to a plain dataclass.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Coroutine,
    Dict,
    Iterator,
    List,
    Optional,
    TypeVar,
    Union,
)

from sqlalchemy import desc
from sqlalchemy.orm import Session, joinedload, sessionmaker

from haven_cli.pipeline.events import (
    Event,
    EventBus,
    EventHandler,
    EventType,
    get_event_bus,
)
from haven_cli.database.connection import get_session_maker
from haven_cli.database.models import (
    AnalysisJob,
    Download,
    EncryptionJob,
    SyncJob,
    TorrentDownload,
    UploadJob,
    Video,
)
from haven_cli.database.repositories import (
    DownloadRepository,
    PipelineSnapshotRepository,
    TorrentDownloadRepository,
    VideoRepository,
)
from haven_cli.plugins.manager import PluginManager, get_plugin_manager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DTOs (unchanged from the previous revision; included for backwards compat)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# BatchOperations (unchanged behavior, but documented to make session scoping
# explicit for callers).
# ---------------------------------------------------------------------------


class BatchOperations:
    """Handles multi-select and batch operations on videos.

    This class is a thin coordinator: it delegates DB writes to
    ``PipelineInterface``, which owns session lifetime. ``BatchOperations``
    itself never touches a session directly.
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
        """Toggle video selection."""
        if video_id in self.selected:
            self.selected.remove(video_id)
            return False
        self.selected.add(video_id)
        return True

    def select_all(self, videos: Optional[List[Any]] = None) -> int:
        """Select all visible videos."""
        if videos is None:
            videos = self.state_manager.get_all_videos()
        self.selected = {v.id for v in videos}
        return len(self.selected)

    def clear_selection(self) -> None:
        self.selected.clear()

    def get_selected(self) -> List[int]:
        return list(self.selected)

    def is_selected(self, video_id: int) -> bool:
        return video_id in self.selected

    def get_selected_count(self) -> int:
        return len(self.selected)

    def has_selection(self) -> bool:
        return len(self.selected) > 0

    async def retry_failed(self) -> BatchResult:
        """Retry failed stages for selected videos."""
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
                except Exception as e:  # pragma: no cover - defensive
                    result.failed.append((video_id, str(e)))
            else:
                if video:
                    result.failed.append((video_id, "Video is not in failed state"))
                else:
                    result.failed.append((video_id, "Video not found"))
        return result

    async def remove_from_queue(self) -> BatchResult:
        """Remove selected videos from pipeline."""
        result = BatchResult()
        for video_id in self.selected:
            try:
                success = await self.pipeline.cancel_video(video_id)
                if success:
                    result.success.append(video_id)
                else:
                    result.failed.append(
                        (video_id, "Video not found or already cancelled")
                    )
            except Exception as e:  # pragma: no cover - defensive
                result.failed.append((video_id, str(e)))
        self.selected.clear()
        return result

    async def force_reprocess(self, stage: Optional[str] = None) -> BatchResult:
        """Force re-process selected videos from given stage."""
        result = BatchResult()
        valid_stages = ["download", "encrypt", "upload", "sync", "analysis", "ingest"]
        if stage is not None and stage not in valid_stages:
            result.failed.append(
                (0, f"Invalid stage: {stage}. Must be one of {valid_stages}")
            )
            return result
        for video_id in self.selected:
            try:
                retry_stage = stage
                if retry_stage is None:
                    video = self.state_manager.get_video(video_id)
                    retry_stage = video.current_stage if video else "download"
                retry_result = await self.pipeline.retry_video(video_id, stage=retry_stage)
                if retry_result.success:
                    result.success.append(video_id)
                else:
                    result.failed.append((video_id, retry_result.message))
            except Exception as e:  # pragma: no cover - defensive
                result.failed.append((video_id, str(e)))
        return result

    def export_list(
        self, filepath: str, videos: Optional[List[Any]] = None
    ) -> Dict[str, Any]:
        """Export selected videos to JSON file."""
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
            with open(filepath, "w") as f:
                json.dump(result, f, indent=2, default=str)
            result["success"] = True
        except Exception as e:
            result["success"] = False
            result["error"] = str(e)
        return result

    def get_selected_videos_info(self) -> List[Dict[str, Any]]:
        """Get detailed information about selected videos."""
        info = []
        for video_id in self.selected:
            video = self.state_manager.get_video(video_id)
            if video:
                info.append(
                    {
                        "id": video.id,
                        "title": video.title,
                        "stage": video.current_stage,
                        "progress": video.current_progress,
                        "status": video.overall_status,
                    }
                )
        return info


# ---------------------------------------------------------------------------
# PipelineInterface
# ---------------------------------------------------------------------------


class PipelineInterface:
    """Primary interface between TUI and Haven pipeline core.

    Lifetime: created once per TUI process, entered with ``async with`` so the
    sessionmaker is initialized and the event bus / plugin manager handles are
    captured. Inside that context every public method opens its own
    short-lived session, runs its work, commits or rolls back, and closes.

    Backwards compatibility:
        Earlier revisions exposed ``self._db_session`` as a long-lived
        SQLAlchemy ``Session``. That attribute is preserved here as ``None``
        so any external ``if pi._db_session: ...`` checks resolve falsy and
        callers fall through to the safer constructor paths. Callers that
        actually need a session should use :meth:`session_factory` instead.
    """

    def __init__(
        self,
        database_path: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
        plugin_manager: Optional[PluginManager] = None,
    ):
        """Initialize the pipeline interface."""
        self._database_path = database_path
        self._event_bus = event_bus
        self._plugin_manager = plugin_manager
        self._session_factory: Optional[sessionmaker] = None
        # Kept for backwards compatibility with older callers that do
        # `if pi._db_session: ...`. We never assign a real session here.
        self._db_session: Optional[Session] = None
        self._subscriptions: Dict[EventType, List[Callable[[Event], None]]] = {}
        self._any_event_handlers: List[Callable[[Event], None]] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "PipelineInterface":
        """Initialize the session factory, event bus, and plugin manager."""
        # Acquire the global haven-cli sessionmaker. This is cheap (engine is
        # already initialized lazily on first use) and binds us to the same
        # database the daemon uses.
        self._session_factory = get_session_maker()

        if self._event_bus is None:
            self._event_bus = get_event_bus()

        if self._plugin_manager is None:
            self._plugin_manager = get_plugin_manager()

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """No long-lived session to clean up; just drop references."""
        # Each method already manages its own session lifetime via
        # _session_scope, so there is nothing to commit/rollback here.
        self._session_factory = None

    @property
    def session_factory(self) -> Optional[sessionmaker]:
        """Public accessor for the underlying sessionmaker.

        Other long-lived TUI components (e.g. the detail screen's repos) can
        use this to mint their own short-lived sessions instead of trying to
        share one with us — sharing was the whole bug.
        """
        return self._session_factory

    @contextmanager
    def _session_scope(self) -> Iterator[Session]:
        """Yield a fresh, short-lived session.

        Each call opens a new connection (drawn from the engine pool), runs
        the caller's work, and on exit either commits (success) or rolls back
        (exception) before closing. Closing returns the connection to the
        pool, which is important under WAL: a stale open transaction would
        otherwise hold back the writer's checkpoint.
        """
        if self._session_factory is None:
            raise RuntimeError(
                "PipelineInterface is not entered. Use 'async with PipelineInterface(...)'."
            )
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _ensure_session(self) -> Session:
        """Deprecated. Always raises.

        This used to return the long-lived session. Any caller still using it
        is buggy and must switch to ``with self._session_scope() as session``.
        We raise loudly so the bug is found in tests rather than silently
        producing stale data again.
        """
        raise RuntimeError(
            "PipelineInterface._ensure_session() is removed. Use _session_scope() "
            "and pass the session explicitly to helpers."
        )

    # ------------------------------------------------------------------
    # Event subscription
    # ------------------------------------------------------------------

    def _wrap_handler(
        self, handler: Callable[[Event], Union[None, Coroutine[Any, Any, None]]]
    ) -> EventHandler:
        """Wrap handler to support both sync and async callbacks."""
        if inspect.iscoroutinefunction(handler):
            return handler  # type: ignore

        async def async_wrapper(event: Event) -> None:
            handler(event)

        return async_wrapper

    def on_event(
        self,
        event_type: EventType,
        handler: Callable[[Event], Union[None, Coroutine[Any, Any, None]]],
    ) -> None:
        """Subscribe to events with automatic sync/async handling."""
        if self._event_bus is None:
            raise RuntimeError("Event bus not initialized")
        if event_type not in self._subscriptions:
            self._subscriptions[event_type] = []
        self._subscriptions[event_type].append(handler)
        wrapped_handler = self._wrap_handler(handler)
        self._event_bus.subscribe(event_type, wrapped_handler)

    def on_any_event(
        self,
        handler: Callable[[Event], Union[None, Coroutine[Any, Any, None]]],
    ) -> None:
        """Subscribe to all events."""
        if self._event_bus is None:
            raise RuntimeError("Event bus not initialized")
        self._any_event_handlers.append(handler)
        wrapped_handler = self._wrap_handler(handler)
        self._event_bus.subscribe_all(wrapped_handler)

    def unsubscribe(
        self,
        event_type: EventType,
        handler: Callable[[Event], Union[None, Coroutine[Any, Any, None]]],
    ) -> bool:
        """Unsubscribe a handler from an event type."""
        if event_type in self._subscriptions:
            try:
                self._subscriptions[event_type].remove(handler)
                return True
            except ValueError:
                pass
        return False

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    def get_video_repository(self) -> VideoRepository:
        """Return a VideoRepository bound to a fresh session.

        WARNING: the returned repository owns a private session that is NOT
        closed by us. Callers must close it themselves. Most call sites
        should not need this — prefer the higher-level methods on this
        class which handle session lifetime correctly.
        """
        if self._session_factory is None:
            raise RuntimeError("PipelineInterface is not entered")
        return VideoRepository(self._session_factory())

    def get_active_videos(self, include_completed: bool = False) -> List[Video]:
        """Get videos currently in the pipeline.

        Returns videos that are pending, active, or failed (and optionally
        completed). Also includes torrent downloads that don't have Video
        records yet. The returned ORM objects are detached from the session
        and have ``pipeline_snapshot`` eagerly loaded.

        Args:
            include_completed: If True, also include videos with
                ``overall_status == "completed"``.

        Returns:
            Detached ``Video`` instances; placeholders for orphan torrents
            have negative IDs and a ``_torrent_view`` attribute.
        """
        from haven_cli.database.models import PipelineSnapshot

        statuses = ["active", "pending", "failed"]
        if include_completed:
            statuses.append("completed")

        with self._session_scope() as session:
            active_id_rows = (
                session.query(PipelineSnapshot.video_id)
                .filter(PipelineSnapshot.overall_status.in_(statuses))
                .all()
            )

            videos: List[Video] = []
            if active_id_rows:
                video_ids = [r[0] for r in active_id_rows]
                # Eager-load the snapshot so callers can read
                # ``video.pipeline_snapshot`` after we expunge.
                videos = (
                    session.query(Video)
                    .options(joinedload(Video.pipeline_snapshot))
                    .filter(Video.id.in_(video_ids))
                    .all()
                )

            # Orphan-torrent placeholders are constructed from a TUI-side
            # view; the placeholder Video is *not* added to the session, so
            # it's safe to return as-is.
            from haven_tui.data.repositories import (
                PipelineSnapshotRepository as TUIPipelineSnapshotRepository,
            )

            snapshot_repo = TUIPipelineSnapshotRepository(session)
            orphan_views = snapshot_repo.get_active_torrents_without_video()

            placeholders: List[Video] = []
            for view in orphan_views:
                placeholder = self._create_torrent_placeholder(view)
                if placeholder is not None:
                    placeholders.append(placeholder)

            # Detach all real ORM objects so callers can safely read attributes
            # after the session closes.
            session.expunge_all()

            return videos + placeholders

    def get_completed_videos(self, limit: int = 100) -> List[Video]:
        """Get videos that have completed all pipeline stages."""
        from haven_cli.database.models import PipelineSnapshot

        with self._session_scope() as session:
            completed_ids = (
                session.query(PipelineSnapshot.video_id)
                .filter(PipelineSnapshot.overall_status == "completed")
                .order_by(desc(PipelineSnapshot.pipeline_completed_at))
                .limit(limit)
                .all()
            )
            if not completed_ids:
                return []
            video_ids = [r[0] for r in completed_ids]
            videos = (
                session.query(Video)
                .options(joinedload(Video.pipeline_snapshot))
                .filter(Video.id.in_(video_ids))
                .all()
            )
            session.expunge_all()
            return videos

    def _create_torrent_placeholder(self, view) -> Optional[Video]:
        """Create a placeholder Video object from a VideoView.

        These placeholders are never persisted; they exist only so the TUI
        can render torrents that don't yet have a corresponding Video row.
        """
        video = Video(
            id=view.id,  # Negative ID to indicate placeholder
            title=view.title,
            source_path=view.source_path,
            file_size=view.file_size,
            plugin_name=view.plugin,
        )
        video._torrent_view = view  # type: ignore[attr-defined]
        return video

    def get_video_detail(self, video_id: int) -> Optional[Video]:
        """Get detailed information about a specific video."""
        with self._session_scope() as session:
            video = (
                session.query(Video)
                .options(joinedload(Video.pipeline_snapshot))
                .filter(Video.id == video_id)
                .first()
            )
            if video is None:
                return None
            session.expunge_all()
            return video

    def get_pipeline_stats(self) -> Dict[str, Any]:
        """Get aggregate pipeline statistics."""
        from haven_cli.database.models import PipelineSnapshot

        with self._session_scope() as session:
            snapshot_repo = PipelineSnapshotRepository(session)
            video_repo = VideoRepository(session)
            stats = snapshot_repo.get_aggregate_stats()
            total_videos = video_repo.count()
            completed_count = (
                session.query(PipelineSnapshot)
                .filter(PipelineSnapshot.overall_status == "completed")
                .count()
            )
            failed_count = (
                session.query(PipelineSnapshot)
                .filter(PipelineSnapshot.overall_status == "failed")
                .count()
            )
            return {
                **stats,
                "total_videos": total_videos,
                "completed_count": completed_count,
                "failed_count": failed_count,
            }

    def search_videos(self, query: str, limit: int = 50) -> List[Video]:
        """Search videos by title or metadata."""
        from sqlalchemy import or_

        with self._session_scope() as session:
            results = (
                session.query(Video)
                .options(joinedload(Video.pipeline_snapshot))
                .filter(
                    or_(
                        Video.title.ilike(f"%{query}%"),
                        Video.creator_handle.ilike(f"%{query}%"),
                        Video.source_uri.ilike(f"%{query}%"),
                    )
                )
                .limit(limit)
                .all()
            )
            session.expunge_all()
            return results

    def get_plugin_manager(self) -> PluginManager:
        """Get the plugin manager instance."""
        if self._plugin_manager is None:
            raise RuntimeError("Plugin manager not initialized")
        return self._plugin_manager

    def get_active_downloads(self) -> List[UnifiedDownload]:
        """Get unified view of all active downloads.

        Returns plain ``UnifiedDownload`` dataclasses (not ORM objects), so
        the caller doesn't need to worry about session attachment.
        """
        downloads: List[UnifiedDownload] = []
        with self._session_scope() as session:
            download_repo = DownloadRepository(session)
            active_downloads = download_repo.get_active_downloads()
            for dl in active_downloads:
                video = (
                    session.query(Video).filter(Video.id == dl.video_id).first()
                )
                title = video.title if video else "Unknown"
                youtube_url = None
                youtube_format = None
                if dl.source_metadata and isinstance(dl.source_metadata, dict):
                    youtube_url = dl.source_metadata.get("url")
                    youtube_format = dl.source_metadata.get("format_id")
                status = dl.status
                if status == "downloading":
                    status = "active"
                downloads.append(
                    UnifiedDownload(
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
                )

            torrent_repo = TorrentDownloadRepository(session)
            active_torrents = torrent_repo.get_active()
            for torrent in active_torrents:
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
                eta = None
                if torrent.download_rate and torrent.download_rate > 0:
                    remaining = torrent.total_size - torrent.downloaded_size
                    eta = int(remaining / torrent.download_rate)
                ratio = 0.0 if torrent.downloaded_size > 0 else None
                downloads.append(
                    UnifiedDownload(
                        id=torrent.id,
                        video_id=-torrent.id,
                        source_type="torrent",
                        title=torrent.title or "Unknown",
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
                )

        downloads.sort(key=_safe_created_at, reverse=True)
        return downloads

    def get_download_history(self, limit: int = 50) -> List[UnifiedDownload]:
        """Get download history combining YouTube and torrent downloads."""
        downloads: List[UnifiedDownload] = []
        with self._session_scope() as session:
            all_downloads = (
                session.query(Download)
                .order_by(desc(Download.created_at))
                .limit(limit)
                .all()
            )
            for dl in all_downloads:
                video = session.query(Video).filter(Video.id == dl.video_id).first()
                title = video.title if video else "Unknown"
                youtube_url = None
                youtube_format = None
                if dl.source_metadata and isinstance(dl.source_metadata, dict):
                    youtube_url = dl.source_metadata.get("url")
                    youtube_format = dl.source_metadata.get("format_id")
                status = dl.status
                if status == "downloading":
                    status = "active"
                downloads.append(
                    UnifiedDownload(
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
                )

            all_torrents = (
                session.query(TorrentDownload)
                .order_by(desc(TorrentDownload.created_at))
                .limit(limit)
                .all()
            )
            for torrent in all_torrents:
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
                eta = None
                if torrent.download_rate and torrent.download_rate > 0:
                    remaining = torrent.total_size - torrent.downloaded_size
                    eta = int(remaining / torrent.download_rate)
                downloads.append(
                    UnifiedDownload(
                        id=torrent.id,
                        video_id=-torrent.id,
                        source_type="torrent",
                        title=torrent.title or "Unknown",
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
                        torrent_ratio=None,
                    )
                )

        downloads.sort(key=_safe_created_at, reverse=True)
        return downloads[:limit]

    def get_download_stats(self) -> DownloadStats:
        """Get aggregate download statistics."""
        from sqlalchemy import func

        stats = DownloadStats()
        with self._session_scope() as session:
            youtube_active_q = session.query(Download).filter(
                Download.source_type == "youtube", Download.status == "downloading"
            )
            stats.youtube_active = youtube_active_q.count()
            stats.youtube_speed = (
                youtube_active_q.with_entities(func.sum(Download.download_rate)).scalar()
                or 0
            )
            youtube_pending = (
                session.query(Download)
                .filter(Download.source_type == "youtube", Download.status == "pending")
                .count()
            )
            youtube_failed = (
                session.query(Download)
                .filter(Download.source_type == "youtube", Download.status == "failed")
                .count()
            )

            torrent_active_q = session.query(TorrentDownload).filter(
                TorrentDownload.status == "downloading"
            )
            stats.torrent_active = torrent_active_q.count()
            stats.torrent_speed = (
                torrent_active_q.with_entities(
                    func.sum(TorrentDownload.download_rate)
                ).scalar()
                or 0
            )
            torrent_pending = (
                session.query(TorrentDownload)
                .filter(TorrentDownload.status == "pending")
                .count()
            )
            torrent_failed = (
                session.query(TorrentDownload)
                .filter(TorrentDownload.status == "failed")
                .count()
            )

            stats.active_count = stats.youtube_active + stats.torrent_active
            stats.pending_count = youtube_pending + torrent_pending
            stats.failed_count = youtube_failed + torrent_failed
            stats.total_speed = stats.youtube_speed + stats.torrent_speed

            today_start = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            stats.completed_today = (
                session.query(Download)
                .filter(
                    Download.source_type == "youtube",
                    Download.status == "completed",
                    Download.completed_at >= today_start,
                )
                .count()
                + session.query(TorrentDownload)
                .filter(
                    TorrentDownload.status == "completed",
                    TorrentDownload.completed_at >= today_start,
                )
                .count()
            )
        return stats

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    async def retry_video(
        self, video_id: int, stage: Optional[str] = None
    ) -> RetryResult:
        """Retry a video from a specific stage.

        All DB mutations happen inside a single session scope so they're
        atomic. Event emission happens after the commit.
        """
        valid_stages = ["download", "encrypt", "upload", "sync", "analysis", "ingest"]
        if stage is not None and stage not in valid_stages:
            return RetryResult(
                success=False,
                message=f"Invalid stage: {stage}. Must be one of {valid_stages}",
            )

        chosen_stage: Optional[str] = stage

        with self._session_scope() as session:
            video = session.query(Video).filter(Video.id == video_id).first()
            if not video:
                return RetryResult(
                    success=False, message=f"Video {video_id} not found"
                )

            snapshot_repo = PipelineSnapshotRepository(session)
            snapshot = snapshot_repo.get_by_video_id(video_id)

            failed_stage = self._find_failed_stage_in_session(session, video_id)

            if not snapshot:
                if chosen_stage is None:
                    chosen_stage = failed_stage or "ingest"
                if chosen_stage != "ingest" and failed_stage:
                    self._reset_stage_and_following_in_session(
                        session, video_id, chosen_stage
                    )
            else:
                if chosen_stage is None:
                    chosen_stage = (
                        failed_stage or snapshot.current_stage or "download"
                    )
                self._reset_stage_and_following_in_session(
                    session, video_id, chosen_stage
                )
                if snapshot.has_error:
                    snapshot.has_error = False
                    snapshot.error_stage = None
                    snapshot.error_message = None
                    snapshot.overall_status = "active"
            # commit happens implicitly on context exit

        # Emit retry event after the DB write has been committed so any
        # listener that re-queries doesn't race the transaction.
        if chosen_stage is None:  # belt and suspenders
            chosen_stage = "ingest"
        await self._emit_retry_event(video_id, chosen_stage)

        return RetryResult(
            success=True,
            message=f"Retrying video from {chosen_stage} stage",
            new_job_id=None,
        )

    def _find_failed_stage_in_session(
        self, session: Session, video_id: int
    ) -> Optional[str]:
        """Find the first failed stage for a video, using the given session."""
        if (
            session.query(Download)
            .filter(Download.video_id == video_id, Download.status == "failed")
            .first()
        ):
            return "download"
        if (
            session.query(EncryptionJob)
            .filter(
                EncryptionJob.video_id == video_id, EncryptionJob.status == "failed"
            )
            .first()
        ):
            return "encrypt"
        if (
            session.query(UploadJob)
            .filter(UploadJob.video_id == video_id, UploadJob.status == "failed")
            .first()
        ):
            return "upload"
        if (
            session.query(SyncJob)
            .filter(SyncJob.video_id == video_id, SyncJob.status == "failed")
            .first()
        ):
            return "sync"
        if (
            session.query(AnalysisJob)
            .filter(
                AnalysisJob.video_id == video_id, AnalysisJob.status == "failed"
            )
            .first()
        ):
            return "analysis"
        return None

    def _reset_stage_and_following_in_session(
        self, session: Session, video_id: int, from_stage: str
    ) -> None:
        """Reset stage status to pending for stage and all following stages."""
        stage_order = ["download", "encrypt", "upload", "sync", "analysis"]
        if from_stage not in stage_order:
            return
        start_idx = stage_order.index(from_stage)
        for stage in stage_order[start_idx:]:
            self._reset_stage_in_session(session, video_id, stage)

    def _reset_stage_in_session(
        self, session: Session, video_id: int, stage: str
    ) -> None:
        """Reset a specific stage to pending status, using the given session."""
        if stage == "download":
            for dl in (
                session.query(Download).filter(Download.video_id == video_id).all()
            ):
                dl.status = "pending"
                dl.error_message = None
                dl.failed_at = None
        elif stage == "encrypt":
            for job in (
                session.query(EncryptionJob)
                .filter(EncryptionJob.video_id == video_id)
                .all()
            ):
                job.status = "pending"
                job.error_message = None
        elif stage == "upload":
            for job in (
                session.query(UploadJob)
                .filter(UploadJob.video_id == video_id)
                .all()
            ):
                job.status = "pending"
                job.error_message = None
        elif stage == "sync":
            for job in (
                session.query(SyncJob).filter(SyncJob.video_id == video_id).all()
            ):
                job.status = "pending"
                job.error_message = None
        elif stage == "analysis":
            for job in (
                session.query(AnalysisJob)
                .filter(AnalysisJob.video_id == video_id)
                .all()
            ):
                job.status = "pending"
                job.error_message = None
        # commit is owned by the surrounding _session_scope.

    async def _emit_retry_event(self, video_id: int, stage: str) -> None:
        """Emit a retry event for a video."""
        if self._event_bus is None:
            return
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
            payload={"video_id": video_id, "retry": True, "stage": stage},
            source="PipelineInterface",
        )
        await self._event_bus.publish(event)

    async def cancel_video(self, video_id: int) -> bool:
        """Cancel all operations for a video."""
        cancelled = False
        with self._session_scope() as session:
            video = session.query(Video).filter(Video.id == video_id).first()
            if not video:
                return False

            for dl in (
                session.query(Download)
                .filter(
                    Download.video_id == video_id,
                    Download.status.in_(["downloading", "pending"]),
                )
                .all()
            ):
                dl.status = "cancelled"

            for job in (
                session.query(EncryptionJob)
                .filter(
                    EncryptionJob.video_id == video_id,
                    EncryptionJob.status.in_(["encrypting", "pending"]),
                )
                .all()
            ):
                job.status = "cancelled"

            for job in (
                session.query(UploadJob)
                .filter(
                    UploadJob.video_id == video_id,
                    UploadJob.status.in_(["uploading", "pending"]),
                )
                .all()
            ):
                job.status = "cancelled"

            for job in (
                session.query(SyncJob)
                .filter(
                    SyncJob.video_id == video_id,
                    SyncJob.status.in_(["syncing", "pending"]),
                )
                .all()
            ):
                job.status = "cancelled"

            for job in (
                session.query(AnalysisJob)
                .filter(
                    AnalysisJob.video_id == video_id,
                    AnalysisJob.status.in_(["analyzing", "pending"]),
                )
                .all()
            ):
                job.status = "cancelled"

            snapshot_repo = PipelineSnapshotRepository(session)
            snapshot = snapshot_repo.get_by_video_id(video_id)
            if snapshot:
                snapshot.overall_status = "cancelled"
            cancelled = True
            # commit on context exit

        if cancelled and self._event_bus:
            event = Event(
                event_type=EventType.PIPELINE_CANCELLED,
                payload={"video_id": video_id},
                source="PipelineInterface",
            )
            await self._event_bus.publish(event)
        return cancelled

    def pause_download(self, video_id: int) -> bool:
        """Pause an active download (YouTube + torrent)."""
        paused = False
        with self._session_scope() as session:
            download_repo = DownloadRepository(session)
            for dl in download_repo.get_by_video_id(video_id):
                if dl.status == "downloading":
                    download_repo.update_status(dl.id, "paused")
                    paused = True
            torrent_repo = TorrentDownloadRepository(session)
            for pattern in (f"video:{video_id}", str(video_id)):
                torrent = torrent_repo.get_by_source_id(pattern)
                if torrent and torrent.status == "downloading":
                    torrent_repo.update_status(torrent.infohash, "paused")
                    paused = True
        return paused

    def resume_download(self, video_id: int) -> bool:
        """Resume a paused download (YouTube + torrent)."""
        resumed = False
        with self._session_scope() as session:
            download_repo = DownloadRepository(session)
            for dl in download_repo.get_by_video_id(video_id):
                if dl.status == "paused":
                    download_repo.update_status(dl.id, "downloading")
                    resumed = True
            torrent_repo = TorrentDownloadRepository(session)
            for pattern in (f"video:{video_id}", str(video_id)):
                torrent = torrent_repo.get_by_source_id(pattern)
                if torrent and torrent.status == "paused":
                    torrent_repo.update_status(torrent.infohash, "downloading")
                    resumed = True
        return resumed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_created_at(x: UnifiedDownload) -> datetime:
    """Return an offset-aware datetime for sorting, even if the source was naive."""
    dt = x.created_at
    if dt is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
