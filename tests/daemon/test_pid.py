"""Tests for PID file management."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch

from haven_cli.daemon.pid import PIDFile


class TestPIDFile:
    """Tests for PIDFile class."""
    
    def test_create_pid_file(self, tmp_path):
        """Test creating a PID file."""
        pid_file = PIDFile(tmp_path / "test.pid")
        
        # Create should write current PID
        pid_file.create()
        
        assert pid_file.path.exists()
        assert pid_file.read() == os.getpid()
    
    def test_create_creates_parent_directories(self, tmp_path):
        """Test that create makes parent directories."""
        pid_file = PIDFile(tmp_path / "subdir" / "test.pid")
        
        pid_file.create()
        
        assert pid_file.path.exists()
        assert pid_file.path.parent.exists()
    
    def test_read_existing_pid_file(self, tmp_path):
        """Test reading a PID from file."""
        pid_file = PIDFile(tmp_path / "test.pid")
        pid_file.create()
        
        pid = pid_file.read()
        
        assert pid == os.getpid()
    
    def test_read_nonexistent_file(self, tmp_path):
        """Test reading from non-existent file returns None."""
        pid_file = PIDFile(tmp_path / "nonexistent.pid")
        
        assert pid_file.read() is None
    
    def test_read_invalid_content(self, tmp_path):
        """Test reading invalid content returns None."""
        pid_file = PIDFile(tmp_path / "test.pid")
        pid_file.path.write_text("invalid")
        
        assert pid_file.read() is None
    
    def test_remove_pid_file(self, tmp_path):
        """Test removing PID file."""
        pid_file = PIDFile(tmp_path / "test.pid")
        pid_file.create()
        
        assert pid_file.path.exists()
        
        pid_file.remove()
        
        assert not pid_file.path.exists()
    
    def test_remove_nonexistent_file(self, tmp_path):
        """Test removing non-existent file doesn't raise."""
        pid_file = PIDFile(tmp_path / "nonexistent.pid")
        
        # Should not raise
        pid_file.remove()
    
    def test_is_running_with_current_process(self, tmp_path):
        """Test is_running with current process."""
        pid_file = PIDFile(tmp_path / "test.pid")
        pid_file.create()
        
        assert pid_file.is_running() is True
    
    def test_is_running_with_nonexistent_file(self, tmp_path):
        """Test is_running with no PID file."""
        pid_file = PIDFile(tmp_path / "nonexistent.pid")
        
        assert pid_file.is_running() is False
    
    def test_is_running_with_stale_pid(self, tmp_path):
        """Test is_running with stale PID."""
        pid_file = PIDFile(tmp_path / "test.pid")
        # Write a PID that doesn't exist (very high number)
        pid_file.path.write_text("999999")
        
        assert pid_file.is_running() is False
    
    def test_get_pid_when_running(self, tmp_path):
        """Test get_pid when process is running."""
        pid_file = PIDFile(tmp_path / "test.pid")
        pid_file.create()
        
        assert pid_file.get_pid() == os.getpid()
    
    def test_get_pid_when_not_running(self, tmp_path):
        """Test get_pid when process is not running."""
        pid_file = PIDFile(tmp_path / "test.pid")
        pid_file.path.write_text("999999")
        
        assert pid_file.get_pid() is None
    
    def test_clear_if_stale_with_running_process(self, tmp_path):
        """Test clear_if_stale doesn't remove file for running process."""
        pid_file = PIDFile(tmp_path / "test.pid")
        pid_file.create()
        
        assert pid_file.clear_if_stale() is False
        assert pid_file.path.exists()
    
    def test_clear_if_stale_with_stale_pid(self, tmp_path):
        """Test clear_if_stale removes file for stale PID."""
        pid_file = PIDFile(tmp_path / "test.pid")
        pid_file.path.write_text("999999")
        
        assert pid_file.clear_if_stale() is True
        assert not pid_file.path.exists()
    
    def test_clear_if_stale_with_no_file(self, tmp_path):
        """Test clear_if_stale with no file."""
        pid_file = PIDFile(tmp_path / "nonexistent.pid")
        
        assert pid_file.clear_if_stale() is False
