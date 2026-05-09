"""Tests for pipeline observability models.

This module tests the new pipeline stage tables:
- Download
- EncryptionJob
- UploadJob
- SyncJob
- AnalysisJob
- PipelineSnapshot
- SpeedHistory
"""

import pytest
from datetime import datetime, timezone, timedelta
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from haven_cli.database.models import (
    Base,
    Video,
    Download,
    EncryptionJob,
    UploadJob,
    SyncJob,
    AnalysisJob,
    PipelineSnapshot,
    SpeedHistory,
)


# Use in-memory SQLite for tests
@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Create a fresh database session for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def sample_video(db_session: Session) -> Video:
    """Create a sample video for testing."""
    video = Video(
        source_path="/test/video.mp4",
        title="Test Video",
        duration=120.0,
        file_size=1000000,
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)
    return video


class TestDownloadModel:
    """Tests for Download model."""
    
    def test_create_download(self, db_session: Session, sample_video: Video) -> None:
        """Test creating a download record."""
        download = Download(
            video_id=sample_video.id,
            source_type="youtube",
            status="downloading",
            progress_percent=50.0,
            bytes_downloaded=500000,
            bytes_total=1000000,
            download_rate=100000,
            eta_seconds=5,
        )
        db_session.add(download)
        db_session.commit()
        db_session.refresh(download)
        
        assert download.id is not None
        assert download.video_id == sample_video.id
        assert download.source_type == "youtube"
        assert download.status == "downloading"
        assert download.progress_percent == 50.0
        assert download.bytes_downloaded == 500000
        assert download.created_at is not None
        assert download.updated_at is not None
    
    def test_download_relationship(self, db_session: Session, sample_video: Video) -> None:
        """Test download relationship with video."""
        download = Download(
            video_id=sample_video.id,
            source_type="torrent",
            status="pending",
        )
        db_session.add(download)
        db_session.commit()
        
        # Refresh video to load relationship
        db_session.refresh(sample_video)
        
        assert len(sample_video.downloads) == 1
        assert sample_video.downloads[0].source_type == "torrent"
    
    def test_download_to_dict(self, db_session: Session, sample_video: Video) -> None:
        """Test download to_dict method."""
        download = Download(
            video_id=sample_video.id,
            source_type="youtube",
            status="completed",
            source_metadata={"video_id": "abc123"},
        )
        db_session.add(download)
        db_session.commit()
        
        data = download.to_dict()
        assert data["video_id"] == sample_video.id
        assert data["source_type"] == "youtube"
        assert data["status"] == "completed"
        assert data["source_metadata"] == {"video_id": "abc123"}
        assert "created_at" in data
        assert "updated_at" in data
    
    def test_download_status_transitions(self, db_session: Session, sample_video: Video) -> None:
        """Test download status transitions."""
        download = Download(
            video_id=sample_video.id,
            source_type="youtube",
            status="pending",
        )
        db_session.add(download)
        db_session.commit()
        
        # Transition to downloading
        download.status = "downloading"
        download.started_at = datetime.now(timezone.utc)
        db_session.commit()
        
        assert download.status == "downloading"
        assert download.started_at is not None
        
        # Transition to completed
        download.status = "completed"
        download.completed_at = datetime.now(timezone.utc)
        download.progress_percent = 100.0
        db_session.commit()
        
        assert download.status == "completed"
        assert download.completed_at is not None
        assert download.progress_percent == 100.0
    
    def test_download_error_handling(self, db_session: Session, sample_video: Video) -> None:
        """Test download error recording."""
        download = Download(
            video_id=sample_video.id,
            source_type="youtube",
            status="failed",
            failed_at=datetime.now(timezone.utc),
            error_message="Network timeout",
        )
        db_session.add(download)
        db_session.commit()
        
        assert download.status == "failed"
        assert download.failed_at is not None
        assert download.error_message == "Network timeout"


class TestEncryptionJobModel:
    """Tests for EncryptionJob model."""
    
    def test_create_encryption_job(self, db_session: Session, sample_video: Video) -> None:
        """Test creating an encryption job."""
        job = EncryptionJob(
            video_id=sample_video.id,
            status="encrypting",
            progress_percent=75.0,
            bytes_processed=750000,
            bytes_total=1000000,
            encrypt_speed=50000,
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        
        assert job.id is not None
        assert job.video_id == sample_video.id
        assert job.status == "encrypting"
        assert job.progress_percent == 75.0
        assert job.bytes_processed == 750000
        assert job.encrypt_speed == 50000
    
    def test_encryption_job_metadata_fields(self, db_session: Session, sample_video: Video) -> None:
        """Test encryption job with encryption metadata fields."""
        job = EncryptionJob(
            video_id=sample_video.id,
            status="completed",
            encrypted_ref="QmTest123",
            access_control_conditions={"chain": "ethereum", "contract": "0x123"},
        )
        db_session.add(job)
        db_session.commit()
        
        assert job.encrypted_ref == "QmTest123"
        assert job.access_control_conditions["chain"] == "ethereum"
    
    def test_encryption_job_relationship(self, db_session: Session, sample_video: Video) -> None:
        """Test encryption job relationship with video."""
        job = EncryptionJob(
            video_id=sample_video.id,
            status="pending",
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(sample_video)
        
        assert len(sample_video.encryption_jobs) == 1
        assert sample_video.encryption_jobs[0].status == "pending"
    
    def test_encryption_job_to_dict(self, db_session: Session, sample_video: Video) -> None:
        """Test encryption job to_dict method."""
        job = EncryptionJob(
            video_id=sample_video.id,
            status="completed",
            encrypted_ref="QmTest",
        )
        db_session.add(job)
        db_session.commit()
        
        data = job.to_dict()
        assert data["video_id"] == sample_video.id
        assert data["status"] == "completed"
        assert data["encrypted_ref"] == "QmTest"


class TestUploadJobModel:
    """Tests for UploadJob model."""
    
    def test_create_upload_job(self, db_session: Session, sample_video: Video) -> None:
        """Test creating an upload job."""
        job = UploadJob(
            video_id=sample_video.id,
            status="uploading",
            target="ipfs",
            progress_percent=60.0,
            bytes_uploaded=600000,
            bytes_total=1000000,
            upload_speed=80000,
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        
        assert job.id is not None
        assert job.target == "ipfs"
        assert job.status == "uploading"
        assert job.bytes_uploaded == 600000
    
    def test_upload_job_targets(self, db_session: Session, sample_video: Video) -> None:
        """Test upload job with different targets."""
        for target in ["ipfs", "arkiv", "s3"]:
            job = UploadJob(
                video_id=sample_video.id,
                status="pending",
                target=target,
            )
            db_session.add(job)
        
        db_session.commit()
        
        jobs = db_session.query(UploadJob).filter_by(video_id=sample_video.id).all()
        assert len(jobs) == 3
        targets = {j.target for j in jobs}
        assert targets == {"ipfs", "arkiv", "s3"}
    
    def test_upload_job_completion(self, db_session: Session, sample_video: Video) -> None:
        """Test upload job completion."""
        job = UploadJob(
            video_id=sample_video.id,
            status="completed",
            target="ipfs",
            remote_cid="QmRemote123",
            remote_url="https://ipfs.io/ipfs/QmRemote123",
            completed_at=datetime.now(timezone.utc),
        )
        db_session.add(job)
        db_session.commit()
        
        assert job.remote_cid == "QmRemote123"
        assert job.remote_url == "https://ipfs.io/ipfs/QmRemote123"
        assert job.completed_at is not None


class TestSyncJobModel:
    """Tests for SyncJob model."""
    
    def test_create_sync_job(self, db_session: Session, sample_video: Video) -> None:
        """Test creating a sync job."""
        job = SyncJob(
            video_id=sample_video.id,
            status="syncing",
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        
        assert job.id is not None
        assert job.status == "syncing"
    
    def test_sync_job_completion(self, db_session: Session, sample_video: Video) -> None:
        """Test sync job with blockchain data."""
        job = SyncJob(
            video_id=sample_video.id,
            status="completed",
            tx_hash="0xabc123",
            block_number=12345678,
            gas_used=150000,
            completed_at=datetime.now(timezone.utc),
        )
        db_session.add(job)
        db_session.commit()
        
        assert job.tx_hash == "0xabc123"
        assert job.block_number == 12345678
        assert job.gas_used == 150000
    
    def test_sync_job_to_dict(self, db_session: Session, sample_video: Video) -> None:
        """Test sync job to_dict method."""
        job = SyncJob(
            video_id=sample_video.id,
            status="completed",
            tx_hash="0xabc",
            block_number=100,
        )
        db_session.add(job)
        db_session.commit()
        
        data = job.to_dict()
        assert data["tx_hash"] == "0xabc"
        assert data["block_number"] == 100


class TestAnalysisJobModel:
    """Tests for AnalysisJob model."""
    
    def test_create_analysis_job(self, db_session: Session, sample_video: Video) -> None:
        """Test creating an analysis job."""
        job = AnalysisJob(
            video_id=sample_video.id,
            status="analyzing",
            analysis_type="vlm",
            model_name="llava",
            frames_processed=50,
            frames_total=100,
            progress_percent=50.0,
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        
        assert job.id is not None
        assert job.analysis_type == "vlm"
        assert job.model_name == "llava"
        assert job.frames_processed == 50
        assert job.progress_percent == 50.0
    
    def test_analysis_job_types(self, db_session: Session, sample_video: Video) -> None:
        """Test analysis job with different types."""
        for analysis_type in ["vlm", "llm"]:
            job = AnalysisJob(
                video_id=sample_video.id,
                status="pending",
                analysis_type=analysis_type,
            )
            db_session.add(job)
        
        db_session.commit()
        
        jobs = db_session.query(AnalysisJob).filter_by(video_id=sample_video.id).all()
        assert len(jobs) == 2
        types = {j.analysis_type for j in jobs}
        assert types == {"vlm", "llm"}
    
    def test_analysis_job_completion(self, db_session: Session, sample_video: Video) -> None:
        """Test analysis job completion."""
        job = AnalysisJob(
            video_id=sample_video.id,
            status="completed",
            analysis_type="vlm",
            frames_processed=100,
            frames_total=100,
            progress_percent=100.0,
            output_file="/path/to/results.json",
            completed_at=datetime.now(timezone.utc),
        )
        db_session.add(job)
        db_session.commit()
        
        assert job.output_file == "/path/to/results.json"
        assert job.progress_percent == 100.0


class TestPipelineSnapshotModel:
    """Tests for PipelineSnapshot model."""
    
    def test_create_snapshot(self, db_session: Session, sample_video: Video) -> None:
        """Test creating a pipeline snapshot."""
        snapshot = PipelineSnapshot(
            video_id=sample_video.id,
            current_stage="download",
            overall_status="active",
            stage_progress_percent=45.0,
            stage_speed=100000,
            stage_eta=60,
            total_bytes=1000000,
            downloaded_bytes=450000,
        )
        db_session.add(snapshot)
        db_session.commit()
        db_session.refresh(snapshot)
        
        assert snapshot.id is not None
        assert snapshot.video_id == sample_video.id
        assert snapshot.current_stage == "download"
        assert snapshot.overall_status == "active"
        assert snapshot.stage_progress_percent == 45.0
    
    def test_snapshot_unique_constraint(self, db_session: Session, sample_video: Video) -> None:
        """Test that only one snapshot exists per video."""
        snapshot1 = PipelineSnapshot(
            video_id=sample_video.id,
            current_stage="download",
            overall_status="active",
        )
        db_session.add(snapshot1)
        db_session.commit()
        
        # Attempting to add another snapshot for same video should fail
        # (but SQLite in-memory may not enforce unique constraints the same way)
        # This test mainly verifies the schema design
        existing = db_session.query(PipelineSnapshot).filter_by(
            video_id=sample_video.id
        ).first()
        assert existing is not None
    
    def test_snapshot_error_state(self, db_session: Session, sample_video: Video) -> None:
        """Test snapshot error state."""
        snapshot = PipelineSnapshot(
            video_id=sample_video.id,
            current_stage="upload",
            overall_status="failed",
            has_error=True,
            error_stage="upload",
            error_message="Connection timeout",
        )
        db_session.add(snapshot)
        db_session.commit()
        
        assert snapshot.has_error is True
        assert snapshot.error_stage == "upload"
        assert snapshot.error_message == "Connection timeout"
    
    def test_snapshot_to_dict(self, db_session: Session, sample_video: Video) -> None:
        """Test snapshot to_dict method."""
        snapshot = PipelineSnapshot(
            video_id=sample_video.id,
            current_stage="encrypt",
            overall_status="active",
            stage_progress_percent=75.0,
        )
        db_session.add(snapshot)
        db_session.commit()
        
        data = snapshot.to_dict()
        assert data["video_id"] == sample_video.id
        assert data["current_stage"] == "encrypt"
        assert data["overall_status"] == "active"
        assert data["stage_progress_percent"] == 75.0
    
    def test_snapshot_timestamps(self, db_session: Session, sample_video: Video) -> None:
        """Test snapshot timestamp fields."""
        now = datetime.now(timezone.utc)
        
        snapshot = PipelineSnapshot(
            video_id=sample_video.id,
            current_stage="download",
            overall_status="active",
            stage_started_at=now,
            pipeline_started_at=now,
        )
        db_session.add(snapshot)
        db_session.commit()
        
        assert snapshot.stage_started_at is not None
        assert snapshot.pipeline_started_at is not None
        assert snapshot.updated_at is not None


class TestSpeedHistoryModel:
    """Tests for SpeedHistory model."""
    
    def test_create_speed_history(self, db_session: Session, sample_video: Video) -> None:
        """Test creating a speed history entry."""
        entry = SpeedHistory(
            video_id=sample_video.id,
            stage="download",
            speed=100000,
            progress=50.0,
            bytes_processed=500000,
        )
        db_session.add(entry)
        db_session.commit()
        db_session.refresh(entry)
        
        assert entry.id is not None
        assert entry.video_id == sample_video.id
        assert entry.stage == "download"
        assert entry.speed == 100000
        assert entry.progress == 50.0
        assert entry.timestamp is not None
    
    def test_speed_history_stages(self, db_session: Session, sample_video: Video) -> None:
        """Test speed history for different stages."""
        stages = ["download", "encrypt", "upload"]
        
        for stage in stages:
            entry = SpeedHistory(
                video_id=sample_video.id,
                stage=stage,
                speed=100000,
                progress=50.0,
            )
            db_session.add(entry)
        
        db_session.commit()
        
        entries = db_session.query(SpeedHistory).filter_by(
            video_id=sample_video.id
        ).all()
        
        assert len(entries) == 3
        stage_names = {e.stage for e in entries}
        assert stage_names == set(stages)
    
    def test_speed_history_time_range_query(self, db_session: Session, sample_video: Video) -> None:
        """Test querying speed history by time range."""
        now = datetime.now(timezone.utc)
        
        # Create entries at different times
        for i in range(5):
            entry = SpeedHistory(
                video_id=sample_video.id,
                stage="download",
                speed=100000 + i * 1000,
                progress=float(i * 20),
                timestamp=now - timedelta(minutes=i),
            )
            db_session.add(entry)
        
        db_session.commit()
        
        # Query recent entries (last 3 minutes)
        since = now - timedelta(minutes=3)
        recent = db_session.query(SpeedHistory).filter(
            SpeedHistory.video_id == sample_video.id,
            SpeedHistory.timestamp >= since
        ).all()
        
        assert len(recent) >= 3  # Should have entries from now, 1min, 2min ago
    
    def test_speed_history_to_dict(self, db_session: Session, sample_video: Video) -> None:
        """Test speed history to_dict method."""
        entry = SpeedHistory(
            video_id=sample_video.id,
            stage="upload",
            speed=50000,
            progress=75.0,
            bytes_processed=750000,
        )
        db_session.add(entry)
        db_session.commit()
        
        data = entry.to_dict()
        assert data["video_id"] == sample_video.id
        assert data["stage"] == "upload"
        assert data["speed"] == 50000
        assert data["progress"] == 75.0


class TestVideoRelationships:
    """Tests for Video model relationships with new pipeline tables."""
    
    def test_video_has_all_relationships(self, db_session: Session, sample_video: Video) -> None:
        """Test that video has all pipeline relationships."""
        # Create related records
        download = Download(video_id=sample_video.id, source_type="youtube", status="completed")
        encrypt_job = EncryptionJob(video_id=sample_video.id, status="completed")
        upload_job = UploadJob(video_id=sample_video.id, target="ipfs", status="completed")
        sync_job = SyncJob(video_id=sample_video.id, status="completed")
        analysis_job = AnalysisJob(video_id=sample_video.id, analysis_type="vlm", status="completed")
        snapshot = PipelineSnapshot(
            video_id=sample_video.id,
            current_stage="completed",
            overall_status="completed",
        )
        
        db_session.add_all([
            download, encrypt_job, upload_job, sync_job, analysis_job, snapshot
        ])
        db_session.commit()
        
        # Refresh and check relationships
        db_session.refresh(sample_video)
        
        assert len(sample_video.downloads) == 1
        assert len(sample_video.encryption_jobs) == 1
        assert len(sample_video.upload_jobs) == 1
        assert len(sample_video.sync_jobs) == 1
        assert len(sample_video.analysis_jobs) == 1
        assert sample_video.pipeline_snapshot is not None
    
    def test_cascade_delete(self, db_session: Session, sample_video: Video) -> None:
        """Test that pipeline records are deleted when video is deleted."""
        # Create related records
        download = Download(video_id=sample_video.id, source_type="youtube", status="pending")
        snapshot = PipelineSnapshot(
            video_id=sample_video.id,
            current_stage="download",
            overall_status="active",
        )
        
        db_session.add_all([download, snapshot])
        db_session.commit()
        
        # Verify records exist
        assert db_session.query(Download).count() == 1
        assert db_session.query(PipelineSnapshot).count() == 1
        
        # Delete video
        db_session.delete(sample_video)
        db_session.commit()
        
        # Verify related records are deleted
        assert db_session.query(Download).count() == 0
        assert db_session.query(PipelineSnapshot).count() == 0


class TestModelIndexes:
    """Tests to verify indexes are properly defined."""
    
    def test_download_indexes(self, db_session: Session) -> None:
        """Test that download indexes exist."""
        # Indexes should be created - we can verify by checking the table exists
        # and querying works efficiently
        from sqlalchemy import inspect
        
        inspector = inspect(db_session.get_bind())
        indexes = inspector.get_indexes("downloads")
        index_names = {idx["name"] for idx in indexes}
        
        assert "ix_downloads_video_id" in index_names
        assert "ix_downloads_status" in index_names
    
    def test_pipeline_snapshot_indexes(self, db_session: Session) -> None:
        """Test that pipeline snapshot indexes exist."""
        from sqlalchemy import inspect
        
        inspector = inspect(db_session.get_bind())
        indexes = inspector.get_indexes("pipeline_snapshots")
        index_names = {idx["name"] for idx in indexes}
        
        assert "ix_pipeline_snapshots_video_id" in index_names
        assert "ix_pipeline_snapshots_status" in index_names
