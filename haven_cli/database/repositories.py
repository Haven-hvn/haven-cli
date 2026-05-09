"""Database repositories for Haven CLI.

Provides high-level data access patterns for common database operations,
including pHash-based duplicate detection and video queries.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Union
from uuid import UUID

from sqlalchemy.orm import Session, Query
from sqlalchemy import desc, func, case, literal

from haven_cli.database.models import (
    Video, RecurringJob, JobExecution, TorrentDownload,
    Download, EncryptionJob, UploadJob, SyncJob, AnalysisJob,
    PipelineSnapshot, SpeedHistory,
)
from haven_cli.media.phash import hamming_distance, calculate_hash_similarity


class VideoRepository:
    """
    Repository for video database operations.

    Provides methods for querying, creating, and updating video records,
    including pHash-based duplicate detection.
    """

    def __init__(self, session: Session):
        """
        Initialize repository with database session.

        Args:
            session: SQLAlchemy session
        """
        self.session = session

    def get_by_id(self, video_id: int) -> Optional[Video]:
        """
        Get video by ID.

        Args:
            video_id: Video ID

        Returns:
            Video if found, None otherwise
        """
        return self.session.query(Video).filter(Video.id == video_id).first()

    def get_by_source_path(self, source_path: str) -> Optional[Video]:
        """
        Get video by source path.

        Args:
            source_path: Source file path

        Returns:
            Video if found, None otherwise
        """
        return self.session.query(Video).filter(
            Video.source_path == source_path
        ).first()

    def get_by_phash(
        self,
        phash: str,
        threshold: int = 10,
    ) -> List[Video]:
        """
        Find videos with similar pHash.

        This method queries the database and filters results by Hamming distance.
        Note: For large databases, this loads all videos with pHash values.

        Args:
            phash: Perceptual hash to search for
            threshold: Maximum Hamming distance for match (default 10)

        Returns:
            List of similar videos sorted by similarity (most similar first)
        """
        # Get all videos with pHash values
        videos = self.session.query(Video).filter(
            Video.phash.isnot(None)
        ).all()

        similar_videos: List[tuple[Video, float]] = []

        for video in videos:
            if video.phash is None:
                continue

            try:
                distance = hamming_distance(phash, video.phash)
                if distance <= threshold:
                    similarity = calculate_hash_similarity(phash, video.phash)
                    similar_videos.append((video, similarity))
            except ValueError:
                # Skip invalid hashes
                continue

        # Sort by similarity (highest first)
        similar_videos.sort(key=lambda x: x[1], reverse=True)

        return [video for video, _ in similar_videos]

    def get_most_similar_by_phash(
        self,
        phash: str,
        threshold: int = 10,
    ) -> Optional[tuple[Video, float]]:
        """
        Find the most similar video by pHash.

        Args:
            phash: Perceptual hash to search for
            threshold: Maximum Hamming distance for match (default 10)

        Returns:
            Tuple of (video, similarity) if found, None otherwise
        """
        similar = self.get_by_phash(phash, threshold)

        if not similar:
            return None

        # Calculate similarity for the first (most similar) video
        similarity = calculate_hash_similarity(phash, similar[0].phash)
        return similar[0], similarity

    def is_duplicate(
        self,
        phash: str,
        exclude_id: Optional[int] = None,
        threshold: int = 10,
    ) -> bool:
        """
        Check if a video with similar pHash already exists.

        Args:
            phash: Perceptual hash to check
            exclude_id: Optional video ID to exclude from check
            threshold: Maximum Hamming distance for duplicate (default 10)

        Returns:
            True if duplicate exists, False otherwise
        """
        query = self.session.query(Video).filter(
            Video.phash.isnot(None)
        )

        if exclude_id is not None:
            query = query.filter(Video.id != exclude_id)

        videos = query.all()

        for video in videos:
            if video.phash is None:
                continue

            try:
                distance = hamming_distance(phash, video.phash)
                if distance <= threshold:
                    return True
            except ValueError:
                continue

        return False

    def find_duplicates(
        self,
        phash: str,
        exclude_id: Optional[int] = None,
        threshold: int = 10,
    ) -> List[Video]:
        """
        Find all videos with similar pHash (duplicates).

        Args:
            phash: Perceptual hash to check
            exclude_id: Optional video ID to exclude from results
            threshold: Maximum Hamming distance for duplicate (default 10)

        Returns:
            List of duplicate videos sorted by similarity
        """
        query = self.session.query(Video).filter(
            Video.phash.isnot(None)
        )

        if exclude_id is not None:
            query = query.filter(Video.id != exclude_id)

        videos = query.all()

        similar_videos: List[tuple[Video, float]] = []

        for video in videos:
            if video.phash is None:
                continue

            try:
                distance = hamming_distance(phash, video.phash)
                if distance <= threshold:
                    similarity = calculate_hash_similarity(phash, video.phash)
                    similar_videos.append((video, similarity))
            except ValueError:
                continue

        # Sort by similarity (highest first)
        similar_videos.sort(key=lambda x: x[1], reverse=True)

        return [video for video, _ in similar_videos]

    def create(self, **kwargs) -> Video:
        """
        Create a new video record.

        Args:
            **kwargs: Video attributes

        Returns:
            Created video instance
        """
        video = Video(**kwargs)
        self.session.add(video)
        self.session.commit()
        self.session.refresh(video)
        return video

    def update(self, video: Video, **kwargs) -> Video:
        """
        Update a video record.

        Args:
            video: Video instance to update
            **kwargs: Attributes to update

        Returns:
            Updated video instance
        """
        for key, value in kwargs.items():
            if hasattr(video, key):
                setattr(video, key, value)

        self.session.commit()
        self.session.refresh(video)
        return video

    def delete(self, video: Video) -> None:
        """
        Delete a video record.

        Args:
            video: Video instance to delete
        """
        self.session.delete(video)
        self.session.commit()

    def get_all(
        self,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Video]:
        """
        Get all videos with optional pagination.

        Args:
            limit: Maximum number of videos to return
            offset: Number of videos to skip

        Returns:
            List of videos
        """
        query = self.session.query(Video).order_by(Video.created_at.desc())

        if offset:
            query = query.offset(offset)
        if limit:
            query = query.limit(limit)

        return query.all()

    def count(self) -> int:
        """
        Get total number of videos.

        Returns:
            Video count
        """
        return self.session.query(Video).count()

    def get_by_cid(self, cid: str) -> Optional[Video]:
        """
        Get video by Filecoin CID.

        Args:
            cid: Content identifier

        Returns:
            Video if found, None otherwise
        """
        return self.session.query(Video).filter(Video.cid == cid).first()

    def get_by_arkiv_key(self, arkiv_key: str) -> Optional[Video]:
        """
        Get video by Arkiv entity key.

        Args:
            arkiv_key: Arkiv blockchain entity key

        Returns:
            Video if found, None otherwise
        """
        return self.session.query(Video).filter(
            Video.arkiv_entity_key == arkiv_key
        ).first()

    def get_by_plugin_source(
        self,
        plugin_name: str,
        source_id: str,
    ) -> Optional[Video]:
        """
        Get video by plugin source.

        Args:
            plugin_name: Plugin name
            source_id: Plugin-specific source ID

        Returns:
            Video if found, None otherwise
        """
        return self.session.query(Video).filter(
            Video.plugin_name == plugin_name,
            Video.plugin_source_id == source_id,
        ).first()

    def get_pending_uploads(self) -> List[Video]:
        """
        Get videos that haven't been uploaded to Filecoin yet.

        Returns:
            List of videos without CID
        """
        return self.session.query(Video).filter(
            Video.cid.is_(None)
        ).order_by(Video.created_at.asc()).all()

    def get_encrypted_videos(self) -> List[Video]:
        """
        Get all encrypted videos.

        Returns:
            List of encrypted videos
        """
        return self.session.query(Video).filter(
            Video.encrypted.is_(True)
        ).all()


class JobRepository:
    """
    Repository for scheduled job persistence.
    
    Provides CRUD operations for recurring jobs, including
    loading and saving job definitions to the database.
    """

    def __init__(self, session: Session):
        """
        Initialize repository with database session.

        Args:
            session: SQLAlchemy session
        """
        self.session = session

    def create(
        self,
        job_id: UUID,
        name: str,
        plugin_name: str,
        schedule: str,
        on_success: str = "archive_new",
        enabled: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
        next_run: Optional[datetime] = None,
    ) -> RecurringJob:
        """
        Create a new job in database.

        Args:
            job_id: Unique job identifier
            name: Human-readable job name
            plugin_name: Name of the plugin to execute
            schedule: Cron expression for scheduling
            on_success: Action to take on successful discovery
            enabled: Whether the job is enabled
            metadata: Additional job metadata
            next_run: Next scheduled run time

        Returns:
            Created RecurringJob instance
        """
        db_job = RecurringJob(
            job_id=str(job_id),
            name=name,
            plugin_name=plugin_name,
            schedule=schedule,
            on_success=on_success,
            enabled=enabled,
            job_metadata=metadata or {},
            next_run=next_run,
            run_count=0,
            error_count=0,
        )
        self.session.add(db_job)
        self.session.commit()
        self.session.refresh(db_job)
        return db_job

    def get_by_id(self, job_id: UUID) -> Optional[RecurringJob]:
        """
        Get a job by its UUID.

        Args:
            job_id: Job UUID

        Returns:
            RecurringJob if found, None otherwise
        """
        return self.session.query(RecurringJob).filter(
            RecurringJob.job_id == str(job_id)
        ).first()

    def get_all(self) -> List[RecurringJob]:
        """
        Get all jobs from database.

        Returns:
            List of all recurring jobs
        """
        return self.session.query(RecurringJob).all()

    def get_enabled(self) -> List[RecurringJob]:
        """
        Get all enabled jobs.

        Returns:
            List of enabled recurring jobs
        """
        return self.session.query(RecurringJob).filter(
            RecurringJob.enabled.is_(True)
        ).all()

    def update(self, job_id: UUID, **kwargs) -> Optional[RecurringJob]:
        """
        Update a job.

        Args:
            job_id: UUID of the job to update
            **kwargs: Attributes to update

        Returns:
            Updated RecurringJob or None if not found
        """
        db_job = self.get_by_id(job_id)
        if not db_job:
            return None

        # Map kwargs to model attributes
        field_mapping = {
            "metadata": "job_metadata",
        }

        for key, value in kwargs.items():
            # Use mapped field name if available
            attr_name = field_mapping.get(key, key)
            if hasattr(db_job, attr_name):
                setattr(db_job, attr_name, value)

        self.session.commit()
        self.session.refresh(db_job)
        return db_job

    def delete(self, job_id: UUID) -> bool:
        """
        Delete a job.

        Args:
            job_id: UUID of the job to delete

        Returns:
            True if deleted, False if not found
        """
        db_job = self.get_by_id(job_id)
        if not db_job:
            return False

        self.session.delete(db_job)
        self.session.commit()
        return True

    def update_stats(
        self,
        job_id: UUID,
        last_run: Optional[datetime] = None,
        next_run: Optional[datetime] = None,
        increment_run: bool = False,
        increment_error: bool = False,
    ) -> Optional[RecurringJob]:
        """
        Update job execution statistics.

        Args:
            job_id: UUID of the job
            last_run: Last run timestamp
            next_run: Next run timestamp
            increment_run: Whether to increment run_count
            increment_error: Whether to increment error_count

        Returns:
            Updated RecurringJob or None if not found
        """
        db_job = self.get_by_id(job_id)
        if not db_job:
            return None

        if last_run is not None:
            db_job.last_run = last_run
        if next_run is not None:
            db_job.next_run = next_run
        if increment_run:
            db_job.run_count += 1
        if increment_error:
            db_job.error_count += 1

        self.session.commit()
        self.session.refresh(db_job)
        return db_job


class JobExecutionRepository:
    """
    Repository for job execution history.
    
    Provides methods for recording and querying job executions.
    """

    def __init__(self, session: Session):
        """
        Initialize repository with database session.

        Args:
            session: SQLAlchemy session
        """
        self.session = session

    def create(
        self,
        job_id: UUID,
        plugin_name: str,
        started_at: datetime,
        completed_at: Optional[datetime] = None,
        success: bool = False,
        sources_found: int = 0,
        sources_archived: int = 0,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        scheduled_job_id: Optional[int] = None,
    ) -> JobExecution:
        """
        Record a job execution.

        Args:
            job_id: UUID of the job
            plugin_name: Name of the plugin that was executed
            started_at: When execution started
            completed_at: When execution completed
            success: Whether execution succeeded
            sources_found: Number of sources discovered
            sources_archived: Number of sources archived
            error: Error message if failed
            metadata: Additional execution metadata
            scheduled_job_id: Foreign key to scheduled job (optional)

        Returns:
            Created JobExecution instance
        """
        execution = JobExecution(
            job_id=str(job_id),
            plugin_name=plugin_name,
            started_at=started_at,
            completed_at=completed_at,
            success=success,
            sources_found=sources_found,
            sources_archived=sources_archived,
            error=error,
            execution_metadata=metadata,
            scheduled_job_id=scheduled_job_id,
        )
        self.session.add(execution)
        self.session.commit()
        self.session.refresh(execution)
        return execution

    def get_by_id(self, execution_id: int) -> Optional[JobExecution]:
        """
        Get an execution by its ID.

        Args:
            execution_id: Execution ID

        Returns:
            JobExecution if found, None otherwise
        """
        return self.session.query(JobExecution).filter(
            JobExecution.id == execution_id
        ).first()

    def get_history(
        self,
        job_id: Optional[UUID] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> List[JobExecution]:
        """
        Get execution history.

        Args:
            job_id: Filter by job ID (optional)
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of job executions ordered by started_at descending
        """
        query = self.session.query(JobExecution).order_by(
            desc(JobExecution.started_at)
        )

        if job_id:
            query = query.filter(JobExecution.job_id == str(job_id))

        return query.offset(offset).limit(limit).all()

    def get_recent_executions(
        self,
        limit: int = 100,
    ) -> List[JobExecution]:
        """
        Get recent executions across all jobs.

        Args:
            limit: Maximum number of results

        Returns:
            List of recent job executions
        """
        return self.session.query(JobExecution).order_by(
            desc(JobExecution.started_at)
        ).limit(limit).all()

    def get_success_count(self, job_id: Optional[UUID] = None) -> int:
        """
        Get count of successful executions.

        Args:
            job_id: Filter by job ID (optional)

        Returns:
            Number of successful executions
        """
        query = self.session.query(JobExecution).filter(
            JobExecution.success.is_(True)
        )
        if job_id:
            query = query.filter(JobExecution.job_id == str(job_id))
        return query.count()

    def get_failure_count(self, job_id: Optional[UUID] = None) -> int:
        """
        Get count of failed executions.

        Args:
            job_id: Filter by job ID (optional)

        Returns:
            Number of failed executions
        """
        query = self.session.query(JobExecution).filter(
            JobExecution.success.is_(False)
        )
        if job_id:
            query = query.filter(JobExecution.job_id == str(job_id))
        return query.count()

    def delete_old_executions(self, before: datetime) -> int:
        """
        Delete executions older than a given date.

        Args:
            before: Delete executions started before this time

        Returns:
            Number of executions deleted
        """
        result = self.session.query(JobExecution).filter(
            JobExecution.started_at < before
        ).delete()
        self.session.commit()
        return result


class TorrentDownloadRepository:
    """
    Repository for BitTorrent download state persistence.
    
    Provides CRUD operations for torrent downloads, including
    resume capability and status tracking.
    """

    def __init__(self, session: Session):
        """
        Initialize repository with database session.

        Args:
            session: SQLAlchemy session
        """
        self.session = session

    def create(
        self,
        infohash: str,
        source_id: str,
        title: Optional[str] = None,
        magnet_uri: Optional[str] = None,
        status: str = "downloading",
        total_size: int = 0,
        output_path: Optional[str] = None,
        selected_file_index: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> TorrentDownload:
        """
        Create a new torrent download record.

        Args:
            infohash: Torrent infohash (unique identifier)
            source_id: MediaSource source_id
            title: Torrent title
            magnet_uri: Magnet URI
            status: Download status
            total_size: Total size in bytes
            output_path: Path to save the downloaded file
            selected_file_index: Index of the file being downloaded
            metadata: Additional metadata
            error_message: Error message if status is 'skipped' or 'failed'

        Returns:
            Created TorrentDownload instance
        """
        download = TorrentDownload(
            infohash=infohash,
            source_id=source_id,
            title=title,
            magnet_uri=magnet_uri,
            status=status,
            total_size=total_size,
            output_path=output_path,
            selected_file_index=selected_file_index,
            download_metadata=metadata or {},
            error_message=error_message,
        )
        self.session.add(download)
        self.session.commit()
        self.session.refresh(download)
        return download

    def get_by_id(self, download_id: int) -> Optional[TorrentDownload]:
        """
        Get a torrent download by its ID.

        Args:
            download_id: Download ID

        Returns:
            TorrentDownload if found, None otherwise
        """
        return self.session.query(TorrentDownload).filter(
            TorrentDownload.id == download_id
        ).first()

    def get_by_infohash(self, infohash: str) -> Optional[TorrentDownload]:
        """
        Get a torrent download by its infohash.

        Args:
            infohash: Torrent infohash

        Returns:
            TorrentDownload if found, None otherwise
        """
        return self.session.query(TorrentDownload).filter(
            TorrentDownload.infohash == infohash
        ).first()

    def get_by_source_id(self, source_id: str) -> Optional[TorrentDownload]:
        """
        Get a torrent download by its source ID.

        Args:
            source_id: MediaSource source_id

        Returns:
            TorrentDownload if found, None otherwise
        """
        return self.session.query(TorrentDownload).filter(
            TorrentDownload.source_id == source_id
        ).first()

    def get_active(self) -> List[TorrentDownload]:
        """
        Get all active (downloading or paused) torrent downloads.

        Returns:
            List of active torrent downloads
        """
        return self.session.query(TorrentDownload).filter(
            TorrentDownload.status.in_(["downloading", "paused"])
        ).order_by(TorrentDownload.started_at.desc()).all()

    def get_by_status(self, status: str) -> List[TorrentDownload]:
        """
        Get torrent downloads by status.

        Args:
            status: Download status to filter by

        Returns:
            List of torrent downloads with the given status
        """
        return self.session.query(TorrentDownload).filter(
            TorrentDownload.status == status
        ).order_by(TorrentDownload.started_at.desc()).all()

    def update(
        self,
        infohash: str,
        **kwargs
    ) -> Optional[TorrentDownload]:
        """
        Update a torrent download.

        Args:
            infohash: Infohash of the torrent to update
            **kwargs: Attributes to update

        Returns:
            Updated TorrentDownload or None if not found
        """
        download = self.get_by_infohash(infohash)
        if not download:
            return None

        # Map kwargs to model attributes
        field_mapping = {
            "metadata": "download_metadata",
        }

        for key, value in kwargs.items():
            # Use mapped field name if available
            attr_name = field_mapping.get(key, key)
            if hasattr(download, attr_name):
                setattr(download, attr_name, value)

        self.session.commit()
        self.session.refresh(download)
        return download

    def update_progress(
        self,
        infohash: str,
        progress: float,
        download_rate: int = 0,
        upload_rate: int = 0,
        peers: int = 0,
        seeds: int = 0,
        downloaded_size: int = 0,
    ) -> Optional[TorrentDownload]:
        """
        Update download progress.

        Args:
            infohash: Torrent infohash
            progress: Download progress (0.0 - 1.0)
            download_rate: Download rate in bytes/sec
            upload_rate: Upload rate in bytes/sec
            peers: Number of connected peers
            seeds: Number of connected seeds
            downloaded_size: Downloaded size in bytes

        Returns:
            Updated TorrentDownload or None if not found
        """
        from datetime import datetime, timezone

        download = self.get_by_infohash(infohash)
        if not download:
            return None

        download.progress = progress
        download.download_rate = download_rate
        download.upload_rate = upload_rate
        download.peers = peers
        download.seeds = seeds
        download.downloaded_size = downloaded_size
        download.last_activity = datetime.now(timezone.utc)

        self.session.commit()
        self.session.refresh(download)
        return download

    def update_status(
        self,
        infohash: str,
        status: str,
        error_message: Optional[str] = None,
    ) -> Optional[TorrentDownload]:
        """
        Update download status.

        Args:
            infohash: Torrent infohash
            status: New status
            error_message: Error message if status is 'failed'

        Returns:
            Updated TorrentDownload or None if not found
        """
        from datetime import datetime, timezone

        download = self.get_by_infohash(infohash)
        if not download:
            return None

        download.status = status
        if error_message:
            download.error_message = error_message
        if status == "completed":
            download.completed_at = datetime.now(timezone.utc)
        download.last_activity = datetime.now(timezone.utc)

        self.session.commit()
        self.session.refresh(download)
        return download

    def update_resume_data(
        self,
        infohash: str,
        resume_data: str,
    ) -> Optional[TorrentDownload]:
        """
        Update resume data for fast resume.

        Args:
            infohash: Torrent infohash
            resume_data: Base64-encoded resume data

        Returns:
            Updated TorrentDownload or None if not found
        """
        download = self.get_by_infohash(infohash)
        if not download:
            return None

        download.resume_data = resume_data
        self.session.commit()
        self.session.refresh(download)
        return download

    def delete(self, infohash: str) -> bool:
        """
        Delete a torrent download record.

        Args:
            infohash: Infohash of the torrent to delete

        Returns:
            True if deleted, False if not found
        """
        download = self.get_by_infohash(infohash)
        if not download:
            return False

        self.session.delete(download)
        self.session.commit()
        return True

    def get_all(
        self,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[TorrentDownload]:
        """
        Get all torrent downloads with optional pagination.

        Args:
            limit: Maximum number of downloads to return
            offset: Number of downloads to skip

        Returns:
            List of torrent downloads
        """
        query = self.session.query(TorrentDownload).order_by(
            TorrentDownload.created_at.desc()
        )

        if offset:
            query = query.offset(offset)
        if limit:
            query = query.limit(limit)

        return query.all()

    def get_stalled(
        self,
        stall_timeout_seconds: int = 300,
    ) -> List[TorrentDownload]:
        """
        Get downloads that may be stalled (no recent activity).

        Args:
            stall_timeout_seconds: Seconds of inactivity to consider stalled

        Returns:
            List of potentially stalled downloads
        """
        from datetime import datetime, timezone, timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stall_timeout_seconds)
        return self.session.query(TorrentDownload).filter(
            TorrentDownload.status == "downloading",
            TorrentDownload.last_activity < cutoff
        ).all()


class DownloadRepository:
    """
    Repository for download operations.
    
    Provides CRUD operations for download records and queries
    for active downloads and download history.
    """
    
    def __init__(self, session: Session):
        """
        Initialize repository with database session.
        
        Args:
            session: SQLAlchemy session
        """
        self.session = session
    
    def create(
        self,
        video_id: int,
        source_type: str,
        status: str = "pending",
        source_metadata: Optional[Dict[str, Any]] = None,
    ) -> Download:
        """
        Create a new download record.
        
        Args:
            video_id: Video ID
            source_type: "youtube" or "torrent"
            status: Initial status
            source_metadata: Source-specific metadata
            
        Returns:
            Created Download instance
        """
        download = Download(
            video_id=video_id,
            source_type=source_type,
            status=status,
            source_metadata=source_metadata or {},
        )
        self.session.add(download)
        self.session.commit()
        self.session.refresh(download)
        return download
    
    def get_by_id(self, download_id: int) -> Optional[Download]:
        """
        Get download by ID.
        
        Args:
            download_id: Download ID
            
        Returns:
            Download if found, None otherwise
        """
        return self.session.query(Download).filter(Download.id == download_id).first()
    
    def get_by_video_id(self, video_id: int) -> List[Download]:
        """
        Get all downloads for a video.
        
        Args:
            video_id: Video ID
            
        Returns:
            List of downloads ordered by created_at desc
        """
        return self.session.query(Download).filter(
            Download.video_id == video_id
        ).order_by(desc(Download.created_at)).all()
    
    def get_active_downloads(self) -> List[Download]:
        """
        Get all active downloads with video info.
        
        Returns:
            List of downloads with status "downloading"
        """
        return self.session.query(Download).filter(
            Download.status == "downloading"
        ).order_by(desc(Download.started_at)).all()
    
    def get_download_history(self, video_id: int, limit: int = 10) -> List[Download]:
        """
        Get download attempts for a video.
        
        Args:
            video_id: Video ID
            limit: Maximum number of results
            
        Returns:
            List of download attempts
        """
        return self.session.query(Download).filter(
            Download.video_id == video_id
        ).order_by(desc(Download.created_at)).limit(limit).all()
    
    def get_aggregate_download_speed(self) -> int:
        """
        Sum of all active download rates for TUI header.
        
        Returns:
            Total download speed in bytes/sec
        """
        result = self.session.query(
            func.sum(Download.download_rate)
        ).filter(
            Download.status == "downloading"
        ).scalar()
        return result or 0
    
    def update_progress(
        self,
        download_id: int,
        bytes_downloaded: int,
        bytes_total: Optional[int] = None,
        download_rate: Optional[int] = None,
        eta_seconds: Optional[int] = None,
    ) -> Optional[Download]:
        """
        Update download progress.
        
        Args:
            download_id: Download ID
            bytes_downloaded: Bytes downloaded so far
            bytes_total: Total bytes to download
            download_rate: Download rate in bytes/sec
            eta_seconds: Estimated time remaining in seconds
            
        Returns:
            Updated Download or None if not found
        """
        download = self.get_by_id(download_id)
        if not download:
            return None
        
        download.bytes_downloaded = bytes_downloaded
        if bytes_total is not None:
            download.bytes_total = bytes_total
        if download_rate is not None:
            download.download_rate = download_rate
        if eta_seconds is not None:
            download.eta_seconds = eta_seconds
        
        if download.bytes_total and download.bytes_total > 0:
            download.progress_percent = (bytes_downloaded / download.bytes_total) * 100
        
        self.session.commit()
        self.session.refresh(download)
        return download
    
    def update_status(
        self,
        download_id: int,
        status: str,
        error_message: Optional[str] = None,
    ) -> Optional[Download]:
        """
        Update download status.
        
        Args:
            download_id: Download ID
            status: New status
            error_message: Error message if status is 'failed'
            
        Returns:
            Updated Download or None if not found
        """
        from datetime import timezone
        
        download = self.get_by_id(download_id)
        if not download:
            return None
        
        download.status = status
        
        if status == "downloading" and not download.started_at:
            download.started_at = datetime.now(timezone.utc)
        elif status == "completed":
            download.completed_at = datetime.now(timezone.utc)
        elif status == "failed":
            download.failed_at = datetime.now(timezone.utc)
            download.error_message = error_message
        
        self.session.commit()
        self.session.refresh(download)
        return download


class EncryptionJobRepository:
    """
    Repository for encryption job operations.
    """
    
    def __init__(self, session: Session):
        self.session = session
    
    def create(
        self,
        video_id: int,
        status: str = "pending",
        bytes_total: Optional[int] = None,
    ) -> EncryptionJob:
        """Create a new encryption job."""
        job = EncryptionJob(
            video_id=video_id,
            status=status,
            bytes_total=bytes_total,
        )
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job
    
    def get_by_id(self, job_id: int) -> Optional[EncryptionJob]:
        """Get encryption job by ID."""
        return self.session.query(EncryptionJob).filter(
            EncryptionJob.id == job_id
        ).first()
    
    def get_by_video_id(self, video_id: int) -> List[EncryptionJob]:
        """Get all encryption jobs for a video."""
        return self.session.query(EncryptionJob).filter(
            EncryptionJob.video_id == video_id
        ).order_by(desc(EncryptionJob.created_at)).all()
    
    def get_active_jobs(self) -> List[EncryptionJob]:
        """Get all active encryption jobs."""
        return self.session.query(EncryptionJob).filter(
            EncryptionJob.status == "encrypting"
        ).all()
    
    def update_progress(
        self,
        job_id: int,
        bytes_processed: int,
        encrypt_speed: Optional[int] = None,
    ) -> Optional[EncryptionJob]:
        """Update encryption progress."""
        job = self.get_by_id(job_id)
        if not job:
            return None
        
        job.bytes_processed = bytes_processed
        if encrypt_speed is not None:
            job.encrypt_speed = encrypt_speed
        if job.bytes_total and job.bytes_total > 0:
            job.progress_percent = (bytes_processed / job.bytes_total) * 100
        
        self.session.commit()
        self.session.refresh(job)
        return job
    
    def update_status(
        self,
        job_id: int,
        status: str,
        error_message: Optional[str] = None,
        encrypted_ref: Optional[str] = None,
    ) -> Optional[EncryptionJob]:
        """Update encryption job status."""
        from datetime import timezone
        
        job = self.get_by_id(job_id)
        if not job:
            return None
        
        job.status = status
        
        if status == "encrypting" and not job.started_at:
            job.started_at = datetime.now(timezone.utc)
        elif status == "completed":
            job.completed_at = datetime.now(timezone.utc)
            if encrypted_ref:
                job.encrypted_ref = encrypted_ref
        elif status == "failed":
            job.error_message = error_message
        
        self.session.commit()
        self.session.refresh(job)
        return job


class UploadJobRepository:
    """
    Repository for upload job operations.
    """
    
    def __init__(self, session: Session):
        self.session = session
    
    def create(
        self,
        video_id: int,
        target: str,
        status: str = "pending",
        bytes_total: Optional[int] = None,
    ) -> UploadJob:
        """Create a new upload job."""
        job = UploadJob(
            video_id=video_id,
            target=target,
            status=status,
            bytes_total=bytes_total,
        )
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job
    
    def get_by_id(self, job_id: int) -> Optional[UploadJob]:
        """Get upload job by ID."""
        return self.session.query(UploadJob).filter(UploadJob.id == job_id).first()
    
    def get_by_video_id(self, video_id: int) -> List[UploadJob]:
        """Get all upload jobs for a video."""
        return self.session.query(UploadJob).filter(
            UploadJob.video_id == video_id
        ).order_by(desc(UploadJob.created_at)).all()
    
    def get_active_uploads(self) -> List[UploadJob]:
        """Get all active uploads."""
        return self.session.query(UploadJob).filter(
            UploadJob.status == "uploading"
        ).all()
    
    def update_progress(
        self,
        job_id: int,
        bytes_uploaded: int,
        upload_speed: Optional[int] = None,
        stage: Optional[str] = None,
    ) -> Optional[UploadJob]:
        """Update upload progress.
        
        Args:
            job_id: Job ID
            bytes_uploaded: Bytes uploaded so far (actual network bytes, 0 during prep)
            upload_speed: Upload speed in bytes/sec (0 during prep phase)
            stage: Upload substage ("connecting", "preparing", "uploading", "confirming")
        """
        job = self.get_by_id(job_id)
        if not job:
            return None
        
        job.bytes_uploaded = bytes_uploaded
        if upload_speed is not None:
            job.upload_speed = upload_speed
        if stage is not None:
            job.stage = stage
        if job.bytes_total and job.bytes_total > 0:
            job.progress_percent = (bytes_uploaded / job.bytes_total) * 100
        
        self.session.commit()
        self.session.refresh(job)
        return job
    
    def complete_upload(
        self,
        job_id: int,
        remote_cid: str,
        remote_url: Optional[str] = None,
    ) -> Optional[UploadJob]:
        """Mark upload as completed."""
        from datetime import timezone
        
        job = self.get_by_id(job_id)
        if not job:
            return None
        
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        job.remote_cid = remote_cid
        if remote_url:
            job.remote_url = remote_url
        
        self.session.commit()
        self.session.refresh(job)
        return job


class SyncJobRepository:
    """
    Repository for blockchain sync job operations.
    """
    
    def __init__(self, session: Session):
        self.session = session
    
    def create(
        self,
        video_id: int,
        status: str = "pending",
    ) -> SyncJob:
        """Create a new sync job."""
        job = SyncJob(
            video_id=video_id,
            status=status,
        )
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job
    
    def get_by_id(self, job_id: int) -> Optional[SyncJob]:
        """Get sync job by ID."""
        return self.session.query(SyncJob).filter(SyncJob.id == job_id).first()
    
    def get_by_video_id(self, video_id: int) -> List[SyncJob]:
        """Get all sync jobs for a video."""
        return self.session.query(SyncJob).filter(
            SyncJob.video_id == video_id
        ).order_by(desc(SyncJob.created_at)).all()
    
    def get_active_syncs(self) -> List[SyncJob]:
        """Get all active sync jobs."""
        return self.session.query(SyncJob).filter(
            SyncJob.status == "syncing"
        ).all()
    
    def complete_sync(
        self,
        job_id: int,
        tx_hash: str,
        block_number: int,
        gas_used: Optional[int] = None,
    ) -> Optional[SyncJob]:
        """Mark sync as completed."""
        from datetime import timezone
        
        job = self.get_by_id(job_id)
        if not job:
            return None
        
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        job.tx_hash = tx_hash
        job.block_number = block_number
        if gas_used is not None:
            job.gas_used = gas_used
        
        self.session.commit()
        self.session.refresh(job)
        return job


class AnalysisJobRepository:
    """
    Repository for VLM/LLM analysis job operations.
    """
    
    def __init__(self, session: Session):
        self.session = session
    
    def create(
        self,
        video_id: int,
        analysis_type: str,
        model_name: Optional[str] = None,
        status: str = "pending",
        frames_total: Optional[int] = None,
    ) -> AnalysisJob:
        """Create a new analysis job."""
        job = AnalysisJob(
            video_id=video_id,
            analysis_type=analysis_type,
            model_name=model_name,
            status=status,
            frames_total=frames_total,
        )
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job
    
    def get_by_id(self, job_id: int) -> Optional[AnalysisJob]:
        """Get analysis job by ID."""
        return self.session.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
    
    def get_by_video_id(self, video_id: int) -> List[AnalysisJob]:
        """Get all analysis jobs for a video."""
        return self.session.query(AnalysisJob).filter(
            AnalysisJob.video_id == video_id
        ).order_by(desc(AnalysisJob.created_at)).all()
    
    def get_active_analyses(self) -> List[AnalysisJob]:
        """Get all active analysis jobs."""
        return self.session.query(AnalysisJob).filter(
            AnalysisJob.status == "analyzing"
        ).all()
    
    def update_progress(
        self,
        job_id: int,
        frames_processed: int,
    ) -> Optional[AnalysisJob]:
        """Update analysis progress."""
        job = self.get_by_id(job_id)
        if not job:
            return None
        
        job.frames_processed = frames_processed
        if job.frames_total and job.frames_total > 0:
            job.progress_percent = (frames_processed / job.frames_total) * 100
        
        self.session.commit()
        self.session.refresh(job)
        return job
    
    def complete_analysis(
        self,
        job_id: int,
        output_file: Optional[str] = None,
    ) -> Optional[AnalysisJob]:
        """Mark analysis as completed."""
        from datetime import timezone
        
        job = self.get_by_id(job_id)
        if not job:
            return None
        
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        if output_file:
            job.output_file = output_file
        
        self.session.commit()
        self.session.refresh(job)
        return job


class PipelineSnapshotRepository:
    """
    Repository for pipeline snapshot operations.
    
    Provides optimized queries for TUI to get aggregate pipeline state
    without expensive joins across multiple job tables.
    """
    
    def __init__(self, session: Session):
        self.session = session
    
    def get_or_create(self, video_id: int) -> PipelineSnapshot:
        """
        Get existing snapshot or create new one.
        
        Args:
            video_id: Video ID
            
        Returns:
            PipelineSnapshot instance
        """
        snapshot = self.session.query(PipelineSnapshot).filter(
            PipelineSnapshot.video_id == video_id
        ).first()
        
        if not snapshot:
            snapshot = PipelineSnapshot(
                video_id=video_id,
                current_stage="pending",
                overall_status="pending",
            )
            self.session.add(snapshot)
            self.session.commit()
            self.session.refresh(snapshot)
        
        return snapshot
    
    def get_by_video_id(self, video_id: int) -> Optional[PipelineSnapshot]:
        """Get snapshot for a specific video."""
        return self.session.query(PipelineSnapshot).filter(
            PipelineSnapshot.video_id == video_id
        ).first()
    
    def get_active_videos(self, limit: int = 100) -> List[PipelineSnapshot]:
        """
        Get videos currently in progress - main TUI view.
        
        Args:
            limit: Maximum number of results
            
        Returns:
            List of active pipeline snapshots
        """
        return self.session.query(PipelineSnapshot).filter(
            PipelineSnapshot.overall_status.in_(["active", "pending"])
        ).order_by(
            desc(PipelineSnapshot.stage_started_at)
        ).limit(limit).all()
    
    def get_video_summary(self, video_id: int) -> Optional[PipelineSnapshot]:
        """
        Get current state for a single video.
        
        Args:
            video_id: Video ID
            
        Returns:
            PipelineSnapshot if found
        """
        return self.get_by_video_id(video_id)
    
    def get_aggregate_stats(self) -> Dict[str, Any]:
        """
        Get stats for TUI header bar.
        
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
            func.count(case((PipelineSnapshot.current_stage == 'analyze', 1))).label('analyzing'),
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
            }
        }
    
    def update_stage(
        self,
        video_id: int,
        stage: str,
        status: str,
        progress_percent: Optional[float] = None,
        stage_speed: Optional[int] = None,
        stage_eta: Optional[int] = None,
    ) -> PipelineSnapshot:
        """
        Update the current stage for a video.
        
        Args:
            video_id: Video ID
            stage: Current stage name
            status: Overall status
            progress_percent: Stage progress (0-100)
            stage_speed: Speed in bytes/sec
            stage_eta: ETA in seconds
            
        Returns:
            Updated PipelineSnapshot
        """
        from datetime import timezone
        
        snapshot = self.get_or_create(video_id)
        
        snapshot.current_stage = stage
        snapshot.overall_status = status
        
        if progress_percent is not None:
            snapshot.stage_progress_percent = progress_percent
        if stage_speed is not None:
            snapshot.stage_speed = stage_speed
        if stage_eta is not None:
            snapshot.stage_eta = stage_eta
        
        if not snapshot.stage_started_at or snapshot.current_stage != stage:
            snapshot.stage_started_at = datetime.now(timezone.utc)
        
        if not snapshot.pipeline_started_at:
            snapshot.pipeline_started_at = datetime.now(timezone.utc)
        
        self.session.commit()
        self.session.refresh(snapshot)
        return snapshot
    
    def update_bytes_metrics(
        self,
        video_id: int,
        total_bytes: Optional[int] = None,
        downloaded_bytes: Optional[int] = None,
        encrypted_bytes: Optional[int] = None,
        uploaded_bytes: Optional[int] = None,
    ) -> Optional[PipelineSnapshot]:
        """Update byte metrics for a video."""
        snapshot = self.get_by_video_id(video_id)
        if not snapshot:
            return None
        
        if total_bytes is not None:
            snapshot.total_bytes = total_bytes
        if downloaded_bytes is not None:
            snapshot.downloaded_bytes = downloaded_bytes
        if encrypted_bytes is not None:
            snapshot.encrypted_bytes = encrypted_bytes
        if uploaded_bytes is not None:
            snapshot.uploaded_bytes = uploaded_bytes
        
        self.session.commit()
        self.session.refresh(snapshot)
        return snapshot
    
    def mark_error(
        self,
        video_id: int,
        error_stage: str,
        error_message: str,
    ) -> Optional[PipelineSnapshot]:
        """Mark a video with an error."""
        snapshot = self.get_by_video_id(video_id)
        if not snapshot:
            return None
        
        snapshot.has_error = True
        snapshot.error_stage = error_stage
        snapshot.error_message = error_message
        snapshot.overall_status = "failed"
        
        self.session.commit()
        self.session.refresh(snapshot)
        return snapshot
    
    def mark_completed(self, video_id: int) -> Optional[PipelineSnapshot]:
        """Mark pipeline as completed for a video."""
        from datetime import timezone
        
        snapshot = self.get_by_video_id(video_id)
        if not snapshot:
            return None
        
        snapshot.overall_status = "completed"
        snapshot.pipeline_completed_at = datetime.now(timezone.utc)
        snapshot.stage_progress_percent = 100.0
        
        self.session.commit()
        self.session.refresh(snapshot)
        return snapshot


class JobHistoryRepository:
    """
    For TUI detail view - get complete job history for a video.
    """
    
    def __init__(self, session: Session):
        self.session = session
    
    def get_video_pipeline_history(self, video_id: int) -> Dict[str, List[Any]]:
        """
        Get all jobs for a video across all stages.
        
        Args:
            video_id: Video ID
            
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
    
    def get_failed_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get recent failures for TUI error view.
        
        Args:
            limit: Maximum number of results
            
        Returns:
            List of failed job records
        """
        # Query each table for failed jobs
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
    """
    Repository for speed history operations.
    
    Provides methods for recording and querying speed metrics
    for graph visualization in the TUI.
    """
    
    def __init__(self, session: Session):
        self.session = session
    
    def create(
        self,
        video_id: int,
        stage: str,
        speed: int,
        progress: float = 0.0,
        bytes_processed: int = 0,
    ) -> SpeedHistory:
        """
        Create a new speed history entry.
        
        Args:
            video_id: Video ID
            stage: Stage name ("download", "encrypt", "upload")
            speed: Speed in bytes/sec
            progress: Progress percentage (0-100)
            bytes_processed: Bytes processed so far
            
        Returns:
            Created SpeedHistory instance
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
    
    def get_speed_history(
        self,
        video_id: int,
        stage: str,
        minutes: int = 5,
    ) -> List[SpeedHistory]:
        """
        Get speed history for graphing.
        
        Args:
            video_id: Video ID
            stage: Stage name
            minutes: Time window in minutes
            
        Returns:
            List of speed history entries
        """
        from datetime import timezone
        
        since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        
        return self.session.query(SpeedHistory).filter(
            SpeedHistory.video_id == video_id,
            SpeedHistory.stage == stage,
            SpeedHistory.timestamp >= since
        ).order_by(SpeedHistory.timestamp).all()
    
    def get_aggregate_speeds(
        self,
        stage: Optional[str] = None,
        minutes: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Get aggregate speeds over time for header graph.
        
        Args:
            stage: Filter by stage (optional)
            minutes: Time window in minutes
            
        Returns:
            List of aggregated speed data points
        """
        from datetime import timezone
        
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
    
    def cleanup_old_samples(self, hours: int = 24) -> int:
        """
        Remove samples older than specified hours.
        
        Args:
            hours: Hours to retain data for
            
        Returns:
            Number of records deleted
        """
        from datetime import timezone
        
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        result = self.session.query(SpeedHistory).filter(
            SpeedHistory.timestamp < cutoff
        ).delete(synchronize_session=False)
        self.session.commit()
        return result


class RepositoryFactory:
    """
    Factory for creating repository instances.

    Usage:
        with get_db_session() as session:
            repos = RepositoryFactory(session)
            videos = repos.videos.get_all()
    """

    def __init__(self, session: Session):
        """
        Initialize factory with database session.

        Args:
            session: SQLAlchemy session
        """
        self.session = session
        self._videos: Optional[VideoRepository] = None
        self._jobs: Optional[JobRepository] = None
        self._executions: Optional[JobExecutionRepository] = None
        self._torrents: Optional[TorrentDownloadRepository] = None
        self._downloads: Optional[DownloadRepository] = None
        self._encryption_jobs: Optional[EncryptionJobRepository] = None
        self._upload_jobs: Optional[UploadJobRepository] = None
        self._sync_jobs: Optional[SyncJobRepository] = None
        self._analysis_jobs: Optional[AnalysisJobRepository] = None
        self._pipeline_snapshots: Optional[PipelineSnapshotRepository] = None
        self._job_history: Optional[JobHistoryRepository] = None
        self._speed_history: Optional[SpeedHistoryRepository] = None

    @property
    def videos(self) -> VideoRepository:
        """
        Get video repository.

        Returns:
            VideoRepository instance
        """
        if self._videos is None:
            self._videos = VideoRepository(self.session)
        return self._videos

    @property
    def jobs(self) -> JobRepository:
        """
        Get job repository.

        Returns:
            JobRepository instance
        """
        if self._jobs is None:
            self._jobs = JobRepository(self.session)
        return self._jobs

    @property
    def executions(self) -> JobExecutionRepository:
        """
        Get job execution repository.

        Returns:
            JobExecutionRepository instance
        """
        if self._executions is None:
            self._executions = JobExecutionRepository(self.session)
        return self._executions

    @property
    def torrents(self) -> TorrentDownloadRepository:
        """
        Get torrent download repository.

        Returns:
            TorrentDownloadRepository instance
        """
        if self._torrents is None:
            self._torrents = TorrentDownloadRepository(self.session)
        return self._torrents

    @property
    def downloads(self) -> DownloadRepository:
        """
        Get download repository.

        Returns:
            DownloadRepository instance
        """
        if self._downloads is None:
            self._downloads = DownloadRepository(self.session)
        return self._downloads

    @property
    def encryption_jobs(self) -> EncryptionJobRepository:
        """
        Get encryption job repository.

        Returns:
            EncryptionJobRepository instance
        """
        if self._encryption_jobs is None:
            self._encryption_jobs = EncryptionJobRepository(self.session)
        return self._encryption_jobs

    @property
    def upload_jobs(self) -> UploadJobRepository:
        """
        Get upload job repository.

        Returns:
            UploadJobRepository instance
        """
        if self._upload_jobs is None:
            self._upload_jobs = UploadJobRepository(self.session)
        return self._upload_jobs

    @property
    def sync_jobs(self) -> SyncJobRepository:
        """
        Get sync job repository.

        Returns:
            SyncJobRepository instance
        """
        if self._sync_jobs is None:
            self._sync_jobs = SyncJobRepository(self.session)
        return self._sync_jobs

    @property
    def analysis_jobs(self) -> AnalysisJobRepository:
        """
        Get analysis job repository.

        Returns:
            AnalysisJobRepository instance
        """
        if self._analysis_jobs is None:
            self._analysis_jobs = AnalysisJobRepository(self.session)
        return self._analysis_jobs

    @property
    def pipeline_snapshots(self) -> PipelineSnapshotRepository:
        """
        Get pipeline snapshot repository.

        Returns:
            PipelineSnapshotRepository instance
        """
        if self._pipeline_snapshots is None:
            self._pipeline_snapshots = PipelineSnapshotRepository(self.session)
        return self._pipeline_snapshots

    @property
    def job_history(self) -> JobHistoryRepository:
        """
        Get job history repository.

        Returns:
            JobHistoryRepository instance
        """
        if self._job_history is None:
            self._job_history = JobHistoryRepository(self.session)
        return self._job_history

    @property
    def speed_history(self) -> SpeedHistoryRepository:
        """
        Get speed history repository.

        Returns:
            SpeedHistoryRepository instance
        """
        if self._speed_history is None:
            self._speed_history = SpeedHistoryRepository(self.session)
        return self._speed_history
