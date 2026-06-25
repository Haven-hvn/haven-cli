"""Speed history service for TUI graph visualization.

This service samples and stores speed metrics from pipeline events
for graph visualization in the TUI.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Callable

from sqlalchemy.orm import Session

from haven_cli.database.models import SpeedHistory
from haven_cli.database.repositories import SpeedHistoryRepository
from haven_cli.pipeline.events import EventType, Event, EventBus

logger = logging.getLogger(__name__)


class SpeedHistoryService:
    """Samples and stores speed history for TUI graphs.
    
    This service subscribes to pipeline progress events and records
    speed metrics for visualization. It buffers samples in memory
    and periodically flushes to the database for efficiency.
    
    Example:
        service = SpeedHistoryService(db_session, event_bus)
        await service.start()
        
        # Later...
        history = service.get_speed_history(video_id=1, stage="download")
        await service.stop()
    """
    
    # Keep 5 minutes of samples at 1-second intervals = 300 samples per active transfer
    MAX_SAMPLES = 300
    SAMPLE_INTERVAL = 1.0  # seconds
    FLUSH_INTERVAL = 10  # samples before flushing to DB
    CLEANUP_INTERVAL = 3600  # seconds between cleanup runs
    
    def __init__(
        self,
        db_session: Session,
        event_bus: Optional[EventBus] = None,
    ):
        """
        Initialize speed history service.
        
        Args:
            db_session: SQLAlchemy database session
            event_bus: Event bus for subscribing to progress events
        """
        self.db = db_session
        self.event_bus = event_bus
        self._repo = SpeedHistoryRepository(db_session)
        self._buffer: Dict[tuple[int, str], List[Dict[str, Any]]] = defaultdict(list)
        self._unsubscribe_handlers: List[Callable[[], None]] = []
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
    
    async def start(self) -> None:
        """Start the service and subscribe to events."""
        if self._running:
            return
        
        self._running = True
        
        # Subscribe to progress events
        if self.event_bus:
            self._subscribe_to_events()
        
        # Start cleanup task
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        
        logger.debug("SpeedHistoryService started")
    
    async def stop(self) -> None:
        """Stop the service and flush remaining buffers."""
        if not self._running:
            return
        
        self._running = False
        
        # Unsubscribe from events
        for unsubscribe in self._unsubscribe_handlers:
            unsubscribe()
        self._unsubscribe_handlers.clear()
        
        # Cancel cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        # Flush remaining buffers
        self._flush_all_buffers()
        
        logger.debug("SpeedHistoryService stopped")
    
    def _subscribe_to_events(self) -> None:
        """Subscribe to progress events from the event bus."""
        if not self.event_bus:
            return
        
        # Define sync wrappers for async handlers.
        #
        # Canonical payload keys (must match the production emitters):
        #   * DOWNLOAD_PROGRESS — see ``haven_tui/data/download_tracker.py``:
        #       download_rate, progress_percent, downloaded_bytes
        #   * ENCRYPT_PROGRESS  — see ``build_encrypt_progress_payload``:
        #       encrypt_speed, progress_percent, bytes_processed
        #   * UPLOAD_PROGRESS   — see ``haven_cli/pipeline/steps/upload_step.py``:
        #       progress_percent, bytes_uploaded
        #       (no explicit speed field; ``_record_sample`` will receive 0)
        def on_download_progress(event: Event) -> None:
            payload = event.payload
            self._record_sample(
                video_id=payload.get('video_id', 0),
                stage='download',
                speed=payload.get('download_rate', 0) or 0,
                progress=payload.get('progress_percent', 0) or 0,
                bytes_processed=payload.get('downloaded_bytes', 0)
                                 or payload.get('bytes_downloaded', 0),
            )

        def on_encrypt_progress(event: Event) -> None:
            payload = event.payload
            self._record_sample(
                video_id=payload.get('video_id', 0),
                stage='encrypt',
                speed=payload.get('encrypt_speed', 0) or 0,
                progress=payload.get('progress_percent', 0) or 0,
                bytes_processed=payload.get('bytes_processed', 0),
            )

        def on_upload_progress(event: Event) -> None:
            payload = event.payload
            self._record_sample(
                video_id=payload.get('video_id', 0),
                stage='upload',
                # upload_step does not emit a speed field; the speed-history
                # consumer relies on the byte-delta tracker in StateManager.
                speed=payload.get('upload_speed', 0) or 0,
                progress=payload.get('progress_percent', 0) or 0,
                bytes_processed=payload.get('bytes_uploaded', 0) or 0,
            )
        
        # Subscribe to progress events (store unsubscribe functions)
        # Task 12: Subscribe to DOWNLOAD_PROGRESS, ENCRYPT_PROGRESS, and UPLOAD_PROGRESS
        unsub_download = self.event_bus.subscribe(
            EventType.DOWNLOAD_PROGRESS, 
            lambda e: asyncio.create_task(self._async_handler(on_download_progress, e))
        )
        unsub_encrypt = self.event_bus.subscribe(
            EventType.ENCRYPT_PROGRESS,
            lambda e: asyncio.create_task(self._async_handler(on_encrypt_progress, e))
        )
        unsub_upload = self.event_bus.subscribe(
            EventType.UPLOAD_PROGRESS,
            lambda e: asyncio.create_task(self._async_handler(on_upload_progress, e))
        )
        
        # Note: We can't directly unsubscribe from EventBus with current API
        # The handlers will just stop processing when _running is False
    
    async def _async_handler(
        self, 
        sync_handler: Callable[[Event], None], 
        event: Event
    ) -> None:
        """Wrap sync handler for async event bus."""
        try:
            sync_handler(event)
        except Exception as e:
            logger.error(f"Error in speed history event handler: {e}")
    
    def _record_sample(
        self,
        video_id: int,
        stage: str,
        speed: int,
        progress: float = 0.0,
        bytes_processed: int = 0,
    ) -> None:
        """Buffer a sample and flush to DB periodically.
        
        Args:
            video_id: Video ID
            stage: Stage name ("download", "encrypt", "upload")
            speed: Speed in bytes/sec
            progress: Progress percentage (0-100)
            bytes_processed: Bytes processed so far
        """
        if not self._running:
            return
        
        key = (video_id, stage)
        
        sample = {
            'timestamp': datetime.now(timezone.utc),
            'speed': max(0, speed),  # Ensure non-negative
            'progress': max(0.0, min(100.0, progress)),  # Clamp to 0-100
            'bytes_processed': max(0, bytes_processed),
        }
        
        self._buffer[key].append(sample)
        
        # Keep only last N samples in memory
        if len(self._buffer[key]) > self.MAX_SAMPLES:
            self._buffer[key].pop(0)
        
        # Flush to DB every N samples
        if len(self._buffer[key]) % self.FLUSH_INTERVAL == 0:
            self._flush_buffer(key)
    
    def _flush_buffer(self, key: tuple[int, str]) -> None:
        """Write buffered samples to database.
        
        Args:
            key: Tuple of (video_id, stage)
        """
        video_id, stage = key
        samples = self._buffer[key]
        
        if not samples:
            return
        
        try:
            # Bulk create entries
            for sample in samples:
                history_entry = SpeedHistory(
                    video_id=video_id,
                    stage=stage,
                    timestamp=sample['timestamp'],
                    speed=sample['speed'],
                    progress=sample['progress'],
                    bytes_processed=sample['bytes_processed'],
                )
                self.db.add(history_entry)
            
            self.db.commit()
            
            # Clear buffer
            self._buffer[key] = []
            
        except Exception as e:
            logger.error(f"Error flushing speed history buffer: {e}")
            self.db.rollback()
    
    def _flush_all_buffers(self) -> None:
        """Flush all pending buffers to database."""
        for key in list(self._buffer.keys()):
            self._flush_buffer(key)
    
    async def _cleanup_loop(self) -> None:
        """Periodic cleanup of old samples."""
        while self._running:
            try:
                await asyncio.sleep(self.CLEANUP_INTERVAL)
                if self._running:
                    deleted = self._repo.cleanup_old_samples(hours=24)
                    if deleted > 0:
                        logger.debug(f"Cleaned up {deleted} old speed history samples")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in speed history cleanup: {e}")
    
    def record_sample(
        self,
        video_id: int,
        stage: str,
        speed: int,
        progress: float = 0.0,
        bytes_processed: int = 0,
    ) -> None:
        """
        Public method to manually record a speed sample.
        
        This can be used by pipeline steps to directly record samples
        without going through the event bus.
        
        Args:
            video_id: Video ID
            stage: Stage name ("download", "encrypt", "upload")
            speed: Speed in bytes/sec
            progress: Progress percentage (0-100)
            bytes_processed: Bytes processed so far
        """
        self._record_sample(video_id, stage, speed, progress, bytes_processed)
    
    def get_speed_history(
        self,
        video_id: int,
        stage: str,
        minutes: int = 5,
    ) -> List[SpeedHistory]:
        """
        Get speed history for graphing.
        
        Args:
            video_id: Video ID
            stage: Stage name
            minutes: Time window in minutes
            
        Returns:
            List of speed history entries
        """
        return self._repo.get_speed_history(video_id, stage, minutes)
    
    def get_aggregate_speeds(
        self,
        stage: Optional[str] = None,
        minutes: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Get aggregate download/upload speeds over time for header graph.
        
        Args:
            stage: Filter by stage (optional)
            minutes: Time window in minutes
            
        Returns:
            List of aggregated speed data points
        """
        return self._repo.get_aggregate_speeds(stage, minutes)
    
    def get_formatted_for_plotille(
        self,
        video_id: int,
        stage: str,
        minutes: int = 5,
        width: int = 60,
        height: int = 10,
    ) -> Dict[str, Any]:
        """
        Get speed history formatted for plotille graphing.
        
        Args:
            video_id: Video ID
            stage: Stage name
            minutes: Time window in minutes
            width: Graph width in characters
            height: Graph height in characters
            
        Returns:
            Dictionary with x_values, y_values, and metadata for plotting
        """
        history = self.get_speed_history(video_id, stage, minutes)
        
        if not history:
            return {
                'x_values': [],
                'y_values': [],
                'min_speed': 0,
                'max_speed': 0,
                'avg_speed': 0,
                'count': 0,
            }
        
        # Format for plotille: list of (index, speed) tuples
        x_values = list(range(len(history)))
        y_values = [h.speed for h in history]
        
        return {
            'x_values': x_values,
            'y_values': y_values,
            'timestamps': [h.timestamp for h in history],
            'progress': [h.progress for h in history],
            'min_speed': min(y_values),
            'max_speed': max(y_values),
            'avg_speed': sum(y_values) // len(y_values) if y_values else 0,
            'count': len(history),
        }


# Singleton instance for application-wide use
_default_service: Optional[SpeedHistoryService] = None


def get_speed_history_service(
    db_session: Optional[Session] = None,
    event_bus: Optional[EventBus] = None,
) -> Optional[SpeedHistoryService]:
    """
    Get or create the default speed history service.
    
    Args:
        db_session: Database session (required for first call)
        event_bus: Event bus for subscriptions
        
    Returns:
        SpeedHistoryService instance or None if no db_session provided
    """
    global _default_service
    
    if _default_service is None and db_session is not None:
        _default_service = SpeedHistoryService(db_session, event_bus)
    
    return _default_service


def reset_speed_history_service() -> None:
    """Reset the default service (useful for testing)."""
    global _default_service
    _default_service = None
