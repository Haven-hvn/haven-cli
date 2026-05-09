"""Video Detail View for Haven TUI.

This module provides a detailed view for a single video showing complete
pipeline state from all job tables, similar to aria2tui's download detail view.
"""

from __future__ import annotations

from typing import Optional, Any, Dict, List
from datetime import datetime
from dataclasses import dataclass

from textual.widgets import Static, Label, Button
from textual.containers import Container, Vertical, Horizontal, Grid
from textual.reactive import reactive
from textual.screen import Screen
from textual.binding import Binding

from haven_tui.data.repositories import JobHistoryRepository, PipelineSnapshotRepository, SpeedHistoryRepository
from haven_tui.models.video_view import VideoView, PipelineStage
from haven_tui.ui.components.speed_graph import SpeedGraphComponent
from haven_tui.ui.views.event_log import VideoLogsScreen
from haven_cli.database.models import (
    Download, EncryptionJob, UploadJob, SyncJob, AnalysisJob
)

# Import StateManager for fallback when database records don't exist yet
from haven_tui.core.state_manager import StateManager, VideoState

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from haven_tui.core.state_manager import StateManager


@dataclass
class StageDisplayInfo:
    """Display information for a pipeline stage."""
    name: str
    status: str
    progress: float
    detail: str
    symbol: str
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class PipelineStageWidget(Static):
    """Widget displaying a single pipeline stage with progress bar."""
    
    DEFAULT_CSS = """
    PipelineStageWidget {
        height: 1;
        width: 100%;
        padding: 0 1;
    }
    
    PipelineStageWidget > .stage-pending { color: $text-muted; }
    PipelineStageWidget > .stage-active { color: $accent; }
    PipelineStageWidget > .stage-completed { color: $success; }
    PipelineStageWidget > .stage-failed { color: $error; }
    PipelineStageWidget > .stage-skipped { color: $warning; }
    """
    
    def __init__(self, stage_info: StageDisplayInfo, **kwargs: Any) -> None:
        """Initialize the stage widget.
        
        Args:
            stage_info: Information about the stage to display
            **kwargs: Additional arguments passed to Static
        """
        super().__init__(**kwargs)
        self.stage_info = stage_info
    
    def compose(self):
        """Compose the stage display."""
        return []
    
    def on_mount(self) -> None:
        """Update display on mount."""
        self._update_display()
    
    def _update_display(self) -> None:
        """Update the stage display."""
        info = self.stage_info
        
        # Get style class based on status
        style_class = self._get_style_class(info.status)
        
        # Build progress bar
        progress_bar = self._format_progress_bar(info.progress, 20)
        
        # Format line
        line = (
            f"[{style_class}]{info.symbol} {info.name:12}"
            f" {progress_bar}"
            f" {info.detail}[/{style_class}]"
        )
        
        self.update(line)
    
    def _get_style_class(self, status: str) -> str:
        """Get CSS style class for status."""
        return {
            "pending": "stage-pending",
            "active": "stage-active",
            "completed": "stage-completed",
            "failed": "stage-failed",
            "skipped": "stage-skipped",
        }.get(status, "stage-pending")
    
    def _format_progress_bar(self, progress: float, width: int = 20) -> str:
        """Format a progress bar using Unicode block characters.
        
        Args:
            progress: Progress percentage (0.0 - 100.0)
            width: Width of the progress bar in characters
            
        Returns:
            Formatted progress bar string
        """
        if progress <= 0:
            return "░" * width
        elif progress >= 100:
            return "█" * width
        
        filled = int((progress / 100.0) * width)
        empty = width - filled
        return "█" * filled + "░" * empty


class PipelineProgressWidget(Static):
    """Widget displaying all pipeline stages for a video."""
    
    DEFAULT_CSS = """
    PipelineProgressWidget {
        width: 100%;
        height: auto;
        padding: 1;
        border: solid $primary;
    }
    
    PipelineProgressWidget > .section-title {
        text-style: bold;
        color: $text;
        margin-bottom: 1;
    }
    """
    
    def __init__(self, **kwargs: Any) -> None:
        """Initialize the pipeline progress widget."""
        super().__init__(**kwargs)
        self._stages: List[StageDisplayInfo] = []
    
    def compose(self):
        """Compose the widget - Static widgets don't yield children."""
        return []
    
    def on_mount(self) -> None:
        """Update display on mount."""
        self._update_display()
    
    def set_stages(self, stages: List[StageDisplayInfo]) -> None:
        """Set the stages to display.
        
        Args:
            stages: List of stage display info
        """
        self._stages = stages
        self._update_display()
    
    def _update_display(self) -> None:
        """Update the pipeline stages display."""
        if not self._stages:
            self.update("[dim]No pipeline data available[/dim]")
            return
        
        lines = []
        lines.append("[bold]Pipeline Progress[/bold]")
        lines.append("")
        
        for stage in self._stages:
            progress_bar = self._format_progress_bar(stage.progress, 20)
            style = self._get_style_class(stage.status)
            
            line = (
                f"[{style}]{stage.symbol} {stage.name:12}"
                f" {progress_bar}"
                f" {stage.detail}[/{style}]"
            )
            lines.append(line)
        
        self.update("\n".join(lines))
    
    def _get_style_class(self, status: str) -> str:
        """Get CSS style class for status."""
        return {
            "pending": "dim",
            "active": "accent",
            "completed": "success",
            "failed": "error",
            "skipped": "warning",
        }.get(status, "dim")
    
    def _format_progress_bar(self, progress: float, width: int = 20) -> str:
        """Format a progress bar using Unicode block characters."""
        if progress <= 0:
            return "░" * width
        elif progress >= 100:
            return "█" * width
        
        filled = int((progress / 100.0) * width)
        empty = width - filled
        return "█" * filled + "░" * empty


class VideoInfoWidget(Static):
    """Widget displaying basic video information."""
    
    DEFAULT_CSS = """
    VideoInfoWidget {
        width: 100%;
        height: auto;
        padding: 1;
        border: solid $primary;
    }
    
    VideoInfoWidget > .section-title {
        text-style: bold;
        color: $text;
        margin-bottom: 1;
    }
    
    VideoInfoWidget > .info-label {
        color: $text-muted;
    }
    
    VideoInfoWidget > .info-value {
        color: $text;
    }
    """
    
    def __init__(self, **kwargs: Any) -> None:
        """Initialize the video info widget."""
        super().__init__(**kwargs)
        self._video: Optional[VideoView] = None
    
    def compose(self):
        """Compose the widget - Static widgets don't yield children."""
        return []
    
    def on_mount(self) -> None:
        """Update display on mount."""
        self._update_display()
    
    def set_video(self, video: VideoView) -> None:
        """Set the video to display.
        
        Args:
            video: Video view model
        """
        self._video = video
        self._update_display()
    
    def _update_display(self) -> None:
        """Update the video information display."""
        if self._video is None:
            self.update("[dim]No video selected[/dim]")
            return
        
        lines = []
        lines.append("[bold]Video Information[/bold]")
        lines.append("")
        lines.append(f"[dim]Title:[/dim]     {self._truncate_text(self._video.title, 50)}")
        lines.append(f"[dim]Source:[/dim]    {self._truncate_text(self._video.source_path, 50)}")
        lines.append(f"[dim]Size:[/dim]      {self._video.formatted_file_size}")
        lines.append(f"[dim]Plugin:[/dim]    {self._video.plugin}")
        lines.append(f"[dim]Status:[/dim]    {self._video.overall_status}")
        
        self.update("\n".join(lines))
    
    def _truncate_text(self, text: str, max_length: int) -> str:
        """Truncate text to fit display."""
        if len(text) <= max_length:
            return text
        return text[:max_length - 3] + "..."


class ResultsWidget(Static):
    """Widget displaying final results (CID, encryption status, etc.)."""
    
    DEFAULT_CSS = """
    ResultsWidget {
        width: 100%;
        height: auto;
        padding: 1;
        border: solid $success;
    }
    
    ResultsWidget > .section-title {
        text-style: bold;
        color: $text;
        margin-bottom: 1;
    }
    """
    
    def __init__(self, **kwargs: Any) -> None:
        """Initialize the results widget."""
        super().__init__(**kwargs)
        self._cid: Optional[str] = None
        self._is_encrypted: bool = False
        self._analysis_complete: bool = False
        self._tx_hash: Optional[str] = None
    
    def compose(self):
        """Compose the widget - Static widgets don't yield children."""
        return []
    
    def on_mount(self) -> None:
        """Update display on mount."""
        self._update_display()
    
    def set_results(
        self,
        cid: Optional[str] = None,
        is_encrypted: bool = False,
        analysis_complete: bool = False,
        tx_hash: Optional[str] = None,
    ) -> None:
        """Set the results to display.
        
        Args:
            cid: IPFS CID from upload
            is_encrypted: Whether video is encrypted
            analysis_complete: Whether AI analysis is complete
            tx_hash: Blockchain transaction hash
        """
        self._cid = cid
        self._is_encrypted = is_encrypted
        self._analysis_complete = analysis_complete
        self._tx_hash = tx_hash
        self._update_display()
    
    def _update_display(self) -> None:
        """Update the results display."""
        lines = []
        lines.append("[bold]Results[/bold]")
        lines.append("")
        
        if self._cid:
            # Truncate CID for display
            cid_display = self._cid[:50] + "..." if len(self._cid) > 50 else self._cid
            lines.append(f"[dim]IPFS CID:[/dim]    [success]{cid_display}[/success]")
        
        if self._is_encrypted:
            lines.append(f"[dim]Encrypted:[/dim]   [success]Yes (Haven-AOL)[/success]")
        
        if self._analysis_complete:
            lines.append(f"[dim]AI Analysis:[/dim] [success]Complete[/success]")
        
        if self._tx_hash:
            tx_display = self._tx_hash[:50] + "..." if len(self._tx_hash) > 50 else self._tx_hash
            lines.append(f"[dim]Transaction:[/dim]  [success]{tx_display}[/success]")
        
        if len(lines) == 2:  # Only header lines
            lines.append("[dim]No results yet[/dim]")
        
        self.update("\n".join(lines))


class VideoDetailHeader(Static):
    """Header widget for video detail view."""
    
    DEFAULT_CSS = """
    VideoDetailHeader {
        height: 3;
        background: $surface-darken-2;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }
    """
    
    def __init__(self, **kwargs: Any) -> None:
        """Initialize the header."""
        super().__init__(**kwargs)
        self._title: str = "Video Details"
    
    def compose(self):
        """Compose the widget - Static widgets don't yield children."""
        return []
    
    def on_mount(self) -> None:
        """Update display on mount."""
        self.update(self._title)
    
    def set_title(self, title: str) -> None:
        """Set the header title.
        
        Args:
            title: Title to display
        """
        self._title = title
        self.update(self._truncate_title(title))
    
    def _truncate_title(self, title: str, max_length: int = 60) -> str:
        """Truncate title to fit display."""
        if len(title) <= max_length:
            return title
        return title[:max_length - 3] + "..."


class VideoDetailFooter(Static):
    """Footer widget for video detail view."""
    
    DEFAULT_CSS = """
    VideoDetailFooter {
        height: 1;
        background: $surface-darken-2;
        color: $text-muted;
        content-align: center middle;
    }
    """
    
    def compose(self):
        """Set up the footer content."""
        self.update(
            "[b] Back  [r] Retry  [l] Logs  [g] Graph  [q] Quit"
        )
        return []


class VideoDetailScreen(Screen):
    """Screen displaying detailed information about a single video.
    
    This screen shows complete pipeline state from all job tables,
    similar to aria2tui's download detail view.
    
    Attributes:
        video_id: ID of the video to display
        job_repo: Repository for accessing job history
        snapshot_repo: Repository for accessing pipeline snapshots
    """
    
    DEFAULT_CSS = """
    VideoDetailScreen {
        layout: vertical;
    }
    
    #detail-container {
        height: 100%;
        width: 100%;
        layout: vertical;
    }
    
    #header-container {
        height: auto;
        dock: top;
    }
    
    #content-container {
        height: 1fr;
        width: 100%;
        layout: vertical;
        overflow: auto;
    }
    
    #footer-container {
        height: auto;
        dock: bottom;
    }
    
    #info-section {
        height: auto;
        margin: 1;
    }
    
    #pipeline-section {
        height: auto;
        margin: 1;
    }
    
    #results-section {
        height: auto;
        margin: 1;
    }
    
    #graph-section {
        height: auto;
        margin: 1;
    }
    """
    
    BINDINGS = [
        Binding("b", "back", "Back"),
        Binding("r", "retry", "Retry"),
        Binding("l", "logs", "Logs"),
        Binding("g", "graph", "Graph"),
        Binding("q", "quit", "Quit"),
    ]
    
    video_id: reactive[Optional[int]] = reactive(None)
    
    def __init__(
        self,
        video_id: int,
        job_repo: Optional[JobHistoryRepository] = None,
        snapshot_repo: Optional[PipelineSnapshotRepository] = None,
        state_manager: Optional[StateManager] = None,
        speed_history_repo: Optional[SpeedHistoryRepository] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the video detail screen.
        
        Args:
            video_id: ID of the video to display
            job_repo: Repository for accessing job history
            snapshot_repo: Repository for accessing pipeline snapshots
            state_manager: StateManager for accessing real-time state (fallback and speed history)
            speed_history_repo: Repository for speed history data (database fallback)
            **kwargs: Additional arguments passed to Screen
        """
        super().__init__(**kwargs)
        self.video_id = video_id
        self._job_repo = job_repo
        self._snapshot_repo = snapshot_repo
        self._state_manager = state_manager
        self._speed_history_repo = speed_history_repo
        self._show_graph: bool = False
    
    def compose(self) -> None:
        """Compose the screen layout."""
        with Container(id="detail-container"):
            with Container(id="header-container"):
                yield VideoDetailHeader(id="detail-header")
            
            with Container(id="content-container"):
                with Container(id="info-section"):
                    yield VideoInfoWidget(id="video-info")
                
                with Container(id="pipeline-section"):
                    yield PipelineProgressWidget(id="pipeline-progress")
                
                with Container(id="results-section"):
                    yield ResultsWidget(id="results")
                
                with Container(id="graph-section"):
                    graph = SpeedGraphComponent(
                        id="speed-graph",
                        state_manager=self._state_manager,
                        speed_history_repo=self._speed_history_repo,
                    )
                    graph.display = False
                    yield graph
            
            with Container(id="footer-container"):
                yield VideoDetailFooter()
    
    def on_mount(self) -> None:
        """Handle mount event - load video data."""
        self._load_video_data()
    
    def _load_video_data(self) -> None:
        """Load video data from repositories."""
        if self.video_id is None:
            return
        
        # Get video summary from snapshot repository
        video: Optional[VideoView] = None
        if self._snapshot_repo:
            video = self._snapshot_repo.get_video_summary(self.video_id)
        
        # Update header
        header = self.query_one("#detail-header", VideoDetailHeader)
        if video:
            header.set_title(f"Video: {video.title}")
        else:
            header.set_title(f"Video ID: {self.video_id}")
        
        # Update video info
        info_widget = self.query_one("#video-info", VideoInfoWidget)
        if video:
            info_widget.set_video(video)
        
        # Load pipeline history
        self._load_pipeline_history()
        
        # Load results
        self._load_results()
        
        # Set up speed graph
        if video and video.current_stage.value in ("download", "encrypt", "upload"):
            graph = self.query_one("#speed-graph", SpeedGraphComponent)
            graph.set_video(self.video_id, video.current_stage.value)
            # State manager was already passed in constructor, but ensure it's set
            if self._state_manager:
                graph.set_state_manager(self._state_manager)
    
    def _load_pipeline_history(self) -> None:
        """Load and display pipeline history from job tables.
        
        Falls back to StateManager if no database records exist (e.g., for
        downloads that are tracked via events but haven't been persisted yet).
        """
        if self.video_id is None:
            return
        
        # First, try to get data from database via job_repo
        history = None
        if self._job_repo is not None:
            try:
                history = self._job_repo.get_video_pipeline_history(self.video_id)
            except Exception:
                history = None
        
        # Check if we have any actual job data from the database
        has_db_data = history and any([
            history.get('downloads', []),
            history.get('analysis_jobs', []),
            history.get('encryption_jobs', []),
            history.get('upload_jobs', []),
            history.get('sync_jobs', []),
        ])
        
        # If no database records but we have a state_manager with this video,
        # use the state_manager data (for real-time downloads not yet persisted)
        if not has_db_data and self._state_manager is not None:
            video_state = self._state_manager.get_video(self.video_id)
            if video_state is not None:
                stages = self._create_stages_from_state(video_state)
                pipeline_widget = self.query_one("#pipeline-progress", PipelineProgressWidget)
                pipeline_widget.set_stages(stages)
                return
        
        # Fall back to database data (original behavior)
        if history is None:
            history = {}
        
        stages: List[StageDisplayInfo] = []
        
        # Process download stage
        downloads = history.get('downloads', [])
        if downloads:
            stages.append(self._create_download_stage(downloads[0]))
        else:
            # Infer download status from downstream stages
            # If encryption/analysis/upload has started/completed, download must be complete
            inferred_status = self._infer_download_status(history)
            if inferred_status == "completed":
                stages.append(self._create_inferred_download_stage())
            else:
                stages.append(self._create_pending_stage("download"))
        
        # Process analysis stage
        analysis_jobs = history.get('analysis_jobs', [])
        if analysis_jobs:
            stages.append(self._create_analysis_stage(analysis_jobs[0]))
        else:
            stages.append(self._create_pending_stage("analysis"))
        
        # Process encryption stage
        encryption_jobs = history.get('encryption_jobs', [])
        if encryption_jobs:
            stages.append(self._create_encryption_stage(encryption_jobs[0]))
        else:
            stages.append(self._create_pending_stage("encrypt"))
        
        # Process upload stage
        upload_jobs = history.get('upload_jobs', [])
        if upload_jobs:
            stages.append(self._create_upload_stage(upload_jobs[0]))
        else:
            stages.append(self._create_pending_stage("upload"))
        
        # Process sync stage
        sync_jobs = history.get('sync_jobs', [])
        if sync_jobs:
            stages.append(self._create_sync_stage(sync_jobs[0]))
        else:
            stages.append(self._create_pending_stage("sync"))
        
        # Update pipeline widget
        pipeline_widget = self.query_one("#pipeline-progress", PipelineProgressWidget)
        pipeline_widget.set_stages(stages)
    
    def _create_stages_from_state(self, state: VideoState) -> List[StageDisplayInfo]:
        """Create stage info from VideoState (StateManager).
        
        This is used as a fallback when no database records exist yet,
        for real-time tracking of downloads that are in progress.
        
        Args:
            state: VideoState from StateManager
            
        Returns:
            List of StageDisplayInfo for all pipeline stages
        """
        stages: List[StageDisplayInfo] = []
        
        # Download stage
        download_status = state.download_status
        download_progress = state.download_progress
        download_detail = self._format_download_detail_from_state(state)
        stages.append(StageDisplayInfo(
            name="download",
            status=self._normalize_status(download_status),
            progress=download_progress,
            detail=download_detail,
            symbol=self._get_status_symbol(self._normalize_status(download_status)),
        ))
        
        # Analysis stage
        analysis_status = state.analysis_status
        analysis_progress = state.analysis_progress
        stages.append(StageDisplayInfo(
            name="analysis",
            status=self._normalize_status(analysis_status),
            progress=analysis_progress,
            detail=self._format_stage_detail_from_state(analysis_status, analysis_progress),
            symbol=self._get_status_symbol(self._normalize_status(analysis_status)),
        ))
        
        # Encryption stage
        encrypt_status = state.encrypt_status
        encrypt_progress = state.encrypt_progress
        stages.append(StageDisplayInfo(
            name="encrypt",
            status=self._normalize_status(encrypt_status),
            progress=encrypt_progress,
            detail=self._format_stage_detail_from_state(encrypt_status, encrypt_progress),
            symbol=self._get_status_symbol(self._normalize_status(encrypt_status)),
        ))
        
        # Upload stage
        upload_status = state.upload_status
        upload_progress = state.upload_progress
        upload_speed = state.upload_speed
        upload_detail = self._format_upload_detail_from_state(upload_status, upload_progress, upload_speed)
        stages.append(StageDisplayInfo(
            name="upload",
            status=self._normalize_status(upload_status),
            progress=upload_progress,
            detail=upload_detail,
            symbol=self._get_status_symbol(self._normalize_status(upload_status)),
        ))
        
        # Sync stage
        sync_status = state.sync_status
        sync_progress = state.sync_progress
        stages.append(StageDisplayInfo(
            name="sync",
            status=self._normalize_status(sync_status),
            progress=sync_progress,
            detail=self._format_stage_detail_from_state(sync_status, sync_progress),
            symbol=self._get_status_symbol(self._normalize_status(sync_status)),
        ))
        
        return stages
    
    def _format_download_detail_from_state(self, state: VideoState) -> str:
        """Format download detail string from VideoState."""
        if state.download_status == "active":
            progress = state.download_progress
            speed = state.download_speed
            detail = f"{progress:.1f}% {self._format_speed(speed)}"
            if state.download_eta:
                detail += f" ETA: {self._format_duration(state.download_eta)}"
            return detail
        elif state.download_status == "completed":
            return "Completed"
        elif state.download_status == "failed":
            return "Failed"
        elif state.download_status == "paused":
            return "Paused"
        else:
            return "Pending"
    
    def _format_upload_detail_from_state(self, status: str, progress: float, speed: float) -> str:
        """Format upload detail string from VideoState."""
        if status == "active":
            detail = f"{progress:.1f}%"
            if speed:
                detail += f" {self._format_speed(speed)}"
            return detail
        elif status == "completed":
            return "Completed"
        elif status == "failed":
            return "Failed"
        elif status == "paused":
            return "Paused"
        else:
            return "Pending"
    
    def _format_stage_detail_from_state(self, status: str, progress: float) -> str:
        """Format generic stage detail string from VideoState."""
        if status == "active":
            return f"{progress:.1f}%"
        elif status == "completed":
            return "Completed"
        elif status == "failed":
            return "Failed"
        elif status == "paused":
            return "Paused"
        else:
            return "Pending"
    
    def _create_download_stage(self, download: Download) -> StageDisplayInfo:
        """Create stage info for download."""
        status = self._normalize_status(download.status)
        symbol = self._get_status_symbol(status)
        detail = self._format_download_detail(download)
        
        return StageDisplayInfo(
            name="download",
            status=status,
            progress=download.progress_percent or 0,
            detail=detail,
            symbol=symbol,
            error_message=download.error_message,
            started_at=download.started_at,
            completed_at=download.completed_at,
        )
    
    def _create_analysis_stage(self, job: AnalysisJob) -> StageDisplayInfo:
        """Create stage info for analysis."""
        status = self._normalize_status(job.status)
        symbol = self._get_status_symbol(status)
        detail = self._format_job_detail(job)
        
        return StageDisplayInfo(
            name="analysis",
            status=status,
            progress=job.progress_percent or 0,
            detail=detail,
            symbol=symbol,
            error_message=job.error_message,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )
    
    def _create_encryption_stage(self, job: EncryptionJob) -> StageDisplayInfo:
        """Create stage info for encryption."""
        status = self._normalize_status(job.status)
        symbol = self._get_status_symbol(status)
        detail = self._format_job_detail(job)
        
        return StageDisplayInfo(
            name="encrypt",
            status=status,
            progress=job.progress_percent or 0,
            detail=detail,
            symbol=symbol,
            error_message=job.error_message,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )
    
    def _create_upload_stage(self, job: UploadJob) -> StageDisplayInfo:
        """Create stage info for upload."""
        status = self._normalize_status(job.status)
        symbol = self._get_status_symbol(status)
        detail = self._format_job_detail(job)
        
        return StageDisplayInfo(
            name="upload",
            status=status,
            progress=job.progress_percent or 0,
            detail=detail,
            symbol=symbol,
            error_message=job.error_message,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )
    
    def _create_sync_stage(self, job: SyncJob) -> StageDisplayInfo:
        """Create stage info for sync."""
        status = self._normalize_status(job.status)
        symbol = self._get_status_symbol(status)
        detail = self._format_job_detail(job)
        
        return StageDisplayInfo(
            name="sync",
            status=status,
            progress=100 if status == "completed" else 0,
            detail=detail,
            symbol=symbol,
            error_message=job.error_message,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )
    
    def _create_pending_stage(self, name: str) -> StageDisplayInfo:
        """Create a pending stage placeholder."""
        return StageDisplayInfo(
            name=name,
            status="pending",
            progress=0,
            detail="Pending",
            symbol="○",
        )
    
    def _infer_download_status(self, history: Dict[str, List[Any]]) -> str:
        """Infer download status from downstream stage statuses.
        
        If encryption, analysis, upload, or sync has started or completed,
        we can infer that download must have completed (since download is
        the first stage in the pipeline).
        
        Args:
            history: Pipeline history dictionary with all job types
            
        Returns:
            "completed" if download can be inferred as complete, "pending" otherwise
        """
        # Check encryption jobs
        encryption_jobs = history.get('encryption_jobs', [])
        if encryption_jobs:
            enc_status = encryption_jobs[0].status
            if enc_status in ("encrypting", "completed", "active"):
                return "completed"
        
        # Check analysis jobs
        analysis_jobs = history.get('analysis_jobs', [])
        if analysis_jobs:
            analysis_status = analysis_jobs[0].status
            if analysis_status in ("analyzing", "completed", "active"):
                return "completed"
        
        # Check upload jobs
        upload_jobs = history.get('upload_jobs', [])
        if upload_jobs:
            upload_status = upload_jobs[0].status
            if upload_status in ("uploading", "completed", "active"):
                return "completed"
        
        # Check sync jobs
        sync_jobs = history.get('sync_jobs', [])
        if sync_jobs:
            sync_status = sync_jobs[0].status
            if sync_status in ("syncing", "completed", "active"):
                return "completed"
        
        return "pending"
    
    def _create_inferred_download_stage(self) -> StageDisplayInfo:
        """Create a download stage marked as completed (inferred from downstream stages).
        
        This is used when no explicit download record exists but downstream
        stages have started/completed, indicating the download must have succeeded.
        """
        return StageDisplayInfo(
            name="download",
            status="completed",
            progress=100.0,
            detail="Completed (inferred)",
            symbol="●",
        )
    
    def _normalize_status(self, status: str) -> str:
        """Normalize job status to display status."""
        status_map = {
            "pending": "pending",
            "downloading": "active",
            "encrypting": "active",
            "uploading": "active",
            "analyzing": "active",
            "syncing": "active",
            "completed": "completed",
            "failed": "failed",
            "skipped": "skipped",
        }
        return status_map.get(status, status)
    
    def _get_status_symbol(self, status: str) -> str:
        """Get Unicode symbol for status."""
        return {
            "pending": "○",
            "active": "◐",
            "completed": "●",
            "failed": "✗",
            "skipped": "⊘",
        }.get(status, "?")
    
    def _format_download_detail(self, download: Download) -> str:
        """Format detail string for download."""
        if download.status == "downloading":
            speed = download.download_rate or 0
            progress = download.progress_percent or 0
            detail = f"{progress:.1f}% {self._format_speed(speed)}"
            if download.eta_seconds:
                detail += f" ETA: {self._format_duration(download.eta_seconds)}"
            return detail
        elif download.status == "completed" and download.completed_at and download.started_at:
            duration = (download.completed_at - download.started_at).total_seconds()
            return f"Done in {self._format_duration(int(duration))}"
        elif download.status == "failed":
            error = download.error_message or "Unknown error"
            return f"Error: {error[:30]}"
        elif download.status == "pending":
            return "Pending"
        else:
            return download.status
    
    def _format_job_detail(self, job: Any) -> str:
        """Format detail string for a job."""
        if job.status in ("downloading", "encrypting", "uploading", "analyzing", "syncing"):
            progress = getattr(job, 'progress_percent', 0) or 0
            speed = getattr(job, 'encrypt_speed', None) or getattr(job, 'upload_speed', None)
            if speed:
                return f"{progress:.1f}% {self._format_speed(speed)}"
            return f"{progress:.1f}%"
        elif job.status == "completed" and job.completed_at and job.started_at:
            duration = (job.completed_at - job.started_at).total_seconds()
            return f"Done in {self._format_duration(int(duration))}"
        elif job.status == "failed":
            error = job.error_message or "Unknown error"
            return f"Error: {error[:30]}"
        elif job.status == "skipped":
            # Show skip reason if available
            error_msg = getattr(job, 'error_message', None)
            if error_msg:
                return f"Skipped: {error_msg[:25]}"
            return "Skipped"
        elif job.status == "pending":
            return "Pending"
        else:
            return job.status
    
    def _format_speed(self, speed: float) -> str:
        """Format speed in human-readable form."""
        if speed == 0:
            return "-"
        
        size = float(speed)
        if size < 1024:
            return f"{size:.0f}B/s"
        size /= 1024
        if size < 1024:
            return f"{size:.1f}KB/s"
        size /= 1024
        if size < 1024:
            return f"{size:.1f}MB/s"
        size /= 1024
        return f"{size:.1f}GB/s"
    
    def _format_duration(self, seconds: int) -> str:
        """Format duration in human-readable form."""
        if seconds < 60:
            return f"{seconds}s"
        minutes, seconds = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m{seconds:02d}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h{minutes:02d}m"
    
    def _load_results(self) -> None:
        """Load and display final results."""
        if self._job_repo is None or self.video_id is None:
            return
        
        # Get latest CID from upload_jobs
        latest_cid = self._job_repo.get_latest_cid(self.video_id)
        
        # Check encryption status
        is_encrypted = self._job_repo.is_encrypted(self.video_id)
        
        # Check analysis status
        history = self._job_repo.get_video_pipeline_history(self.video_id)
        analysis_jobs = history.get('analysis_jobs', [])
        analysis_complete = (
            analysis_jobs and analysis_jobs[0].status == "completed"
        )
        
        # Get sync info for tx_hash
        tx_hash = None
        sync_info = self._job_repo.get_sync_info(self.video_id)
        if sync_info:
            tx_hash = sync_info.get('tx_hash')
        
        # Update results widget
        results_widget = self.query_one("#results", ResultsWidget)
        results_widget.set_results(
            cid=latest_cid,
            is_encrypted=is_encrypted,
            analysis_complete=analysis_complete,
            tx_hash=tx_hash,
        )
    
    def action_back(self) -> None:
        """Navigate back to the list view."""
        self.app.pop_screen()
    
    def action_retry(self) -> None:
        """Retry failed stages."""
        self.app.notify(f"Retry requested for video {self.video_id}", timeout=3.0)
    
    def action_logs(self) -> None:
        """View logs for this video."""
        # Get video title if available
        video_title = ""
        if self._snapshot_repo:
            video = self._snapshot_repo.get_video_summary(self.video_id)
            if video:
                video_title = video.title
        
        # Push the video logs screen
        self.app.push_screen(VideoLogsScreen(
            video_id=self.video_id,
            video_title=video_title,
        ))
    
    def action_graph(self) -> None:
        """Toggle speed graph display."""
        self._show_graph = not self._show_graph
        graph = self.query_one("#speed-graph", SpeedGraphComponent)
        graph.display = self._show_graph
        if self._show_graph:
            graph.refresh_graph()
            graph.start_auto_refresh(interval=1.0)
        else:
            graph.stop_auto_refresh()


class VideoDetailView:
    """Detailed view for a single video's pipeline state from job tables.
    
    This class provides a high-level interface for the video detail view,
    managing the screen and providing integration with the repositories.
    
    Example:
        >>> view = VideoDetailView(
        ...     video_id=1,
        ...     job_repo=job_repo,
        ...     snapshot_repo=snapshot_repo,
        ...     state_manager=state_manager,
        ...     speed_history_repo=speed_history_repo,
        ... )
        >>> screen = view.create_screen()
    
    Attributes:
        video_id: ID of the video to display
        job_repo: Repository for accessing job history
        snapshot_repo: Repository for accessing pipeline snapshots
        state_manager: StateManager for accessing real-time state and speed history
        speed_history_repo: Repository for speed history data (database fallback)
        screen: The VideoDetailScreen instance
    """
    
    def __init__(
        self,
        video_id: int,
        job_repo: Optional[JobHistoryRepository] = None,
        snapshot_repo: Optional[PipelineSnapshotRepository] = None,
        state_manager: Optional[StateManager] = None,
        speed_history_repo: Optional[SpeedHistoryRepository] = None,
    ) -> None:
        """Initialize the video detail view.
        
        Args:
            video_id: ID of the video to display
            job_repo: Repository for accessing job history
            snapshot_repo: Repository for accessing pipeline snapshots
            state_manager: StateManager for accessing real-time state and speed history
            speed_history_repo: Repository for speed history data (database fallback)
        """
        self.video_id = video_id
        self.job_repo = job_repo
        self.snapshot_repo = snapshot_repo
        self.state_manager = state_manager
        self.speed_history_repo = speed_history_repo
        self.screen: Optional[VideoDetailScreen] = None
    
    def create_screen(self) -> VideoDetailScreen:
        """Create the video detail screen.
        
        Returns:
            The configured VideoDetailScreen instance
        """
        self.screen = VideoDetailScreen(
            video_id=self.video_id,
            job_repo=self.job_repo,
            snapshot_repo=self.snapshot_repo,
            state_manager=self.state_manager,
            speed_history_repo=self.speed_history_repo,
        )
        return self.screen
    
    def refresh(self) -> None:
        """Refresh the video detail display."""
        if self.screen is not None:
            self.screen._load_video_data()
