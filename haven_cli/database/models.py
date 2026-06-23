"""
SQLAlchemy models for Haven CLI database.

Based on backend/app/models/video.py and backend/app/models/recurring_job.py
Adapted for CLI context with simplified schema.
"""

from datetime import datetime, timezone
from typing import Optional, List, TYPE_CHECKING, Any, Dict
from uuid import UUID

from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    Boolean,
    Float,
    ForeignKey,
    DateTime,
    Text,
    JSON,
    Index,
)
from sqlalchemy.orm import relationship, Mapped, mapped_column, declarative_base

# Create base class for all models
Base = declarative_base()


class Video(Base):
    """
    Video metadata model.
    
    Stores information about archived videos including:
    - Basic metadata (path, title, duration, size)
    - Content identification (pHash for deduplication)
    - Filecoin storage information (CIDs)
    - Encryption status
    - Plugin source information
    - Arkiv blockchain sync status
    """
    
    __tablename__ = "videos"
    
    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    
    # Basic metadata
    source_path: Mapped[str] = mapped_column(String, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    duration: Mapped[float] = mapped_column(Float, nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[str] = mapped_column(String, nullable=True)
    
    # Content identification (for deduplication)
    phash: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    
    # Source information
    source_uri: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    creator_handle: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # Filecoin storage
    cid: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    piece_cid: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    filecoin_data_set_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    filecoin_uploaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Arkiv blockchain sync
    arkiv_entity_key: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    
    # Encryption status
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    encryption_metadata: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # AI/VLM analysis
    has_ai_data: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    vlm_json_cid: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # Plugin metadata
    plugin_name: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    plugin_source_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    plugin_metadata: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, 
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    
    # Relationships
    timestamps: Mapped[List["Timestamp"]] = relationship(
        "Timestamp", 
        back_populates="video", 
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    
    # Pipeline observability relationships (Task 1.1)
    downloads: Mapped[List["Download"]] = relationship(
        "Download",
        back_populates="video",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Download.created_at.desc()"
    )
    
    encryption_jobs: Mapped[List["EncryptionJob"]] = relationship(
        "EncryptionJob",
        back_populates="video",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="EncryptionJob.created_at.desc()"
    )
    
    upload_jobs: Mapped[List["UploadJob"]] = relationship(
        "UploadJob",
        back_populates="video",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="UploadJob.created_at.desc()"
    )
    
    sync_jobs: Mapped[List["SyncJob"]] = relationship(
        "SyncJob",
        back_populates="video",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SyncJob.created_at.desc()"
    )
    
    analysis_jobs: Mapped[List["AnalysisJob"]] = relationship(
        "AnalysisJob",
        back_populates="video",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="AnalysisJob.created_at.desc()"
    )
    
    pipeline_snapshot: Mapped[Optional["PipelineSnapshot"]] = relationship(
        "PipelineSnapshot",
        back_populates="video",
        cascade="all, delete-orphan",
        lazy="selectin",
        uselist=False
    )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert video to dictionary representation."""
        return {
            "id": self.id,
            "source_path": self.source_path,
            "title": self.title,
            "duration": self.duration,
            "file_size": self.file_size,
            "mime_type": self.mime_type,
            "phash": self.phash,
            "source_uri": self.source_uri,
            "creator_handle": self.creator_handle,
            "cid": self.cid,
            "piece_cid": self.piece_cid,
            "filecoin_data_set_id": self.filecoin_data_set_id,
            "filecoin_uploaded_at": (
                self.filecoin_uploaded_at.isoformat() 
                if self.filecoin_uploaded_at else None
            ),
            "arkiv_entity_key": self.arkiv_entity_key,
            "encrypted": self.encrypted,
            "has_ai_data": self.has_ai_data,
            "vlm_json_cid": self.vlm_json_cid,
            "plugin_name": self.plugin_name,
            "plugin_source_id": self.plugin_source_id,
            "plugin_metadata": self.plugin_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Timestamp(Base):
    """
    AI-generated timestamp model.
    
    Stores timestamp segments identified by VLM analysis,
    including tag names, time ranges, and confidence scores.
    """
    
    __tablename__ = "timestamps"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(
        Integer, 
        ForeignKey("videos.id"), 
        nullable=False,
        index=True
    )
    
    # Timestamp data
    tag_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    
    # Relationships
    video: Mapped[Video] = relationship("Video", back_populates="timestamps")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert timestamp to dictionary representation."""
        return {
            "id": self.id,
            "video_id": self.video_id,
            "tag_name": self.tag_name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class JobExecution(Base):
    """
    Job execution history model.
    
    Tracks executions of recurring jobs for monitoring
    and debugging purposes.
    """
    
    __tablename__ = "job_executions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        String(36),  # Store UUID as string for SQLite compatibility
        nullable=False,
        index=True
    )
    plugin_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    
    # Execution timing
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Results
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sources_found: Mapped[int] = mapped_column(Integer, default=0)
    sources_archived: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Additional execution metadata (plugin-specific)
    # Named 'execution_metadata' to avoid conflict with SQLAlchemy's reserved 'metadata' attribute
    execution_metadata: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    
    # Optional foreign key to scheduled_jobs for referential integrity
    # Nullable to allow orphaned history (jobs can be deleted but history kept)
    scheduled_job_id: Mapped[Optional[int]] = mapped_column(
        Integer, 
        ForeignKey("recurring_jobs.id"), 
        nullable=True,
        index=True
    )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert job execution to dictionary representation."""
        return {
            "id": self.id,
            "job_id": str(self.job_id),
            "plugin_name": self.plugin_name,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "success": self.success,
            "sources_found": self.sources_found,
            "sources_archived": self.sources_archived,
            "error": self.error,
            "metadata": self.execution_metadata,
            "scheduled_job_id": self.scheduled_job_id,
        }


class RecurringJob(Base):
    """
    Recurring job configuration model.
    
    Stores configuration for scheduled plugin jobs.
    Adapted from backend/app/models/recurring_job.py and aligned
    with the RecurringJob dataclass in job_scheduler.py.
    """
    
    __tablename__ = "recurring_jobs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        String(36),  # Store UUID as string
        nullable=False,
        unique=True,
        index=True
    )
    plugin_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    
    # Schedule (cron format: "minute hour day month weekday")
    schedule: Mapped[str] = mapped_column(String, nullable=False)
    
    # What to do with results (archive_all, archive_new, log_only)
    on_success: Mapped[str] = mapped_column(String, default="archive_new")
    
    # Metadata for job configuration (plugin-specific settings)
    job_metadata: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, default=dict, name="metadata"
    )
    
    # Status
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Execution tracking
    last_run: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_run: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    
    # Statistics
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert recurring job to dictionary representation."""
        return {
            "id": self.id,
            "job_id": str(self.job_id),
            "plugin_name": self.plugin_name,
            "name": self.name,
            "schedule": self.schedule,
            "on_success": self.on_success,
            "metadata": self.job_metadata,
            "enabled": self.enabled,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "run_count": self.run_count,
            "error_count": self.error_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ============================================================================
# Pipeline Observability Models (Task 1.1)
# ============================================================================

class Download(Base):
    """Tracks download progress for any source type (YouTube or BitTorrent)."""
    __tablename__ = "downloads"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(
        Integer, 
        ForeignKey("videos.id"), 
        nullable=False,
        index=True
    )
    source_type: Mapped[str] = mapped_column(String, nullable=False)  # "youtube" | "torrent"
    
    # Common progress fields
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    progress_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bytes_downloaded: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bytes_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    download_rate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # bytes/sec
    eta_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Timing
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Source-specific data (JSON for flexibility)
    source_metadata: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    
    # Relationship
    video: Mapped["Video"] = relationship("Video", back_populates="downloads")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert download to dictionary representation."""
        return {
            "id": self.id,
            "video_id": self.video_id,
            "source_type": self.source_type,
            "status": self.status,
            "progress_percent": self.progress_percent,
            "bytes_downloaded": self.bytes_downloaded,
            "bytes_total": self.bytes_total,
            "download_rate": self.download_rate,
            "eta_seconds": self.eta_seconds,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "failed_at": self.failed_at.isoformat() if self.failed_at else None,
            "error_message": self.error_message,
            "source_metadata": self.source_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class EncryptionJob(Base):
    """Tracks encryption progress for videos."""
    __tablename__ = "encryption_jobs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(
        Integer, 
        ForeignKey("videos.id"), 
        nullable=False,
        index=True
    )
    
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    progress_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Progress tracking
    bytes_processed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bytes_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    encrypt_speed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # bytes/sec
    
    # Encryption-specific metadata
    encrypted_ref: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    access_control_conditions: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    
    # Timing
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    
    # Relationship
    video: Mapped["Video"] = relationship("Video", back_populates="encryption_jobs")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert encryption job to dictionary representation."""
        return {
            "id": self.id,
            "video_id": self.video_id,
            "status": self.status,
            "progress_percent": self.progress_percent,
            "bytes_processed": self.bytes_processed,
            "bytes_total": self.bytes_total,
            "encrypt_speed": self.encrypt_speed,
            "encrypted_ref": self.encrypted_ref,
            "access_control_conditions": self.access_control_conditions,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class UploadJob(Base):
    """Tracks upload progress to storage backends."""
    __tablename__ = "upload_jobs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(
        Integer, 
        ForeignKey("videos.id"), 
        nullable=False,
        index=True
    )
    
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    target: Mapped[str] = mapped_column(String, nullable=False)  # "ipfs" | "arkiv"
    
    # Upload substage for detailed progress tracking
    # Values: "connecting", "preparing", "uploading", "confirming", "complete"
    stage: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    progress_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bytes_uploaded: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bytes_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    upload_speed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # bytes/sec
    
    # Result
    remote_cid: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    remote_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # Timing
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    
    # Relationship
    video: Mapped["Video"] = relationship("Video", back_populates="upload_jobs")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert upload job to dictionary representation."""
        return {
            "id": self.id,
            "video_id": self.video_id,
            "status": self.status,
            "stage": self.stage,
            "target": self.target,
            "progress_percent": self.progress_percent,
            "bytes_uploaded": self.bytes_uploaded,
            "bytes_total": self.bytes_total,
            "upload_speed": self.upload_speed,
            "remote_cid": self.remote_cid,
            "remote_url": self.remote_url,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SyncJob(Base):
    """Tracks blockchain synchronization progress."""
    __tablename__ = "sync_jobs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(
        Integer, 
        ForeignKey("videos.id"), 
        nullable=False,
        index=True
    )
    
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    
    # Transaction tracking
    tx_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    block_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    gas_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Timing
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    
    # Relationship
    video: Mapped["Video"] = relationship("Video", back_populates="sync_jobs")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert sync job to dictionary representation."""
        return {
            "id": self.id,
            "video_id": self.video_id,
            "status": self.status,
            "tx_hash": self.tx_hash,
            "block_number": self.block_number,
            "gas_used": self.gas_used,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AnalysisJob(Base):
    """Tracks VLM/LLM analysis progress."""
    __tablename__ = "analysis_jobs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(
        Integer, 
        ForeignKey("videos.id"), 
        nullable=False,
        index=True
    )
    
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    
    # Frame-level progress
    frames_processed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    frames_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    progress_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Analysis configuration
    analysis_type: Mapped[str] = mapped_column(String, nullable=False)  # "vlm" | "llm"
    model_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # Results reference
    output_file: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # Timing
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    
    # Relationship
    video: Mapped["Video"] = relationship("Video", back_populates="analysis_jobs")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert analysis job to dictionary representation."""
        return {
            "id": self.id,
            "video_id": self.video_id,
            "status": self.status,
            "frames_processed": self.frames_processed,
            "frames_total": self.frames_total,
            "progress_percent": self.progress_percent,
            "analysis_type": self.analysis_type,
            "model_name": self.model_name,
            "output_file": self.output_file,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PipelineSnapshot(Base):
    """Pre-computed pipeline state for TUI queries.
    
    Updated frequently by pipeline steps so TUI can query a single row
    per video instead of joining multiple tables.
    """
    __tablename__ = "pipeline_snapshots"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(
        Integer, 
        ForeignKey("videos.id"), 
        nullable=False,
        unique=True,
        index=True
    )
    
    # Current stage (derived from job statuses)
    current_stage: Mapped[str] = mapped_column(String, nullable=False)
    overall_status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    
    # Progress for current stage
    stage_progress_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stage_speed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # bytes/sec
    stage_eta: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)   # seconds
    
    # Aggregate metrics
    total_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    downloaded_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    encrypted_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    uploaded_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Error state
    has_error: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_stage: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Timestamps
    stage_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    pipeline_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    pipeline_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    
    # Relationship
    video: Mapped["Video"] = relationship("Video", back_populates="pipeline_snapshot")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert pipeline snapshot to dictionary representation."""
        return {
            "id": self.id,
            "video_id": self.video_id,
            "current_stage": self.current_stage,
            "overall_status": self.overall_status,
            "stage_progress_percent": self.stage_progress_percent,
            "stage_speed": self.stage_speed,
            "stage_eta": self.stage_eta,
            "total_bytes": self.total_bytes,
            "downloaded_bytes": self.downloaded_bytes,
            "encrypted_bytes": self.encrypted_bytes,
            "uploaded_bytes": self.uploaded_bytes,
            "has_error": self.has_error,
            "error_stage": self.error_stage,
            "error_message": self.error_message,
            "stage_started_at": self.stage_started_at.isoformat() if self.stage_started_at else None,
            "pipeline_started_at": self.pipeline_started_at.isoformat() if self.pipeline_started_at else None,
            "pipeline_completed_at": self.pipeline_completed_at.isoformat() if self.pipeline_completed_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SpeedHistory(Base):
    """Time-series data for speed graphs in TUI."""
    __tablename__ = "speed_history"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(
        Integer, 
        ForeignKey("videos.id"), 
        nullable=False,
        index=True
    )
    stage: Mapped[str] = mapped_column(String, nullable=False)  # "download" | "encrypt" | "upload"
    
    # Metrics at this point in time
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, 
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True
    )
    speed: Mapped[int] = mapped_column(Integer, nullable=False)  # bytes/sec
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # 0-100
    bytes_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    
    # Composite index for time-range queries
    __table_args__ = (
        Index('ix_speed_history_video_stage_time', 'video_id', 'stage', 'timestamp'),
    )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert speed history entry to dictionary representation."""
        return {
            "id": self.id,
            "video_id": self.video_id,
            "stage": self.stage,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "speed": self.speed,
            "progress": self.progress,
            "bytes_processed": self.bytes_processed,
        }


# Additional indexes for common queries
Index("ix_videos_created_at", Video.created_at.desc())
Index("ix_job_executions_started_at", JobExecution.started_at.desc())
Index("ix_downloads_status", Download.status)
Index("ix_downloads_video_status", Download.video_id, Download.status)
Index("ix_encryption_jobs_status", EncryptionJob.status)
Index("ix_upload_jobs_status", UploadJob.status)
Index("ix_sync_jobs_status", SyncJob.status)
Index("ix_analysis_jobs_status", AnalysisJob.status)
Index("ix_pipeline_snapshots_status", PipelineSnapshot.overall_status)


class TorrentDownload(Base):
    """
    BitTorrent download state tracking model.
    
    Tracks download state across restarts, supports resume capability,
    and provides status queries for long-running torrent downloads.
    """
    
    __tablename__ = "torrent_downloads"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    
    # Torrent identification
    infohash: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    source_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    magnet_uri: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # Download status: downloading, paused, completed, failed, stalled
    status: Mapped[str] = mapped_column(String, nullable=False, default="downloading")
    
    # Progress tracking
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    download_rate: Mapped[int] = mapped_column(Integer, default=0)  # bytes/sec
    upload_rate: Mapped[int] = mapped_column(Integer, default=0)  # bytes/sec
    peers: Mapped[int] = mapped_column(Integer, default=0)
    seeds: Mapped[int] = mapped_column(Integer, default=0)
    total_size: Mapped[int] = mapped_column(BigInteger, default=0)
    downloaded_size: Mapped[int] = mapped_column(BigInteger, default=0)
    
    # File information
    output_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    selected_file_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Timing
    started_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_activity: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    
    # Error tracking
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Resume data (libtorrent fast resume data as base64)
    resume_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Additional metadata
    download_metadata: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, nullable=True, name="metadata"
    )
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert torrent download to dictionary representation."""
        return {
            "id": self.id,
            "infohash": self.infohash,
            "source_id": self.source_id,
            "title": self.title,
            "magnet_uri": self.magnet_uri,
            "status": self.status,
            "progress": self.progress,
            "download_rate": self.download_rate,
            "upload_rate": self.upload_rate,
            "peers": self.peers,
            "seeds": self.seeds,
            "total_size": self.total_size,
            "downloaded_size": self.downloaded_size,
            "output_path": self.output_path,
            "selected_file_index": self.selected_file_index,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "error_message": self.error_message,
            "metadata": self.download_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PipelineEvent(Base):
    """Durable, append-only tail of pipeline events for cross-process delivery.

    The in-process ``EventBus`` defined in ``haven_cli.pipeline.events`` lives
    inside whichever Python process happens to import it. The daemon process
    (``haven run daemon``) and the TUI process (``haven tui``) are distinct
    processes; their buses are completely separate, which means progress
    events emitted on the daemon's bus never reach the TUI.

    To bridge the gap we persist a copy of every event the daemon emits into
    this table. The TUI process then tails the table by ``id`` and replays
    the events through its local bus, where ``StateManager`` is already
    subscribed. This was added in Milestone B of the TUI improvements
    proposal — see ``docs/TUI_IMPROVEMENTS_PROPOSAL.md`` R2 / B1.

    Notes on schema choices:

    * ``id`` is an autoincrement integer that doubles as a monotonic
      sequence number. Consumers track the last ``id`` they processed and
      ask for ``id > last_id`` on the next poll.
    * ``payload_json`` is the event payload as JSON. We store it as a
      string rather than a JSON column so SQLite doesn't try to interpret
      it on every read; the consumer parses on demand.
    * Indexed by ``(id)`` (primary key, implicit) and ``(ts, event_type)``
      for the event log screen's filter queries.

    The table is written by the writer (daemon) and read by readers (TUI
    instances). Under WAL — enabled by ``haven_cli/database/connection.py``
    — readers don't block the writer and the TUI sees newly-committed rows
    as soon as the daemon's transaction commits.
    """

    __tablename__ = "pipeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Wall-clock timestamp the event was published. UTC.
    ts: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # ``EventType.name`` — e.g. ``DOWNLOAD_PROGRESS``. We store the *name*
    # rather than the integer value because Python ``Enum.auto()`` values
    # are not stable across reorderings of the enum, and we don't want to
    # tie the on-disk format to source ordering.
    event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # ``Event.correlation_id`` UUID stringified. Optional. Lets the event
    # log screen group related events (one video's whole pipeline run).
    correlation_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)

    # The component that produced the event (e.g. "DownloadStep",
    # "PipelineInterface"). Useful for debugging.
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # JSON-encoded payload. We don't validate or normalize on the writer
    # side — the consumer is responsible for parsing.
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_pipeline_events_ts_type", "ts", "event_type"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts.isoformat() if self.ts else None,
            "event_type": self.event_type,
            "correlation_id": self.correlation_id,
            "source": self.source,
            "payload_json": self.payload_json,
        }

