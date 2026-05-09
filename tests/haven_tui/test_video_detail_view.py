"""Tests for the Video Detail View.

This module tests the VideoDetailView, VideoDetailScreen, and related widgets
to ensure they meet the acceptance criteria for Task 3.4.
"""

import asyncio
import os
import tempfile
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from unittest.mock import MagicMock, Mock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

import sys
sys.path.insert(0, '/home/tower/Documents/workspace/haven-cli')

from haven_cli.database.models import (
    Base,
    Video,
    Download,
    EncryptionJob,
    UploadJob,
    SyncJob,
    AnalysisJob,
    PipelineSnapshot,
)
from haven_tui.data.repositories import JobHistoryRepository, PipelineSnapshotRepository
from haven_tui.models.video_view import VideoView, PipelineStage
from haven_tui.ui.views.video_detail import (
    VideoDetailView,
    VideoDetailScreen,
    VideoInfoWidget,
    PipelineProgressWidget,
    ResultsWidget,
    VideoDetailHeader,
    VideoDetailFooter,
    StageDisplayInfo,
    PipelineStageWidget,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def temp_db_path():
    """Create a temporary database file."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    os.unlink(path)


@pytest.fixture
def database_engine(temp_db_path):
    """Create a database engine with all tables."""
    engine = create_engine(f"sqlite:///{temp_db_path}")
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(database_engine) -> Session:
    """Create a fresh database session for each test."""
    SessionLocal = sessionmaker(bind=database_engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def job_history_repo(db_session) -> JobHistoryRepository:
    """Create a JobHistoryRepository for testing."""
    return JobHistoryRepository(db_session)


@pytest.fixture
def snapshot_repo(db_session) -> PipelineSnapshotRepository:
    """Create a PipelineSnapshotRepository for testing."""
    return PipelineSnapshotRepository(db_session)


# =============================================================================
# Test Data Helpers
# =============================================================================

def create_test_video(session: Session, title: str = "Test Video") -> Video:
    """Helper to create a test video."""
    video = Video(
        source_path=f"/test/{title.replace(' ', '_')}.mp4",
        title=title,
        duration=300.0,
        file_size=10485760,  # 10 MB
        plugin_name="youtube",
    )
    session.add(video)
    session.commit()
    session.refresh(video)
    return video


def create_test_download(
    session: Session,
    video_id: int,
    status: str = "completed",
    progress: float = 100.0,
) -> Download:
    """Helper to create a test download record."""
    download = Download(
        video_id=video_id,
        source_type="youtube",
        status=status,
        progress_percent=progress,
        bytes_downloaded=10485760 if status == "completed" else int(10485760 * progress / 100),
        bytes_total=10485760,
        download_rate=1024000 if status == "downloading" else 0,
        eta_seconds=120 if status == "downloading" else None,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        completed_at=datetime.now(timezone.utc) if status == "completed" else None,
        error_message=None if status != "failed" else "Download error",
    )
    session.add(download)
    session.commit()
    session.refresh(download)
    return download


def create_test_encryption_job(
    session: Session,
    video_id: int,
    status: str = "completed",
    progress: float = 100.0,
) -> EncryptionJob:
    """Helper to create a test encryption job."""
    job = EncryptionJob(
        video_id=video_id,
        status=status,
        progress_percent=progress,
        bytes_processed=10485760 if status == "completed" else int(10485760 * progress / 100),
        bytes_total=10485760,
        encrypt_speed=512000 if status == "encrypting" else 0,
        encrypted_ref="test-encrypted-ref" if status == "completed" else None,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=3),
        completed_at=datetime.now(timezone.utc) if status == "completed" else None,
        error_message=None if status != "failed" else "Encryption error",
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def create_test_upload_job(
    session: Session,
    video_id: int,
    status: str = "completed",
    progress: float = 100.0,
) -> UploadJob:
    """Helper to create a test upload job."""
    job = UploadJob(
        video_id=video_id,
        target="ipfs",
        status=status,
        progress_percent=progress,
        bytes_uploaded=10485760 if status == "completed" else int(10485760 * progress / 100),
        bytes_total=10485760,
        upload_speed=256000 if status == "uploading" else 0,
        remote_cid="bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fxyz" if status == "completed" else None,
        remote_url="https://ipfs.io/ipfs/test" if status == "completed" else None,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        completed_at=datetime.now(timezone.utc) if status == "completed" else None,
        error_message=None if status != "failed" else "Upload error",
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def create_test_sync_job(
    session: Session,
    video_id: int,
    status: str = "completed",
) -> SyncJob:
    """Helper to create a test sync job."""
    job = SyncJob(
        video_id=video_id,
        status=status,
        tx_hash="0x1234567890abcdef" if status == "completed" else None,
        block_number=12345 if status == "completed" else None,
        gas_used=50000 if status == "completed" else None,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        completed_at=datetime.now(timezone.utc) if status == "completed" else None,
        error_message=None if status != "failed" else "Sync error",
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def create_test_analysis_job(
    session: Session,
    video_id: int,
    status: str = "completed",
    progress: float = 100.0,
) -> AnalysisJob:
    """Helper to create a test analysis job."""
    job = AnalysisJob(
        video_id=video_id,
        status=status,
        progress_percent=progress,
        frames_processed=300 if status == "completed" else int(300 * progress / 100),
        frames_total=300,
        analysis_type="vlm",
        model_name="test-model",
        output_file="/test/analysis.json" if status == "completed" else None,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=4),
        completed_at=datetime.now(timezone.utc) if status == "completed" else None,
        error_message=None if status != "failed" else "Analysis error",
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def create_pipeline_snapshot(
    session: Session,
    video_id: int,
    stage: str = "complete",
    status: str = "completed",
) -> PipelineSnapshot:
    """Helper to create a pipeline snapshot."""
    snapshot = PipelineSnapshot(
        video_id=video_id,
        current_stage=stage,
        overall_status=status,
        stage_progress_percent=100.0,
        stage_speed=0,
        stage_eta=None,
        total_bytes=10485760,
        downloaded_bytes=10485760,
        encrypted_bytes=10485760,
        uploaded_bytes=10485760,
        has_error=False,
        error_message=None,
        stage_started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        pipeline_started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        pipeline_completed_at=datetime.now(timezone.utc) if status == "completed" else None,
        updated_at=datetime.now(timezone.utc),
    )
    session.add(snapshot)
    session.commit()
    session.refresh(snapshot)
    return snapshot


# =============================================================================
# Unit Tests for StageDisplayInfo
# =============================================================================

class TestStageDisplayInfo:
    """Tests for the StageDisplayInfo dataclass."""
    
    def test_stage_display_info_creation(self):
        """Test creating StageDisplayInfo."""
        info = StageDisplayInfo(
            name="download",
            status="completed",
            progress=100.0,
            detail="Done in 2m 30s",
            symbol="●",
        )
        
        assert info.name == "download"
        assert info.status == "completed"
        assert info.progress == 100.0
        assert info.detail == "Done in 2m 30s"
        assert info.symbol == "●"
    
    def test_stage_display_info_with_error(self):
        """Test StageDisplayInfo with error."""
        info = StageDisplayInfo(
            name="upload",
            status="failed",
            progress=50.0,
            detail="Error: Connection timeout",
            symbol="✗",
            error_message="Connection timeout",
        )
        
        assert info.status == "failed"
        assert info.error_message == "Connection timeout"


# =============================================================================
# Unit Tests for PipelineStageWidget
# =============================================================================

class TestPipelineStageWidget:
    """Tests for the PipelineStageWidget."""
    
    def test_progress_bar_zero(self):
        """Test progress bar at 0%."""
        info = StageDisplayInfo(
            name="download",
            status="pending",
            progress=0.0,
            detail="Pending",
            symbol="○",
        )
        widget = PipelineStageWidget(info)
        result = widget._format_progress_bar(0.0, 10)
        assert "░" in result
    
    def test_progress_bar_complete(self):
        """Test progress bar at 100%."""
        info = StageDisplayInfo(
            name="download",
            status="completed",
            progress=100.0,
            detail="Done",
            symbol="●",
        )
        widget = PipelineStageWidget(info)
        result = widget._format_progress_bar(100.0, 10)
        assert "█" in result
    
    def test_progress_bar_partial(self):
        """Test progress bar at 50%."""
        info = StageDisplayInfo(
            name="download",
            status="active",
            progress=50.0,
            detail="50%",
            symbol="◐",
        )
        widget = PipelineStageWidget(info)
        result = widget._format_progress_bar(50.0, 10)
        assert "█" in result
        assert "░" in result
    
    def test_style_class_mapping(self):
        """Test status to style class mapping."""
        info = StageDisplayInfo(
            name="download",
            status="completed",
            progress=100.0,
            detail="Done",
            symbol="●",
        )
        widget = PipelineStageWidget(info)
        
        assert widget._get_style_class("pending") == "stage-pending"
        assert widget._get_style_class("active") == "stage-active"
        assert widget._get_style_class("completed") == "stage-completed"
        assert widget._get_style_class("failed") == "stage-failed"
        assert widget._get_style_class("skipped") == "stage-skipped"


# =============================================================================
# Unit Tests for VideoInfoWidget
# =============================================================================

class TestVideoInfoWidget:
    """Tests for the VideoInfoWidget."""
    
    def test_video_info_widget_creation(self):
        """Test VideoInfoWidget creation."""
        widget = VideoInfoWidget()
        assert widget._video is None
    
    def test_truncate_text_short(self):
        """Test text truncation for short text."""
        widget = VideoInfoWidget()
        text = "Short"
        result = widget._truncate_text(text, 50)
        assert result == text
    
    def test_truncate_text_long(self):
        """Test text truncation for long text."""
        widget = VideoInfoWidget()
        text = "A" * 60
        result = widget._truncate_text(text, 50)
        assert len(result) <= 50
        assert result.endswith("...")


# =============================================================================
# Unit Tests for PipelineProgressWidget
# =============================================================================

class TestPipelineProgressWidget:
    """Tests for the PipelineProgressWidget."""
    
    def test_empty_stages(self):
        """Test rendering with no stages."""
        widget = PipelineProgressWidget()
        widget._stages = []
        result = widget._render()
        assert "No pipeline data" in result
    
    def test_single_stage(self):
        """Test rendering with a single stage."""
        widget = PipelineProgressWidget()
        stage = StageDisplayInfo(
            name="download",
            status="completed",
            progress=100.0,
            detail="Done",
            symbol="●",
        )
        widget.set_stages([stage])
        result = widget._render()
        assert "download" in result
        assert "●" in result
    
    def test_multiple_stages(self):
        """Test rendering with multiple stages."""
        widget = PipelineProgressWidget()
        stages = [
            StageDisplayInfo("download", "completed", 100.0, "Done", "●"),
            StageDisplayInfo("encrypt", "active", 50.0, "50%", "◐"),
            StageDisplayInfo("upload", "pending", 0.0, "Pending", "○"),
        ]
        widget.set_stages(stages)
        result = widget._render()
        assert "download" in result
        assert "encrypt" in result
        assert "upload" in result


# =============================================================================
# Unit Tests for ResultsWidget
# =============================================================================

class TestResultsWidget:
    """Tests for the ResultsWidget."""
    
    def test_empty_results(self):
        """Test rendering with no results."""
        widget = ResultsWidget()
        result = widget._render()
        assert "No results" in result
    
    def test_cid_display(self):
        """Test CID display."""
        widget = ResultsWidget()
        widget.set_results(cid="bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fxyz")
        result = widget._render()
        assert "bafybei" in result
    
    def test_encryption_status(self):
        """Test encryption status display."""
        widget = ResultsWidget()
        widget.set_results(is_encrypted=True)
        result = widget._render()
        assert "Encrypted" in result
        assert "Haven-AOL" in result
    
    def test_analysis_complete(self):
        """Test analysis complete display."""
        widget = ResultsWidget()
        widget.set_results(analysis_complete=True)
        result = widget._render()
        assert "AI Analysis" in result
    
    def test_tx_hash(self):
        """Test transaction hash display."""
        widget = ResultsWidget()
        widget.set_results(tx_hash="0x1234567890abcdef")
        result = widget._render()
        assert "0x123456" in result


# =============================================================================
# Unit Tests for VideoDetailHeader
# =============================================================================

class TestVideoDetailHeader:
    """Tests for the VideoDetailHeader."""
    
    def test_header_creation(self):
        """Test header creation."""
        header = VideoDetailHeader()
        assert header._title == "Video Details"
    
    def test_set_title(self):
        """Test setting title."""
        header = VideoDetailHeader()
        header.set_title("Test Video Title")
        assert header._title == "Test Video Title"
    
    def test_truncate_long_title(self):
        """Test title truncation."""
        header = VideoDetailHeader()
        long_title = "A" * 100
        result = header._truncate_title(long_title, 60)
        assert len(result) <= 60
        assert result.endswith("...")


# =============================================================================
# Unit Tests for VideoDetailFooter
# =============================================================================

class TestVideoDetailFooter:
    """Tests for the VideoDetailFooter."""
    
    def test_footer_creation(self):
        """Test footer creation."""
        footer = VideoDetailFooter()
        assert footer is not None


# =============================================================================
# Integration Tests for VideoDetailView
# =============================================================================

class TestVideoDetailView:
    """Integration tests for VideoDetailView."""
    
    def test_view_initialization(self, job_history_repo, snapshot_repo):
        """Test VideoDetailView can be initialized."""
        view = VideoDetailView(
            video_id=1,
            job_repo=job_history_repo,
            snapshot_repo=snapshot_repo,
        )
        
        assert view.video_id == 1
        assert view.job_repo == job_history_repo
        assert view.snapshot_repo == snapshot_repo
        assert view.screen is None
    
    def test_view_creates_screen(self, job_history_repo, snapshot_repo):
        """Test VideoDetailView creates a screen."""
        view = VideoDetailView(
            video_id=1,
            job_repo=job_history_repo,
            snapshot_repo=snapshot_repo,
        )
        
        screen = view.create_screen()
        
        assert screen is not None
        assert isinstance(screen, VideoDetailScreen)
        assert screen.video_id == 1


# =============================================================================
# Integration Tests for VideoDetailScreen
# =============================================================================

class TestVideoDetailScreen:
    """Integration tests for VideoDetailScreen."""
    
    def test_screen_bindings(self):
        """Test that all keyboard bindings are defined."""
        screen = VideoDetailScreen(video_id=1)
        
        bindings = screen.BINDINGS
        binding_keys = [b.key for b in bindings]
        
        assert "b" in binding_keys  # Back
        assert "r" in binding_keys  # Retry
        assert "l" in binding_keys  # Logs
        assert "g" in binding_keys  # Graph
        assert "q" in binding_keys  # Quit
    
    def test_reactive_video_id(self):
        """Test that video_id is a reactive property."""
        screen = VideoDetailScreen(video_id=1)
        
        assert hasattr(screen, 'video_id')
        assert screen.video_id == 1
        
        screen.video_id = 2
        assert screen.video_id == 2


# =============================================================================
# Integration Tests with Database
# =============================================================================

class TestVideoDetailWithDatabase:
    """Integration tests requiring database."""
    
    def test_get_video_pipeline_history(self, db_session, job_history_repo):
        """AC1: Queries all job tables (downloads, analysis_jobs, etc.)."""
        # Create test video
        video = create_test_video(db_session)
        
        # Create jobs in all stages
        download = create_test_download(db_session, video.id, "completed", 100.0)
        analysis = create_test_analysis_job(db_session, video.id, "completed", 100.0)
        encryption = create_test_encryption_job(db_session, video.id, "completed", 100.0)
        upload = create_test_upload_job(db_session, video.id, "completed", 100.0)
        sync = create_test_sync_job(db_session, video.id, "completed")
        
        # Get pipeline history
        history = job_history_repo.get_video_pipeline_history(video.id)
        
        # Verify all job tables are queried
        assert 'downloads' in history
        assert 'analysis_jobs' in history
        assert 'encryption_jobs' in history
        assert 'upload_jobs' in history
        assert 'sync_jobs' in history
        
        # Verify jobs are returned
        assert len(history['downloads']) == 1
        assert len(history['analysis_jobs']) == 1
        assert len(history['encryption_jobs']) == 1
        assert len(history['upload_jobs']) == 1
        assert len(history['sync_jobs']) == 1
    
    def test_get_latest_cid(self, db_session, job_history_repo):
        """AC1: Gets CID from upload_jobs."""
        video = create_test_video(db_session)
        upload = create_test_upload_job(db_session, video.id, "completed", 100.0)
        
        latest_cid = job_history_repo.get_latest_cid(video.id)
        
        assert latest_cid is not None
        assert latest_cid == upload.remote_cid
    
    def test_is_encrypted(self, db_session, job_history_repo):
        """AC1: Checks encryption status from encryption_jobs."""
        video = create_test_video(db_session)
        encryption = create_test_encryption_job(db_session, video.id, "completed", 100.0)
        
        is_encrypted = job_history_repo.is_encrypted(video.id)
        
        assert is_encrypted is True
    
    def test_show_pipeline_stages(self, db_session, job_history_repo):
        """AC2: Shows all pipeline stages with status from respective tables."""
        video = create_test_video(db_session)
        
        # Create jobs at different stages
        download = create_test_download(db_session, video.id, "completed", 100.0)
        analysis = create_test_analysis_job(db_session, video.id, "skipped", 0.0)
        encryption = create_test_encryption_job(db_session, video.id, "completed", 100.0)
        upload = create_test_upload_job(db_session, video.id, "uploading", 82.0)
        sync = create_test_sync_job(db_session, video.id, "pending")
        
        history = job_history_repo.get_video_pipeline_history(video.id)
        
        # Verify each stage has correct status
        assert history['downloads'][0].status == "completed"
        assert history['analysis_jobs'][0].status == "skipped"
        assert history['encryption_jobs'][0].status == "completed"
        assert history['upload_jobs'][0].status == "uploading"
        assert history['sync_jobs'][0].status == "pending"
    
    def test_progress_bar_from_job_records(self, db_session, job_history_repo):
        """AC3: Progress bar for each stage from job records."""
        video = create_test_video(db_session)
        
        # Create job with specific progress
        upload = create_test_upload_job(db_session, video.id, "uploading", 82.5)
        
        history = job_history_repo.get_video_pipeline_history(video.id)
        
        assert history['upload_jobs'][0].progress_percent == 82.5
    
    def test_timing_information_completed(self, db_session, job_history_repo):
        """AC4: Timing information (duration for completed)."""
        video = create_test_video(db_session)
        
        # Create completed job with timing
        started = datetime.now(timezone.utc) - timedelta(minutes=5)
        completed = datetime.now(timezone.utc)
        
        download = Download(
            video_id=video.id,
            source_type="youtube",
            status="completed",
            progress_percent=100.0,
            started_at=started,
            completed_at=completed,
        )
        db_session.add(download)
        db_session.commit()
        
        history = job_history_repo.get_video_pipeline_history(video.id)
        job = history['downloads'][0]
        
        assert job.started_at is not None
        assert job.completed_at is not None
        
        # Calculate duration
        duration = (job.completed_at - job.started_at).total_seconds()
        assert duration > 0
    
    def test_timing_information_active(self, db_session, job_history_repo):
        """AC4: Timing information (ETA for active)."""
        video = create_test_video(db_session)
        
        # Create active download with ETA
        download = create_test_download(db_session, video.id, "downloading", 50.0)
        download.eta_seconds = 120  # 2 minutes
        db_session.commit()
        
        history = job_history_repo.get_video_pipeline_history(video.id)
        job = history['downloads'][0]
        
        assert job.eta_seconds == 120
    
    def test_error_messages_for_failed_stages(self, db_session, job_history_repo):
        """AC5: Error messages for failed stages from job.error_message."""
        video = create_test_video(db_session)
        
        # Create failed job with error message
        upload = UploadJob(
            video_id=video.id,
            target="ipfs",
            status="failed",
            error_message="Connection timeout: could not reach IPFS node",
        )
        db_session.add(upload)
        db_session.commit()
        
        history = job_history_repo.get_video_pipeline_history(video.id)
        job = history['upload_jobs'][0]
        
        assert job.status == "failed"
        assert job.error_message is not None
        assert "timeout" in job.error_message
    
    def test_final_results(self, db_session, job_history_repo):
        """AC6: Final results (CID, encryption status, etc.)."""
        video = create_test_video(db_session)
        
        # Create completed jobs
        encryption = create_test_encryption_job(db_session, video.id, "completed", 100.0)
        upload = create_test_upload_job(db_session, video.id, "completed", 100.0)
        analysis = create_test_analysis_job(db_session, video.id, "completed", 100.0)
        sync = create_test_sync_job(db_session, video.id, "completed")
        
        # Get results
        latest_cid = job_history_repo.get_latest_cid(video.id)
        is_encrypted = job_history_repo.is_encrypted(video.id)
        sync_info = job_history_repo.get_sync_info(video.id)
        
        # Verify all results
        assert latest_cid == upload.remote_cid
        assert is_encrypted is True
        assert sync_info is not None
        assert sync_info['tx_hash'] == sync.tx_hash


# =============================================================================
# Acceptance Criteria Tests
# =============================================================================

class TestAcceptanceCriteria:
    """Tests for meeting the acceptance criteria for Task 3.4."""
    
    def test_ac1_queries_all_job_tables(self, db_session, job_history_repo):
        """AC1: Queries all job tables (downloads, analysis_jobs, encryption_jobs, upload_jobs, sync_jobs)."""
        video = create_test_video(db_session)
        
        # Create one job in each table
        create_test_download(db_session, video.id)
        create_test_analysis_job(db_session, video.id)
        create_test_encryption_job(db_session, video.id)
        create_test_upload_job(db_session, video.id)
        create_test_sync_job(db_session, video.id)
        
        history = job_history_repo.get_video_pipeline_history(video.id)
        
        # Verify all 5 job tables are queried
        assert 'downloads' in history
        assert 'analysis_jobs' in history
        assert 'encryption_jobs' in history
        assert 'upload_jobs' in history
        assert 'sync_jobs' in history
    
    def test_ac2_shows_all_pipeline_stages(self, db_session, job_history_repo):
        """AC2: Shows all pipeline stages with status from respective tables."""
        video = create_test_video(db_session)
        
        # Create jobs with different statuses
        create_test_download(db_session, video.id, "completed", 100.0)
        create_test_analysis_job(db_session, video.id, "completed", 100.0)
        create_test_encryption_job(db_session, video.id, "active", 50.0)
        create_test_upload_job(db_session, video.id, "pending", 0.0)
        create_test_sync_job(db_session, video.id, "pending")
        
        history = job_history_repo.get_video_pipeline_history(video.id)
        
        # Verify all stages are shown with correct status
        assert history['downloads'][0].status == "completed"
        assert history['analysis_jobs'][0].status == "completed"
        assert history['encryption_jobs'][0].status == "active"
        assert history['upload_jobs'][0].status == "pending"
        assert history['sync_jobs'][0].status == "pending"
    
    def test_ac3_progress_bar_for_each_stage(self):
        """AC3: Progress bar for each stage from job records."""
        stages = [
            StageDisplayInfo("download", "completed", 100.0, "Done", "●"),
            StageDisplayInfo("analysis", "completed", 100.0, "Done", "●"),
            StageDisplayInfo("encrypt", "active", 45.0, "45%", "◐"),
            StageDisplayInfo("upload", "pending", 0.0, "Pending", "○"),
            StageDisplayInfo("sync", "pending", 0.0, "Pending", "○"),
        ]
        
        widget = PipelineProgressWidget()
        widget.set_stages(stages)
        result = widget._render()
        
        # Verify progress bars are rendered
        assert "█" in result  # Completed portions
        assert "░" in result  # Empty portions
    
    def test_ac4_timing_information(self, db_session, job_history_repo):
        """AC4: Timing information (duration for completed, ETA for active)."""
        video = create_test_video(db_session)
        
        # Completed job with timing
        create_test_download(db_session, video.id, "completed", 100.0)
        
        # Active job with ETA
        download2 = create_test_download(db_session, video.id, "downloading", 50.0)
        download2.eta_seconds = 300
        db_session.commit()
        
        history = job_history_repo.get_video_pipeline_history(video.id)
        
        # Verify timing information
        completed_job = [j for j in history['downloads'] if j.status == "completed"][0]
        active_job = [j for j in history['downloads'] if j.status == "downloading"][0]
        
        assert completed_job.completed_at is not None
        assert active_job.eta_seconds == 300
    
    def test_ac5_error_messages(self, db_session, job_history_repo):
        """AC5: Error messages for failed stages from job.error_message."""
        video = create_test_video(db_session)
        
        # Failed upload job with error
        upload = UploadJob(
            video_id=video.id,
            target="ipfs",
            status="failed",
            error_message="IPFS daemon not responding",
        )
        db_session.add(upload)
        db_session.commit()
        
        history = job_history_repo.get_video_pipeline_history(video.id)
        
        assert history['upload_jobs'][0].status == "failed"
        assert "daemon" in history['upload_jobs'][0].error_message
    
    def test_ac6_final_results(self, db_session, job_history_repo):
        """AC6: Final results (CID from upload_jobs, encryption status, etc.)."""
        video = create_test_video(db_session)
        
        # Create completed upload job with CID
        upload = create_test_upload_job(db_session, video.id, "completed", 100.0)
        encryption = create_test_encryption_job(db_session, video.id, "completed", 100.0)
        
        # Get results
        cid = job_history_repo.get_latest_cid(video.id)
        is_encrypted = job_history_repo.is_encrypted(video.id)
        
        assert cid is not None
        assert cid.startswith("bafy")
        assert is_encrypted is True
    
    def test_ac7_navigation_back(self):
        """AC7: Navigation back to list view."""
        screen = VideoDetailScreen(video_id=1)
        
        # Verify back action exists
        assert hasattr(screen, 'action_back')
        
        # Verify back binding is defined
        binding_keys = [b.key for b in screen.BINDINGS]
        assert "b" in binding_keys


# =============================================================================
# Performance Tests
# =============================================================================

class TestVideoDetailPerformance:
    """Performance tests for video detail view."""
    
    def test_stage_rendering_performance(self):
        """Test stage rendering performance."""
        import time
        
        stages = [
            StageDisplayInfo(f"stage_{i}", "completed", 100.0, "Done", "●")
            for i in range(100)
        ]
        
        widget = PipelineProgressWidget()
        
        start = time.time()
        widget.set_stages(stages)
        widget._render()
        elapsed = time.time() - start
        
        # Should render 100 stages in less than 1 second
        assert elapsed < 1.0
    
    def test_progress_bar_formatting_performance(self):
        """Test progress bar formatting performance."""
        import time
        
        widget = PipelineStageWidget(
            StageDisplayInfo("test", "active", 50.0, "50%", "◐")
        )
        
        start = time.time()
        for i in range(1000):
            widget._format_progress_bar(float(i % 100), 20)
        elapsed = time.time() - start
        
        # Should format 1000 progress bars in less than 1 second
        assert elapsed < 1.0
