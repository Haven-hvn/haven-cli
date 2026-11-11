"""Tests for BitTorrent plugin."""

import pytest
from unittest.mock import Mock, patch, MagicMock

from haven_cli.plugins.builtin.bittorrent import BitTorrentPlugin, BitTorrentConfig
from haven_cli.plugins.base import MediaSource


class TestBitTorrentConfig:
    """Tests for BitTorrentConfig."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = BitTorrentConfig()
        assert config.download_dir == "downloads/bittorrent"
        assert config.max_concurrent_downloads == 3
        assert config.max_download_speed == 0
        assert config.max_upload_speed == 0
        assert config.seed_ratio == 0.0
        assert config.seed_time == 0
        assert len(config.video_extensions) > 0
        assert config.min_video_size == 10 * 1024 * 1024
        assert config.max_video_size == 50 * 1024 * 1024 * 1024
        assert config.select_mode == "all"
        assert config.sources == []
        assert config.enabled is True

    def test_from_dict(self):
        """Test creating config from dictionary."""
        config = BitTorrentConfig.from_dict({
            "download_dir": "/tmp/torrents",
            "max_concurrent_downloads": 5,
            "max_download_speed": 1024 * 1024,
            "select_mode": "largest",
            "sources": [
                {
                    "name": "test",
                    "type": "forum",
                    "domain": "example.com",
                    "forum_id": "1",
                }
            ],
        })
        assert config.download_dir == "/tmp/torrents"
        assert config.max_concurrent_downloads == 5
        assert config.max_download_speed == 1024 * 1024
        assert config.select_mode == "largest"
        assert len(config.sources) == 1
        assert config.sources[0]["name"] == "test"

    def test_invalid_select_mode_rejected(self):
        """Only 'all' and 'largest' are valid selection modes."""
        with pytest.raises(ValueError):
            BitTorrentConfig.from_dict({"select_mode": "smallest"})


class TestBitTorrentPlugin:
    """Tests for BitTorrentPlugin."""
    
    def test_plugin_info(self):
        """Test plugin information."""
        plugin = BitTorrentPlugin()
        info = plugin.info
        
        assert info.name == "bittorrent"
        assert info.display_name == "BitTorrent Archiver"
        assert info.version == "1.0.0"
        assert "libtorrent" in info.description.lower()
        assert info.author == "Haven Team"
        assert "bittorrent" in info.media_types
    
    
    
    
    
    
    
    
    
    def test_configure(self):
        """Test updating plugin configuration."""
        plugin = BitTorrentPlugin()
        
        new_config = {
            "download_dir": "/new/path",
            "max_concurrent_downloads": 10,
        }
        
        plugin.configure(new_config)
        
        assert plugin._bt_config.download_dir == "/new/path"
        assert plugin._bt_config.max_concurrent_downloads == 10
    


class TestBitTorrentSizeLimits:
    """Tests for BitTorrent size limit functionality."""
    
    def test_default_size_limits(self):
        """Test default size limit values."""
        config = BitTorrentConfig()
        
        # Default min is 10 MB
        assert config.min_video_size == 10 * 1024 * 1024
        # Default max is 50 GB
        assert config.max_video_size == 50 * 1024 * 1024 * 1024
    
    def test_custom_size_limits(self):
        """Test custom size limit configuration."""
        config = BitTorrentConfig.from_dict({
            "min_video_size": 100 * 1024 * 1024,  # 100 MB
            "max_video_size": 10 * 1024 * 1024 * 1024,  # 10 GB
        })
        
        assert config.min_video_size == 100 * 1024 * 1024
        assert config.max_video_size == 10 * 1024 * 1024 * 1024
    
    def test_size_limits_from_plugin_config(self):
        """Test that size limits are properly loaded from plugin config."""
        # The config structure follows: plugins.plugin_settings.bittorrent
        plugin = BitTorrentPlugin(config={
            "plugins": {
                "plugin_settings": {
                    "bittorrent": {
                        "min_video_size": 50 * 1024 * 1024,  # 50 MB
                        "max_video_size": 20 * 1024 * 1024 * 1024,  # 20 GB
                    }
                }
            }
        })
        
        assert plugin._bt_config.min_video_size == 50 * 1024 * 1024
        assert plugin._bt_config.max_video_size == 20 * 1024 * 1024 * 1024

    def test_flat_plugin_settings(self):
        """Settings passed directly (scheduler/configure path) are applied."""
        plugin = BitTorrentPlugin(config={
            "min_video_size": 50 * 1024 * 1024,
            "select_mode": "largest",
        })

        assert plugin._bt_config.min_video_size == 50 * 1024 * 1024
        assert plugin._bt_config.select_mode == "largest"
    
    def test_size_limit_validation_reasonable_values(self):
        """Test that reasonable size limit values are accepted."""
        # Small limits
        config1 = BitTorrentConfig.from_dict({
            "min_video_size": 1024,  # 1 KB
            "max_video_size": 1024 * 1024,  # 1 MB
        })
        assert config1.min_video_size == 1024
        assert config1.max_video_size == 1024 * 1024
        
        # Large limits
        config2 = BitTorrentConfig.from_dict({
            "min_video_size": 1024 * 1024 * 1024,  # 1 GB
            "max_video_size": 100 * 1024 * 1024 * 1024,  # 100 GB
        })
        assert config2.min_video_size == 1024 * 1024 * 1024
        assert config2.max_video_size == 100 * 1024 * 1024 * 1024
