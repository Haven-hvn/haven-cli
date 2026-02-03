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
    lit_encryption_metadata: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
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


# Additional indexes for common queries
Index("ix_videos_created_at", Video.created_at.desc())
Index("ix_job_executions_started_at", JobExecution.started_at.desc())
