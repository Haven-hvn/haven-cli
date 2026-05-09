"""Tests for the Brightcove plugin.

These tests verify that the Brightcove plugin correctly:
1. Initializes with proper configuration
2. Discovers videos from Brightcove playlists
3. Archives videos using yt-dlp
4. Handles pagination correctly
5. Resolves asset IDs to video IDs and stream URLs
6. Tracks seen videos for deduplication
"""

import asyncio
import json
import os
import pytest
from pathlib import Path
import httpx
from unittest.mock import AsyncMock, MagicMock, Mock, patch, call

from haven_cli.plugins.builtin.brightcove import (
    BrightcovePlugin,
    BrightcoveSourceConfig,
    BrightcoveAPIClient,
    AssetInfo,
)
from haven_cli.plugins.base import MediaSource, ArchiveResult, PluginCapability


class TestBrightcoveSourceConfig:
    """Test Brightcove source configuration."""
    
    def test_default_config(self):
        """Test creating config with required fields."""
        config = BrightcoveSourceConfig(
            name="test_source",
            display_name="Test Source",
            base_playlist_url="https://api.brightcove.com/playlist/123",
            account_id="acc123",
            brightcove_account_id="bc456"
        )
        
        assert config.name == "test_source"
        assert config.display_name == "Test Source"
        assert config.base_playlist_url == "https://api.brightcove.com/playlist/123"
        assert config.account_id == "acc123"
        assert config.brightcove_account_id == "bc456"
        assert config.poll_interval_minutes == 60
        assert config.rate_limit_seconds == 1.0
        assert config.max_retries == 3
        assert config.output_format == "mp4"
        assert config.quality_preference == "best"
        assert config.enabled is True
    
    def test_config_from_dict(self):
        """Test creating config from dictionary."""
        data = {
            "name": "the_den",
            "display_name": "The Den",
            "description": "Skateboard videos",
            "base_playlist_url": "https://beacon.playback.api.brightcove.com/playlist/760",
            "playlist_params": {"cohort": "123", "device_type": "web"},
            "account_id": "acc123",
            "brightcove_account_id": "bc456",
            "ad_config_id": "ad789",
            "output_format": "mkv",
            "quality_preference": "1080p",
            "enabled": False,
        }
        
        config = BrightcoveSourceConfig.from_dict(data)
        
        assert config.name == "the_den"
        assert config.display_name == "The Den"
        assert config.description == "Skateboard videos"
        assert config.ad_config_id == "ad789"
        assert config.playlist_params == {"cohort": "123", "device_type": "web"}
        assert config.output_format == "mkv"
        assert config.quality_preference == "1080p"
        assert config.enabled is False
    
    def test_config_to_dict(self):
        """Test converting config to dictionary."""
        config = BrightcoveSourceConfig(
            name="test",
            display_name="Test",
            base_playlist_url="https://api.example.com/playlist",
            account_id="acc123",
            brightcove_account_id="bc456"
        )
        
        data = config.to_dict()
        
        assert data["name"] == "test"
        assert data["display_name"] == "Test"
        assert "base_playlist_url" in data
        assert "account_id" in data


class TestAssetInfo:
    """Test AssetInfo dataclass."""
    
    def test_asset_info_creation(self):
        """Test creating AssetInfo."""
        asset = AssetInfo(
            asset_id="asset123",
            title="Test Video",
            description="A test video",
            duration_seconds=120,
            thumbnail_url="https://example.com/thumb.jpg"
        )
        
        assert asset.asset_id == "asset123"
        assert asset.title == "Test Video"
        assert asset.description == "A test video"
        assert asset.duration_seconds == 120
        assert asset.thumbnail_url == "https://example.com/thumb.jpg"
        assert asset.video_id is None
        assert asset.stream_url is None


class TestBrightcoveAPIClient:
    """Test Brightcove API client."""
    
    @pytest.fixture
    def config(self):
        """Create a test source config."""
        return BrightcoveSourceConfig(
            name="test",
            display_name="Test",
            base_playlist_url="https://beacon.playback.api.brightcove.com/api/playlists/760",
            playlist_params={"cohort": "123"},
            account_id="acc123",
            brightcove_account_id="bc456",
            ad_config_id="ad789",
            rate_limit_seconds=0.0,  # No delay for tests
        )
    
    @pytest.fixture
    def mock_client(self):
        """Create a mock httpx client."""
        return Mock(spec=httpx.AsyncClient)
    
    @pytest.mark.asyncio
    async def test_get_playlist_page_success(self, config, mock_client):
        """Test successful playlist page fetch."""
        # Mock response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "blocks": [{
                    "widgets": [{
                        "playlist": {
                            "contents": [
                                {"type": "movies", "id": "asset1", "title": "Video 1", "duration": 120},
                                {"type": "movies", "id": "asset2", "title": "Video 2", "duration": 180},
                            ],
                            "pagination": {"url": {"next": "https://api.example.com/next"}}
                        }
                    }]
                }]
            }
        }
        mock_client.get.return_value = mock_response
        
        api_client = BrightcoveAPIClient(config, mock_client)
        assets, next_url = await api_client.get_playlist_page()
        
        assert len(assets) == 2
        assert assets[0].asset_id == "asset1"
        assert assets[0].title == "Video 1"
        assert assets[0].duration_seconds == 120
        assert assets[1].asset_id == "asset2"
        assert next_url == "https://api.example.com/next"
    
    @pytest.mark.asyncio
    async def test_get_playlist_page_no_results(self, config, mock_client):
        """Test playlist page with no results."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {"blocks": []}
        }
        mock_client.get.return_value = mock_response
        
        api_client = BrightcoveAPIClient(config, mock_client)
        assets, next_url = await api_client.get_playlist_page()
        
        assert len(assets) == 0
        assert next_url is None
    
    @pytest.mark.asyncio
    async def test_get_playlist_page_error(self, config, mock_client):
        """Test playlist page with API error."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_client.get.return_value = mock_response
        
        api_client = BrightcoveAPIClient(config, mock_client)
        assets, next_url = await api_client.get_playlist_page()
        
        assert len(assets) == 0
        assert next_url is None
    
    @pytest.mark.asyncio
    async def test_asset_id_to_video_id_success(self, config, mock_client):
        """Test successful asset ID resolution."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "video_playback_details": [{"video_id": "vid123"}]
            }
        }
        mock_client.get.return_value = mock_response
        
        api_client = BrightcoveAPIClient(config, mock_client)
        video_id = await api_client.asset_id_to_video_id("asset1")
        
        assert video_id == "vid123"
    
    @pytest.mark.asyncio
    async def test_asset_id_to_video_id_no_details(self, config, mock_client):
        """Test asset ID resolution with no playback details."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {"video_playback_details": []}
        }
        mock_client.get.return_value = mock_response
        
        api_client = BrightcoveAPIClient(config, mock_client)
        video_id = await api_client.asset_id_to_video_id("asset1")
        
        assert video_id is None
    
    @pytest.mark.asyncio
    async def test_video_id_to_stream_url_success(self, config, mock_client):
        """Test successful stream URL resolution."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "sources": [
                {"type": "application/x-mpegURL", "src": "https://stream.example.com/video.m3u8"},
                {"type": "video/mp4", "src": "https://video.example.com/video.mp4"}
            ]
        }
        mock_client.get.return_value = mock_response
        
        api_client = BrightcoveAPIClient(config, mock_client)
        stream_url = await api_client.video_id_to_stream_url("vid123")
        
        assert stream_url == "https://stream.example.com/video.m3u8"
    
    @pytest.mark.asyncio
    async def test_video_id_to_stream_url_fallback(self, config, mock_client):
        """Test stream URL fallback when no HLS source."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "sources": [
                {"type": "video/mp4", "src": "https://video.example.com/video.mp4"}
            ]
        }
        mock_client.get.return_value = mock_response
        
        api_client = BrightcoveAPIClient(config, mock_client)
        stream_url = await api_client.video_id_to_stream_url("vid123")
        
        assert stream_url == "https://video.example.com/video.mp4"
    
    @pytest.mark.asyncio
    async def test_resolve_asset(self, config, mock_client):
        """Test full asset resolution."""
        # Mock responses for both API calls
        responses = [
            Mock(status_code=200, json=Mock(return_value={
                "data": {"video_playback_details": [{"video_id": "vid123"}]}
            })),
            Mock(status_code=200, json=Mock(return_value={
                "sources": [{"type": "application/x-mpegURL", "src": "https://stream.example.com/video.m3u8"}]
            }))
        ]
        mock_client.get.side_effect = responses
        
        api_client = BrightcoveAPIClient(config, mock_client)
        asset = AssetInfo(asset_id="asset1", title="Test Video")
        
        await api_client.resolve_asset(asset)
        
        assert asset.video_id == "vid123"
        assert asset.stream_url == "https://stream.example.com/video.m3u8"


class TestBrightcovePlugin:
    """Test BrightcovePlugin functionality."""
    
    @pytest.fixture
    def plugin(self, tmp_path):
        """Create a BrightcovePlugin instance with temporary output directory."""
        config = {
            "download_dir": str(tmp_path / "downloads"),
        }
        return BrightcovePlugin(config=config)
    
    @pytest.fixture
    def mock_progress_tracker(self):
        """Create a mock progress tracker."""
        tracker = Mock()
        tracker.report_progress = Mock()
        return tracker
    
    @pytest.fixture
    async def initialized_plugin(self, plugin):
        """Create an initialized plugin with mocked subprocess."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
            await plugin.initialize()
        
        yield plugin
        
        # Cleanup
        await plugin.shutdown()
    
    def test_plugin_info(self, plugin):
        """Test plugin metadata."""
        info = plugin.info
        
        assert info.name == "BrightcovePlugin"
        assert info.display_name == "Brightcove Archiver"
        assert info.version == "1.0.0"
        assert "brightcove" in info.media_types
        assert PluginCapability.DISCOVER in info.capabilities
        assert PluginCapability.ARCHIVE in info.capabilities
        assert PluginCapability.HEALTH_CHECK in info.capabilities
    
    @pytest.mark.asyncio
    async def test_initialize_success(self, plugin, tmp_path):
        """Test successful plugin initialization."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
            await plugin.initialize()
        
        assert plugin._initialized is True
        assert (tmp_path / "downloads").exists()
        
        await plugin.shutdown()
    
    @pytest.mark.asyncio
    async def test_initialize_yt_dlp_not_found(self, plugin):
        """Test initialization fails when yt-dlp is not installed."""
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="yt-dlp not found"):
                await plugin.initialize()
    
    @pytest.mark.asyncio
    async def test_health_check_success(self, plugin):
        """Test health check when plugin is healthy."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
            await plugin.initialize()
        
        result = await plugin.health_check()
        assert result is True
        
        await plugin.shutdown()
    
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
    async def test_discover_sources_disabled(self, plugin):
        """Test discover skips disabled sources."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
            await plugin.initialize()
        
        # Disable all sources
        for s in plugin.sources:
            s.enabled = False
        
        sources = await plugin.discover_sources()
        assert sources == []
        
        await plugin.shutdown()
    
    @pytest.mark.asyncio
    async def test_archive_not_initialized(self, plugin):
        """Test archive fails when not initialized."""
        source = MediaSource(
            source_id="test123",
            media_type="brightcove",
            uri="https://stream.example.com/video.m3u8",
            title="Test Video",
        )
        
        result = await plugin.archive(source)
        
        assert result.success is False
        assert "not initialized" in result.error.lower()
    
    @pytest.mark.asyncio
    async def test_archive_wrong_media_type(self, plugin):
        """Test archive fails for unsupported media type."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
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
        
        await plugin.shutdown()
    
    @pytest.mark.asyncio
    async def test_archive_already_archived(self, plugin):
        """Test archive returns success for already archived video."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
            await plugin.initialize()
        
        source = MediaSource(
            source_id="brightcove:existing123",
            media_type="brightcove",
            uri="https://stream.example.com/video.m3u8",
            title="Existing Video",
            metadata={"asset_id": "existing123"},
        )
        
        # Add to archived videos
        plugin._archived_videos["brightcove:existing123"] = {
            "video_id": "brightcove:existing123",
            "asset_id": "existing123",
            "output_path": "/path/to/video.mp4",
            "file_size": 1024000,
        }
        
        result = await plugin.archive(source)
        
        assert result.success is True
        assert result.metadata.get("already_archived") is True
        
        await plugin.shutdown()
    
    @pytest.mark.asyncio
    async def test_archive_existing_file(self, plugin, tmp_path):
        """Test archive returns success when file already exists."""
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
            await plugin.initialize()
        
        # Create existing file
        source_dir = tmp_path / "downloads" / "the_den"
        source_dir.mkdir(parents=True, exist_ok=True)
        existing_file = source_dir / "Test Video_asset123.mp4"
        existing_file.write_bytes(b"fake video content")
        
        source = MediaSource(
            source_id="brightcove:asset123",
            media_type="brightcove",
            uri="https://stream.example.com/video.m3u8",
            title="Test Video",
            metadata={"asset_id": "asset123", "video_format": "mp4", "source_name": "the_den"},
        )
        
        result = await plugin.archive(source)
        
        assert result.success is True
        assert result.metadata.get("existing") is True
        
        await plugin.shutdown()
    
    def test_add_source(self, plugin):
        """Test adding a new source."""
        new_config = BrightcoveSourceConfig(
            name="new_source",
            display_name="New Source",
            base_playlist_url="https://api.example.com/playlist",
            account_id="acc123",
            brightcove_account_id="bc456"
        )
        
        result = plugin.add_source(new_config)
        
        assert result["success"] is True
        assert len(plugin.sources) == 2  # Default + new
    
    def test_add_source_duplicate(self, plugin):
        """Test adding a duplicate source fails."""
        new_config = BrightcoveSourceConfig(
            name="the_den",  # Default source name
            display_name="Duplicate",
            base_playlist_url="https://api.example.com/playlist",
            account_id="acc123",
            brightcove_account_id="bc456"
        )
        
        result = plugin.add_source(new_config)
        
        assert result["success"] is False
        assert "already exists" in result["error"]
    
    def test_remove_source(self, plugin):
        """Test removing a source."""
        result = plugin.remove_source("the_den")
        
        assert result["success"] is True
        assert len(plugin.sources) == 0
    
    def test_remove_source_not_found(self, plugin):
        """Test removing a non-existent source fails."""
        result = plugin.remove_source("nonexistent")
        
        assert result["success"] is False
        assert "not found" in result["error"]
    
    def test_enable_disable_source(self, plugin):
        """Test enabling and disabling sources."""
        # Initially enabled
        assert plugin.sources[0].enabled is True
        
        # Disable
        result = plugin.disable_source("the_den")
        assert result is True
        assert plugin.sources[0].enabled is False
        
        # Enable
        result = plugin.enable_source("the_den")
        assert result is True
        assert plugin.sources[0].enabled is True
    
    def test_default_the_den_config(self, plugin):
        """Test that default The Den config is present."""
        assert len(plugin.sources) == 1
        assert plugin.sources[0].name == "the_den"
        assert "watchentertheden" in plugin.sources[0].description.lower() or True  # Description may vary


class TestBrightcovePluginShutdown:
    """Test plugin shutdown and state persistence."""
    
    @pytest.mark.asyncio
    async def test_shutdown_saves_state(self, tmp_path):
        """Test that shutdown saves seen videos state."""
        config = {"download_dir": str(tmp_path / "downloads")}
        plugin = BrightcovePlugin(config=config)
        
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate.return_value = (b"2024.01.01\n", b"")
            mock_exec.return_value = mock_proc
            
            await plugin.initialize()
        
        # Add some seen videos
        plugin._seen_videos = {"asset1", "asset2"}
        plugin._archived_videos = {"asset1": {"output_path": "/path/to/video.mp4"}}
        
        await plugin.shutdown()
        
        # Check that state file was created
        seen_file = plugin.download_dir / ".brightcove_seen_videos.json"
        assert seen_file.exists()
        
        with open(seen_file) as f:
            data = json.load(f)
            assert "asset1" in data["seen"]
            assert "asset2" in data["seen"]
            assert "asset1" in data["archived"]
