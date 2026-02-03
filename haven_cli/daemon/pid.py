"""PID file management for daemon process tracking."""

import os
from pathlib import Path
from typing import Optional


class PIDFile:
    """Manage daemon PID file.
    
    This class handles the creation, reading, and removal of a PID file
    used to track whether the daemon process is running.
    
    Example:
        pid_file = PIDFile(Path("/var/run/haven.pid"))
        
        # Check if daemon is running
        if pid_file.is_running():
            print("Daemon already running")
        else:
            # Create PID file
            pid_file.create()
            try:
                # Run daemon...
                pass
            finally:
                pid_file.remove()
    """
    
    def __init__(self, path: Path):
        """Initialize PID file manager.
        
        Args:
            path: Path to the PID file
        """
        self.path = path
    
    def create(self) -> None:
        """Create PID file with current process ID.
        
        Creates the parent directory if it doesn't exist.
        """
        # Ensure parent directory exists
        self.path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write current PID
        self.path.write_text(str(os.getpid()))
    
    def remove(self) -> None:
        """Remove PID file.
        
        Silently ignores if the file doesn't exist.
        """
        if self.path.exists():
            self.path.unlink()
    
    def read(self) -> Optional[int]:
        """Read PID from file.
        
        Returns:
            The PID as an integer, or None if the file doesn't exist
            or contains invalid data.
        """
        if not self.path.exists():
            return None
        
        try:
            content = self.path.read_text().strip()
            return int(content)
        except (ValueError, OSError):
            return None
    
    def is_running(self) -> bool:
        """Check if daemon process is running.
        
        This checks if the PID in the file corresponds to a running
        process. It handles stale PID files (where the process has
        died but the file remains).
        
        Returns:
            True if a daemon process appears to be running
        """
        pid = self.read()
        if pid is None:
            return False
        
        try:
            # Signal 0 is used to check if process exists
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            # Process doesn't exist - stale PID file
            return False
    
    def get_pid(self) -> Optional[int]:
        """Get the PID from the file if the process is running.
        
        Returns:
            The running daemon's PID, or None if not running
        """
        if self.is_running():
            return self.read()
        return None
    
    def clear_if_stale(self) -> bool:
        """Remove PID file if it's stale (process not running).
        
        Returns:
            True if the file was stale and has been removed
        """
        pid = self.read()
        if pid is None:
            return False
        
        try:
            os.kill(pid, 0)
            return False  # Process is running
        except (OSError, ProcessLookupError):
            # Process not running - remove stale file
            self.remove()
            return True
