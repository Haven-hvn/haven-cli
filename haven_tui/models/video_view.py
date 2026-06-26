"""Video view models for Haven TUI.

Aggregated view of video for TUI display from PipelineSnapshot.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any


class SortField(Enum):
    """Fields available for sorting the video list."""
    DATE_ADDED = "date_added"
    TITLE = "title"
    PROGRESS = "progress"
    SPEED = "speed"
    SIZE = "size"
    STAGE = "stage"


class SortOrder(Enum):
    """Sort order direction."""
    ASCENDING = "asc"
    DESCENDING = "desc"


class PipelineStage(Enum):
    """Pipeline stages for video processing."""
    PENDING = "pending"
    DOWNLOAD = "download"
    INGEST = "ingest"
    ANALYSIS = "analysis"
    ENCRYPT = "encrypt"
    UPLOAD = "upload"
    SYNC = "sync"
    COMPLETE = "complete"


class StageStatus(Enum):
    """Status for individual pipeline stages."""
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class FilterState:
    """Current filter configuration for video list.
    
    This class holds all filter criteria that can be applied to the video list.
    Filters can be combined (AND logic) to narrow down results.
    
    Attributes:
        stage: Filter by pipeline stage (e.g., DOWNLOAD, ENCRYPT, UPLOAD)
        plugin: Filter by plugin name (e.g., "youtube", "bittorrent")
        status: Filter by stage status (e.g., ACTIVE, COMPLETED, FAILED)
        search_query: Text search across title, URI, and CID
        show_completed: Whether to include completed videos in results
        show_failed: Whether to include failed videos in results
        show_only_errors: If True, show only videos with errors
    
    Example:
        >>> filter_state = FilterState(
        ...     stage=PipelineStage.DOWNLOAD,
        ...     status=StageStatus.ACTIVE,
        ...     show_completed=False,
        ...     search_query="big buck bunny"
        ... )
    """
    stage: Optional[PipelineStage] = None
    plugin: Optional[str] = None
    status: Optional[StageStatus] = None
    search_query: str = ""
    show_completed: bool = True
    show_failed: bool = True
    show_only_errors: bool = False
    
    def is_active(self) -> bool:
        """Check if any filter is active (non-default).
        
        Returns:
            True if any filter criterion is set.
        """
        return (
            self.stage is not None
            or self.plugin is not None
            or self.status is not None
            or self.search_query != ""
            or not self.show_completed  # False means hide completed (non-default)
            or not self.show_failed  # False means hide failed (non-default)
            or self.show_only_errors
        )
    
    def reset(self) -> None:
        """Reset all filters to default values."""
        self.stage = None
        self.plugin = None
        self.status = None
        self.search_query = ""
        self.show_completed = True
        self.show_failed = True
        self.show_only_errors = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert filter state to dictionary.
        
        Returns:
            Dictionary representation of filter state.
        """
        return {
            "stage": self.stage.value if self.stage else None,
            "plugin": self.plugin,
            "status": self.status.value if self.status else None,
            "search_query": self.search_query,
            "show_completed": self.show_completed,
            "show_failed": self.show_failed,
            "show_only_errors": self.show_only_errors,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FilterState":
        """Create FilterState from dictionary.
        
        Args:
            data: Dictionary containing filter values.
            
        Returns:
            New FilterState instance.
        """
        stage = None
        if data.get("stage"):
            try:
                stage = PipelineStage(data["stage"])
            except ValueError:
                pass
        
        status = None
        if data.get("status"):
            try:
                status = StageStatus(data["status"])
            except ValueError:
                pass
        
        return cls(
            stage=stage,
            plugin=data.get("plugin"),
            status=status,
            search_query=data.get("search_query", ""),
            show_completed=data.get("show_completed", True),
            show_failed=data.get("show_failed", True),
            show_only_errors=data.get("show_only_errors", False),
        )


@dataclass
class StageInfo:
    """Information about a specific pipeline stage."""
    stage: PipelineStage
    status: StageStatus
    progress: float = 0.0  # 0.0 - 100.0
    speed: int = 0  # bytes/sec
    eta: Optional[int] = None  # seconds remaining
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    
    @property
    def is_active(self) -> bool:
        """Check if stage is currently active."""
        return self.status == StageStatus.ACTIVE
    
    @property
    def is_complete(self) -> bool:
        """Check if stage is completed."""
        return self.status == StageStatus.COMPLETED
    
    @property
    def has_failed(self) -> bool:
        """Check if stage has failed."""
        return self.status == StageStatus.FAILED


@dataclass
class VideoView:
    """Aggregated view of video for TUI display from PipelineSnapshot."""
    id: int
    title: str
    source_path: str
    current_stage: PipelineStage
    stage_progress: float = 0.0  # 0.0 - 100.0
    stage_speed: int = 0  # bytes/sec (if applicable)
    stage_eta: Optional[int] = None  # seconds remaining
    overall_status: str = "pending"  # "active", "pending", "completed", "failed"
    has_error: bool = False
    error_message: Optional[str] = None
    file_size: int = 0
    plugin: str = "unknown"
    
    # Arkiv blockchain entity key (set by batch or inline sync)
    arkiv_entity_key: Optional[str] = None
    
    # Transaction hash for the Arkiv sync transaction
    arkiv_tx_hash: Optional[str] = None
    
    # Optional detailed stage info for expanded view
    stage_details: Optional[List[StageInfo]] = None
    
    @property
    def is_complete(self) -> bool:
        """Check if video completed all pipeline stages."""
        return self.current_stage == PipelineStage.COMPLETE
    
    @property
    def is_active(self) -> bool:
        """Check if video is actively processing."""
        return self.overall_status == "active"
    
    @property
    def is_pending(self) -> bool:
        """Check if video is pending."""
        return self.overall_status == "pending"
    
    @property
    def has_failed(self) -> bool:
        """Check if video has failed."""
        return self.overall_status == "failed" or self.has_error
    
    @property
    def formatted_speed(self) -> str:
        """Format speed for display."""
        if self.stage_speed == 0:
            return "-"
        return self._human_readable_bytes(self.stage_speed) + "/s"
    
    @property
    def formatted_eta(self) -> str:
        """Format ETA for display."""
        if self.stage_eta is None:
            return "--:--"
        minutes, seconds = divmod(self.stage_eta, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}h{minutes:02d}m"
        return f"{minutes}:{seconds:02d}"
    
    @property
    def formatted_file_size(self) -> str:
        """Format file size for display."""
        if self.file_size == 0:
            return "-"
        return self._human_readable_bytes(self.file_size)
    
    @property
    def formatted_progress(self) -> str:
        """Format progress percentage for display."""
        return f"{self.stage_progress:.1f}%"
    
    @property
    def display_title(self) -> str:
        """Get display title (truncated if needed)."""
        max_len = 50
        if len(self.title) > max_len:
            return self.title[:max_len - 3] + "..."
        return self.title
    
    def _human_readable_bytes(self, size: int) -> str:
        """Convert bytes to human readable format."""
        if size < 1024:
            return f"{size}B"
        size = size / 1024
        if size < 1024:
            return f"{size:.1f}KB"
        size = size / 1024
        if size < 1024:
            return f"{size:.1f}MB"
        size = size / 1024
        return f"{size:.1f}GB"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert view to dictionary representation."""
        return {
            "id": self.id,
            "title": self.title,
            "source_path": self.source_path,
            "current_stage": self.current_stage.value,
            "stage_progress": self.stage_progress,
            "stage_speed": self.stage_speed,
            "stage_eta": self.stage_eta,
            "overall_status": self.overall_status,
            "has_error": self.has_error,
            "error_message": self.error_message,
            "file_size": self.file_size,
            "plugin": self.plugin,
            "formatted_speed": self.formatted_speed,
            "formatted_eta": self.formatted_eta,
            "is_complete": self.is_complete,
            "is_active": self.is_active,
        }



class VideoSorter:
    """Sorts video list by various criteria.
    
    This class provides flexible sorting options for video lists, supporting
    multiple sort fields and ascending/descending order. It works with both
    VideoView objects and VideoState objects from the state manager.
    
    Attributes:
        field: Current sort field (default: DATE_ADDED)
        order: Current sort order (default: DESCENDING)
    
    Example:
        >>> sorter = VideoSorter()
        >>> sorted_videos = sorter.sort(videos)
        >>> sorter.set_sort(SortField.TITLE, SortOrder.ASCENDING)
        >>> sorted_videos = sorter.sort(videos)
    """
    
    def __init__(self):
        """Initialize the video sorter with default settings."""
        self.field = SortField.DATE_ADDED
        self.order = SortOrder.DESCENDING
    
    def sort(self, videos: List[Any]) -> List[Any]:
        """Sort videos by current field and order.
        
        Args:
            videos: List of videos to sort (VideoView or VideoState objects)
            
        Returns:
            Sorted list of videos
        """
        reverse = self.order == SortOrder.DESCENDING
        
        if self.field == SortField.DATE_ADDED:
            return sorted(videos, key=lambda v: self._get_date_added(v), reverse=reverse)
        elif self.field == SortField.TITLE:
            return sorted(videos, key=lambda v: self._get_title(v).lower(), reverse=reverse)
        elif self.field == SortField.PROGRESS:
            return sorted(videos, key=lambda v: self._get_progress(v), reverse=reverse)
        elif self.field == SortField.SPEED:
            return sorted(videos, key=lambda v: self._get_speed(v), reverse=reverse)
        elif self.field == SortField.SIZE:
            return sorted(videos, key=lambda v: self._get_size(v), reverse=reverse)
        elif self.field == SortField.STAGE:
            return sorted(videos, key=lambda v: self._get_stage(v), reverse=reverse)
        
        return videos
    
    def _get_date_added(self, video: Any) -> datetime:
        """Get the date added for sorting.
        
        Handles both VideoView (added_at) and VideoState (created_at).
        Normalizes all datetimes to offset-aware to avoid comparison errors.
        """
        dt = None
        if hasattr(video, 'added_at') and video.added_at is not None:
            dt = video.added_at
        elif hasattr(video, 'created_at') and video.created_at is not None:
            dt = video.created_at
        
        if dt is not None:
            # Ensure offset-aware: if naive, assume UTC
            # This prevents TypeError when comparing offset-naive and offset-aware datetimes
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        
        return datetime.min.replace(tzinfo=timezone.utc)
    
    def _get_title(self, video: Any) -> str:
        """Get the title for sorting."""
        if hasattr(video, 'title'):
            return video.title or ""
        return ""
    
    def _get_progress(self, video: Any) -> float:
        """Get the progress for sorting.
        
        Handles both VideoView (stage_progress) and VideoState (current_progress).
        """
        if hasattr(video, 'stage_progress'):
            return video.stage_progress
        elif hasattr(video, 'current_progress'):
            return video.current_progress
        return 0.0
    
    def _get_speed(self, video: Any) -> int:
        """Get the speed for sorting.
        
        Handles both VideoView (stage_speed) and VideoState (current_speed).
        """
        if hasattr(video, 'stage_speed'):
            return video.stage_speed
        elif hasattr(video, 'current_speed'):
            return int(video.current_speed)
        return 0
    
    def _get_size(self, video: Any) -> int:
        """Get the file size for sorting."""
        if hasattr(video, 'file_size'):
            return video.file_size
        return 0
    
    def _get_stage(self, video: Any) -> str:
        """Get the stage for sorting.
        
        Handles both VideoView (current_stage as enum or string) and 
        VideoState (current_stage as string).
        """
        if hasattr(video, 'current_stage'):
            stage = video.current_stage
            if isinstance(stage, Enum):
                return stage.value
            return str(stage)
        return ""
    
    def set_sort(self, field: SortField, order: Optional[SortOrder] = None) -> None:
        """Set sort field and optionally order.
        
        Args:
            field: The field to sort by
            order: Optional sort order (if None, keeps current order)
        """
        self.field = field
        if order is not None:
            self.order = order
    
    def toggle_order(self) -> SortOrder:
        """Toggle between ascending/descending.
        
        Returns:
            The new sort order after toggling
        """
        if self.order == SortOrder.ASCENDING:
            self.order = SortOrder.DESCENDING
        else:
            self.order = SortOrder.ASCENDING
        return self.order
    
    def get_sort_description(self) -> str:
        """Get a human-readable description of the current sort.
        
        Returns:
            String describing the current sort field and order
        """
        field_descriptions = {
            SortField.DATE_ADDED: "Date added",
            SortField.TITLE: "Title",
            SortField.PROGRESS: "Progress",
            SortField.SPEED: "Speed",
            SortField.SIZE: "Size",
            SortField.STAGE: "Stage",
        }
        
        field_name = field_descriptions.get(self.field, self.field.value)
        order_symbol = "↓" if self.order == SortOrder.DESCENDING else "↑"
        
        return f"{field_name} {order_symbol}"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert sort state to dictionary.
        
        Returns:
            Dictionary representation of sort state
        """
        return {
            "field": self.field.value,
            "order": self.order.value,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VideoSorter":
        """Create VideoSorter from dictionary.
        
        Args:
            data: Dictionary containing sort values
            
        Returns:
            New VideoSorter instance with restored state
        """
        sorter = cls()
        
        if "field" in data:
            try:
                sorter.field = SortField(data["field"])
            except ValueError:
                pass
        
        if "order" in data:
            try:
                sorter.order = SortOrder(data["order"])
            except ValueError:
                pass
        
        return sorter
