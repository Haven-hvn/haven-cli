"""Database repositories for Haven CLI.

Provides high-level data access patterns for common database operations,
including pHash-based duplicate detection and video queries.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Dict, Any
from uuid import UUID

from sqlalchemy.orm import Session, Query
from sqlalchemy import desc

from haven_cli.database.models import Video, RecurringJob, JobExecution
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
