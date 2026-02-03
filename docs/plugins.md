# Plugins Documentation

Haven CLI uses a flexible plugin system for archiving content from various sources.

## Table of Contents

1. [Overview](#overview)
2. [Built-in Plugins](#built-in-plugins)
3. [Plugin Management](#plugin-management)
4. [Creating Custom Plugins](#creating-custom-plugins)
5. [Plugin Configuration](#plugin-configuration)
6. [Plugin API Reference](#plugin-api-reference)

## Overview

The plugin system allows Haven to archive content from multiple sources through a unified interface. Each plugin implements the `ArchiverPlugin` interface and can be enabled, configured, and scheduled independently.

### Plugin Capabilities

Plugins can implement various capabilities:

| Capability | Description |
|------------|-------------|
| `DISCOVER` | Can discover new media sources (e.g., new YouTube videos) |
| `ARCHIVE` | Can archive/download media from a source |
| `STREAM` | Can handle live streams |
| `METADATA` | Can extract rich metadata from sources |

## Built-in Plugins

### YouTube Plugin

Archives videos from YouTube channels and playlists.

**Features:**
- Monitor multiple channels and playlists
- Quality selection (best, 1080p, 720p, 480p, 360p)
- Cookie authentication for age-restricted content
- Duplicate detection
- Automatic retry logic

**Configuration:**

```toml
[plugins.settings.YouTubePlugin]
channel_ids = ["UC_x5XG1OV2P6uZZ5FSM9Ttw"]
playlist_ids = ["PLxxxxxxxxxxxxxx"]
max_videos = 10
quality = "1080p"
format = "mp4"
output_dir = "~/haven/downloads/youtube"
cookies_file = "~/.config/haven/youtube_cookies.txt"
download_subtitles = false
max_retries = 3
```

See [YouTube Plugin Guide](youtube_plugin.md) for detailed documentation.

### BitTorrent Plugin

Archives torrents from RSS feeds and DHT.

**Features:**
- RSS feed monitoring
- DHT discovery
- Automatic seeding management
- Bandwidth limiting

**Configuration:**

```toml
[plugins.settings.BitTorrentPlugin]
rss_feeds = ["https://example.com/feed.rss"]
max_torrents = 100
output_dir = "~/haven/downloads/torrents"
max_bandwidth_up = 1024
max_bandwidth_down = 8192
```

### PumpFun Plugin

Records PumpFun live streams.

**Features:**
- Real-time stream recording
- Automatic segmenting
- Chat log archiving

**Configuration:**

```toml
[plugins.settings.PumpFunPlugin]
stream_ids = []
record_chat = true
segment_duration = 3600
output_dir = "~/haven/downloads/pumpfun"
```

### OpenRing Plugin

Captures WebRTC streams.

**Features:**
- WebRTC stream recording
- SIP/RTP support
- Multi-track recording

**Configuration:**

```toml
[plugins.settings.OpenRingPlugin]
endpoints = []
record_audio = true
record_video = true
output_dir = "~/haven/downloads/webrtc"
```

## Plugin Management

### Listing Plugins

```bash
# List active plugins
haven plugins list

# List all plugins including disabled
haven plugins list --all
```

### Enabling/Disabling Plugins

```bash
# Enable a plugin
haven plugins enable YouTubePlugin

# Disable a plugin
haven plugins disable YouTubePlugin
```

### Viewing Plugin Information

```bash
# Show plugin details
haven plugins info YouTubePlugin
```

### Configuring Plugins

```bash
# Show current configuration
haven plugins configure YouTubePlugin --show

# Set configuration values
haven plugins configure YouTubePlugin --set api_key=YOUR_KEY
haven plugins configure YouTubePlugin --set channel_ids=UCxxx,UCyyy
```

### Testing Plugins

```bash
# Run health check
haven plugins test YouTubePlugin

# Test discovery
haven plugins test YouTubePlugin --discover

# Test archiving
haven plugins test YouTubePlugin --archive https://youtube.com/watch?v=...
```

## Creating Custom Plugins

### Plugin Interface

Custom plugins must implement the `ArchiverPlugin` interface:

```python
from abc import ABC, abstractmethod
from typing import Any, Optional
from dataclasses import dataclass
from enum import Enum, auto

class PluginCapability(Enum):
    DISCOVER = auto()
    ARCHIVE = auto()
    STREAM = auto()
    METADATA = auto()

@dataclass
class MediaSource:
    source_id: str
    media_type: str
    uri: str
    metadata: dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

@dataclass
class ArchiveResult:
    success: bool
    output_path: Optional[str] = None
    file_size: int = 0
    duration: Optional[float] = None
    error: Optional[str] = None

class ArchiverPlugin(ABC):
    """Base interface for archiver plugins."""
    
    def __init__(self, config: Optional[dict[str, Any]] = None):
        self._config = config or {}
        self._initialized = False
        self.enabled = True
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Plugin name."""
        pass
    
    @property
    @abstractmethod
    def version(self) -> str:
        """Plugin version."""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """Plugin description."""
        pass
    
    @property
    @abstractmethod
    def capabilities(self) -> set[PluginCapability]:
        """Set of supported capabilities."""
        pass
    
    def has_capability(self, capability: PluginCapability) -> bool:
        """Check if plugin has a capability."""
        return capability in self.capabilities
    
    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the plugin."""
        pass
    
    @abstractmethod
    async def shutdown(self) -> None:
        """Cleanup plugin resources."""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check if plugin is healthy."""
        pass
    
    async def discover_sources(self) -> list[MediaSource]:
        """Discover new media sources.
        
        Required capability: DISCOVER
        """
        raise NotImplementedError("Plugin does not support discovery")
    
    async def archive(self, source: MediaSource) -> ArchiveResult:
        """Archive a media source.
        
        Required capability: ARCHIVE
        """
        raise NotImplementedError("Plugin does not support archiving")
    
    def configure(self, config: dict[str, Any]) -> None:
        """Update plugin configuration."""
        self._config.update(config)
```

### Example Custom Plugin

Here's a complete example of a custom plugin:

```python
import asyncio
from pathlib import Path
from haven_cli.plugins.base import (
    ArchiverPlugin, PluginCapability, MediaSource, ArchiveResult
)

class RSSFeedPlugin(ArchiverPlugin):
    """Example plugin for archiving media from RSS feeds."""
    
    @property
    def name(self) -> str:
        return "RSSFeedPlugin"
    
    @property
    def version(self) -> str:
        return "1.0.0"
    
    @property
    def description(self) -> str:
        return "Archive media from RSS feeds"
    
    @property
    def capabilities(self) -> set[PluginCapability]:
        return {PluginCapability.DISCOVER, PluginCapability.ARCHIVE}
    
    async def initialize(self) -> None:
        """Initialize the plugin."""
        # Validate required config
        if "feed_url" not in self._config:
            raise ValueError("RSSFeedPlugin requires 'feed_url' in config")
        
        # Import required libraries
        try:
            import feedparser
        except ImportError:
            raise ImportError("RSSFeedPlugin requires 'feedparser' package")
        
        self._initialized = True
    
    async def shutdown(self) -> None:
        """Cleanup resources."""
        self._initialized = False
    
    async def health_check(self) -> bool:
        """Check if RSS feed is accessible."""
        import aiohttp
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self._config["feed_url"], timeout=10) as resp:
                    return resp.status == 200
        except Exception:
            return False
    
    async def discover_sources(self) -> list[MediaSource]:
        """Discover media from RSS feed."""
        import feedparser
        
        feed = feedparser.parse(self._config["feed_url"])
        sources = []
        
        for entry in feed.entries[:self._config.get("max_items", 10)]:
            # Look for media links in the entry
            media_url = None
            if "links" in entry:
                for link in entry.links:
                    if link.get("type", "").startswith("video/"):
                        media_url = link.href
                        break
            
            if media_url:
                source = MediaSource(
                    source_id=entry.id,
                    media_type="video/rss",
                    uri=media_url,
                    metadata={
                        "title": entry.get("title", ""),
                        "published": entry.get("published", ""),
                        "summary": entry.get("summary", "")[:200],
                    }
                )
                sources.append(source)
        
        return sources
    
    async def archive(self, source: MediaSource) -> ArchiveResult:
        """Download media from source."""
        import aiohttp
        from pathlib import Path
        
        output_dir = Path(self._config.get("output_dir", "./downloads"))
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate output filename
        safe_title = "".join(c for c in source.metadata.get("title", "unknown") 
                             if c.isalnum() or c in " ._-").rstrip()
        output_path = output_dir / f"{safe_title}.mp4"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(source.uri) as resp:
                    resp.raise_for_status()
                    
                    # Download file
                    with open(output_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            f.write(chunk)
            
            file_size = output_path.stat().st_size
            
            return ArchiveResult(
                success=True,
                output_path=str(output_path),
                file_size=file_size,
            )
        
        except Exception as e:
            return ArchiveResult(
                success=False,
                error=str(e),
            )


# Register the plugin
from haven_cli.plugins.registry import register_plugin

register_plugin(RSSFeedPlugin)
```

### Plugin Registration

There are several ways to register custom plugins:

#### 1. Direct Registration

```python
from haven_cli.plugins.registry import register_plugin
from my_plugin import MyCustomPlugin

register_plugin(MyCustomPlugin)
```

#### 2. Plugin Directory

Place plugin files in a directory and add to config:

```toml
[plugins]
plugin_dirs = ["/path/to/custom/plugins"]
```

#### 3. Entry Points (for packaged plugins)

In your `pyproject.toml`:

```toml
[project.entry-points."haven_cli.plugins"]
my_plugin = "my_package.plugins:MyCustomPlugin"
```

## Plugin Configuration

### Global Plugin Settings

```toml
[plugins]
# Additional directories to search for plugins
plugin_dirs = ["/custom/plugins"]

# Plugins to enable by default
enabled_plugins = ["YouTubePlugin", "RSSFeedPlugin"]

# Plugins to disable
disabled_plugins = ["BitTorrentPlugin"]
```

### Per-Plugin Configuration

Each plugin has its own configuration section:

```toml
[plugins.settings.YouTubePlugin]
channel_ids = []
playlist_ids = []
max_videos = 10
quality = "best"

[plugins.settings.RSSFeedPlugin]
feed_url = "https://example.com/feed.xml"
max_items = 20
output_dir = "~/downloads/rss"
```

## Plugin API Reference

### PluginManager

```python
from haven_cli.plugins.manager import get_plugin_manager

manager = get_plugin_manager()

# Get a plugin
plugin = manager.get_plugin("YouTubePlugin")

# Initialize all plugins
await manager.initialize_all()

# Register a plugin
manager.register(MyCustomPlugin)

# Get all loaded plugins
plugins = manager.get_all_plugins()
```

### PluginRegistry

```python
from haven_cli.plugins.registry import get_registry

registry = get_registry()

# Discover all plugins
registry.discover_all()

# Get plugin info
info = registry.get_info("YouTubePlugin")

# Load a plugin class
plugin_class = registry.load("YouTubePlugin")

# Register a custom plugin
registry.register("my_plugin", MyCustomPlugin)
```

### Using Plugins Programmatically

```python
import asyncio
from haven_cli.plugins.manager import get_plugin_manager

async def main():
    # Get plugin manager
    manager = get_plugin_manager()
    
    # Initialize all plugins
    await manager.initialize_all()
    
    # Get a specific plugin
    plugin = manager.get_plugin("YouTubePlugin")
    
    if not plugin:
        print("Plugin not found")
        return
    
    # Check if healthy
    if not await plugin.health_check():
        print("Plugin is not healthy")
        return
    
    # Discover sources
    if plugin.has_capability(PluginCapability.DISCOVER):
        sources = await plugin.discover_sources()
        print(f"Discovered {len(sources)} sources")
        
        # Archive first source
        if sources and plugin.has_capability(PluginCapability.ARCHIVE):
            result = await plugin.archive(sources[0])
            if result.success:
                print(f"Archived: {result.output_path}")
            else:
                print(f"Failed: {result.error}")
    
    # Shutdown
    await manager.shutdown_all()

if __name__ == "__main__":
    asyncio.run(main())
```

## Best Practices

### Error Handling

Always handle errors gracefully in plugins:

```python
async def archive(self, source: MediaSource) -> ArchiveResult:
    try:
        # Archive logic
        return ArchiveResult(success=True, ...)
    except NetworkError as e:
        return ArchiveResult(success=False, error=f"Network error: {e}")
    except Exception as e:
        return ArchiveResult(success=False, error=f"Unexpected error: {e}")
```

### Resource Management

Properly manage resources in initialize/shutdown:

```python
async def initialize(self) -> None:
    self._session = aiohttp.ClientSession()
    self._initialized = True

async def shutdown(self) -> None:
    if self._session:
        await self._session.close()
    self._initialized = False
```

### Configuration Validation

Validate configuration in initialize:

```python
async def initialize(self) -> None:
    required_keys = ["api_key", "endpoint"]
    for key in required_keys:
        if key not in self._config:
            raise ValueError(f"{self.name} requires '{key}' in config")
    self._initialized = True
```

### Rate Limiting

Implement rate limiting for external APIs:

```python
import asyncio
from asyncio import Semaphore

class MyPlugin(ArchiverPlugin):
    def __init__(self, config=None):
        super().__init__(config)
        self._semaphore = Semaphore(5)  # Max 5 concurrent requests
    
    async def archive(self, source: MediaSource) -> ArchiveResult:
        async with self._semaphore:
            # Make API request
            ...
```

## Troubleshooting

### Plugin Not Found

If a plugin is not found:

1. Check if it's enabled: `haven plugins list --all`
2. Enable it: `haven plugins enable PluginName`
3. Check configuration: `haven plugins configure PluginName --show`

### Health Check Fails

If health check fails:

1. Check plugin configuration
2. Verify external dependencies are installed
3. Check network connectivity
4. Review logs with `--verbose`

### Discovery Returns No Sources

If discovery returns empty:

1. Check configuration (e.g., channel IDs for YouTube)
2. Verify API credentials
3. Check if sources already archived (duplicate detection)
4. Test with `haven plugins test PluginName --discover`
