"""Data Access Layer for Haven TUI (Repository Pattern).

Provides repositories for querying video and pipeline data from the haven-cli database
using the table-based design. Optimized for TUI display and polling efficiency.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any, Tuple

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, case, desc, literal

from haven_cli.database.models import (
    Video, Download, EncryptionJob, UploadJob,
    SyncJob, AnalysisJob, PipelineSnapshot, SpeedHistory,
    TorrentDownload,
)
from haven_tui.models.video_view import VideoView, PipelineStage, StageInfo, StageStatus


class PipelineSnapshotRepository:
    """
    Repository for querying pipeline state via PipelineSnapshot table.

    This is the primary interface for the TUI main view - single table query
    for efficient polling.
    """

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy session
        """
        self.session = session

    def get_active_videos(self, limit: int = 1000, offset: int = 0) -> List[VideoView]:
        """Get videos currently in pipeline (not completed).

        Args:
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of VideoView objects for active videos (including failed)
        """
        query = self.session.query(PipelineSnapshot).filter(
            PipelineSnapshot.overall_status.in_(["active", "pending", "failed"])
        ).order_by(
            desc(PipelineSnapshot.stage_started_at)
        )

        if offset:
            query = query.offset(offset)
        if limit:
            query = query.limit(limit)

        snapshots = query.all()
        return [self._snapshot_to_view(s) for s in snapshots]

    def get_videos_by_stage(self, stage: PipelineStage, limit: int = 100, offset: int = 0) -> List[VideoView]:
        """Get videos in a specific pipeline stage.

        Args:
            stage: Pipeline stage to filter by
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of VideoView objects for videos in the specified stage
        """
        query = self.session.query(PipelineSnapshot).filter(
            PipelineSnapshot.current_stage == stage.value,
            PipelineSnapshot.overall_status == "active"
        ).order_by(
            desc(PipelineSnapshot.stage_started_at)
        )

        if offset:
            query = query.offset(offset)
        if limit:
            query = query.limit(limit)

        snapshots = query.all()
        return [self._snapshot_to_view(s) for s in snapshots]

    def get_video_summary(self, video_id: int) -> Optional[VideoView]:
        """Get current state for a single video.

        Args:
            video_id: Video ID to look up

        Returns:
            VideoView if found, None otherwise
        """
        snapshot = self.session.query(PipelineSnapshot).filter_by(
            video_id=video_id
        ).first()

        if snapshot:
            return self._snapshot_to_view(snapshot)
        return None

    def get_videos_by_status(self, status: str, limit: int = 100, offset: int = 0) -> List[VideoView]:
        """Get videos by overall status.

        Args:
            status: Overall status to filter by ("active", "pending", "completed", "failed")
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of VideoView objects with the specified status
        """
        query = self.session.query(PipelineSnapshot).filter(
            PipelineSnapshot.overall_status == status
        ).order_by(
            desc(PipelineSnapshot.updated_at)
        )

        if offset:
            query = query.offset(offset)
        if limit:
            query = query.limit(limit)

        snapshots = query.all()
        return [self._snapshot_to_view(s) for s in snapshots]

    def get_videos_with_errors(self, limit: int = 100, offset: int = 0) -> List[VideoView]:
        """Get videos that have errors.

        Args:
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of VideoView objects with errors
        """
        query = self.session.query(PipelineSnapshot).filter(
            PipelineSnapshot.has_error == True
        ).order_by(
            desc(PipelineSnapshot.updated_at)
        )

        if offset:
            query = query.offset(offset)
        if limit:
            query = query.limit(limit)

        snapshots = query.all()
        return [self._snapshot_to_view(s) for s in snapshots]

    def get_active_torrents_without_video(self, limit: int = 100) -> List[VideoView]:
        """Get active torrent downloads that don't have corresponding Video records.

        This method finds torrents in the torrent_downloads table that are actively
        downloading (status = 'downloading', 'paused', or 'checking') but don't have
        a matching entry in the videos table via output_path lookup.

        Args:
            limit: Maximum number of results

        Returns:
            List of VideoView objects representing torrents without video records.
            These entries use negative IDs to distinguish them from regular videos.
        """
        # Query active torrents
        active_torrents = self.session.query(TorrentDownload).filter(
            TorrentDownload.status.in_([
                "downloading", "paused", "checking", "failed"
            ])
        ).order_by(
            desc(TorrentDownload.started_at)
        ).limit(limit).all()

        # Filter out torrents that have associated videos
        orphan_torrents = []
        for torrent in active_torrents:
            # Check if this torrent has an associated video
            has_video = False
            if torrent.output_path:
                video = self.session.query(Video).filter_by(
                    source_path=torrent.output_path
                ).first()
                if video:
                    has_video = True
            
            # Also check by source_id if it looks like "video:{id}"
            if not has_video and torrent.source_id and torrent.source_id.startswith("video:"):
                try:
                    video_id = int(torrent.source_id.split(":")[1])
                    video = self.session.query(Video).filter_by(id=video_id).first()
                    if video:
                        has_video = True
                except (ValueError, IndexError):
                    pass
            
            if not has_video:
                orphan_torrents.append(torrent)

        # Convert to VideoView objects
        return [self._torrent_to_view(t) for t in orphan_torrents]

    def _torrent_to_view(self, torrent: TorrentDownload) -> VideoView:
        """Convert a TorrentDownload to a VideoView.

        This creates a placeholder VideoView for torrents that don't have
        associated Video records yet. The ID is negated to distinguish
        these from regular videos.

        Args:
            torrent: TorrentDownload model instance

        Returns:
            VideoView object representing the torrent download
        """
        # Map torrent status to overall status
        status_map = {
            "downloading": "active",
            "paused": "pending",
            "checking": "active",
            "completed": "completed",
            "failed": "failed",
            "stalled": "failed",
        }
        overall_status = status_map.get(torrent.status, "pending")

        # Calculate ETA
        eta = None
        if torrent.download_rate and torrent.download_rate > 0:
            remaining = torrent.total_size - torrent.downloaded_size
            eta = int(remaining / torrent.download_rate)

        # Use negative ID to indicate torrent-only entry (not a real video)
        # This allows the TUI to distinguish and handle them appropriately
        torrent_id = -torrent.id

        return VideoView(
            id=torrent_id,
            title=torrent.title or f"Torrent {torrent.infohash[:16]}...",
            source_path=torrent.output_path or "",
            current_stage=PipelineStage.DOWNLOAD,
            stage_progress=torrent.progress * 100,
            stage_speed=torrent.download_rate,
            stage_eta=eta,
            overall_status=overall_status,
            has_error=torrent.status == "failed" or torrent.status == "stalled",
            error_message=torrent.error_message,
            file_size=torrent.total_size,
            plugin="bittorrent",
        )

    def get_aggregate_stats(self) -> Dict[str, Any]:
        """Get stats for TUI header bar.

        Returns:
            Dictionary with active count, total speed, and stage breakdown
        """
        result = self.session.query(
            func.count().label('total_active'),
            func.sum(PipelineSnapshot.stage_speed).label('total_speed'),
            func.count(case((PipelineSnapshot.current_stage == 'download', 1))).label('downloading'),
            func.count(case((PipelineSnapshot.current_stage == 'encrypt', 1))).label('encrypting'),
            func.count(case((PipelineSnapshot.current_stage == 'upload', 1))).label('uploading'),
            func.count(case((PipelineSnapshot.current_stage == 'sync', 1))).label('syncing'),
            func.count(case((PipelineSnapshot.current_stage == 'analysis', 1))).label('analyzing'),
            func.sum(case((PipelineSnapshot.has_error == True, 1))).label('errors'),
        ).filter(
            PipelineSnapshot.overall_status == 'active'
        ).first()

        return {
            'active_count': result.total_active or 0,
            'total_speed': result.total_speed or 0,
            'by_stage': {
                'download': result.downloading or 0,
                'encrypt': result.encrypting or 0,
                'upload': result.uploading or 0,
                'sync': result.syncing or 0,
                'analyze': result.analyzing or 0,
            },
            'error_count': result.errors or 0,
        }

    def get_completed_count(self, since: Optional[datetime] = None) -> int:
        """Get count of completed videos.

        Args:
            since: Only count videos completed since this time

        Returns:
            Number of completed videos
        """
        query = self.session.query(func.count(PipelineSnapshot.id)).filter(
            PipelineSnapshot.overall_status == 'completed'
        )

        if since:
            query = query.filter(PipelineSnapshot.pipeline_completed_at >= since)

        return query.scalar() or 0

    def get_failed_count(self) -> int:
        """Get count of failed videos.

        Returns:
            Number of failed videos
        """
        return self.session.query(func.count(PipelineSnapshot.id)).filter(
            PipelineSnapshot.overall_status == 'failed'
        ).scalar() or 0

    def search_videos(self, query: str, limit: int = 50) -> List[VideoView]:
        """Search videos by title in snapshot.

        Note: This joins with Video table for title search.

        Args:
            query: Search query string
            limit: Maximum number of results

        Returns:
            List of matching VideoView objects
        """
        snapshots = self.session.query(PipelineSnapshot).join(
            Video, PipelineSnapshot.video_id == Video.id
        ).filter(
            Video.title.ilike(f'%{query}%')
        ).limit(limit).all()

        return [self._snapshot_to_view(s) for s in snapshots]

    def _snapshot_to_view(self, snapshot: PipelineSnapshot) -> VideoView:
        """Convert snapshot to TUI view model.

        Args:
            snapshot: PipelineSnapshot database object

        Returns:
            VideoView object
        """
        # Get video data, handling case where video might be None
        video_title = "Unknown"
        source_path = ""
        plugin_name = "unknown"

        if snapshot.video:
            video_title = snapshot.video.title or "Unknown"
            source_path = snapshot.video.source_path or ""
            plugin_name = snapshot.video.plugin_name or "unknown"

        # Handle unknown stages gracefully
        try:
            stage = PipelineStage(snapshot.current_stage)
        except ValueError:
            stage = PipelineStage.PENDING

        return VideoView(
            id=snapshot.video_id,
            title=video_title,
            source_path=source_path,
            current_stage=stage,
            stage_progress=snapshot.stage_progress_percent or 0,
            stage_speed=snapshot.stage_speed or 0,
            stage_eta=snapshot.stage_eta,
            overall_status=snapshot.overall_status,
            has_error=snapshot.has_error,
            error_message=snapshot.error_message,
            file_size=snapshot.total_bytes or 0,
            plugin=plugin_name,
        )


class DownloadRepository:
    """Repository for download-specific queries."""

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy session
        """
        self.session = session

    def get_active_downloads(self, limit: int = 100) -> List[Download]:
        """Get all active downloads with video info.

        Args:
            limit: Maximum number of results

        Returns:
            List of active Download objects with video loaded
        """
        return self.session.query(Download).options(
            joinedload(Download.video)
        ).filter(
            Download.status == "downloading"
        ).order_by(
            desc(Download.started_at)
        ).limit(limit).all()

    def get_download_by_video(self, video_id: int) -> Optional[Download]:
        """Get latest download record for a video.

        Args:
            video_id: Video ID to look up

        Returns:
            Most recent Download for the video, or None
        """
        return self.session.query(Download).filter_by(
            video_id=video_id
        ).order_by(
            desc(Download.created_at)
        ).first()

    def get_download_history(self, video_id: int, limit: int = 10) -> List[Download]:
        """Get download history for a video.

        Args:
            video_id: Video ID to look up
            limit: Maximum number of results

        Returns:
            List of Download objects ordered by created_at desc
        """
        return self.session.query(Download).filter_by(
            video_id=video_id
        ).order_by(
            desc(Download.created_at)
        ).limit(limit).all()

    def get_aggregate_download_speed(self) -> int:
        """Sum of all active download rates for TUI header.

        Returns:
            Total download speed in bytes/sec
        """
        result = self.session.query(
            func.sum(Download.download_rate)
        ).filter(
            Download.status == "downloading"
        ).scalar()
        return result or 0

    def get_pending_downloads(self, limit: int = 100) -> List[Download]:
        """Get downloads waiting to start.

        Args:
            limit: Maximum number of results

        Returns:
            List of pending Download objects
        """
        return self.session.query(Download).options(
            joinedload(Download.video)
        ).filter(
            Download.status == "pending"
        ).order_by(
            desc(Download.created_at)
        ).limit(limit).all()


class JobHistoryRepository:
    """For TUI detail view - get complete job history for a video."""

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy session
        """
        self.session = session

    def get_video_pipeline_history(self, video_id: int) -> Dict[str, List[Any]]:
        """Get all jobs for a video across all stages.

        Args:
            video_id: Video ID to look up

        Returns:
            Dictionary with lists of all job types
        """
        return {
            'downloads': self.session.query(Download).filter_by(
                video_id=video_id
            ).order_by(desc(Download.created_at)).all(),

            'analysis_jobs': self.session.query(AnalysisJob).filter_by(
                video_id=video_id
            ).order_by(desc(AnalysisJob.created_at)).all(),

            'encryption_jobs': self.session.query(EncryptionJob).filter_by(
                video_id=video_id
            ).order_by(desc(EncryptionJob.created_at)).all(),

            'upload_jobs': self.session.query(UploadJob).filter_by(
                video_id=video_id
            ).order_by(desc(UploadJob.created_at)).all(),

            'sync_jobs': self.session.query(SyncJob).filter_by(
                video_id=video_id
            ).order_by(desc(SyncJob.created_at)).all(),
        }

    def get_latest_cid(self, video_id: int) -> Optional[str]:
        """Get the most recent successful upload CID.

        Args:
            video_id: Video ID to look up

        Returns:
            CID string if found, None otherwise
        """
        upload = self.session.query(UploadJob).filter_by(
            video_id=video_id,
            status="completed"
        ).order_by(
            desc(UploadJob.completed_at)
        ).first()

        return upload.remote_cid if upload else None

    def is_encrypted(self, video_id: int) -> bool:
        """Check if video has completed encryption.

        Args:
            video_id: Video ID to check

        Returns:
            True if video has a completed encryption job
        """
        return self.session.query(EncryptionJob).filter_by(
            video_id=video_id,
            status="completed"
        ).first() is not None

    def get_encryption_info(self, video_id: int) -> Optional[Dict[str, Any]]:
        """Get encryption information for a video.

        Args:
            video_id: Video ID to look up

        Returns:
            Dictionary with encryption info if found, None otherwise
        """
        job = self.session.query(EncryptionJob).filter_by(
            video_id=video_id
        ).order_by(
            desc(EncryptionJob.created_at)
        ).first()

        if not job:
            return None

        return {
            'status': job.status,
            'progress': job.progress_percent,
            'encrypted_ref': job.encrypted_ref,
            'completed_at': job.completed_at.isoformat() if job.completed_at else None,
        }

    def get_upload_info(self, video_id: int) -> Optional[Dict[str, Any]]:
        """Get latest upload information for a video.

        Args:
            video_id: Video ID to look up

        Returns:
            Dictionary with upload info if found, None otherwise
        """
        job = self.session.query(UploadJob).filter_by(
            video_id=video_id
        ).order_by(
            desc(UploadJob.created_at)
        ).first()

        if not job:
            return None

        return {
            'status': job.status,
            'target': job.target,
            'remote_cid': job.remote_cid,
            'remote_url': job.remote_url,
            'progress': job.progress_percent,
            'completed_at': job.completed_at.isoformat() if job.completed_at else None,
        }

    def get_sync_info(self, video_id: int) -> Optional[Dict[str, Any]]:
        """Get latest sync information for a video.

        Args:
            video_id: Video ID to look up

        Returns:
            Dictionary with sync info if found, None otherwise
        """
        job = self.session.query(SyncJob).filter_by(
            video_id=video_id
        ).order_by(
            desc(SyncJob.created_at)
        ).first()

        if not job:
            return None

        return {
            'status': job.status,
            'tx_hash': job.tx_hash,
            'block_number': job.block_number,
            'completed_at': job.completed_at.isoformat() if job.completed_at else None,
        }

    def get_failed_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent failed jobs across all stages.

        Args:
            limit: Maximum number of results

        Returns:
            List of failed job records with stage, video_id, timestamp, and error
        """
        # Query each table for failed jobs using union
        downloads = self.session.query(
            literal("download").label("stage"),
            Download.video_id,
            Download.failed_at.label("timestamp"),
            Download.error_message
        ).filter(Download.status == "failed")

        encrypts = self.session.query(
            literal("encrypt").label("stage"),
            EncryptionJob.video_id,
            EncryptionJob.completed_at.label("timestamp"),
            EncryptionJob.error_message
        ).filter(EncryptionJob.status == "failed")

        uploads = self.session.query(
            literal("upload").label("stage"),
            UploadJob.video_id,
            UploadJob.completed_at.label("timestamp"),
            UploadJob.error_message
        ).filter(UploadJob.status == "failed")

        syncs = self.session.query(
            literal("sync").label("stage"),
            SyncJob.video_id,
            SyncJob.completed_at.label("timestamp"),
            SyncJob.error_message
        ).filter(SyncJob.status == "failed")

        analyses = self.session.query(
            literal("analyze").label("stage"),
            AnalysisJob.video_id,
            AnalysisJob.completed_at.label("timestamp"),
            AnalysisJob.error_message
        ).filter(AnalysisJob.status == "failed")

        # Union all and order by timestamp
        union_query = downloads.union(
            encrypts, uploads, syncs, analyses
        ).order_by(desc("timestamp")).limit(limit)

        results = union_query.all()

        return [
            {
                "stage": r.stage,
                "video_id": r.video_id,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "error_message": r.error_message,
            }
            for r in results
        ]


class SpeedHistoryRepository:
    """Repository for speed history queries (for graphing)."""

    def __init__(
        self,
        session: Optional[Session] = None,
        db_url: Optional[str] = None,
        max_history_seconds: int = 300,
    ):
        """Initialize repository with database session or URL.

        Args:
            session: SQLAlchemy session (optional if db_url provided)
            db_url: Database URL string (optional if session provided)
            max_history_seconds: Maximum history to keep in seconds (default 300 = 5 min)
        """
        if session is not None:
            self.session = session
        elif db_url is not None:
            # Create session from db_url
            from haven_cli.database.connection import get_db_session
            self.session = get_db_session()
            self._owns_session = True
        else:
            raise ValueError("Either session or db_url must be provided")
        
        self.max_history_seconds = max_history_seconds

    def get_speed_history(
        self,
        video_id: int,
        stage: str,
        minutes: int = 5
    ) -> List[SpeedHistory]:
        """Get speed history for graphing.

        Args:
            video_id: Video ID to look up
            stage: Stage name ("download", "encrypt", "upload")
            minutes: Time window in minutes

        Returns:
            List of SpeedHistory entries
        """
        since = datetime.now(timezone.utc) - timedelta(minutes=minutes)

        return self.session.query(SpeedHistory).filter(
            SpeedHistory.video_id == video_id,
            SpeedHistory.stage == stage,
            SpeedHistory.timestamp >= since
        ).order_by(SpeedHistory.timestamp).all()

    def get_aggregate_speeds(
        self,
        stage: Optional[str] = None,
        minutes: int = 5
    ) -> List[Dict[str, Any]]:
        """Get aggregate speeds over time for header graph.

        Args:
            stage: Filter by stage (optional)
            minutes: Time window in minutes

        Returns:
            List of aggregated speed data points
        """
        since = datetime.now(timezone.utc) - timedelta(minutes=minutes)

        query = self.session.query(
            func.strftime('%Y-%m-%d %H:%M', SpeedHistory.timestamp).label('bucket'),
            SpeedHistory.stage,
            func.avg(SpeedHistory.speed).label('avg_speed'),
            func.count().label('sample_count'),
        ).filter(
            SpeedHistory.timestamp >= since
        )

        if stage:
            query = query.filter(SpeedHistory.stage == stage)

        results = query.group_by(
            'bucket',
            SpeedHistory.stage
        ).order_by('bucket').all()

        return [
            {
                "bucket": r.bucket,
                "stage": r.stage,
                "avg_speed": r.avg_speed,
                "sample_count": r.sample_count,
            }
            for r in results
        ]

    def get_speed_trends(
        self,
        video_id: int,
        stage: str,
        interval_minutes: int = 1
    ) -> List[Dict[str, Any]]:
        """Get speed trends for detailed graphing.

        Args:
            video_id: Video ID to look up
            stage: Stage name
            interval_minutes: Aggregation interval in minutes

        Returns:
            List of trend data points
        """
        since = datetime.now(timezone.utc) - timedelta(minutes=interval_minutes * 60)

        results = self.session.query(
            func.strftime('%Y-%m-%d %H:%M', SpeedHistory.timestamp).label('time_bucket'),
            func.avg(SpeedHistory.speed).label('avg_speed'),
            func.max(SpeedHistory.speed).label('max_speed'),
            func.min(SpeedHistory.speed).label('min_speed'),
            func.avg(SpeedHistory.progress).label('avg_progress'),
        ).filter(
            SpeedHistory.video_id == video_id,
            SpeedHistory.stage == stage,
            SpeedHistory.timestamp >= since
        ).group_by(
            'time_bucket'
        ).order_by('time_bucket').all()

        return [
            {
                "time": r.time_bucket,
                "avg_speed": r.avg_speed,
                "max_speed": r.max_speed,
                "min_speed": r.min_speed,
                "avg_progress": r.avg_progress,
            }
            for r in results
        ]

    def record_speed(
        self,
        video_id: int,
        stage: str,
        speed: int,
        progress: float = 0.0,
        bytes_processed: int = 0
    ) -> SpeedHistory:
        """Record a new speed history entry.

        Args:
            video_id: Video ID
            stage: Stage name
            speed: Speed in bytes/sec
            progress: Progress percentage (0-100)
            bytes_processed: Bytes processed so far

        Returns:
            Created SpeedHistory entry
        """
        entry = SpeedHistory(
            video_id=video_id,
            stage=stage,
            speed=speed,
            progress=progress,
            bytes_processed=bytes_processed,
        )
        self.session.add(entry)
        self.session.commit()
        self.session.refresh(entry)
        return entry

    def cleanup_old_entries(self, days: int = 7) -> int:
        """Remove speed history entries older than specified days.

        Args:
            days: Number of days to keep

        Returns:
            Number of entries deleted
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = self.session.query(SpeedHistory).filter(
            SpeedHistory.timestamp < cutoff
        ).delete()
        self.session.commit()
        return result


class AnalyticsRepository:
    """Repository for analytics queries and pipeline performance metrics.
    
    Provides aggregated statistics about pipeline performance including:
    - Videos processed per day/week
    - Average time per stage
    - Success/failure rates
    - Plugin usage distribution
    - Throughput trends
    """

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy session
        """
        self.session = session

    def get_videos_per_day(self, days: int = 7) -> Dict[str, int]:
        """Get count of videos processed per day.
        
        Args:
            days: Number of days to look back (default 7)
            
        Returns:
            Dictionary mapping date strings (YYYY-MM-DD) to video counts
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)
        
        results = self.session.query(
            func.date(Video.created_at).label('date'),
            func.count().label('count')
        ).filter(
            Video.created_at >= since
        ).group_by(
            func.date(Video.created_at)
        ).all()
        
        # Build complete date range with 0 for missing days
        from collections import OrderedDict
        daily_counts = OrderedDict()
        
        # Initialize all days with 0
        for i in range(days):
            date_key = (datetime.now(timezone.utc) - timedelta(days=i)).strftime('%Y-%m-%d')
            daily_counts[date_key] = 0
        
        # Fill in actual counts
        for r in results:
            date_key = str(r.date) if r.date else None
            if date_key and date_key in daily_counts:
                daily_counts[date_key] = r.count
        
        # Return in reverse chronological order (newest first)
        return dict(reversed(list(daily_counts.items())))

    def get_avg_time_per_stage(self, days: int = 30) -> Dict[str, float]:
        """Get average time spent in each stage.
        
        Args:
            days: Number of days to look back for completed jobs (default 30)
            
        Returns:
            Dictionary mapping stage names to average time in seconds
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)
        stages = {}
        
        # Download stage
        dl_result = self.session.query(
            func.avg(
                func.julianday(Download.completed_at) - func.julianday(Download.started_at)
            ).label('avg_days')
        ).filter(
            Download.status == "completed",
            Download.completed_at != None,
            Download.started_at != None,
            Download.completed_at >= since
        ).first()
        
        # Convert from fractional days to seconds
        dl_avg = dl_result.avg_days if dl_result and dl_result.avg_days else 0
        stages["download"] = dl_avg * 86400 if dl_avg else 0
        
        # Encrypt stage
        enc_result = self.session.query(
            func.avg(
                func.julianday(EncryptionJob.completed_at) - func.julianday(EncryptionJob.started_at)
            ).label('avg_days')
        ).filter(
            EncryptionJob.status == "completed",
            EncryptionJob.completed_at != None,
            EncryptionJob.started_at != None,
            EncryptionJob.completed_at >= since
        ).first()
        
        enc_avg = enc_result.avg_days if enc_result and enc_result.avg_days else 0
        stages["encrypt"] = enc_avg * 86400 if enc_avg else 0
        
        # Upload stage
        up_result = self.session.query(
            func.avg(
                func.julianday(UploadJob.completed_at) - func.julianday(UploadJob.started_at)
            ).label('avg_days')
        ).filter(
            UploadJob.status == "completed",
            UploadJob.completed_at != None,
            UploadJob.started_at != None,
            UploadJob.completed_at >= since
        ).first()
        
        up_avg = up_result.avg_days if up_result and up_result.avg_days else 0
        stages["upload"] = up_avg * 86400 if up_avg else 0
        
        # Analysis stage
        analysis_result = self.session.query(
            func.avg(
                func.julianday(AnalysisJob.completed_at) - func.julianday(AnalysisJob.started_at)
            ).label('avg_days')
        ).filter(
            AnalysisJob.status == "completed",
            AnalysisJob.completed_at != None,
            AnalysisJob.started_at != None,
            AnalysisJob.completed_at >= since
        ).first()
        
        analysis_avg = analysis_result.avg_days if analysis_result and analysis_result.avg_days else 0
        stages["analyze"] = analysis_avg * 86400 if analysis_avg else 0
        
        # Sync stage
        sync_result = self.session.query(
            func.avg(
                func.julianday(SyncJob.completed_at) - func.julianday(SyncJob.started_at)
            ).label('avg_days')
        ).filter(
            SyncJob.status == "completed",
            SyncJob.completed_at != None,
            SyncJob.started_at != None,
            SyncJob.completed_at >= since
        ).first()
        
        sync_avg = sync_result.avg_days if sync_result and sync_result.avg_days else 0
        stages["sync"] = sync_avg * 86400 if sync_avg else 0
        
        return stages

    def get_success_rates(self, days: int = 30) -> Dict[str, Dict[str, float]]:
        """Get success/failure rates by stage.
        
        Args:
            days: Number of days to look back (default 30)
            
        Returns:
            Dictionary mapping stage names to dict with 'success_rate' and 'total'
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)
        results = {}
        
        # Define stages with their (model, name) tuples
        stages = [
            (Download, "download"),
            (EncryptionJob, "encrypt"),
            (UploadJob, "upload"),
            (AnalysisJob, "analyze"),
            (SyncJob, "sync"),
        ]
        
        for model, name in stages:
            # Total count with created_at filter
            total_query = self.session.query(func.count()).filter(
                model.created_at >= since
            )
            total = total_query.scalar() or 0
            
            # Success count (completed status)
            success_query = self.session.query(func.count()).filter(
                model.status == "completed",
                model.created_at >= since
            )
            success = success_query.scalar() or 0
            
            # Failed count
            failed_query = self.session.query(func.count()).filter(
                model.status.in_(["failed", "error"]),
                model.created_at >= since
            )
            failed = failed_query.scalar() or 0
            
            if total > 0:
                success_rate = (success / total) * 100
                failure_rate = (failed / total) * 100
            else:
                success_rate = 0.0
                failure_rate = 0.0
            
            results[name] = {
                "success_rate": success_rate,
                "failure_rate": failure_rate,
                "success": success,
                "failed": failed,
                "total": total,
            }
        
        return results

    def get_plugin_usage_distribution(self, days: int = 30) -> Dict[str, int]:
        """Get plugin usage distribution (count of videos by plugin).
        
        Args:
            days: Number of days to look back (default 30)
            
        Returns:
            Dictionary mapping plugin names to video counts
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)
        
        results = self.session.query(
            Video.plugin_name.label('plugin'),
            func.count().label('count')
        ).filter(
            Video.created_at >= since
        ).group_by(
            Video.plugin_name
        ).all()
        
        distribution = {}
        for r in results:
            plugin = r.plugin or "unknown"
            distribution[plugin] = r.count
        
        return distribution

    def get_plugin_usage_percentages(self, days: int = 30) -> Dict[str, float]:
        """Get plugin usage as percentages.
        
        Args:
            days: Number of days to look back (default 30)
            
        Returns:
            Dictionary mapping plugin names to percentage (0-100)
        """
        distribution = self.get_plugin_usage_distribution(days)
        
        total = sum(distribution.values())
        if total == 0:
            return {}
        
        return {
            plugin: (count / total) * 100
            for plugin, count in distribution.items()
        }

    def get_throughput_trends(self, days: int = 7) -> Dict[str, List[Dict[str, Any]]]:
        """Get throughput trends over time.
        
        Args:
            days: Number of days to look back (default 7)
            
        Returns:
            Dictionary mapping stage names to list of daily throughput data
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)
        
        trends = {
            "download": [],
            "encrypt": [],
            "upload": [],
            "analyze": [],
            "sync": [],
        }
        
        # Download throughput by day
        dl_results = self.session.query(
            func.date(Download.completed_at).label('date'),
            func.sum(Download.bytes_downloaded).label('bytes'),
            func.count().label('count')
        ).filter(
            Download.status == "completed",
            Download.completed_at != None,
            Download.completed_at >= since
        ).group_by(
            func.date(Download.completed_at)
        ).all()
        
        for r in dl_results:
            trends["download"].append({
                "date": str(r.date) if r.date else None,
                "bytes": r.bytes or 0,
                "count": r.count,
            })
        
        # Upload throughput by day
        up_results = self.session.query(
            func.date(UploadJob.completed_at).label('date'),
            func.sum(UploadJob.bytes_uploaded).label('bytes'),
            func.count().label('count')
        ).filter(
            UploadJob.status == "completed",
            UploadJob.completed_at != None,
            UploadJob.completed_at >= since
        ).group_by(
            func.date(UploadJob.completed_at)
        ).all()
        
        for r in up_results:
            trends["upload"].append({
                "date": str(r.date) if r.date else None,
                "bytes": r.bytes or 0,
                "count": r.count,
            })
        
        # Encrypt throughput by day (bytes processed)
        enc_results = self.session.query(
            func.date(EncryptionJob.completed_at).label('date'),
            func.sum(EncryptionJob.bytes_processed).label('bytes'),
            func.count().label('count')
        ).filter(
            EncryptionJob.status == "completed",
            EncryptionJob.completed_at != None,
            EncryptionJob.completed_at >= since
        ).group_by(
            func.date(EncryptionJob.completed_at)
        ).all()
        
        for r in enc_results:
            trends["encrypt"].append({
                "date": str(r.date) if r.date else None,
                "bytes": r.bytes or 0,
                "count": r.count,
            })
        
        return trends

    def get_pipeline_summary(self) -> Dict[str, Any]:
        """Get overall pipeline summary statistics.
        
        Returns:
            Dictionary with comprehensive pipeline statistics
        """
        # Total videos
        total_videos = self.session.query(func.count(Video.id)).scalar() or 0
        
        # Videos by status (based on pipeline snapshot)
        active_count = self.session.query(func.count(PipelineSnapshot.id)).filter(
            PipelineSnapshot.overall_status == "active"
        ).scalar() or 0
        
        completed_count = self.session.query(func.count(PipelineSnapshot.id)).filter(
            PipelineSnapshot.overall_status == "completed"
        ).scalar() or 0
        
        failed_count = self.session.query(func.count(PipelineSnapshot.id)).filter(
            PipelineSnapshot.overall_status == "failed"
        ).scalar() or 0
        
        pending_count = self.session.query(func.count(PipelineSnapshot.id)).filter(
            PipelineSnapshot.overall_status == "pending"
        ).scalar() or 0
        
        # Total data processed
        total_downloaded = self.session.query(
            func.sum(Download.bytes_downloaded)
        ).filter(
            Download.status == "completed"
        ).scalar() or 0
        
        total_uploaded = self.session.query(
            func.sum(UploadJob.bytes_uploaded)
        ).filter(
            UploadJob.status == "completed"
        ).scalar() or 0
        
        total_encrypted = self.session.query(
            func.sum(EncryptionJob.bytes_processed)
        ).filter(
            EncryptionJob.status == "completed"
        ).scalar() or 0
        
        return {
            "videos": {
                "total": total_videos,
                "active": active_count,
                "completed": completed_count,
                "failed": failed_count,
                "pending": pending_count,
            },
            "data_processed": {
                "downloaded_bytes": total_downloaded,
                "uploaded_bytes": total_uploaded,
                "encrypted_bytes": total_encrypted,
            },
            "success_rates_7d": self.get_success_rates(days=7),
            "videos_per_day_7d": self.get_videos_per_day(days=7),
        }
