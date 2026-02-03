"""Tests for the YouTube plugin.

These tests verify that the YouTube plugin correctly:
1. Initializes with proper configuration
2. Discovers videos from channels and playlists
3. Archives videos using yt-dlp
4. Handles errors gracefully
"""

import asyncio
import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from haven_cli.plugins.builtin.youtube import YouTubePlugin, YouTubeConfig
from haven_cli.plugins.base import MediaSource, ArchiveResult, PluginCapability


class TestYouTubeConfig:
    """Test YouTube configuration dataclass."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = YouTubeConfig()
        assert config.channel_ids == []
        assert config.playlist_ids == []
        assert config.max_videos == 10
        assert config.quality == "best"
        assert config.format == "mp4"
        assert config.download_subtitles is False
        assert config.max_retries == 3
    
    def test_config_from_dict(self):
        """Test creating config from dictionary."""
        data = {
            "channel_ids": ["UC123", "UC456"],
            "playlist_ids": ["PL789"],
            "max_videos": 20,
            "quality": "1080p",
            "format": "webm",
            "output_dir": "~/videos",
            "cookies_file": "~/.cookies.txt",
            "download_subtitles": True,
            "max_retries": 5,
        }
        config = YouTubeConfig.from_dict(data)
        
        assert config.channel_ids == ["UC123", "UC456"]
        assert config.playlist_ids == ["PL789"]
        assert config.max_videos == 20
        assert config.quality == "1080p"
        assert config.format == "webm"
        assert "videos" in str(config.output_dir)  # Expanded path
        assert "cookies.txt" in str(config.cookies_file)
        assert config.download_subtitles is True
        assert config.max_retries == 5
    
    def test_config_home_directory_expansion(self):
        """Test that ~ is expanded to home directory."""
        config = YouTubeConfig.from_dict({
            "output_dir": "~/test_videos",
            "cookies_file": "~/test_cookies.txt"
        })
        
        home = str(Path.home())
        assert home in str(config.output_dir)
        assert home in str(config.cookies_file)


class TestYouTubePlugin:
    """Test YouTubePlugin functionality."""
    
    @pytest.fixture
    def plugin(self, tmp_path):
        """Create a YouTubePlugin instance with temporary output directory."""
        config = {
            "channel_ids": ["UC_test123"],
            "playlist_ids": ["PL_test456"],
            "max_videos": 5,
            "quality": "720p",
            "output_dir": str(tmp_path / "downloads"),
        }
        return YouTubePlugin(config=config)
    
    @pytest.fixture
    async def initialized_plugin(self, plugin):
        """Create an initialized plugin with mocked subprocess."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            # Mock yt-dlp --version
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
            # Mock Deno/Node.js detection (not available)
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=1)
                
                # Run initialization
                await plugin.initialize()
        
        yield plugin
    
    def test_plugin_info(self, plugin):
        """Test plugin metadata."""
        info = plugin.info
        
        assert info.name == "YouTubePlugin"
        assert info.display_name == "YouTube Archiver"
        assert info.version == "1.0.0"
        assert "youtube" in info.media_types
        assert PluginCapability.DISCOVER in info.capabilities
        assert PluginCapability.ARCHIVE in info.capabilities
        assert PluginCapability.METADATA in info.capabilities
        assert PluginCapability.HEALTH_CHECK in info.capabilities
    
    def test_plugin_name_property(self, plugin):
        """Test plugin name property."""
        assert plugin.name == "YouTubePlugin"
    
    @pytest.mark.asyncio
    async def test_initialize_success(self, plugin, tmp_path):
        """Test successful plugin initialization."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
            # Mock JS runtime detection failure (optional)
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=1)
                
                await plugin.initialize()
        
        assert plugin._initialized is True
        assert (tmp_path / "downloads").exists()
    
    @pytest.mark.asyncio
    async def test_initialize_yt_dlp_not_found(self, plugin):
        """Test initialization fails when yt-dlp is not installed."""
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="yt-dlp not found"):
                await plugin.initialize()
    
    @pytest.mark.asyncio
    async def test_health_check_success(self, plugin):
        """Test health check when plugin is healthy."""
        # Initialize plugin first
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=1)
                await plugin.initialize()
        
        # Now test health check
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
            result = await plugin.health_check()
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_health_check_not_initialized(self, plugin):
        """Test health check fails when not initialized."""
        result = await plugin.health_check()
        assert result is False
    
    @pytest.mark.asyncio
    async def test_discover_sources_not_initialized(self, plugin):
        """Test discover fails when not initialized."""
        sources = await plugin.discover_sources()
        assert sources == []
    
    @pytest.mark.asyncio
    async def test_discover_from_channel(self, plugin):
        """Test discovering videos from a channel."""
        # Initialize plugin with only channel_ids (no playlists)
        plugin._yt_config.channel_ids = ["UC_test123"]
        plugin._yt_config.playlist_ids = []
        
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=1)
                await plugin.initialize()
        
        mock_videos = [
            MediaSource(
                source_id="video1",
                media_type="youtube",
                uri="https://youtube.com/watch?v=video1",
                title="Test Video 1",
                metadata={"duration": 120},
            )
        ]
        
        with patch.object(plugin, "_extract_video_list") as mock_extract:
            mock_extract.return_value = mock_videos
            
            sources = await plugin.discover_sources()
        
        assert len(sources) == 1
        assert sources[0].source_id == "video1"
        assert sources[0].media_type == "youtube"
    
    @pytest.mark.asyncio
    async def test_archive_not_initialized(self, plugin):
        """Test archive fails when not initialized."""
        source = MediaSource(
            source_id="test123",
            media_type="youtube",
            uri="https://youtube.com/watch?v=test123",
            title="Test Video",
        )
        
        result = await plugin.archive(source)
        
        assert result.success is False
        assert "not initialized" in result.error.lower()
    
    @pytest.mark.asyncio
    async def test_archive_wrong_media_type(self, plugin):
        """Test archive fails for non-YouTube media type."""
        # Initialize plugin
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=1)
                await plugin.initialize()
        
        source = MediaSource(
            source_id="test123",
            media_type="bittorrent",
            uri="magnet:test",
            title="Test",
        )
        
        result = await plugin.archive(source)
        
        assert result.success is False
        assert "Unsupported media type" in result.error
    
    @pytest.mark.asyncio
    async def test_archive_already_archived(self, plugin):
        """Test archive returns success for already archived video."""
        # Initialize plugin
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=1)
                await plugin.initialize()
        
        source = MediaSource(
            source_id="existing123",
            media_type="youtube",
            uri="https://youtube.com/watch?v=existing123",
            title="Existing Video",
        )
        
        # Add to archived videos
        plugin._archived_videos["existing123"] = {
            "video_id": "existing123",
            "output_path": "/path/to/video.mp4",
            "file_size": 1024000,
        }
        
        result = await plugin.archive(source)
        
        assert result.success is True
        assert result.metadata.get("already_archived") is True
    
    def test_is_retryable_error(self, plugin):
        """Test error classification."""
        # Non-retryable errors
        assert not plugin._is_retryable_error("Video unavailable")
        assert not plugin._is_retryable_error("Private video")
        assert not plugin._is_retryable_error("copyright claim")
        assert not plugin._is_retryable_error("404 Not Found")
        
        # Retryable errors
        assert plugin._is_retryable_error("network error")
        assert plugin._is_retryable_error("connection timeout")
        assert plugin._is_retryable_error("429 Too Many Requests")
        assert plugin._is_retryable_error("JavaScript runtime error")
    
    def test_detect_js_runtime(self, plugin):
        """Test JavaScript runtime detection."""
        # Test Deno detection
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0)
            runtime_type, runtime_path = plugin._detect_js_runtime()
            assert runtime_type == "deno"
            assert runtime_path == "deno"
        
        # Test Node.js fallback
        with patch("subprocess.run") as mock_run:
            def side_effect(cmd, **kwargs):
                if cmd[0] == "deno":
                    return Mock(returncode=1)
                elif cmd[0] == "node":
                    return Mock(returncode=0)
                return Mock(returncode=1)
            
            mock_run.side_effect = side_effect
            runtime_type, runtime_path = plugin._detect_js_runtime()
            assert runtime_type == "nodejs"
            assert runtime_path == "node"
        
        # Test no runtime available
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1)
            runtime_type, runtime_path = plugin._detect_js_runtime()
            assert runtime_type is None
            assert runtime_path is None
    
    def test_extract_output_path_from_merge(self, plugin):
        """Test extracting output path from merge output."""
        stdout = '[ffmpeg] Merging formats into "/path/to/video.mp4"'
        result = plugin._extract_output_path(stdout, "", "")
        
        # Won't match since file doesn't exist
        assert result is None
    
    @pytest.mark.asyncio
    async def test_configure_updates_config(self, plugin):
        """Test that configure updates plugin configuration."""
        # Initialize plugin
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=1)
                await plugin.initialize()
        
        plugin.configure({
            "max_videos": 50,
            "quality": "1080p",
        })
        
        assert plugin._yt_config.max_videos == 50
        assert plugin._yt_config.quality == "1080p"
    
    @pytest.mark.asyncio
    async def test_shutdown_saves_state(self, plugin, tmp_path):
        """Test that shutdown saves seen videos state."""
        # Initialize plugin
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=1)
                await plugin.initialize()
        
        plugin._seen_videos = {"video1", "video2"}
        plugin._archived_videos = {"video1": {"output_path": "/path"}}
        
        await plugin.shutdown()
        
        seen_file = plugin._yt_config.output_dir / ".youtube_seen_videos.json"
        assert seen_file.exists()
        
        with open(seen_file) as f:
            data = json.load(f)
            assert "video1" in data["seen"]
            assert "video2" in data["seen"]


class TestYouTubePluginIntegration:
    """Integration tests for YouTube plugin.
    
    These tests require yt-dlp to be installed and may make network requests.
    They are marked as integration tests and should be run separately.
    """
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_initialize_with_real_yt_dlp(self, tmp_path):
        """Test initialization with real yt-dlp installation."""
        config = {"output_dir": str(tmp_path / "downloads")}
        plugin = YouTubePlugin(config=config)
        
        # This will fail if yt-dlp is not installed
        try:
            await plugin.initialize()
            assert plugin._initialized is True
        except RuntimeError as e:
            pytest.skip(f"yt-dlp not installed: {e}")
    
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_health_check_with_real_yt_dlp(self, tmp_path):
        """Test health check with real yt-dlp installation."""
        config = {"output_dir": str(tmp_path / "downloads")}
        plugin = YouTubePlugin(config=config)
        
        try:
            await plugin.initialize()
            result = await plugin.health_check()
            assert result is True
        except RuntimeError:
            pytest.skip("yt-dlp not installed")
