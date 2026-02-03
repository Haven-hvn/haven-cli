"""Base class for archiver plugins.

Plugins are the data sources for the Haven pipeline. Each plugin
implements methods to discover media sources and archive them.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional


class PluginCapability(Enum):
    """Capabilities that a plugin can provide."""
    
    DISCOVER = auto()      # Can discover media sources
    ARCHIVE = auto()       # Can archive/download media
    STREAM = auto()        # Can handle live streams
    SEARCH = auto()        # Can search for content
    METADATA = auto()      # Can extract metadata
    HEALTH_CHECK = auto()  # Can perform health checks


@dataclass
class PluginInfo:
    """Information about a plugin.
    
    Attributes:
        name: Unique plugin identifier
        display_name: Human-readable name
        version: Plugin version
        description: Plugin description
        author: Plugin author
        media_types: Types of media this plugin handles
        capabilities: Plugin capabilities
        config_schema: JSON schema for plugin configuration
    """
    
    name: str
    display_name: str = ""
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    media_types: List[str] = field(default_factory=list)
    capabilities: List[PluginCapability] = field(default_factory=list)
    config_schema: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self) -> None:
        if not self.display_name:
            self.display_name = self.name


@dataclass
class MediaSource:
    """A media source discovered by a plugin.
    
    Represents a piece of media that can be archived.
    
    Attributes:
        source_id: Unique identifier for this source
        media_type: Type of media (youtube, bittorrent, etc.)
        uri: URI to access the media
        title: Title of the media
        priority: Archive priority (high, medium, low)
        metadata: Additional source-specific metadata
    """
    
    source_id: str
    media_type: str
    uri: str
    title: str = ""
    priority: str = "medium"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ArchiveResult:
    """Result of archiving a media source.
    
    Attributes:
        success: Whether archiving succeeded
        output_path: Path to the archived file
        file_size: Size of the archived file in bytes
        duration: Duration of media in seconds (if applicable)
        error: Error message if failed
        metadata: Additional result metadata
    """
    
    success: bool
    output_path: str = ""
    file_size: int = 0
    duration: int = 0
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class ArchiverPlugin(ABC):
    """Abstract base class for archiver plugins.
    
    Plugins must implement:
    - info property: Return plugin information
    - discover_sources(): Find media sources to archive
    - archive(): Download/archive a specific source
    
    Plugins may implement:
    - initialize(): Setup plugin (called once on load)
    - shutdown(): Cleanup plugin (called on unload)
    - health_check(): Verify plugin is working
    - configure(): Update plugin configuration
    
    Example:
        class YouTubePlugin(ArchiverPlugin):
            @property
            def info(self) -> PluginInfo:
                return PluginInfo(
                    name="YouTubePlugin",
                    display_name="YouTube Archiver",
                    media_types=["youtube"],
                    capabilities=[
                        PluginCapability.DISCOVER,
                        PluginCapability.ARCHIVE,
                    ],
                )
            
            async def discover_sources(self) -> List[MediaSource]:
                # Find videos from configured channels
                ...
            
            async def archive(self, source: MediaSource) -> ArchiveResult:
                # Download video using yt-dlp
                ...
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the plugin.
        
        Args:
            config: Plugin configuration
        """
        self._config = config or {}
        self._enabled = True
        self._initialized = False
    
    @property
    @abstractmethod
    def info(self) -> PluginInfo:
        """Get plugin information.
        
        Returns:
            PluginInfo describing this plugin
        """
        pass
    
    @property
    def name(self) -> str:
        """Get plugin name."""
        return self.info.name
    
    @property
    def enabled(self) -> bool:
        """Check if plugin is enabled."""
        return self._enabled
    
    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Enable or disable the plugin."""
        self._enabled = value
    
    @property
    def config(self) -> Dict[str, Any]:
        """Get plugin configuration."""
        return self._config.copy()
    
    async def initialize(self) -> None:
        """Initialize the plugin.
        
        Called once when the plugin is loaded. Override to perform
        setup tasks like connecting to APIs, loading credentials, etc.
        """
        self._initialized = True
    
    async def shutdown(self) -> None:
        """Shutdown the plugin.
        
        Called when the plugin is unloaded. Override to perform
        cleanup tasks like closing connections, saving state, etc.
        """
        self._initialized = False
    
    def configure(self, config: Dict[str, Any]) -> None:
        """Update plugin configuration.
        
        Args:
            config: New configuration values to merge
        """
        self._config.update(config)
    
    @abstractmethod
    async def discover_sources(self) -> List[MediaSource]:
        """Discover media sources to archive.
        
        This method is called by the job scheduler to find new
        media that should be archived.
        
        Returns:
            List of MediaSource objects representing available media
        """
        pass
    
    @abstractmethod
    async def archive(self, source: MediaSource) -> ArchiveResult:
        """Archive a media source.
        
        Download or capture the media and save it locally.
        
        Args:
            source: The media source to archive
            
        Returns:
            ArchiveResult with the outcome
        """
        pass
    
    async def health_check(self) -> bool:
        """Check if the plugin is healthy and operational.
        
        Override to implement actual health checking logic.
        
        Returns:
            True if plugin is healthy
        """
        return self._initialized and self._enabled
    
    def has_capability(self, capability: PluginCapability) -> bool:
        """Check if plugin has a specific capability.
        
        Args:
            capability: The capability to check
            
        Returns:
            True if plugin has the capability
        """
        return capability in self.info.capabilities
    
    def validate_config(self) -> List[str]:
        """Validate the current configuration.
        
        Override to implement configuration validation.
        
        Returns:
            List of validation error messages (empty if valid)
        """
        return []


class StreamingPlugin(ArchiverPlugin):
    """Base class for plugins that handle live streams.
    
    Extends ArchiverPlugin with streaming-specific methods.
    """
    
    @abstractmethod
    async def start_recording(
        self,
        source: MediaSource,
        output_dir: str,
    ) -> str:
        """Start recording a live stream.
        
        Args:
            source: The stream source to record
            output_dir: Directory to save recordings
            
        Returns:
            Recording session ID
        """
        pass
    
    @abstractmethod
    async def stop_recording(self, session_id: str) -> ArchiveResult:
        """Stop a recording session.
        
        Args:
            session_id: The recording session to stop
            
        Returns:
            ArchiveResult with the recorded file
        """
        pass
    
    async def get_recording_status(self, session_id: str) -> Dict[str, Any]:
        """Get status of a recording session.
        
        Args:
            session_id: The recording session ID
            
        Returns:
            Status information dictionary
        """
        return {"session_id": session_id, "status": "unknown"}
