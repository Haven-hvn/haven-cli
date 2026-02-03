"""Tests for run CLI command."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock, MagicMock

from typer.testing import CliRunner

from haven_cli.cli.run import app, _setup_logging


runner = CliRunner()


class TestSetupLogging:
    """Tests for _setup_logging function."""
    
    def test_setup_logging_verbose(self, tmp_path):
        """Test logging setup with verbose mode."""
        import logging
        
        log_file = tmp_path / "test.log"
        
        _setup_logging(verbose=True, log_file=log_file)
        
        # Check that logging is configured (root logger has handlers)
        root_logger = logging.getLogger()
        assert len(root_logger.handlers) > 0
    
    def test_setup_logging_not_verbose(self, tmp_path):
        """Test logging setup without verbose mode."""
        import logging
        
        log_file = tmp_path / "test.log"
        
        _setup_logging(verbose=False, log_file=log_file)
        
        # Check that logging is configured (root logger has handlers)
        root_logger = logging.getLogger()
        assert len(root_logger.handlers) > 0
    
    def test_setup_logging_creates_log_directory(self, tmp_path):
        """Test that log setup creates directory for log file."""
        log_file = tmp_path / "subdir" / "test.log"
        
        _setup_logging(verbose=False, log_file=log_file)
        
        assert log_file.parent.exists()


class TestRunCommand:
    """Tests for the run command."""
    
    def test_run_help(self):
        """Test run command help output."""
        result = runner.invoke(app, ["--help"])
        
        assert result.exit_code == 0
        assert "Haven daemon" in result.output
        assert "--daemon" in result.output
        assert "--verbose" in result.output
        assert "--max-concurrent" in result.output
    
    def test_run_has_subcommands(self):
        """Test that run command has subcommands."""
        result = runner.invoke(app, ["--help"])
        
        assert result.exit_code == 0
        # Check for subcommands in help output
        assert "Commands" in result.output or "status" in result.output


class TestPIDFileIntegration:
    """Integration tests for PID file functionality."""
    
    def test_pid_file_create_and_remove(self, tmp_path):
        """Test creating and removing a PID file."""
        from haven_cli.daemon.pid import PIDFile
        
        pid_file = PIDFile(tmp_path / "test.pid")
        
        # Should not be running initially
        assert pid_file.is_running() is False
        
        # Create PID file
        pid_file.create()
        assert pid_file.path.exists()
        
        # Should detect current process as running
        assert pid_file.is_running() is True
        
        # Remove PID file
        pid_file.remove()
        assert not pid_file.path.exists()
    
    def test_pid_file_stale_detection(self, tmp_path):
        """Test detection of stale PID files."""
        from haven_cli.daemon.pid import PIDFile
        
        pid_file = PIDFile(tmp_path / "test.pid")
        
        # Write a stale PID (non-existent process)
        pid_file.path.write_text("999999")
        
        # Should detect as not running
        assert pid_file.is_running() is False
        
        # Clear stale file
        assert pid_file.clear_if_stale() is True
        assert not pid_file.path.exists()


class TestDaemonService:
    """Tests for daemon service functionality."""
    
    @pytest.mark.asyncio
    async def test_haven_daemon_lifecycle(self):
        """Test daemon lifecycle."""
        from haven_cli.daemon.service import HavenDaemon
        from haven_cli.config import HavenConfig
        
        # Create a mock config
        mock_config = Mock(spec=HavenConfig)
        mock_config.__dict__ = {
            "data_dir": Path("/tmp/haven"),
            "config_dir": Path("/tmp/haven/config"),
        }
        
        daemon = HavenDaemon(mock_config, max_concurrent=4)
        
        # Initial state
        assert daemon.is_running is False
        assert daemon.pipeline_manager is None
        assert daemon.scheduler is None
        
        # Test shutdown event
        daemon.request_shutdown()
        assert daemon._shutdown_event.is_set()
    
    @pytest.mark.asyncio
    async def test_haven_daemon_request_shutdown(self):
        """Test daemon request shutdown functionality."""
        from haven_cli.daemon.service import HavenDaemon
        import asyncio
        
        mock_config = Mock()
        mock_config.__dict__ = {
            "data_dir": Path("/tmp/haven"),
            "config_dir": Path("/tmp/haven/config"),
        }
        
        daemon = HavenDaemon(mock_config)
        daemon._shutdown_event = asyncio.Event()
        
        # Event should not be set initially
        assert not daemon._shutdown_event.is_set()
        
        # Request shutdown
        daemon.request_shutdown()
        
        # Event should be set now
        assert daemon._shutdown_event.is_set()
