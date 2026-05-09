"""Tests for pipeline observability repositories.

This module tests the new repository classes:
- DownloadRepository
- EncryptionJobRepository
- UploadJobRepository
- SyncJobRepository
- AnalysisJobRepository
- PipelineSnapshotRepository
- JobHistoryRepository
- SpeedHistoryRepository
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
from haven_cli.database.repositories import (
    DownloadRepository,
    EncryptionJobRepository,
    UploadJobRepository,
    SyncJobRepository,
    AnalysisJobRepository,
    PipelineSnapshotRepository,
    JobHistoryRepository,
    SpeedHistoryRepository,
    RepositoryFactory,
)


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


@pytest.fixture
def sample_videos(db_session: Session) -> list[Video]:
    """Create multiple sample videos for testing."""
    videos = []
    for i in range(3):
        video = Video(
            source_path=f"/test/video{i}.mp4",
            title=f"Test Video {i}",
            duration=120.0 + i * 10,
            file_size=1000000 + i * 100000,
        )
        db_session.add(video)
        videos.append(video)
    
    db_session.commit()
    for video in videos:
        db_session.refresh(video)
    
    return videos


class TestDownloadRepository:
    """Tests for DownloadRepository."""
    
    def test_create_download(self, db_session: Session, sample_video: Video) -> None:
        """Test creating a download record."""
        repo = DownloadRepository(db_session)
        
        download = repo.create(
            video_id=sample_video.id,
            source_type="youtube",
            status="pending",
            source_metadata={"video_id": "abc123"},
        )
        
        assert download.id is not None
        assert download.video_id == sample_video.id
        assert download.source_type == "youtube"
        assert download.source_metadata == {"video_id": "abc123"}
    
    def test_get_by_id(self, db_session: Session, sample_video: Video) -> None:
        """Test getting download by ID."""
        repo = DownloadRepository(db_session)
        
        created = repo.create(video_id=sample_video.id, source_type="youtube")
        fetched = repo.get_by_id(created.id)
        
        assert fetched is not None
        assert fetched.id == created.id
    
    def test_get_by_video_id(self, db_session: Session, sample_video: Video) -> None:
        """Test getting downloads by video ID."""
        repo = DownloadRepository(db_session)
        
        repo.create(video_id=sample_video.id, source_type="youtube")
        repo.create(video_id=sample_video.id, source_type="torrent")
        
        downloads = repo.get_by_video_id(sample_video.id)
        
        assert len(downloads) == 2
        assert {d.source_type for d in downloads} == {"youtube", "torrent"}
    
    def test_get_active_downloads(self, db_session: Session, sample_videos: list[Video]) -> None:
        """Test getting active downloads."""
        repo = DownloadRepository(db_session)
        
        # Create active downloads
        repo.create(video_id=sample_videos[0].id, source_type="youtube", status="downloading")
        repo.create(video_id=sample_videos[1].id, source_type="torrent", status="downloading")
        repo.create(video_id=sample_videos[2].id, source_type="youtube", status="completed")
        
        active = repo.get_active_downloads()
        
        assert len(active) == 2
        assert all(d.status == "downloading" for d in active)
    
    def test_update_progress(self, db_session: Session, sample_video: Video) -> None:
        """Test updating download progress."""
        repo = DownloadRepository(db_session)
        
        download = repo.create(video_id=sample_video.id, source_type="youtube")
        
        updated = repo.update_progress(
            download_id=download.id,
            bytes_downloaded=500000,
            bytes_total=1000000,
            download_rate=100000,
            eta_seconds=5,
        )
        
        assert updated is not None
        assert updated.bytes_downloaded == 500000
        assert updated.progress_percent == 50.0
        assert updated.download_rate == 100000
        assert updated.eta_seconds == 5
    
    def test_update_status(self, db_session: Session, sample_video: Video) -> None:
        """Test updating download status."""
        repo = DownloadRepository(db_session)
        
        download = repo.create(video_id=sample_video.id, source_type="youtube")
        
        updated = repo.update_status(download.id, "downloading")
        
        assert updated is not None
        assert updated.status == "downloading"
        assert updated.started_at is not None
        
        # Complete the download
        updated = repo.update_status(download.id, "completed")
        assert updated.status == "completed"
        assert updated.completed_at is not None
    
    def test_get_aggregate_download_speed(self, db_session: Session, sample_videos: list[Video]) -> None:
        """Test getting aggregate download speed."""
        repo = DownloadRepository(db_session)
        
        # Create active downloads with rates
        d1 = repo.create(video_id=sample_videos[0].id, source_type="youtube", status="downloading")
        d2 = repo.create(video_id=sample_videos[1].id, source_type="torrent", status="downloading")
        
        repo.update_progress(d1.id, 100000, 1000000, 100000, 10)
        repo.update_progress(d2.id, 200000, 1000000, 200000, 5)
        
        total_speed = repo.get_aggregate_download_speed()
        
        assert total_speed == 300000
    
    def test_get_download_history(self, db_session: Session, sample_video: Video) -> None:
        """Test getting download history for a video."""
        repo = DownloadRepository(db_session)
        
        # Create multiple download attempts
        for i in range(5):
            download = repo.create(video_id=sample_video.id, source_type="youtube")
            repo.update_status(download.id, "failed" if i < 2 else "completed")
        
        history = repo.get_download_history(sample_video.id, limit=10)
        
        assert len(history) == 5


class TestEncryptionJobRepository:
    """Tests for EncryptionJobRepository."""
    
    def test_create_job(self, db_session: Session, sample_video: Video) -> None:
        """Test creating an encryption job."""
        repo = EncryptionJobRepository(db_session)
        
        job = repo.create(
            video_id=sample_video.id,
            status="pending",
            bytes_total=1000000,
        )
        
        assert job.id is not None
        assert job.video_id == sample_video.id
        assert job.bytes_total == 1000000
    
    def test_get_active_jobs(self, db_session: Session, sample_videos: list[Video]) -> None:
        """Test getting active encryption jobs."""
        repo = EncryptionJobRepository(db_session)
        
        repo.create(video_id=sample_videos[0].id, status="encrypting")
        repo.create(video_id=sample_videos[1].id, status="encrypting")
        repo.create(video_id=sample_videos[2].id, status="completed")
        
        active = repo.get_active_jobs()
        
        assert len(active) == 2
    
    def test_update_progress(self, db_session: Session, sample_video: Video) -> None:
        """Test updating encryption progress."""
        repo = EncryptionJobRepository(db_session)
        
        job = repo.create(video_id=sample_video.id, bytes_total=1000000)
        
        updated = repo.update_progress(
            job_id=job.id,
            bytes_processed=750000,
            encrypt_speed=50000,
        )
        
        assert updated is not None
        assert updated.bytes_processed == 750000
        assert updated.progress_percent == 75.0
        assert updated.encrypt_speed == 50000
    
    def test_update_status_with_encrypted_ref(self, db_session: Session, sample_video: Video) -> None:
        """Test completing encryption with encrypted reference."""
        repo = EncryptionJobRepository(db_session)
        
        job = repo.create(video_id=sample_video.id)
        
        updated = repo.update_status(
            job_id=job.id,
            status="completed",
            encrypted_ref="QmTest123",
        )
        
        assert updated is not None
        assert updated.status == "completed"
        assert updated.encrypted_ref == "QmTest123"
        assert updated.completed_at is not None


class TestUploadJobRepository:
    """Tests for UploadJobRepository."""
    
    def test_create_job(self, db_session: Session, sample_video: Video) -> None:
        """Test creating an upload job."""
        repo = UploadJobRepository(db_session)
        
        job = repo.create(
            video_id=sample_video.id,
            target="ipfs",
            bytes_total=1000000,
        )
        
        assert job.id is not None
        assert job.target == "ipfs"
    
    def test_get_active_uploads(self, db_session: Session, sample_videos: list[Video]) -> None:
        """Test getting active uploads."""
        repo = UploadJobRepository(db_session)
        
        repo.create(video_id=sample_videos[0].id, target="ipfs", status="uploading")
        repo.create(video_id=sample_videos[1].id, target="arkiv", status="uploading")
        repo.create(video_id=sample_videos[2].id, target="ipfs", status="completed")
        
        active = repo.get_active_uploads()
        
        assert len(active) == 2
    
    def test_update_progress(self, db_session: Session, sample_video: Video) -> None:
        """Test updating upload progress."""
        repo = UploadJobRepository(db_session)
        
        job = repo.create(video_id=sample_video.id, target="ipfs", bytes_total=1000000)
        
        updated = repo.update_progress(
            job_id=job.id,
            bytes_uploaded=600000,
            upload_speed=80000,
        )
        
        assert updated is not None
        assert updated.bytes_uploaded == 600000
        assert updated.progress_percent == 60.0
    
    def test_complete_upload(self, db_session: Session, sample_video: Video) -> None:
        """Test completing an upload."""
        repo = UploadJobRepository(db_session)
        
        job = repo.create(video_id=sample_video.id, target="ipfs")
        
        completed = repo.complete_upload(
            job_id=job.id,
            remote_cid="QmRemote123",
            remote_url="https://ipfs.io/ipfs/QmRemote123",
        )
        
        assert completed is not None
        assert completed.status == "completed"
        assert completed.remote_cid == "QmRemote123"
        assert completed.remote_url == "https://ipfs.io/ipfs/QmRemote123"


class TestSyncJobRepository:
    """Tests for SyncJobRepository."""
    
    def test_create_job(self, db_session: Session, sample_video: Video) -> None:
        """Test creating a sync job."""
        repo = SyncJobRepository(db_session)
        
        job = repo.create(video_id=sample_video.id, status="pending")
        
        assert job.id is not None
        assert job.video_id == sample_video.id
    
    def test_get_active_syncs(self, db_session: Session, sample_videos: list[Video]) -> None:
        """Test getting active syncs."""
        repo = SyncJobRepository(db_session)
        
        repo.create(video_id=sample_videos[0].id, status="syncing")
        repo.create(video_id=sample_videos[1].id, status="syncing")
        repo.create(video_id=sample_videos[2].id, status="completed")
        
        active = repo.get_active_syncs()
        
        assert len(active) == 2
    
    def test_complete_sync(self, db_session: Session, sample_video: Video) -> None:
        """Test completing a sync job."""
        repo = SyncJobRepository(db_session)
        
        job = repo.create(video_id=sample_video.id)
        
        completed = repo.complete_sync(
            job_id=job.id,
            tx_hash="0xabc123",
            block_number=12345678,
            gas_used=150000,
        )
        
        assert completed is not None
        assert completed.status == "completed"
        assert completed.tx_hash == "0xabc123"
        assert completed.block_number == 12345678
        assert completed.gas_used == 150000


class TestAnalysisJobRepository:
    """Tests for AnalysisJobRepository."""
    
    def test_create_job(self, db_session: Session, sample_video: Video) -> None:
        """Test creating an analysis job."""
        repo = AnalysisJobRepository(db_session)
        
        job = repo.create(
            video_id=sample_video.id,
            analysis_type="vlm",
            model_name="llava",
            frames_total=100,
        )
        
        assert job.id is not None
        assert job.analysis_type == "vlm"
        assert job.model_name == "llava"
        assert job.frames_total == 100
    
    def test_get_active_analyses(self, db_session: Session, sample_videos: list[Video]) -> None:
        """Test getting active analyses."""
        repo = AnalysisJobRepository(db_session)
        
        repo.create(video_id=sample_videos[0].id, analysis_type="vlm", status="analyzing")
        repo.create(video_id=sample_videos[1].id, analysis_type="llm", status="analyzing")
        repo.create(video_id=sample_videos[2].id, analysis_type="vlm", status="completed")
        
        active = repo.get_active_analyses()
        
        assert len(active) == 2
    
    def test_update_progress(self, db_session: Session, sample_video: Video) -> None:
        """Test updating analysis progress."""
        repo = AnalysisJobRepository(db_session)
        
        job = repo.create(
            video_id=sample_video.id,
            analysis_type="vlm",
            frames_total=100,
        )
        
        updated = repo.update_progress(job_id=job.id, frames_processed=50)
        
        assert updated is not None
        assert updated.frames_processed == 50
        assert updated.progress_percent == 50.0
    
    def test_complete_analysis(self, db_session: Session, sample_video: Video) -> None:
        """Test completing analysis."""
        repo = AnalysisJobRepository(db_session)
        
        job = repo.create(video_id=sample_video.id, analysis_type="vlm")
        
        completed = repo.complete_analysis(
            job_id=job.id,
            output_file="/path/to/results.json",
        )
        
        assert completed is not None
        assert completed.status == "completed"
        assert completed.output_file == "/path/to/results.json"


class TestPipelineSnapshotRepository:
    """Tests for PipelineSnapshotRepository."""
    
    def test_get_or_create(self, db_session: Session, sample_video: Video) -> None:
        """Test getting or creating a snapshot."""
        repo = PipelineSnapshotRepository(db_session)
        
        snapshot = repo.get_or_create(sample_video.id)
        
        assert snapshot.id is not None
        assert snapshot.video_id == sample_video.id
        assert snapshot.current_stage == "pending"
    
    def test_get_or_create_existing(self, db_session: Session, sample_video: Video) -> None:
        """Test getting existing snapshot."""
        repo = PipelineSnapshotRepository(db_session)
        
        created = repo.get_or_create(sample_video.id)
        created.current_stage = "download"
        db_session.commit()
        
        fetched = repo.get_or_create(sample_video.id)
        
        assert fetched.id == created.id
        assert fetched.current_stage == "download"
    
    def test_get_active_videos(self, db_session: Session, sample_videos: list[Video]) -> None:
        """Test getting active videos."""
        repo = PipelineSnapshotRepository(db_session)
        
        # Create snapshots with different statuses
        for i, video in enumerate(sample_videos):
            snapshot = repo.get_or_create(video.id)
            snapshot.overall_status = "active" if i < 2 else "completed"
            snapshot.current_stage = "download"
        
        db_session.commit()
        
        active = repo.get_active_videos()
        
        assert len(active) == 2
    
    def test_get_aggregate_stats(self, db_session: Session, sample_videos: list[Video]) -> None:
        """Test getting aggregate stats."""
        repo = PipelineSnapshotRepository(db_session)
        
        # Create active snapshots in different stages
        stages = ["download", "encrypt", "upload"]
        for i, video in enumerate(sample_videos):
            snapshot = repo.get_or_create(video.id)
            snapshot.overall_status = "active"
            snapshot.current_stage = stages[i % len(stages)]
            snapshot.stage_speed = 100000 * (i + 1)
        
        db_session.commit()
        
        stats = repo.get_aggregate_stats()
        
        assert stats["active_count"] == 3
        assert stats["total_speed"] == 600000  # 100000 + 200000 + 300000
        assert stats["by_stage"]["download"] == 1
        assert stats["by_stage"]["encrypt"] == 1
        assert stats["by_stage"]["upload"] == 1
    
    def test_update_stage(self, db_session: Session, sample_video: Video) -> None:
        """Test updating stage."""
        repo = PipelineSnapshotRepository(db_session)
        
        snapshot = repo.update_stage(
            video_id=sample_video.id,
            stage="download",
            status="active",
            progress_percent=50.0,
            stage_speed=100000,
            stage_eta=60,
        )
        
        assert snapshot.current_stage == "download"
        assert snapshot.overall_status == "active"
        assert snapshot.stage_progress_percent == 50.0
        assert snapshot.stage_speed == 100000
        assert snapshot.stage_eta == 60
        assert snapshot.stage_started_at is not None
    
    def test_update_bytes_metrics(self, db_session: Session, sample_video: Video) -> None:
        """Test updating byte metrics."""
        repo = PipelineSnapshotRepository(db_session)
        
        repo.get_or_create(sample_video.id)
        
        updated = repo.update_bytes_metrics(
            video_id=sample_video.id,
            total_bytes=1000000,
            downloaded_bytes=500000,
            encrypted_bytes=400000,
            uploaded_bytes=300000,
        )
        
        assert updated is not None
        assert updated.total_bytes == 1000000
        assert updated.downloaded_bytes == 500000
        assert updated.encrypted_bytes == 400000
        assert updated.uploaded_bytes == 300000
    
    def test_mark_error(self, db_session: Session, sample_video: Video) -> None:
        """Test marking error state."""
        repo = PipelineSnapshotRepository(db_session)
        
        repo.get_or_create(sample_video.id)
        
        updated = repo.mark_error(
            video_id=sample_video.id,
            error_stage="upload",
            error_message="Connection timeout",
        )
        
        assert updated is not None
        assert updated.has_error is True
        assert updated.error_stage == "upload"
        assert updated.error_message == "Connection timeout"
        assert updated.overall_status == "failed"
    
    def test_mark_completed(self, db_session: Session, sample_video: Video) -> None:
        """Test marking pipeline as completed."""
        repo = PipelineSnapshotRepository(db_session)
        
        repo.get_or_create(sample_video.id)
        
        updated = repo.mark_completed(sample_video.id)
        
        assert updated is not None
        assert updated.overall_status == "completed"
        assert updated.stage_progress_percent == 100.0
        assert updated.pipeline_completed_at is not None


class TestJobHistoryRepository:
    """Tests for JobHistoryRepository."""
    
    def test_get_video_pipeline_history(self, db_session: Session, sample_video: Video) -> None:
        """Test getting complete pipeline history for a video."""
        repo = JobHistoryRepository(db_session)
        
        # Create various job records
        download_repo = DownloadRepository(db_session)
        download_repo.create(video_id=sample_video.id, source_type="youtube")
        
        encrypt_repo = EncryptionJobRepository(db_session)
        encrypt_repo.create(video_id=sample_video.id)
        
        upload_repo = UploadJobRepository(db_session)
        upload_repo.create(video_id=sample_video.id, target="ipfs")
        
        sync_repo = SyncJobRepository(db_session)
        sync_repo.create(video_id=sample_video.id)
        
        analysis_repo = AnalysisJobRepository(db_session)
        analysis_repo.create(video_id=sample_video.id, analysis_type="vlm")
        
        # Get history
        history = repo.get_video_pipeline_history(sample_video.id)
        
        assert len(history["downloads"]) == 1
        assert len(history["encryption_jobs"]) == 1
        assert len(history["upload_jobs"]) == 1
        assert len(history["sync_jobs"]) == 1
        assert len(history["analysis_jobs"]) == 1
    
    def test_get_failed_jobs(self, db_session: Session, sample_videos: list[Video]) -> None:
        """Test getting failed jobs across all types."""
        repo = JobHistoryRepository(db_session)
        
        # Create failed jobs
        download_repo = DownloadRepository(db_session)
        download = download_repo.create(video_id=sample_videos[0].id, source_type="youtube")
        download_repo.update_status(download.id, "failed", "Network error")
        
        upload_repo = UploadJobRepository(db_session)
        upload = upload_repo.create(video_id=sample_videos[1].id, target="ipfs")
        upload.status = "failed"
        upload.error_message = "Upload timeout"
        db_session.commit()
        
        failed = repo.get_failed_jobs(limit=10)
        
        assert len(failed) == 2
        stages = {f["stage"] for f in failed}
        assert "download" in stages
        assert "upload" in stages


class TestSpeedHistoryRepository:
    """Tests for SpeedHistoryRepository."""
    
    def test_create(self, db_session: Session, sample_video: Video) -> None:
        """Test creating speed history entry."""
        repo = SpeedHistoryRepository(db_session)
        
        entry = repo.create(
            video_id=sample_video.id,
            stage="download",
            speed=100000,
            progress=50.0,
            bytes_processed=500000,
        )
        
        assert entry.id is not None
        assert entry.speed == 100000
    
    def test_get_speed_history(self, db_session: Session, sample_video: Video) -> None:
        """Test getting speed history."""
        repo = SpeedHistoryRepository(db_session)
        
        # Create entries
        for i in range(5):
            repo.create(
                video_id=sample_video.id,
                stage="download",
                speed=100000 + i * 1000,
                progress=float(i * 20),
            )
        
        history = repo.get_speed_history(sample_video.id, "download", minutes=5)
        
        assert len(history) == 5
    
    def test_get_aggregate_speeds(self, db_session: Session, sample_video: Video) -> None:
        """Test getting aggregate speeds."""
        repo = SpeedHistoryRepository(db_session)
        
        # Create entries for different stages
        for stage in ["download", "encrypt", "upload"]:
            for i in range(3):
                repo.create(
                    video_id=sample_video.id,
                    stage=stage,
                    speed=100000,
                    progress=float(i * 33),
                )
        
        aggregates = repo.get_aggregate_speeds(minutes=5)
        
        assert len(aggregates) >= 3
    
    def test_cleanup_old_samples(self, db_session: Session, sample_video: Video) -> None:
        """Test cleaning up old samples."""
        repo = SpeedHistoryRepository(db_session)
        
        # Create a recent entry
        repo.create(video_id=sample_video.id, stage="download", speed=100000)
        
        # Create an old entry
        old_entry = SpeedHistory(
            video_id=sample_video.id,
            stage="download",
            speed=50000,
            timestamp=datetime.now(timezone.utc) - timedelta(hours=48),
        )
        db_session.add(old_entry)
        db_session.commit()
        
        # Cleanup samples older than 24 hours
        deleted = repo.cleanup_old_samples(hours=24)
        
        assert deleted == 1
        
        # Verify only recent entry remains
        remaining = db_session.query(SpeedHistory).count()
        assert remaining == 1


class TestRepositoryFactory:
    """Tests for RepositoryFactory."""
    
    def test_factory_creates_repositories(self, db_session: Session) -> None:
        """Test that factory creates all repository types."""
        factory = RepositoryFactory(db_session)
        
        assert factory.videos is not None
        assert factory.jobs is not None
        assert factory.executions is not None
        assert factory.torrents is not None
        assert factory.downloads is not None
        assert factory.encryption_jobs is not None
        assert factory.upload_jobs is not None
        assert factory.sync_jobs is not None
        assert factory.analysis_jobs is not None
        assert factory.pipeline_snapshots is not None
        assert factory.job_history is not None
        assert factory.speed_history is not None
    
    def test_factory_reuses_instances(self, db_session: Session) -> None:
        """Test that factory reuses repository instances."""
        factory = RepositoryFactory(db_session)
        
        # Get same repository twice
        repo1 = factory.downloads
        repo2 = factory.downloads
        
        assert repo1 is repo2
    
    def test_factory_repository_functionality(self, db_session: Session, sample_video: Video) -> None:
        """Test that factory repositories work correctly."""
        factory = RepositoryFactory(db_session)
        
        # Use factory to create a download
        download = factory.downloads.create(
            video_id=sample_video.id,
            source_type="youtube",
        )
        
        assert download.id is not None
        
        # Use factory to get the download back
        fetched = factory.downloads.get_by_id(download.id)
        
        assert fetched is not None
        assert fetched.id == download.id
