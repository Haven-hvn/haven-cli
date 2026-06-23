"""Event types and in-process EventBus for pipeline communication.

This module provides the event-driven communication infrastructure for the pipeline.
Events are aligned with the P specification from HavenPlayer.p.

Cross-process delivery (Milestone B of the TUI improvements proposal)
---------------------------------------------------------------------
The EventBus itself is in-process. To bridge events to other processes
(notably ``haven tui`` running in parallel with ``haven run daemon``) the
bus optionally writes a copy of every published event to the
``pipeline_events`` SQLite table via a sink callback. The sink is
opt-in and registered by the daemon at startup; the TUI doesn't enable
it (it only consumes). See ``EventBus.attach_persistent_sink``.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Callable, Coroutine, Dict, List, Optional, Protocol, Set
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)



class EventType(Enum):
    """Pipeline event types aligned with HavenPlayer.p specification.
    
    Events follow the flow:
    Plugin Discovery → Archive → Ingest → Analyze → Encrypt → Upload → Sync
    """
    
    # Plugin events
    SOURCES_DISCOVERED = auto()       # e_plugin_sources_found
    ARCHIVE_STARTED = auto()          # e_plugin_archive
    ARCHIVE_COMPLETE = auto()         # e_plugin_archive_complete
    
    # Pipeline flow events
    VIDEO_INGESTED = auto()           # e_video_ingested
    ANALYSIS_REQUESTED = auto()       # e_analysis_requested
    ANALYSIS_COMPLETE = auto()        # e_analysis_complete
    ANALYSIS_FAILED = auto()          # e_analysis_failed
    ENCRYPT_REQUESTED = auto()        # e_encrypt_requested
    ENCRYPT_PROGRESS = auto()         # e_encrypt_progress
    ENCRYPT_COMPLETE = auto()         # e_encrypt_complete
    UPLOAD_REQUESTED = auto()         # e_upload_requested
    UPLOAD_PROGRESS = auto()          # e_upload_progress
    UPLOAD_COMPLETE = auto()          # e_upload_complete
    UPLOAD_FAILED = auto()            # e_upload_failed
    SYNC_REQUESTED = auto()           # e_sync_to_arkiv
    SYNC_COMPLETE = auto()            # e_sync_complete
    DOWNLOAD_PROGRESS = auto()        # e_download_progress
    
    # Cleanup events
    CLEANUP_STARTED = auto()          # e_cleanup_started
    CLEANUP_COMPLETE = auto()         # e_cleanup_complete
    CLEANUP_FAILED = auto()           # e_cleanup_failed
    
    # Pipeline lifecycle events
    PIPELINE_STARTED = auto()
    PIPELINE_COMPLETE = auto()
    PIPELINE_FAILED = auto()
    PIPELINE_CANCELLED = auto()
    
    # Step lifecycle events
    STEP_STARTED = auto()
    STEP_COMPLETE = auto()
    STEP_FAILED = auto()
    STEP_SKIPPED = auto()
    
    # System events
    HEALTH_CHECK = auto()             # e_health_check
    CONFIG_UPDATE = auto()            # e_config_update
    WORKER_STATUS = auto()            # e_worker_status_update


@dataclass
class Event:
    """Base event class for pipeline communication.
    
    Attributes:
        event_type: The type of event
        payload: Event-specific data
        event_id: Unique identifier for this event
        correlation_id: ID linking related events (e.g., same video through pipeline)
        timestamp: When the event was created
        source: Component that emitted the event
        metadata: Additional contextual information
    """
    
    event_type: EventType
    payload: Dict[str, Any] = field(default_factory=dict)
    event_id: UUID = field(default_factory=uuid4)
    correlation_id: Optional[UUID] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def with_correlation(self, correlation_id: UUID) -> "Event":
        """Create a copy of this event with a correlation ID."""
        return Event(
            event_type=self.event_type,
            payload=self.payload.copy(),
            event_id=self.event_id,
            correlation_id=correlation_id,
            timestamp=self.timestamp,
            source=self.source,
            metadata=self.metadata.copy(),
        )


# Type alias for event handlers
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """In-process async event bus for pipeline communication.
    
    Provides publish/subscribe functionality using asyncio for non-blocking
    event distribution. Designed for single-process operation with potential
    future migration to distributed systems (Redis, NATS).
    
    Example:
        bus = EventBus()
        
        async def handler(event: Event) -> None:
            print(f"Received: {event.event_type}")
        
        bus.subscribe(EventType.VIDEO_INGESTED, handler)
        await bus.publish(Event(EventType.VIDEO_INGESTED, {"path": "/video.mp4"}))
    """
    
    def __init__(self) -> None:
        """Initialize the event bus."""
        self._handlers: Dict[EventType, List[EventHandler]] = {}
        self._global_handlers: List[EventHandler] = []
        self._event_history: List[Event] = []
        self._history_enabled: bool = False
        self._max_history: int = 1000
        self._lock = asyncio.Lock()
        # Optional sink that durably persists events for cross-process
        # delivery (see module docstring + B1 in the proposal). Set via
        # attach_persistent_sink. The sink is invoked synchronously after
        # the in-process handlers fan out, so a slow sink slows the bus —
        # the SQLite writer is fast enough that this hasn't been a problem
        # in practice, but we run it in a thread executor anyway to keep
        # the bus async-safe.
        self._persistent_sink: Optional[Callable[[Event], None]] = None

    
    def subscribe(
        self,
        event_type: EventType,
        handler: EventHandler,
    ) -> Callable[[], None]:
        """Subscribe a handler to a specific event type.
        
        Args:
            event_type: The event type to subscribe to
            handler: Async function to call when event is published
            
        Returns:
            Unsubscribe function to remove this subscription
        """
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        
        self._handlers[event_type].append(handler)
        
        def unsubscribe() -> None:
            self._handlers[event_type].remove(handler)
        
        return unsubscribe
    
    def subscribe_all(self, handler: EventHandler) -> Callable[[], None]:
        """Subscribe a handler to all event types.
        
        Useful for logging, metrics, or debugging.
        
        Args:
            handler: Async function to call for every event
            
        Returns:
            Unsubscribe function to remove this subscription
        """
        self._global_handlers.append(handler)
        
        def unsubscribe() -> None:
            self._global_handlers.remove(handler)
        
        return unsubscribe
    
    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribed handlers.
        
        Events are delivered asynchronously to all handlers. Handlers are
        executed concurrently using asyncio.gather().
        
        Args:
            event: The event to publish
        """
        async with self._lock:
            if self._history_enabled:
                self._event_history.append(event)
                if len(self._event_history) > self._max_history:
                    self._event_history = self._event_history[-self._max_history:]
        
        # Collect all handlers for this event
        handlers: List[EventHandler] = []
        handlers.extend(self._global_handlers)
        
        if event.event_type in self._handlers:
            handlers.extend(self._handlers[event.event_type])
        
        if not handlers:
            return
        
        # Execute all handlers concurrently
        await asyncio.gather(
            *[self._safe_call(handler, event) for handler in handlers],
            return_exceptions=True,
        )

        # Run the persistent sink after the in-process fan-out so a slow or
        # broken sink can't starve our subscribers. We push it through the
        # default thread executor because the sink is synchronous (it does
        # SQLite writes) and we don't want to block the asyncio event loop.
        if self._persistent_sink is not None:
            sink = self._persistent_sink
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, sink, event)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning(
                    "EventBus persistent sink failed for %s: %s",
                    event.event_type,
                    e,
                )

    def attach_persistent_sink(
        self, sink: Optional[Callable[[Event], None]]
    ) -> None:
        """Attach (or detach) a synchronous, durable event sink.

        The sink is invoked once per published event, after the in-process
        handlers have fanned out. Pass ``None`` to detach.

        Typical usage from the daemon::

            from haven_cli.pipeline.events import (
                get_event_bus,
                make_sqlite_event_sink,
            )
            get_event_bus().attach_persistent_sink(
                make_sqlite_event_sink()
            )

        The TUI does **not** attach a sink; it only consumes the table.
        Otherwise we'd duplicate every event the TUI replays back to the
        table, creating an infinite ingestion loop.

        Args:
            sink: A callable taking a single ``Event`` and returning
                ``None``. Must be safe to call from a worker thread.
                Passing ``None`` removes the current sink.
        """
        self._persistent_sink = sink

    async def _safe_call(self, handler: EventHandler, event: Event) -> None:

        """Safely call a handler, catching and logging exceptions."""
        try:
            await handler(event)
        except Exception as e:
            # Log error but don't propagate to other handlers
            # TODO: Integrate with proper logging system
            print(f"Event handler error for {event.event_type}: {e}")
    
    def enable_history(self, max_size: int = 1000) -> None:
        """Enable event history tracking.
        
        Args:
            max_size: Maximum number of events to retain
        """
        self._history_enabled = True
        self._max_history = max_size
    
    def disable_history(self) -> None:
        """Disable event history tracking and clear history."""
        self._history_enabled = False
        self._event_history.clear()
    
    def get_history(
        self,
        event_type: Optional[EventType] = None,
        correlation_id: Optional[UUID] = None,
        limit: Optional[int] = None,
    ) -> List[Event]:
        """Get event history, optionally filtered.
        
        Args:
            event_type: Filter by event type
            correlation_id: Filter by correlation ID
            limit: Maximum number of events to return
            
        Returns:
            List of events matching the filter criteria
        """
        events = self._event_history
        
        if event_type is not None:
            events = [e for e in events if e.event_type == event_type]
        
        if correlation_id is not None:
            events = [e for e in events if e.correlation_id == correlation_id]
        
        if limit is not None:
            events = events[-limit:]
        
        return events
    
    def clear(self) -> None:
        """Clear all subscriptions and history."""
        self._handlers.clear()
        self._global_handlers.clear()
        self._event_history.clear()


# Singleton event bus instance for the application
_default_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get the default application-wide event bus.
    
    Returns:
        The singleton EventBus instance
    """
    global _default_bus
    if _default_bus is None:
        _default_bus = EventBus()
    return _default_bus


def reset_event_bus() -> None:
    """Reset the default event bus (useful for testing)."""
    global _default_bus
    if _default_bus is not None:
        _default_bus.clear()
    _default_bus = None


def make_sqlite_event_sink() -> Callable[[Event], None]:
    """Build a sink that persists events to the ``pipeline_events`` table.

    The returned callable opens a short-lived SQLAlchemy session per call,
    writes the event row, commits, and closes. We deliberately do NOT hold
    a long-lived session here for the same reason ``PipelineInterface``
    doesn't (see TUI_IMPROVEMENTS_PROPOSAL.md R1) — a long-lived writer
    transaction under SQLite blocks readers and pins snapshots.

    The function is meant to be passed to :meth:`EventBus.attach_persistent_sink`
    in the daemon process. It is safe to call from a thread (the
    sessionmaker handles connection-per-thread via ``check_same_thread=False``
    set in ``haven_cli/database/connection.py``).

    Returns:
        A ``(Event) -> None`` callable. Errors are logged but never raised
        — losing one event row to a transient SQLite error is preferable
        to crashing the pipeline.
    """
    # Imported here to avoid circular import at module load.
    from haven_cli.database.connection import get_session_maker
    from haven_cli.database.models import PipelineEvent

    SessionLocal = get_session_maker()

    def _sink(event: Event) -> None:
        # Serialize the payload defensively. Some payloads contain UUIDs
        # or datetimes that aren't JSON-native; ``default=str`` is the
        # cheapest correct fallback.
        try:
            payload_json = json.dumps(event.payload, default=str)
        except Exception:
            # If the payload genuinely can't be serialized we still want
            # the event row to exist so the consumer's ``last_id`` cursor
            # advances and we don't replay forever.
            payload_json = "{}"

        session = SessionLocal()
        try:
            row = PipelineEvent(
                ts=event.timestamp,
                event_type=event.event_type.name,
                correlation_id=str(event.correlation_id) if event.correlation_id else None,
                source=event.source or None,
                payload_json=payload_json,
            )
            session.add(row)
            session.commit()
        except Exception as e:
            logger.warning(
                "Failed to persist %s event: %s", event.event_type, e
            )
            try:
                session.rollback()
            except Exception:
                pass
        finally:
            session.close()

    return _sink

