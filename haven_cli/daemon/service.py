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
from haven_cli.pipeline.manager import PipelineManager, create_default_pipeline, create_batched_pipeline
from haven_cli.pipeline.batch_accumulator import BatchAccumulator
from haven_cli.pipeline.flush_queue import FlushQueue
from haven_cli.pipeline.events import get_event_bus, make_sqlite_event_sink
from haven_cli.scheduler.job_scheduler import JobScheduler
from haven_cli.services.speed_history import SpeedHistoryService


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
        daemon = HavenDaemon(config, max_concurrent=1)
        
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
        max_concurrent: int = 1,
    ):
        """Initialize the daemon service.
        
        Args:
            config: Haven configuration
            max_concurrent: Maximum concurrent pipeline executions
        """
        self._config = config
        self._max_concurrent = max_concurrent
        self._pipeline_manager: Optional[PipelineManager] = None
        self._accumulator: Optional[BatchAccumulator] = None
        self._flush_queue: Optional[FlushQueue] = None
        self._flush_loop_task: Optional[asyncio.Task] = None
        self._scheduler: Optional[JobScheduler] = None
        # SpeedHistoryService subscribes to *_PROGRESS events and persists
        # samples into the ``speed_history`` table. Without it, the TUI's
        # SpeedGraph shows "[No speed data available]" because the rows it
        # reads from never get written. The service holds one DB session for
        # its lifetime (it's a writer; WAL mode keeps it from blocking the
        # TUI's per-call readers).
        self._speed_history_service: Optional[SpeedHistoryService] = None
        self._speed_history_session: Optional[Any] = None
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
        
        # Initialize JS bridge with debug mode if configured
        from haven_cli.js_runtime.manager import JSBridgeManager
        from haven_cli.config import get_config
        
        config = get_config()
        debug_mode = getattr(config, 'debug', False) or os.environ.get('DEBUG') == '1' or os.environ.get('LOG_LEVEL', '').lower() == 'debug'
        filecoin_mode = config.blockchain.effective_filecoin_network_mode
        
        # Configure JS bridge with Filecoin network for correct Synapse RPC endpoint
        JSBridgeManager.get_instance().configure(
            debug=debug_mode,
            network_mode=filecoin_mode,
        )
        
        if debug_mode:
            logger.info("Enabling JS bridge debug mode")
        
        await JSBridgeManager.get_instance().get_bridge()
        logger.info("JS Bridge initialized")

        # Attach the SQLite event sink so progress events emitted on this
        # process's bus are durably written to ``pipeline_events`` for
        # cross-process consumers (notably ``haven tui``). See
        # docs/TUI_IMPROVEMENTS_PROPOSAL.md R2 / B1 / B2.
        try:
            # Make sure the table exists on first run. ``create_all`` is
            # idempotent — it only creates missing tables.
            from haven_cli.database.connection import init_engine
            from haven_cli.database.models import Base

            engine = init_engine(self._config)
            Base.metadata.create_all(bind=engine)

            get_event_bus().attach_persistent_sink(make_sqlite_event_sink())
            logger.info("Event bus persistent sink (pipeline_events) attached")
        except Exception as e:  # pragma: no cover - defensive
            # Persistence failure must not prevent the daemon from running.
            # Without the sink the TUI falls back to its polling path,
            # which still works (just at lower fidelity).
            logger.warning(
                "Could not attach pipeline_events sink: %s; cross-process "
                "TUI updates will rely on polling.",
                e,
            )

        # Start the SpeedHistoryService only when the user opts in. It
        # subscribes to *_PROGRESS events and writes ``speed_history`` rows
        # that the TUI's SpeedGraph reads. Without it the graph reads an
        # empty table and shows "[No speed data available]" for every
        # video. Degrades gracefully: a failure here only loses the graph
        # data, not the pipeline.
        if self._config.pipeline.speed_history_enabled:
            try:
                from haven_cli.database.connection import get_session_maker

                SessionMaker = get_session_maker()
                self._speed_history_session = SessionMaker()
                self._speed_history_service = SpeedHistoryService(
                    db_session=self._speed_history_session,
                    event_bus=get_event_bus(),
                )
                await self._speed_history_service.start()
                logger.info("SpeedHistoryService started (speed_history table)")
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(
                    "Could not start SpeedHistoryService: %s; TUI speed graph "
                    "will show '[No speed data available]'.",
                    e,
                )
                # Clean up partial init so stop() doesn't try to use a
                # half-built service.
                self._speed_history_service = None
                if self._speed_history_session is not None:
                    try:
                        self._speed_history_session.close()
                    except Exception:
                        pass
                    self._speed_history_session = None
        else:
            logger.info(
                "SpeedHistoryService disabled (set pipeline.speed_history_enabled=true "
                "or HAVEN_SPEED_HISTORY_ENABLED=true to enable)"
            )

        # Initialize pipeline manager — batched or default based on config

        batch_sync_enabled = getattr(config.pipeline, "batch_sync_enabled", False)
        sync_enabled = getattr(config.pipeline, "sync_enabled", False)
        
        if batch_sync_enabled and sync_enabled:
            # Phase 4: read all three batch-sync knobs from config so
            # HAVEN_BATCH_SYNC_FLUSH_TIMEOUT / HAVEN_BATCH_SYNC_MAX_PENDING
            # actually take effect. The previous implementation only read
            # batch_size and silently fell through to BatchAccumulator's
            # module defaults for the other two.
            batch_size = getattr(config.pipeline, "batch_sync_size", 10)
            flush_timeout = getattr(config.pipeline, "batch_sync_flush_timeout", 18000.0)
            max_pending = getattr(config.pipeline, "batch_sync_max_pending", 50)
            self._pipeline_manager, self._accumulator, self._flush_queue = create_batched_pipeline(
                max_concurrent=self._max_concurrent,
                batch_size=batch_size,
                flush_timeout=flush_timeout,
                max_pending=max_pending,
                config=self._config.__dict__,
            )
            await self._flush_queue.start()
            logger.info(
                "Batched pipeline initialized (batch_size=%d, flush_timeout=%.1fs, "
                "max_pending=%d, steps=%d)",
                batch_size,
                flush_timeout,
                max_pending,
                len(self._pipeline_manager.steps),
            )
        else:
            self._pipeline_manager = create_default_pipeline(
                max_concurrent=self._max_concurrent,
                config=self._config.__dict__,
            )
            logger.info(f"Pipeline manager initialized with {len(self._pipeline_manager.steps)} steps")
        
        # Initialize scheduler (pass accumulator for batched sync mode)
        self._scheduler = JobScheduler(
            pipeline_manager=self._pipeline_manager,
            config=self._config.__dict__,
            accumulator=self._accumulator,
        )
        
        # Start scheduler
        await self._scheduler.start()
        logger.info("Job scheduler started")
        
        # Start background flush loop (batched mode)
        if self._accumulator is not None and self._flush_queue is not None:
            self._flush_loop_task = asyncio.create_task(self._flush_loop())
            logger.info("Batch flush loop started")
        
        self._running = True
        logger.info("Haven daemon started successfully")
    
    async def _flush_loop(self) -> None:
        """Background loop: poll the accumulator and enqueue ready batches.

        This runs continuously while the daemon is alive. It calls
        accumulator.flush() which blocks until either:
        - batch_size items are buffered (immediate flush), or
        - flush_timeout elapses with a non-empty buffer (partial flush).

        Each flushed batch is enqueued to the FlushQueue for processing
        by BatchSyncProcessor (attestation + entity creation).
        """
        assert self._accumulator is not None
        assert self._flush_queue is not None

        logger.debug("Flush loop running")
        while self._running:
            try:
                batch = await self._accumulator.flush()
                if batch:
                    await self._flush_queue.enqueue(batch)
                    logger.info(
                        "Flushed batch of %d contexts to sync queue",
                        len(batch),
                    )
            except Exception as exc:
                logger.error("Flush loop error: %s", exc, exc_info=True)
                # Brief sleep to avoid tight-loop on persistent errors
                await asyncio.sleep(1.0)

        logger.debug("Flush loop exiting")

    async def stop(self) -> None:
        """Stop the daemon services.
        
        This gracefully shuts down all components in reverse order
        of their initialization.
        """
        logger.info("Stopping Haven daemon...")
        
        self._running = False
        
        # Cancel flush loop
        if self._flush_loop_task is not None and not self._flush_loop_task.done():
            self._flush_loop_task.cancel()
            try:
                await self._flush_loop_task
            except asyncio.CancelledError:
                pass
            logger.info("Flush loop stopped")
        
        # Stop scheduler
        if self._scheduler:
            try:
                await self._scheduler.stop()
                logger.info("Job scheduler stopped")
            except Exception as e:
                logger.warning(f"Error stopping scheduler: {e}")

        # Stop speed-history service (flushes any buffered samples).
        if self._speed_history_service is not None:
            try:
                await self._speed_history_service.stop()
                logger.info("SpeedHistoryService stopped")
            except Exception as e:
                logger.warning(f"Error stopping SpeedHistoryService: {e}")
            finally:
                # Close the long-lived session even if stop() raised so we
                # don't leak a connection on shutdown.
                if self._speed_history_session is not None:
                    try:
                        self._speed_history_session.close()
                    except Exception:
                        pass
                    self._speed_history_session = None
                self._speed_history_service = None
        
        # Drain accumulator and stop flush queue (batched mode)
        if self._accumulator is not None and self._flush_queue is not None:
            try:
                remaining = await self._accumulator.drain()
                if remaining:
                    logger.info("Draining %d pending contexts to flush queue", len(remaining))
                    await self._flush_queue.enqueue(remaining)
                await self._flush_queue.stop()
                logger.info("Flush queue stopped")
            except Exception as e:
                logger.warning(f"Error stopping flush queue: {e}")
        
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
            "max_concurrent": 1,
            "verbose": True,
        })
    """
    daemon = HavenDaemon(
        config,
        max_concurrent=options.get("max_concurrent", 1),
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
