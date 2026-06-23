"""Cross-process event tail for the TUI.

Background
----------
``haven_cli.pipeline.events.EventBus`` is in-process. The daemon
(``haven run daemon``) and the TUI (``haven tui``) live in separate
processes, so events emitted by the daemon never reach the TUI's bus.
That's the second of the two root causes documented in
``docs/TUI_IMPROVEMENTS_PROPOSAL.md`` (R2).

Milestone B fixes this by:

1. Having the daemon attach a sink that durably writes every event to
   the ``pipeline_events`` SQLite table (see
   ``haven_cli.pipeline.events.make_sqlite_event_sink``).
2. Having the TUI run :class:`SqliteEventConsumer` (defined here) which
   tails that table by ``id`` and republishes each row onto the TUI's
   *local* bus, where ``StateManager`` is already subscribed via
   ``PipelineInterface.on_event``.

The consumer is intentionally simple. It polls SQLite at a fixed
interval (default 250 ms — fast enough to feel real-time, slow enough
to keep CPU near zero on an idle pipeline). SQLite's
``PRAGMA data_version`` lets us short-circuit the SELECT when nothing
has changed, so the steady-state cost is one ``PRAGMA`` per tick.

We also watch for the table going missing (e.g. the user wiped the
database while the TUI was running) and recover gracefully by re-
running ``Base.metadata.create_all`` and resetting the cursor.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from haven_cli.pipeline.events import Event, EventBus, EventType

logger = logging.getLogger(__name__)


class SqliteEventConsumer:
    """Tail ``pipeline_events`` and replay rows onto an in-process bus.

    Lifecycle
    ~~~~~~~~~
    Construct, then ``await start()``. The consumer kicks off a background
    asyncio task that loops until ``await stop()``. Stop is idempotent
    and joins the background task before returning.

    Cursor semantics
    ~~~~~~~~~~~~~~~~
    On startup we set the cursor to ``SELECT MAX(id) FROM pipeline_events``
    so we don't replay the entire backlog (which could be hours of
    progress events) into the TUI on launch. Callers that *do* want a
    backlog replay can pass ``catch_up_seconds`` to read a recent
    window.
    """

    def __init__(
        self,
        session_factory: sessionmaker,
        target_bus: EventBus,
        poll_interval_seconds: float = 0.25,
        max_batch: int = 500,
        catch_up_seconds: float = 0.0,
    ) -> None:
        """Initialize the consumer.

        Args:
            session_factory: ``sessionmaker`` bound to the haven-cli
                engine. The consumer mints short-lived sessions; it does
                not hold one open.
            target_bus: The local in-process bus to replay events onto.
                ``StateManager`` should already be subscribed to it via
                ``PipelineInterface.on_event``.
            poll_interval_seconds: How often to check the table. 250 ms
                is a good default for a TUI; smaller values waste CPU
                without improving perceived latency.
            max_batch: Cap on rows fetched per tick so a long backlog
                doesn't stall the UI. Excess rows are picked up on the
                next tick.
            catch_up_seconds: If > 0, on startup read events newer than
                this many seconds instead of starting from "now". Useful
                if the TUI was started right after the daemon and we
                want to see the videos that were active a moment ago.
        """
        self._session_factory = session_factory
        self._bus = target_bus
        self._poll_interval = max(0.05, poll_interval_seconds)
        self._max_batch = max(1, max_batch)
        self._catch_up_seconds = max(0.0, catch_up_seconds)

        self._last_id: int = 0
        self._last_data_version: Optional[int] = None
        self._task: Optional[asyncio.Task] = None
        self._running: bool = False

        # Health metrics — exposed for the status bar (Milestone C5).
        self.events_dispatched: int = 0
        self.last_dispatch_at: Optional[float] = None
        self.last_error: Optional[str] = None

    async def start(self) -> None:
        """Start the background tail task."""
        if self._running:
            logger.debug("SqliteEventConsumer already running; ignoring start()")
            return

        # Initialize cursor before we start the loop so that the first
        # tick has a sensible starting point. This is a sync DB call run
        # in a thread to avoid blocking the loop while we're (re-)opening
        # the WAL connection.
        await asyncio.get_running_loop().run_in_executor(
            None, self._init_cursor
        )

        self._running = True
        self._task = asyncio.create_task(self._run(), name="sqlite_event_consumer")
        logger.debug(
            "SqliteEventConsumer started (poll=%.2fs, last_id=%d)",
            self._poll_interval,
            self._last_id,
        )

    async def stop(self) -> None:
        """Stop the background tail task."""
        if not self._running:
            return
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.debug("SqliteEventConsumer stopped")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _init_cursor(self) -> None:
        """Pick a starting ``last_id`` value.

        The default is "newest row at startup time" so the TUI doesn't
        replay yesterday's progress events. If the user passed
        ``catch_up_seconds`` we instead pick the smallest id whose
        timestamp is within that window.
        """
        session = self._session_factory()
        try:
            if self._catch_up_seconds > 0:
                row = session.execute(
                    text(
                        "SELECT COALESCE(MIN(id), 0) FROM pipeline_events "
                        "WHERE ts >= datetime('now', :delta)"
                    ),
                    {"delta": f"-{self._catch_up_seconds:.0f} seconds"},
                ).first()
                # MIN(id) - 1 because we use id > last_id in the loop.
                self._last_id = max(0, (row[0] if row else 0) - 1)
            else:
                row = session.execute(
                    text("SELECT COALESCE(MAX(id), 0) FROM pipeline_events")
                ).first()
                self._last_id = row[0] if row else 0
        except OperationalError:
            # Table doesn't exist yet — TUI started before the daemon
            # ever ran. We'll create it lazily on the first poll.
            self._last_id = 0
        finally:
            session.close()

    async def _run(self) -> None:
        """Main poll loop."""
        while self._running:
            try:
                # Run the (synchronous) SQLite read in a worker thread so
                # we don't pin the asyncio event loop on slow disks.
                events = await asyncio.get_running_loop().run_in_executor(
                    None, self._fetch_batch
                )
                for event in events:
                    # Replay onto the local bus. Each handler is async so
                    # we await per-event; this also gives StateManager
                    # backpressure if it's slow.
                    try:
                        await self._bus.publish(event)
                        self.events_dispatched += 1
                        self.last_dispatch_at = asyncio.get_event_loop().time()
                    except Exception as e:  # pragma: no cover - defensive
                        logger.warning(
                            "SqliteEventConsumer publish failed for %s: %s",
                            event.event_type,
                            e,
                        )
                        self.last_error = str(e)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("SqliteEventConsumer poll failed: %s", e)
                self.last_error = str(e)

            try:
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                raise

    def _fetch_batch(self) -> list[Event]:
        """Fetch any rows with id > last_id, return as Event objects.

        Uses ``PRAGMA data_version`` as a cheap "anything changed?" check
        so an idle pipeline does ~one PRAGMA per tick instead of a real
        query.
        """
        session = self._session_factory()
        try:
            # PRAGMA data_version returns a monotonic counter that bumps
            # whenever the database file is modified by *any* connection.
            # If it hasn't moved since our last check we know there's
            # nothing for us — skip the SELECT entirely.
            try:
                dv = session.execute(text("PRAGMA data_version")).scalar()
            except Exception:
                dv = None

            if (
                dv is not None
                and self._last_data_version is not None
                and dv == self._last_data_version
            ):
                return []
            self._last_data_version = dv

            try:
                rows = session.execute(
                    text(
                        "SELECT id, ts, event_type, correlation_id, source, "
                        "payload_json FROM pipeline_events "
                        "WHERE id > :last_id "
                        "ORDER BY id ASC LIMIT :lim"
                    ),
                    {"last_id": self._last_id, "lim": self._max_batch},
                ).fetchall()
            except OperationalError:
                # Table missing — daemon hasn't run yet, or DB was wiped.
                # Try to create it; if that fails, give up quietly.
                try:
                    from haven_cli.database.connection import init_engine
                    from haven_cli.database.models import Base

                    engine = init_engine()
                    Base.metadata.create_all(bind=engine)
                except Exception:
                    pass
                return []

            events: list[Event] = []
            for row in rows:
                row_id, ts, et_name, corr_id, source, payload_json = row
                try:
                    event_type = EventType[et_name]
                except KeyError:
                    # Unknown event type — daemon is newer than this TUI.
                    # Skip rather than crash; the table cursor still
                    # advances so we don't replay forever.
                    self._last_id = max(self._last_id, row_id)
                    continue

                try:
                    payload = json.loads(payload_json) if payload_json else {}
                except Exception:
                    payload = {}

                correlation: Optional[UUID] = None
                if corr_id:
                    try:
                        correlation = UUID(corr_id)
                    except Exception:
                        correlation = None

                events.append(
                    Event(
                        event_type=event_type,
                        payload=payload,
                        correlation_id=correlation,
                        timestamp=ts,
                        source=source or "",
                    )
                )
                self._last_id = max(self._last_id, row_id)

            return events
        finally:
            session.close()
