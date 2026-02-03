# YouTube Plugin

The YouTube Plugin is a built-in archiver plugin for Haven CLI that enables discovering and downloading videos from YouTube channels and playlists using yt-dlp.

## Features

- **Channel & Playlist Support**: Monitor multiple YouTube channels and playlists
- **Quality Selection**: Choose video quality from best, 1080p, 720p, 480p, or 360p
- **Format Selection**: Download in mp4, webm, or mkv formats
- **Cookie Authentication**: Support for YouTube cookies to download age-restricted content
- **Duplicate Detection**: Track seen videos to avoid downloading duplicates
- **Retry Logic**: Automatic retries for transient failures
- **JavaScript Runtime Support**: Automatic detection of Deno or Node.js for enhanced decryption

## Prerequisites

You must have yt-dlp installed on your system:

```bash
# macOS
brew install yt-dlp

# Linux (Ubuntu/Debian)
sudo apt install yt-dlp

# Or install via pip
pip install yt-dlp

# For latest version
pip install -U yt-dlp
```

Optional: Install Deno or Node.js for enhanced YouTube signature decryption:

```bash
# Deno (recommended)
curl -fsSL https://deno.land/install.sh | sh

# Or Node.js
brew install node  # macOS
sudo apt install nodejs  # Ubuntu/Debian
```

## Configuration

Add YouTube plugin configuration to your Haven config file (`~/.haven/config.toml`):

```toml
[plugins.YouTubePlugin]
# YouTube channel IDs to monitor
channel_ids = ["UC_x5XG1OV2P6uZZ5FSM9Ttw", "UCanother_channel"]

# YouTube playlist IDs to monitor
playlist_ids = ["PLxxxxxxxxxxxxxx"]

# Maximum videos to discover per channel/playlist
max_videos = 20

# Video quality: best, 1080p, 720p, 480p, 360p
quality = "1080p"

# Container format: mp4, webm, mkv
format = "mp4"

# Output directory for downloads
output_dir = "~/haven/downloads/youtube"

# Path to YouTube cookies file (for age-restricted content)
cookies_file = "~/.config/haven/youtube_cookies.txt"

# Download subtitles
download_subtitles = false

# Maximum retry attempts
max_retries = 3
```

## Getting YouTube Cookies

To download age-restricted videos, you need to provide YouTube cookies:

1. **Browser Extension Method** (Recommended):
   - Install "Get cookies.txt LOCALLY" extension (Chrome/Firefox)
   - Sign in to YouTube in your browser
   - Click the extension icon and export cookies
   - Save to the path specified in `cookies_file`

2. **yt-dlp Method**:
   ```bash
   yt-dlp --cookies-from-browser chrome --cookies ~/haven/youtube_cookies.txt
   ```

## Usage

### Initialize the Plugin

```python
from haven_cli.plugins import PluginManager

manager = PluginManager()
await manager.initialize_all()

# Or manually
from haven_cli.plugins.builtin import YouTubePlugin

plugin = YouTubePlugin(config={
    "channel_ids": ["UC_x5XG1OV2P6uZZ5FSM9Ttw"],
    "quality": "1080p",
    "output_dir": "~/videos/youtube"
})
await plugin.initialize()
```

### Discover Videos

```python
# Discover new videos from configured channels/playlists
sources = await plugin.discover_sources()

for source in sources:
    print(f"Found: {source.title}")
    print(f"ID: {source.source_id}")
    print(f"URI: {source.uri}")
    print(f"Duration: {source.metadata.get('duration')} seconds")
```

### Archive a Video

```python
# Archive a discovered video
result = await plugin.archive(sources[0])

if result.success:
    print(f"Downloaded to: {result.output_path}")
    print(f"Size: {result.file_size} bytes")
    print(f"Duration: {result.duration} seconds")
else:
    print(f"Failed: {result.error}")
```

### Health Check

```python
# Check if plugin is healthy
is_healthy = await plugin.health_check()
print(f"Plugin healthy: {is_healthy}")
```

## Programmatic Usage Example

```python
import asyncio
from pathlib import Path
from haven_cli.plugins.builtin import YouTubePlugin

async def main():
    # Configure plugin
    plugin = YouTubePlugin(config={
        "channel_ids": ["UCBa659QWEk1AI4Tg--mrJ2A"],  # Tom Scott
        "max_videos": 5,
        "quality": "1080p",
        "output_dir": "~/videos/youtube"
    })
    
    # Initialize
    await plugin.initialize()
    
    # Discover videos
    sources = await plugin.discover_sources()
    print(f"Discovered {len(sources)} new videos")
    
    # Archive first video
    if sources:
        result = await plugin.archive(sources[0])
        if result.success:
            print(f"Downloaded: {result.output_path}")
        else:
            print(f"Failed: {result.error}")
    
    # Cleanup
    await plugin.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting

### yt-dlp not found

Make sure yt-dlp is installed and in your PATH:

```bash
which yt-dlp
yt-dlp --version
```

### JavaScript Runtime Warning

If you see "No JavaScript runtime detected", some videos may fail to download. Install Deno or Node.js:

```bash
curl -fsSL https://deno.land/install.sh | sh
```

### Rate Limiting

YouTube may rate-limit excessive requests. The plugin includes automatic retries with exponential backoff. If you continue to experience issues:

1. Reduce `max_videos` setting
2. Add delays between operations
3. Use cookies from an authenticated session

### Cookie Issues

If age-restricted videos still fail after setting cookies:

1. Ensure cookies are in Netscape format
2. Verify cookie file path is correct
3. Re-export cookies (they expire)
4. Ensure you're signed into YouTube when exporting

## API Reference

### YouTubePlugin

The main plugin class implementing `ArchiverPlugin`.

#### Methods

- `initialize()`: Initialize the plugin and verify yt-dlp
- `shutdown()`: Save state and cleanup
- `health_check()`: Check if plugin is operational
- `discover_sources()`: Discover new videos from channels/playlists
- `archive(source)`: Download a video
- `configure(config)`: Update plugin configuration

### YouTubeConfig

Configuration dataclass for the plugin.

#### Attributes

- `channel_ids`: List of YouTube channel IDs
- `playlist_ids`: List of YouTube playlist IDs
- `max_videos`: Maximum videos to discover (default: 10)
- `quality`: Video quality (default: "best")
- `format`: Container format (default: "mp4")
- `output_dir`: Output directory (default: "./downloads")
- `cookies_file`: Path to cookies file (optional)
- `download_subtitles`: Download subtitles (default: False)
- `max_retries`: Max retry attempts (default: 3)
