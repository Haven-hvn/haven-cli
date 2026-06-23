"""Main TUI application for Haven.

This module provides the main entry point for the Haven TUI application
using the Textual framework.

Notable design notes
--------------------
* The app NEVER holds a long-lived SQLAlchemy ``Session``. The previous
  revision passed ``pipeline_interface._db_session`` to ``JobHistoryRepository``
  and ``PipelineSnapshotRepository``, which (combined with SQLite's default
  rollback journal) pinned the entire TUI to a frozen DB snapshot. Instead we
  hand each repository a *factory* (``sessionmaker``) and the repository opens
  a short-lived session per call. See ``docs/TUI_IMPROVEMENTS_PROPOSAL.md``
  R1, A1, and C1.
* The ``r`` keybinding at the App level used to call ``self.refresh()`` which
  only re-renders existing widgets; it never re-queried the database. We now
  delegate to the focused screen's ``action_refresh`` so the user gets the
  same behavior on every screen. See A5 in the proposal.
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional


from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Static

# Import from haven_tui package
from haven_tui.config import HavenTUIConfig, HavenTUIConfig as _Cfg, get_default_config_path
from haven_tui.core.state_manager import StateManager
from haven_tui.core.pipeline_interface import PipelineInterface
from haven_tui.data.event_consumer import TUIEventConsumer as EventConsumer
from haven_tui.data.refresher import DataRefresher as Refresher
from haven_tui.data.repositories import (
    JobHistoryRepository,
    PipelineSnapshotRepository,
    SpeedHistoryRepository,
)
from haven_tui.data.sqlite_event_consumer import SqliteEventConsumer

from haven_tui.ui.views.analytics import AnalyticsDashboardScreen
from haven_tui.ui.views.event_log import EventLogScreen
from haven_tui.ui.views.video_detail import VideoDetailScreen
from haven_tui.ui.views.video_list import VideoListScreen


logger = logging.getLogger(__name__)


def _configure_tui_logging() -> None:
    """Set up file-based logging for the TUI process.

    Textual takes over stdout for the duration of the run, so any log
    messages emitted via ``print``/the default StreamHandler vanish into
    a void the user can't see. That makes diagnosing problems like
    "the TUI looks frozen" extraordinarily difficult — see C4 in the
    proposal.

    We add a rotating file handler to the haven_tui / haven_cli loggers
    pointing at ``$HAVEN_TUI_LOG_FILE`` (or the default
    ``~/.local/share/haven/tui.log``). ``HAVEN_TUI_DEBUG=1`` promotes
    the log level from INFO to DEBUG.

    Idempotent — running it twice doesn't duplicate handlers.
    """
    sentinel = "haven_tui_file_logging"
    root = logging.getLogger("haven_tui")
    if any(getattr(h, "_haven_tui_file_logging", False) for h in root.handlers):
        return  # already configured

    log_path_env = os.environ.get("HAVEN_TUI_LOG_FILE")
    if log_path_env:
        log_path = Path(log_path_env).expanduser()
    else:
        log_path = Path.home() / ".local" / "share" / "haven" / "tui.log"

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=2 * 1024 * 1024,  # 2 MiB
            backupCount=3,
            encoding="utf-8",
        )
    except Exception:
        # If we can't write the log file (read-only home, weird FS,
        # etc.) we silently skip — better to lose logs than to crash
        # the TUI on startup.
        return

    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler._haven_tui_file_logging = True  # type: ignore[attr-defined]

    debug = os.environ.get("HAVEN_TUI_DEBUG") == "1"
    level = logging.DEBUG if debug else logging.INFO
    handler.setLevel(level)

    # Attach to both packages so we capture haven_cli's pipeline activity
    # too (the daemon-side bus events the SqliteEventConsumer replays).
    for name in ("haven_tui", "haven_cli"):
        log = logging.getLogger(name)
        log.addHandler(handler)
        if log.level == logging.NOTSET or log.level > level:
            log.setLevel(level)


class _ScopedRepo:

    """Wraps a ``Repository(session)`` class so callers can use it without
    worrying about session lifetime.

    Each attribute access opens a fresh, short-lived session, instantiates the
    underlying repository, runs the requested method, then closes the session.
    This is cheap (the engine pool reuses connections) and ensures we never
    pin a stale read snapshot.

    Example::

        repo = _ScopedRepo(JobHistoryRepository, session_factory)
        history = repo.get_video_pipeline_history(video_id)

    Limitations:
        * Returned ORM objects are detached (``session.expunge_all()`` runs
          before close), so callers must not trigger lazy attribute loads.
          Most TUI consumers already convert results to plain dicts/dataclasses,
          so this is fine in practice.
        * Methods that return SQLAlchemy ``Query`` objects, generators that
          outlive the call, or that mutate cross-call state are not supported
          by this wrapper. The TUI only uses one-shot read methods so this
          limitation is acceptable.
    """

    def __init__(self, repo_cls, session_factory):
        self._repo_cls = repo_cls
        self._session_factory = session_factory

    def __getattr__(self, name: str):
        repo_cls = self._repo_cls
        session_factory = self._session_factory

        def _call(*args, **kwargs):
            session = session_factory()
            try:
                repo = repo_cls(session)
                result = getattr(repo, name)(*args, **kwargs)
                # Detach any ORM objects so they survive past session close.
                try:
                    session.expunge_all()
                except Exception:  # pragma: no cover - already-detached objects
                    pass
                # If the underlying call mutated rows, commit before close so
                # the next short-lived session sees the change.
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        return _call



class HavenTUIApp(App[None]):
    """Main TUI application for Haven video pipeline monitoring.
    
    This application provides a terminal-based interface for:
    - Monitoring video pipeline progress
    - Viewing video status and details
    - Controlling downloads, encryption, uploads
    - Viewing metrics and logs
    - Managing filters and search
    
    Example:
        >>> app = HavenTUIApp()
        >>> app.run()
    
    Attributes:
        config: The TUI configuration
        state_manager: Manages application state
        pipeline_interface: Interface to the pipeline
        event_consumer: Consumes events from the pipeline
        refresher: Handles periodic data refresh
        speed_history_repo: Repository for speed history data
    """
    
    CSS = """
    Screen {
        align: center middle;
    }
    
    #main-container {
        width: 100%;
        height: 100%;
        padding: 0;
    }
    
    .welcome {
        width: 100%;
        height: auto;
        content-align: center middle;
        text-align: center;
    }
    
    .welcome-title {
        text-style: bold;
        color: $accent;
    }
    
    .loading {
        width: 100%;
        height: 100%;
        content-align: center middle;
        text-align: center;
    }
    """
    
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("?", "help", "Help"),
    ]
    
    # Note: video_detail screen is created dynamically with video_id
    # Screens are installed dynamically in _setup_screens() with proper dependencies
    SCREENS = {}
    
    def __init__(
        self,
        config: Optional[HavenTUIConfig] = None,
        config_path: Optional[str] = None,
    ) -> None:
        """Initialize the TUI application.
        
        Args:
            config: Optional TUI configuration. If not provided, will load
                from config file or use defaults.
            config_path: Optional path to configuration file.
        """
        super().__init__()

        # Wire up file logging before we do anything else so that any
        # error during configuration loading or component init is
        # captured. Idempotent.
        _configure_tui_logging()

        # Load configuration
        if config:

            self.config = config
        elif config_path:
            self.config = HavenTUIConfig.load(Path(config_path))
        else:
            default_path = get_default_config_path()
            if default_path.exists():
                self.config = HavenTUIConfig.load(default_path)
            else:
                self.config = HavenTUIConfig()
        
        # Initialize core components (will be set up in on_mount)
        self.state_manager: Optional[StateManager] = None
        self.pipeline_interface: Optional[PipelineInterface] = None
        self.event_consumer: Optional[EventConsumer] = None
        self.refresher: Optional[Refresher] = None
        self.speed_history_repo: Optional[SpeedHistoryRepository] = None
        self.job_history_repo: Optional[JobHistoryRepository] = None
        self.snapshot_repo: Optional[PipelineSnapshotRepository] = None
        self._init_error: Optional[str] = None
    
    def compose(self) -> ComposeResult:
        """Compose the UI layout.
        
        Yields:
            UI components for the application.
        """
        with Container(id="main-container"):
            if self._init_error:
                yield Static(
                    f"[bold red]Error:[/bold red] {self._init_error}\n\n"
                    "Please check your configuration and try again.",
                    classes="loading",
                )
            else:
                yield Static(
                    "[bold blue]Haven TUI[/bold blue]\n\n"
                    "[dim]Loading...[/dim]",
                    classes="loading",
                )
    
    async def on_mount(self) -> None:
        """Handle application mount event - initialize components."""
        self.title = "Haven TUI"
        self.sub_title = "Video Pipeline Monitor"
        
        try:
            await self._initialize_components()
            
            # Push the main video list screen
            if self.state_manager:
                try:
                    await self.push_screen("video_list")
                except Exception as screen_error:
                    # If screen is already installed, just switch to it
                    if "already installed" in str(screen_error).lower():
                        self.switch_screen("video_list")
                    else:
                        raise screen_error
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._init_error = str(e)
            self.refresh()
    
    async def _initialize_components(self) -> None:
        """Initialize all application components.

        After this returns we have:

        * A ``PipelineInterface`` that holds a session FACTORY (not a session).
        * A ``StateManager`` whose database reads always go through the
          interface, which uses short-lived sessions.
        * Per-call repository facades (``job_history_repo``, ``snapshot_repo``)
          that mint short-lived sessions on demand from the same factory.
        * A ``SpeedHistoryRepository`` that does the same internally (see its
          docstring for details).

        We deliberately do NOT use ``self.pipeline_interface._db_session`` —
        that attribute is intentionally always ``None`` in the new code path
        and only kept for backwards compatibility with old code that probes
        it. See R1 / A1 / C1 in the proposal.
        """
        # Get database URL from config
        db_url = self.config.database.connection_string

        # Initialize pipeline interface with database path
        self.pipeline_interface = PipelineInterface(
            database_path=db_url.replace("sqlite:///", ""),
        )

        # Enter the async context to initialize the session factory + event bus.
        await self.pipeline_interface.__aenter__()

        # Initialize state manager with pipeline interface
        self.state_manager = StateManager(pipeline=self.pipeline_interface)
        await self.state_manager.initialize()

        # Speed history repository: pass db_url to trigger the factory mode in
        # the repository's constructor. Each query opens its own session.
        self.speed_history_repo = SpeedHistoryRepository(
            db_url=db_url,
            max_history_seconds=self.config.display.graph_history_seconds,
        )

        # Detail-view repositories. We wrap them in _ScopedRepo so each method
        # call opens its own short-lived session. Detail screens treat these
        # as the same shape as a normal Repository, so they don't have to
        # know about session lifetime.
        session_factory = self.pipeline_interface.session_factory
        if session_factory is None:  # pragma: no cover - lifetime invariant
            raise RuntimeError(
                "PipelineInterface session factory not initialized; "
                "did __aenter__ run?"
            )
        self.job_history_repo = _ScopedRepo(JobHistoryRepository, session_factory)
        self.snapshot_repo = _ScopedRepo(PipelineSnapshotRepository, session_factory)

        # Event consumer (Milestone B). The consumer tails the
        # ``pipeline_events`` SQLite table that the daemon writes to and
        # republishes each row onto the in-process EventBus that the
        # PipelineInterface (and therefore StateManager) is subscribed to.
        # That makes the TUI receive cross-process progress events
        # without depending on a single shared EventBus instance.
        try:
            from haven_cli.pipeline.events import get_event_bus

            self._sqlite_event_consumer: Optional[SqliteEventConsumer] = (
                SqliteEventConsumer(
                    session_factory=session_factory,
                    target_bus=get_event_bus(),
                    poll_interval_seconds=0.25,
                )
            )
            await self._sqlite_event_consumer.start()
            logger.debug("SqliteEventConsumer started")
        except Exception as e:  # pragma: no cover - defensive
            # If the consumer can't start (table missing on first ever
            # run, etc.) the polling path in VideoListScreen still works,
            # so degrade gracefully.
            logger.warning(
                "SqliteEventConsumer failed to start: %s; "
                "TUI will rely on polling only.",
                e,
            )
            self._sqlite_event_consumer = None

        # Legacy slots retained for backwards compatibility with code
        # paths that probe for them. The Milestone B implementation does
        # NOT use these — they were the dead, never-wired
        # ``TUIEventConsumer`` / ``DataRefresher`` from the original
        # codebase. Leaving as None until C8 cleans them up.
        self.event_consumer = None
        self.refresher = None

        # Update screens with initialized components
        self._setup_screens()


    
    def _setup_screens(self) -> None:
        """Set up screens with initialized components."""
        # Create video list screen with all components
        video_list_screen = VideoListScreen(
            state_manager=self.state_manager,
            config=self.config,
            speed_history_repo=self.speed_history_repo,
            pipeline_interface=self.pipeline_interface,
        )
        
        # Install the screen
        self.install_screen(video_list_screen, "video_list")
        
        # Create and install analytics dashboard screen
        analytics_screen = AnalyticsDashboardScreen(
            config=self.config,
        )
        self.install_screen(analytics_screen, "analytics")
        
        # Create and install event log screen
        event_log_screen = EventLogScreen()
        self.install_screen(event_log_screen, "event_log")
        
        # Note: video_detail screen is created dynamically with video_id
    
    def action_refresh(self) -> None:
        """Refresh the display.

        Previously this only called ``self.refresh()``, which is a Textual
        re-render of existing widgets — it never re-queried the database. As
        a result the user-facing ``r`` key felt broken on the welcome screen,
        analytics, and event log: nothing visible changed because the
        underlying state hadn't been re-fetched. (The video list screen had
        its own override that did the right thing, masking the problem on
        the most common screen.)

        The fix: if the currently focused Screen has its own
        ``action_refresh`` we delegate to it, so each screen owns its own
        notion of "refresh" (re-poll DB, redraw, clear cache, etc). We also
        still call ``self.refresh()`` so a screen with no override at least
        gets a redraw and the user gets feedback.
        """
        screen = self.screen
        screen_action = getattr(screen, "action_refresh", None)
        if screen_action is not None and screen_action is not self.action_refresh:
            try:
                screen_action()
                return
            except Exception:  # pragma: no cover - defensive: don't crash on r
                logger.exception("Screen action_refresh raised; falling back to App.refresh()")
        self.refresh()

    
    def action_help(self) -> None:
        """Show help information."""
        help_text = """
[b]Haven TUI Help[/b]

[b]Global Keys:[/b]
  q - Quit application
  r - Refresh display
  ? - Show this help

[b]Navigation:[/b]
  ↑/↓ - Navigate up/down
  Enter - Select item / View details
  Esc - Go back

[b]Filters:[/b]
  f - Open filter dialog
  c - Toggle completed videos
  e - Show errors only
  x - Clear all filters

[b]Sorting:[/b]
  s - Change sort field
  S - Toggle sort order

[b]View:[/b]
  g - Toggle speed graph
  a - Toggle analytics view
  l - Show event log

[b]Actions:[/b]
  space - Select item (batch mode)
  b - Toggle batch mode
  R - Retry failed items
  X - Cancel selected items
"""
        self.notify(help_text, title="Help", timeout=10.0)
    
    async def on_unmount(self) -> None:
        """Handle application unmount - cleanup resources."""
        # Stop the cross-process event consumer first so it doesn't try
        # to publish onto a bus whose StateManager handlers are about to
        # disappear.
        consumer = getattr(self, "_sqlite_event_consumer", None)
        if consumer is not None:
            try:
                await consumer.stop()
            except Exception:  # pragma: no cover - defensive
                logger.exception("Error stopping SqliteEventConsumer")

        # Stop legacy background tasks (currently always None — kept for
        # forwards compatibility with C8 cleanup).
        if self.refresher:
            await self.refresher.stop()

        if self.event_consumer:
            await self.event_consumer.stop()

        # Shutdown state manager
        if self.state_manager:
            await self.state_manager.shutdown()

        # Exit pipeline interface context
        if self.pipeline_interface:
            await self.pipeline_interface.__aexit__(None, None, None)



def main(args: Optional[list[str]] = None) -> int:
    """Main entry point for Haven TUI.
    
    Args:
        args: Command line arguments (optional).
        
    Returns:
        Exit code (0 for success, non-zero for errors).
    """
    # Check Python version
    if sys.version_info < (3, 9):
        print(
            "Error: Python 3.9 or higher is required.",
            file=sys.stderr,
        )
        return 1
    
    # Parse arguments (simple implementation)
    config_path: Optional[str] = None
    if args:
        for i, arg in enumerate(args):
            if arg in ("-c", "--config") and i + 1 < len(args):
                config_path = args[i + 1]
            elif arg.startswith("--config="):
                config_path = arg.split("=", 1)[1]
            elif arg in ("-h", "--help"):
                print(__doc__ or "Haven TUI - Terminal User Interface for Haven")
                print("\nUsage: haven-tui [OPTIONS]")
                print("\nOptions:")
                print("  -c, --config PATH    Path to configuration file")
                print("  -h, --help          Show this help message")
                print("  -v, --version       Show version information")
                return 0
            elif arg in ("-v", "--version"):
                from haven_tui import __version__
                print(f"haven-tui {__version__}")
                return 0
    
    try:
        # Create and run the app
        app = HavenTUIApp(config_path=config_path)
        app.run()
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
