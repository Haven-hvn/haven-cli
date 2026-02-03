"""Pipeline context for carrying data through processing steps.

The PipelineContext is the primary data container that flows through
the pipeline, accumulating results and state from each step.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4


@dataclass
class VideoMetadata:
    """Metadata about a video being processed.
    
    Aligned with VideoMetadata type from HavenPlayer.p specification.
    
    Attributes:
        path: Local file path to the video
        title: Video title
        duration: Duration in seconds
        file_size: File size in bytes
        mime_type: MIME type of the video
        phash: Perceptual hash for deduplication
        creator_handle: Creator/channel identifier
        source_uri: Original source URL
        has_ai_data: Whether VLM analysis has been performed
        width: Video width in pixels
        height: Video height in pixels
        fps: Frames per second
        codec: Video codec name
        bitrate: Bitrate in bits per second
        audio_codec: Audio codec name
        audio_channels: Number of audio channels
        container: Container format (mp4, mkv, etc.)
        has_audio: Whether the video has audio
    """
    
    path: str
    title: str = ""
    duration: float = 0.0
    file_size: int = 0
    mime_type: str = "video/mp4"
    phash: str = ""
    creator_handle: str = ""
    source_uri: str = ""
    has_ai_data: bool = False
    width: int = 0
    height: int = 0
    fps: float = 0.0
    codec: str = ""
    bitrate: int = 0
    audio_codec: str = ""
    audio_channels: int = 0
    container: str = ""
    has_audio: bool = False


@dataclass
class AIAnalysisResult:
    """Result of VLM video analysis.
    
    Aligned with AIAnalysisResult type from HavenPlayer.p specification.
    """
    
    video_path: str
    timestamps: List[Dict[str, Any]] = field(default_factory=list)
    tags: Dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass
class EncryptionMetadata:
    """Metadata about file encryption via Lit Protocol.
    
    Aligned with EncryptionMetadata type from HavenPlayer.p specification.
    """
    
    ciphertext: str = ""
    data_to_encrypt_hash: str = ""
    access_control_conditions: List[Dict[str, Any]] = field(default_factory=list)
    chain: str = "ethereum"


@dataclass
class UploadResult:
    """Result of Filecoin upload.
    
    Aligned with UploadResult type from HavenPlayer.p specification.
    """
    
    video_path: str
    root_cid: str = ""
    piece_cid: str = ""
    transaction_hash: str = ""
    encryption_metadata: Optional[EncryptionMetadata] = None


@dataclass
class PipelineContext:
    """Context object carrying data through pipeline steps.
    
    Each video gets its own PipelineContext instance that accumulates
    data as it flows through the pipeline steps. The context provides:
    
    - Unique correlation ID for tracking related events
    - Source file path and metadata
    - Results from each processing step
    - Pipeline options and configuration
    - Error accumulation for debugging
    
    Attributes:
        context_id: Unique identifier for this pipeline execution
        source_path: Path to the source video file
        options: Pipeline options (encrypt, vlm_enabled, etc.)
        video_metadata: Extracted video metadata
        analysis_result: VLM analysis result (if performed)
        encryption_metadata: Encryption details (if encrypted)
        upload_result: Filecoin upload result (if uploaded)
        arkiv_entity_key: Arkiv blockchain entity key (if synced)
        errors: List of errors encountered during processing
        created_at: When this context was created
        updated_at: Last update timestamp
        step_data: Arbitrary data storage for steps
    """
    
    source_path: Path
    options: Dict[str, Any] = field(default_factory=dict)
    context_id: UUID = field(default_factory=uuid4)
    video_id: Optional[int] = None
    video_metadata: Optional[VideoMetadata] = None
    analysis_result: Optional[AIAnalysisResult] = None
    encryption_metadata: Optional[EncryptionMetadata] = None
    encrypted_video_path: Optional[str] = None
    upload_result: Optional[UploadResult] = None
    arkiv_entity_key: Optional[str] = None
    errors: List[Dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    step_data: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self) -> None:
        """Ensure source_path is a Path object."""
        if isinstance(self.source_path, str):
            self.source_path = Path(self.source_path)
    
    @property
    def correlation_id(self) -> UUID:
        """Alias for context_id for event correlation."""
        return self.context_id
    
    @property
    def video_path(self) -> str:
        """Get the video file path as a string."""
        return str(self.source_path)
    
    @property
    def filename(self) -> str:
        """Get the video filename."""
        return self.source_path.name
    
    @property
    def title(self) -> str:
        """Get video title from metadata or derive from filename."""
        if self.video_metadata and self.video_metadata.title:
            return self.video_metadata.title
        return self.source_path.stem
    
    # Option accessors
    
    @property
    def encrypt_enabled(self) -> bool:
        """Check if encryption is enabled for this pipeline run."""
        return bool(self.options.get("encrypt", False))
    
    @property
    def vlm_enabled(self) -> bool:
        """Check if VLM analysis is enabled."""
        return bool(self.options.get("vlm_enabled", True))
    
    @property
    def arkiv_sync_enabled(self) -> bool:
        """Check if Arkiv sync is enabled."""
        return bool(self.options.get("arkiv_sync_enabled", True))
    
    @property
    def dataset_id(self) -> Optional[int]:
        """Get the Filecoin dataset ID."""
        return self.options.get("dataset_id")
    
    # State management
    
    def touch(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = datetime.utcnow()
    
    def set_step_data(self, step_name: str, key: str, value: Any) -> None:
        """Store data from a step for later use.
        
        Args:
            step_name: Name of the step storing data
            key: Data key
            value: Data value
        """
        if step_name not in self.step_data:
            self.step_data[step_name] = {}
        self.step_data[step_name][key] = value
        self.touch()
    
    def get_step_data(self, step_name: str, key: str, default: Any = None) -> Any:
        """Retrieve data stored by a step.
        
        Args:
            step_name: Name of the step that stored data
            key: Data key
            default: Default value if not found
            
        Returns:
            The stored value or default
        """
        return self.step_data.get(step_name, {}).get(key, default)
    
    def add_error(
        self,
        step_name: str,
        code: str,
        message: str,
        **details: Any,
    ) -> None:
        """Record an error that occurred during processing.
        
        Args:
            step_name: Name of the step where error occurred
            code: Error code
            message: Error message
            **details: Additional error details
        """
        self.errors.append({
            "step": step_name,
            "code": code,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
            **details,
        })
        self.touch()
    
    @property
    def has_errors(self) -> bool:
        """Check if any errors have been recorded."""
        return len(self.errors) > 0
    
    # Result accessors
    
    @property
    def phash(self) -> Optional[str]:
        """Get the perceptual hash if calculated."""
        if self.video_metadata:
            return self.video_metadata.phash
        return None
    
    @property
    def cid(self) -> Optional[str]:
        """Get the uploaded CID if available."""
        if self.upload_result:
            return self.upload_result.root_cid
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize context to a dictionary.
        
        Useful for logging, debugging, and persistence.
        """
        return {
            "context_id": str(self.context_id),
            "source_path": str(self.source_path),
            "options": self.options,
            "video_metadata": {
                "path": self.video_metadata.path,
                "title": self.video_metadata.title,
                "duration": self.video_metadata.duration,
                "file_size": self.video_metadata.file_size,
                "phash": self.video_metadata.phash,
            } if self.video_metadata else None,
            "has_analysis": self.analysis_result is not None,
            "has_encryption": self.encryption_metadata is not None,
            "cid": self.cid,
            "arkiv_entity_key": self.arkiv_entity_key,
            "error_count": len(self.errors),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class BatchContext:
    """Context for batch processing multiple videos.
    
    Provides coordination for parallel pipeline execution.
    """
    
    batch_id: UUID = field(default_factory=uuid4)
    contexts: List[PipelineContext] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    def add(self, context: PipelineContext) -> None:
        """Add a pipeline context to the batch."""
        self.contexts.append(context)
    
    @property
    def size(self) -> int:
        """Get the number of contexts in the batch."""
        return len(self.contexts)
    
    @property
    def completed_count(self) -> int:
        """Get the number of completed contexts."""
        return sum(1 for c in self.contexts if c.cid is not None or c.has_errors)
    
    @property
    def error_count(self) -> int:
        """Get the number of contexts with errors."""
        return sum(1 for c in self.contexts if c.has_errors)
