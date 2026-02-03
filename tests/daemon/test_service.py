"""Tests for daemon service."""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock, MagicMock, mock_open

from haven_cli.daemon.service import HavenDaemon, run_daemon, daemonize
from haven_cli.config import HavenConfig


class TestHavenDaemon:
    """Tests for HavenDaemon class."""
    
    @pytest.fixture
    def mock_config(self):
        """Create a mock configuration."""
        config = Mock(spec=HavenConfig)
        config.__dict__ = {
            "data_dir": Path("/tmp/haven"),
            "config_dir": Path("/tmp/haven/config"),
        }
        return config
    
    @pytest.mark.asyncio
    async def test_daemon_initialization(self, mock_config):
        """Test daemon initialization."""
        daemon = HavenDaemon(mock_config, max_concurrent=8)
        
        assert daemon._config == mock_config
        assert daemon._max_concurrent == 8
        assert daemon._pipeline_manager is None
        assert daemon._scheduler is None
        assert daemon._running is False
    
    @pytest.mark.asyncio
    async def test_daemon_start(self, mock_config):
        """Test daemon start."""
        daemon = HavenDaemon(mock_config, max_concurrent=4)
        
        # Mock JSBridgeManager - need to patch where it's imported
        mock_bridge_manager = AsyncMock()
        
        # Mock pipeline manager
        mock_pipeline_manager = Mock()
        mock_pipeline_manager.steps = [Mock(), Mock(), Mock()]
        
        # Mock scheduler
        mock_scheduler = Mock()
        mock_scheduler.start = AsyncMock()
        
        with patch("haven_cli.js_runtime.manager.JSBridgeManager.get_instance", return_value=mock_bridge_manager):
            with patch("haven_cli.daemon.service.create_default_pipeline", return_value=mock_pipeline_manager):
                with patch("haven_cli.daemon.service.JobScheduler", return_value=mock_scheduler):
                    await daemon.start()
        
        assert daemon._running is True
        assert daemon._pipeline_manager == mock_pipeline_manager
        assert daemon._scheduler == mock_scheduler
        mock_scheduler.start.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_daemon_stop(self, mock_config):
        """Test daemon stop."""
        daemon = HavenDaemon(mock_config)
        
        # Set up mocks
        daemon._running = True
        daemon._scheduler = Mock()
        daemon._scheduler.stop = AsyncMock()
        
        mock_bridge_manager = AsyncMock()
        
        with patch("haven_cli.js_runtime.manager.JSBridgeManager.get_instance", return_value=mock_bridge_manager):
            await daemon.stop()
        
        assert daemon._running is False
        daemon._scheduler.stop.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_daemon_stop_no_scheduler(self, mock_config):
        """Test daemon stop when scheduler is None."""
        daemon = HavenDaemon(mock_config)
        daemon._running = True
        daemon._scheduler = None
        
        mock_bridge_manager = AsyncMock()
        
        with patch("haven_cli.js_runtime.manager.JSBridgeManager.get_instance", return_value=mock_bridge_manager):
            # Should not raise
            await daemon.stop()
        
        assert daemon._running is False
    
    @pytest.mark.asyncio
    async def test_request_shutdown(self, mock_config):
        """Test request_shutdown sets event."""
        daemon = HavenDaemon(mock_config)
        
        # Set up the shutdown event
        daemon._shutdown_event = asyncio.Event()
        assert not daemon._shutdown_event.is_set()
        
        daemon.request_shutdown()
        
        assert daemon._shutdown_event.is_set()
    
    @pytest.mark.asyncio
    async def test_run_until_shutdown(self, mock_config):
        """Test run_until_shutdown waits for event."""
        daemon = HavenDaemon(mock_config)
        
        # Set up the shutdown event
        daemon._shutdown_event = asyncio.Event()
        
        # Request shutdown after a short delay
        async def delayed_shutdown():
            await asyncio.sleep(0.01)
            daemon.request_shutdown()
        
        # Run both tasks
        await asyncio.gather(
            daemon.run_until_shutdown(),
            delayed_shutdown()
        )
        
        # If we get here, run_until_shutdown returned correctly
        assert True
    
    @pytest.mark.asyncio
    async def test_is_running_property(self, mock_config):
        """Test is_running property."""
        daemon = HavenDaemon(mock_config)
        
        assert daemon.is_running is False
        
        daemon._running = True
        assert daemon.is_running is True
    
    @pytest.mark.asyncio
    async def test_pipeline_manager_property(self, mock_config):
        """Test pipeline_manager property."""
        daemon = HavenDaemon(mock_config)
        
        assert daemon.pipeline_manager is None
        
        mock_manager = Mock()
        daemon._pipeline_manager = mock_manager
        assert daemon.pipeline_manager == mock_manager
    
    @pytest.mark.asyncio
    async def test_scheduler_property(self, mock_config):
        """Test scheduler property."""
        daemon = HavenDaemon(mock_config)
        
        assert daemon.scheduler is None
        
        mock_scheduler = Mock()
        daemon._scheduler = mock_scheduler
        assert daemon.scheduler == mock_scheduler


class TestRunDaemon:
    """Tests for run_daemon function."""
    
    @pytest.mark.asyncio
    async def test_run_daemon_starts_and_stops(self):
        """Test run_daemon starts daemon and handles shutdown."""
        mock_config = Mock(spec=HavenConfig)
        mock_config.__dict__ = {
            "data_dir": Path("/tmp/haven"),
            "config_dir": Path("/tmp/haven/config"),
        }
        
        options = {"max_concurrent": 4, "verbose": False}
        
        # Mock HavenDaemon
        mock_daemon = AsyncMock()
        
        with patch("haven_cli.daemon.service.HavenDaemon", return_value=mock_daemon):
            # Cancel the daemon after a short delay
            async def cancel_daemon():
                await asyncio.sleep(0.01)
                mock_daemon.request_shutdown()
            
            await asyncio.gather(
                run_daemon(mock_config, options),
                cancel_daemon()
            )
        
        mock_daemon.start.assert_called_once()
        mock_daemon.stop.assert_called_once()
        mock_daemon.run_until_shutdown.assert_called_once()


class TestDaemonize:
    """Tests for daemonize function."""
    
    @patch("sys.platform", "win32")
    def test_daemonize_on_windows(self, caplog):
        """Test daemonize does nothing on Windows."""
        import logging
        
        with caplog.at_level(logging.WARNING):
            daemonize()
        
        assert "not supported on Windows" in caplog.text
    
    @patch("sys.platform", "linux")
    @patch("os.fork")
    @patch("os.setsid")
    @patch("os.dup2")
    @patch("sys.stdout")
    @patch("sys.stderr")
    @patch("sys.stdin")
    def test_daemonize_on_linux(self, mock_stdin, mock_stderr, mock_stdout, 
                                 mock_dup2, mock_setsid, mock_fork):
        """Test daemonize forks on Linux."""
        # First fork returns 0 (child), second fork returns 0 (grandchild)
        mock_fork.side_effect = [0, 0]
        
        # Should not raise (we're in the grandchild process after forks)
        with patch("builtins.open", mock_open()):
            daemonize()
        
        # First fork should be called
        assert mock_fork.call_count == 2
        mock_setsid.assert_called_once()
