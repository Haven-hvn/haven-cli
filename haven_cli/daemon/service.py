"""Main daemon service for Haven CLI.

This module provides the core daemon functionality including:
- Service lifecycle management (start/stop)
- Signal handling for graceful shutdown
- Background daemon mode with process forking
- Integration with PipelineManager and JobScheduler
"""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from haven_cli.config import HavenConfig
from haven_cli.pipeline.manager import PipelineManager, create_default_pipeline
from haven_cli.scheduler.job_scheduler import JobScheduler

logger = logging.getLogger(__name__)


class HavenDaemon:
    """Main daemon service for Haven CLI.
    
    The HavenDaemon orchestrates the pipeline processing and job scheduling
    components. It manages their lifecycle and provides graceful shutdown
    capabilities.
    
    Attributes:
        _config: Haven configuration
        _max_concurrent: Maximum concurrent pipeline executions
        _pipeline_manager: Pipeline manager instance
        _scheduler: Job scheduler instance
        _running: Whether the daemon is running
        _shutdown_event: Event to signal shutdown
    
    Example:
        daemon = HavenDaemon(config, max_concurrent=4)
        
        # Start daemon
        await daemon.start()
        
        # Run until shutdown signal
        await daemon.run_until_shutdown()
        
        # Stop daemon
        await daemon.stop()
    """
    
    def __init__(
        self,
        config: HavenConfig,
        max_concurrent: int = 4,
    ):
        """Initialize the daemon service.
        
        Args:
            config: Haven configuration
            max_concurrent: Maximum concurrent pipeline executions
        """
        self._config = config
        self._max_concurrent = max_concurrent
        self._pipeline_manager: Optional[PipelineManager] = None
        self._scheduler: Optional[JobScheduler] = None
        self._running = False
        self._shutdown_event = asyncio.Event()
    
    async def start(self) -> None:
        """Start the daemon services.
        
        This initializes and starts all daemon components:
        1. JS Bridge for blockchain operations
        2. Pipeline manager with default steps
        3. Job scheduler for recurring tasks
        
        Raises:
            RuntimeError: If a component fails to start
        """
        logger.info("Starting Haven daemon...")
        
        # Initialize JS bridge
        from haven_cli.js_runtime.manager import JSBridgeManager
        await JSBridgeManager.get_instance().get_bridge()
        logger.info("JS Bridge initialized")
        
        # Initialize pipeline manager with default steps
        self._pipeline_manager = create_default_pipeline(
            max_concurrent=self._max_concurrent,
            config=self._config.__dict__,
        )
        logger.info(f"Pipeline manager initialized with {len(self._pipeline_manager.steps)} steps")
        
        # Initialize scheduler
        self._scheduler = JobScheduler(
            pipeline_manager=self._pipeline_manager,
            config=self._config.__dict__,
        )
        
        # Start scheduler
        await self._scheduler.start()
        logger.info("Job scheduler started")
        
        self._running = True
        logger.info("Haven daemon started successfully")
    
    async def stop(self) -> None:
        """Stop the daemon services.
        
        This gracefully shuts down all components in reverse order
        of their initialization.
        """
        logger.info("Stopping Haven daemon...")
        
        self._running = False
        
        # Stop scheduler
        if self._scheduler:
            try:
                await self._scheduler.stop()
                logger.info("Job scheduler stopped")
            except Exception as e:
                logger.warning(f"Error stopping scheduler: {e}")
        
        # Shutdown JS bridge
        from haven_cli.js_runtime.manager import JSBridgeManager
        try:
            await JSBridgeManager.get_instance().shutdown()
            logger.info("JS Bridge shutdown")
        except Exception as e:
            logger.warning(f"Error shutting down JS Bridge: {e}")
        
        logger.info("Haven daemon stopped")
    
    async def run_until_shutdown(self) -> None:
        """Run daemon until shutdown signal received.
        
        This method blocks until request_shutdown() is called,
        typically via a signal handler.
        """
        await self._shutdown_event.wait()
    
    def request_shutdown(self) -> None:
        """Request daemon shutdown.
        
        This sets the shutdown event, which will cause run_until_shutdown()
        to return and allow the daemon to stop gracefully.
        """
        logger.info("Shutdown requested")
        self._shutdown_event.set()
    
    @property
    def is_running(self) -> bool:
        """Check if the daemon is running.
        
        Returns:
            True if the daemon is running
        """
        return self._running
    
    @property
    def pipeline_manager(self) -> Optional[PipelineManager]:
        """Get the pipeline manager instance.
        
        Returns:
            The pipeline manager, or None if not started
        """
        return self._pipeline_manager
    
    @property
    def scheduler(self) -> Optional[JobScheduler]:
        """Get the job scheduler instance.
        
        Returns:
            The job scheduler, or None if not started
        """
        return self._scheduler


async def run_daemon(config: HavenConfig, options: Dict[str, Any]) -> None:
    """Run the Haven daemon with signal handling.
    
    This function sets up signal handlers for graceful shutdown and
    runs the daemon until a shutdown signal is received.
    
    Args:
        config: Haven configuration
        options: Daemon options including:
            - max_concurrent: Maximum concurrent pipelines
            - verbose: Enable verbose logging
    
    Example:
        await run_daemon(config, {
            "max_concurrent": 4,
            "verbose": True,
        })
    """
    daemon = HavenDaemon(
        config,
        max_concurrent=options.get("max_concurrent", 4),
    )
    
    # Set up signal handlers
    loop = asyncio.get_event_loop()
    
    def handle_signal(sig: signal.Signals) -> None:
        """Handle shutdown signals.
        
        Args:
            sig: The signal that was received
        """
        logger.info(f"Received signal {sig.name}, initiating shutdown...")
        daemon.request_shutdown()
    
    # Register signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            signal.signal(sig, lambda signum, frame: handle_signal(signal.Signals(signum)))
    
    try:
        await daemon.start()
        await daemon.run_until_shutdown()
    finally:
        await daemon.stop()


def daemonize(log_file: Optional[Path] = None) -> None:
    """Fork process to run as daemon.
    
    This function converts the current process into a background daemon
    by forking twice (standard Unix daemon technique) and redirecting
    standard file descriptors.
    
    Args:
        log_file: Path to log file for stdout/stderr redirection.
                 If None, output is redirected to /dev/null.
    
    Note:
        This function only works on Unix-like systems. On Windows,
        it returns without doing anything.
    """
    # Skip on Windows
    if sys.platform == "win32":
        logger.warning("Daemon mode not supported on Windows")
        return
    
    # First fork
    pid = os.fork()
    if pid > 0:
        # Parent exits
        sys.exit(0)
    
    # Create new session
    os.setsid()
    
    # Second fork
    pid = os.fork()
    if pid > 0:
        # Parent exits
        sys.exit(0)
    
    # Now running as daemon
    # Redirect standard file descriptors
    sys.stdout.flush()
    sys.stderr.flush()
    
    # Redirect stdin to /dev/null
    with open('/dev/null', 'r') as devnull:
        os.dup2(devnull.fileno(), sys.stdin.fileno())
    
    # Redirect stdout/stderr to log file or /dev/null
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, 'a+') as f:
            os.dup2(f.fileno(), sys.stdout.fileno())
            os.dup2(f.fileno(), sys.stderr.fileno())
    else:
        with open('/dev/null', 'a+') as devnull:
            os.dup2(devnull.fileno(), sys.stdout.fileno())
            os.dup2(devnull.fileno(), sys.stderr.fileno())
    
    logger.info(f"Daemon process started (PID: {os.getpid()})")
