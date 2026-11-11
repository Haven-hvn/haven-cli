"""BitTorrent plugin for Haven CLI.

This plugin provides BitTorrent downloading capabilities with support
for multiple magnet link sources through a pluggable source interface.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import functools
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar, TYPE_CHECKING

import libtorrent as lt

T = TypeVar('T')

from haven_cli.plugins.base import (
    ArchiveResult,
    ArchiverPlugin,
    MediaSource,
    PluginCapability,
    PluginInfo,
)
from haven_cli.plugins.builtin.bittorrent.sources.base import (
    MagnetLink,
    MagnetSource,
    SourceConfig,
    SourceHealth,
)
from haven_cli.plugins.builtin.bittorrent.sources.forum import (
    ForumScraperSource,
    ForumSourceConfig,
)
from haven_cli.database.connection import get_db_session
from haven_cli.database.repositories import RepositoryFactory

# Lazy imports to avoid circular dependency
if TYPE_CHECKING:
    from haven_tui.data.download_tracker import DownloadProgressTracker, BitTorrentProgressAdapter

logger = logging.getLogger(__name__)


@dataclass
class BitTorrentConfig:
    """Configuration for the BitTorrent plugin.
    
    Attributes:
        download_dir: Directory to save downloaded files
        max_concurrent_downloads: Maximum concurrent torrent downloads
        max_download_speed: Maximum download speed in bytes/sec (0 = unlimited)
        max_upload_speed: Maximum upload speed in bytes/sec (0 = unlimited)
        seed_ratio: Minimum seed ratio before stopping (0 = don't stop)
        seed_time: Minimum seed time in seconds (0 = don't stop)
        video_extensions: List of video file extensions to prioritize
        min_video_size: Minimum video file size in bytes
        max_video_size: Maximum video file size in bytes
        select_mode: Which videos to archive from a multi-file torrent;
            ``"all"`` keeps every eligible video, ``"largest"`` keeps only
            the biggest eligible one
        sources: List of source configurations
        enabled: Whether the plugin is enabled
        max_download_time: Maximum time to wait for download in seconds (0 = unlimited)
        stall_timeout: Time with no progress before considering stalled (seconds)
        min_progress_percent: Minimum progress to accept as partial success
        allow_partial_downloads: Whether to allow returning partial downloads
        enable_background_downloads: Whether to enable background download tracking
    """
    
    download_dir: str = "downloads/bittorrent"
    max_concurrent_downloads: int = 3
    max_download_speed: int = 0
    max_upload_speed: int = 0
    seed_ratio: float = 0.0
    seed_time: int = 0
    video_extensions: List[str] = field(default_factory=lambda: [
        ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"
    ])
    min_video_size: int = 10 * 1024 * 1024  # 10 MB
    max_video_size: int = 50 * 1024 * 1024 * 1024  # 50 GB
    select_mode: str = "all"  # "all" or "largest"
    sources: List[Dict[str, Any]] = field(default_factory=list)
    enabled: bool = True
    # Background download tracking options (Option #2)
    max_download_time: int = 0  # 0 = unlimited
    stall_timeout: int = 300  # 5 minutes
    min_progress_percent: float = 0.0
    allow_partial_downloads: bool = False
    enable_background_downloads: bool = True
    # Metadata fetch options for decentralized torrents
    metadata_timeout: int = 120  # 2 minutes per attempt
    metadata_retries: int = 5  # Number of retry attempts
    # Connection limits
    max_connections: int = 50  # Max peer connections per torrent

    def __post_init__(self) -> None:
        if self.select_mode not in ("all", "largest"):
            raise ValueError(
                f"select_mode must be 'all' or 'largest', got {self.select_mode!r}"
            )

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "BitTorrentConfig":
        """Create config from dictionary."""
        return cls(
            download_dir=config.get("download_dir", "downloads/bittorrent"),
            max_concurrent_downloads=config.get("max_concurrent_downloads", 3),
            max_download_speed=config.get("max_download_speed", 0),
            max_upload_speed=config.get("max_upload_speed", 0),
            seed_ratio=config.get("seed_ratio", 0.0),
            seed_time=config.get("seed_time", 0),
            video_extensions=config.get("video_extensions", [
                ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"
            ]),
            min_video_size=config.get("min_video_size", 10 * 1024 * 1024),
            max_video_size=config.get("max_video_size", 50 * 1024 * 1024 * 1024),
            select_mode=str(config.get("select_mode", "all")).strip().lower(),
            sources=config.get("sources", []),
            enabled=config.get("enabled", True),
            max_download_time=config.get("max_download_time", 0),
            stall_timeout=config.get("stall_timeout", 300),
            min_progress_percent=config.get("min_progress_percent", 0.0),
            allow_partial_downloads=config.get("allow_partial_downloads", False),
            enable_background_downloads=config.get("enable_background_downloads", True),
            metadata_timeout=config.get("metadata_timeout", 120),
            metadata_retries=config.get("metadata_retries", 5),
            max_connections=config.get("max_connections", 50),
        )


class BitTorrentPlugin(ArchiverPlugin):
    """BitTorrent archiver plugin.
    
    This plugin discovers magnet links from configured sources and
    downloads them using libtorrent. It supports multiple sources
    through the ForumScraperSource interface.
    
    Example:
        plugin = BitTorrentPlugin(config={
            "sources": [
                {
                    "name": "sample_video",
                    "type": "forum",
                    "domain": "sample.com",
                    "forum_id": "1",
                    "max_threads": 5,
                }
            ]
        })
        await plugin.initialize()
        
        # Discover sources
        sources = await plugin.discover_sources()
        
        # Archive a source
        result = await plugin.archive(sources[0])
    """
    
    # Default timeouts for blocking operations (seconds)
    DEFAULT_DB_TIMEOUT = 30.0
    DEFAULT_LIBTORRENT_TIMEOUT = 60.0
    DEFAULT_FILE_IO_TIMEOUT = 30.0
    
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        """Initialize the BitTorrent plugin.
        
        Args:
            config: Plugin configuration dictionary
        """
        super().__init__(config)
        # Extract bittorrent-specific config.  Settings arrive either
        # nested (``plugins.plugin_settings.bittorrent``, used by the
        # daemon) or as the plugin-specific dict itself (``configure()``
        # / ``PluginManager.register(config=...)`` from the scheduler).
        raw = self._config
        plugins = raw.get("plugins", raw) if isinstance(raw, dict) else raw
        if hasattr(plugins, "plugin_settings"):
            bt_settings = plugins.plugin_settings.get("bittorrent", {})
        elif isinstance(plugins, dict) and isinstance(plugins.get("plugin_settings"), dict):
            bt_settings = plugins["plugin_settings"].get("bittorrent", {})
        elif isinstance(plugins, dict):
            bt_settings = plugins
        else:
            bt_settings = {}
        self._bt_config = BitTorrentConfig.from_dict(bt_settings)
        self._sources: List[MagnetSource] = []
        self._seen_infohashes: set[str] = set()
        self._archived_torrents: Dict[str, Dict[str, Any]] = {}
        # B5: resolve the download dir to an absolute path once, so every
        # stored ``torrent_downloads.output_path`` (and libtorrent save_path)
        # is absolute. A repo-relative path like
        # ``downloads/bittorrent/<name>/<file>.mp4`` breaks pipeline steps
        # that resolve it from a different working directory.
        self._download_dir = Path(self._bt_config.download_dir).resolve()
        self._session: Optional[lt.session] = None
        self._active_downloads: Dict[str, lt.torrent_handle] = {}
        self._db_session_factory = get_db_session
        # Thread pool executor for blocking operations
        self._executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        # Lock to prevent concurrent access to libtorrent session
        self._session_lock = asyncio.Lock()
        # Flag to indicate shutdown in progress
        self._shutdown_event = asyncio.Event()
        # Progress tracker and bridge for unified downloads table
        self._progress_tracker: Optional["DownloadProgressTracker"] = None
        self._adapters: Dict[str, "BitTorrentProgressAdapter"] = {}
    
    async def _run_in_executor(
        self,
        func: Callable[..., T],
        *args,
        timeout: Optional[float] = None,
        **kwargs
    ) -> T:
        """Run a blocking function in the thread pool executor with timeout.
        
        This prevents blocking the asyncio event loop and ensures that
        slow operations don't deadlock the CLI.
        
        Args:
            func: Blocking function to run
            *args: Positional arguments for the function
            timeout: Maximum time to wait (None for no timeout)
            **kwargs: Keyword arguments for the function
            
        Returns:
            Result of the function call
            
        Raises:
            asyncio.TimeoutError: If the operation times out
            Exception: If the function raises an exception
        """
        if self._executor is None:
            raise RuntimeError("Executor not initialized")
        
        # Handle both regular functions and methods that need self
        if args and hasattr(args[0], func.__name__ if hasattr(func, '__name__') else ''):
            # It's likely a bound method, args[0] is self
            loop = asyncio.get_event_loop()
            future = loop.run_in_executor(self._executor, functools.partial(func, *args, **kwargs))
        else:
            # Regular function call
            loop = asyncio.get_event_loop()
            future = loop.run_in_executor(self._executor, functools.partial(func, *args, **kwargs))
        
        if timeout is not None:
            return await asyncio.wait_for(future, timeout=timeout)
        return await future
    
    async def _run_db_operation(
        self,
        func: Callable[..., T],
        *args,
        timeout: Optional[float] = None,
        **kwargs
    ) -> T:
        """Run a database operation with timeout and error handling.
        
        Args:
            func: Database operation function
            *args: Positional arguments
            timeout: Timeout in seconds (default: DEFAULT_DB_TIMEOUT)
            **kwargs: Keyword arguments
            
        Returns:
            Result of the database operation
            
        Raises:
            asyncio.TimeoutError: If the operation times out
        """
        if timeout is None:
            timeout = self.DEFAULT_DB_TIMEOUT
        
        try:
            return await self._run_in_executor(func, *args, timeout=timeout, **kwargs)
        except asyncio.TimeoutError:
            logger.error(f"Database operation timed out after {timeout}s")
            raise
        except Exception as e:
            logger.warning(f"Database operation failed: {e}")
            raise
    
    @property
    def info(self) -> PluginInfo:
        """Get plugin information."""
        return PluginInfo(
            name="bittorrent",
            display_name="BitTorrent Archiver",
            version="1.0.0",
            description="Archive torrents from forum-based sources using libtorrent",
            author="Haven Team",
            media_types=["bittorrent"],
            capabilities=[
                PluginCapability.DISCOVER,
                PluginCapability.ARCHIVE,
                PluginCapability.METADATA,
                PluginCapability.HEALTH_CHECK,
            ],
        )
    
    def set_progress_tracker(self, tracker: "DownloadProgressTracker") -> None:
        """Set up progress tracker for unified downloads table.
        
        This should be called before initialize() to enable writing
        download progress to the unified downloads table.
        
        Args:
            tracker: The DownloadProgressTracker to report progress to
        """
        self._progress_tracker = tracker
        logger.debug("Progress tracker set for BitTorrent plugin")

    @staticmethod
    def _select_video_files(
        files: Any,
        video_extensions: List[str],
        min_video_size: int,
        max_video_size: int,
        mode: str = "all",
    ) -> tuple[List[tuple[int, str, int]], List[tuple[int, str, int, str]]]:
        """Split a torrent's video files into eligible and skipped (B10).

        Every video file whose size is within
        ``[min_video_size, max_video_size]`` is eligible — not only the
        largest.  Oversize and undersize videos are reported individually
        so the caller can archive the rest and explain each skip.

        With ``mode="largest"`` only the biggest eligible video is kept;
        the other eligible videos are reported as skipped so the reason
        is visible in the logs.

        Returns:
            ``(eligible, skipped)`` where eligible items are
            ``(index, file_path, size)`` and skipped items additionally
            carry a human-readable reason.
        """
        eligible: List[tuple[int, str, int]] = []
        skipped: List[tuple[int, str, int, str]] = []
        for i in range(files.num_files()):
            file_path = files.file_path(i)
            file_size = files.file_size(i)
            file_ext = os.path.splitext(file_path.lower())[1]
            if file_ext not in video_extensions:
                continue
            if file_size > max_video_size:
                skipped.append((
                    i, file_path, file_size,
                    f"exceeds max size limit of {max_video_size} bytes",
                ))
            elif file_size < min_video_size:
                skipped.append((
                    i, file_path, file_size,
                    f"below minimum size limit of {min_video_size} bytes",
                ))
            else:
                eligible.append((i, file_path, file_size))

        if mode == "largest" and len(eligible) > 1:
            largest = max(eligible, key=lambda item: item[2])
            for i, file_path, file_size in eligible:
                if i == largest[0]:
                    continue
                skipped.append((
                    i, file_path, file_size,
                    f"select_mode=largest: only the largest video is archived "
                    f"({largest[2]} bytes)",
                ))
            eligible = [largest]
        elif mode not in ("all", "largest"):
            raise ValueError(
                f"select_mode must be 'all' or 'largest', got {mode!r}"
            )
        return eligible, skipped

    @staticmethod
    def _torrent_titles(
        source: MediaSource,
        output_paths: List[str],
    ) -> Dict[str, str]:
        """Per-file titles for a multi-video torrent (B10).

        Single-file results keep the legacy shape (no per-file titles;
        the torrent title flows through ``source.metadata``).  With
        several files each gets ``"<torrent title> - <file name>"`` so
        the scheduler's per-file ``output_titles`` distinguishes them.
        """
        if len(output_paths) <= 1:
            return {}
        base = (source.metadata or {}).get("title") or source.title or ""
        titles: Dict[str, str] = {}
        for output_path in output_paths:
            name = Path(output_path).name
            titles[str(output_path)] = f"{base} - {name}".strip(" -") or name
        return titles

    @staticmethod
    def _result_metadata(
        source: MediaSource,
        output_paths: List[str],
        output_titles: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Build the ``ArchiveResult.metadata`` for one or more files."""
        metadata: Dict[str, Any] = dict(source.metadata or {})
        metadata["output_paths"] = list(output_paths)
        if output_titles:
            metadata["output_titles"] = dict(output_titles)
        return metadata

    async def initialize(self) -> None:
        """Initialize the plugin.
        
        Creates download directory, initializes sources, and sets up
        libtorrent session.
        
        Raises:
            RuntimeError: If libtorrent is not available
        """
        # Verify libtorrent is available
        try:
            logger.info(f"libtorrent version: {lt.version}")
        except ImportError:
            raise RuntimeError(
                "libtorrent not found. Please install it: "
                "https://www.libtorrent.org/"
            )
        
        # Create thread pool executor for blocking operations
        # Use max_workers=4 to prevent overwhelming the system
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="bittorrent_worker"
        )
        logger.info("Thread pool executor initialized")
        
        # Create download directory (in executor to avoid blocking)
        await self._run_in_executor(
            self._download_dir.mkdir,
            parents=True,
            exist_ok=True,
            timeout=self.DEFAULT_FILE_IO_TIMEOUT
        )

        # B5: validate stored output_paths at startup — any relative or
        # missing path is logged as an ERROR instead of being silently
        # missed by later pipeline steps.
        await self._validate_download_paths()

        # Initialize libtorrent session (in executor)
        def _create_session():
            settings = {
                'listen_interfaces': '0.0.0.0:6881,[::]:6881',
                'enable_dht': True,
                'enable_lsd': True,
                'enable_upnp': True,
                'enable_natpmp': True,
            }
            
            if self._bt_config.max_download_speed > 0:
                settings['download_rate_limit'] = self._bt_config.max_download_speed
            
            if self._bt_config.max_upload_speed > 0:
                settings['upload_rate_limit'] = self._bt_config.max_upload_speed
            
            return lt.session(settings)
        
        self._session = await self._run_in_executor(
            _create_session,
            timeout=self.DEFAULT_LIBTORRENT_TIMEOUT
        )
        logger.info("libtorrent session initialized")
        
        # Load sources from config
        await self._load_sources()
        
        # Load seen infohashes from persistent storage if available
        seen_file = self._download_dir / ".bittorrent_seen.json"
        if seen_file.exists():
            try:
                def _load_seen():
                    with open(seen_file, "r") as f:
                        return json.load(f)
                
                data = await self._run_in_executor(
                    _load_seen,
                    timeout=self.DEFAULT_FILE_IO_TIMEOUT
                )
                self._seen_infohashes = set(data.get("seen", []))
                self._archived_torrents = data.get("archived", {})
            except Exception as e:
                logger.warning(f"Could not load seen infohashes: {e}")
        
        # Restore pending downloads from database
        if self._bt_config.enable_background_downloads:
            await self._restore_pending_downloads()
        
        self._initialized = True
        logger.info("BitTorrentPlugin initialized successfully")

    async def _validate_download_paths(self) -> None:
        """Log an ERROR for any stored output_path that is relative/missing (B5).

        Called once from :meth:`initialize`. Historical rows written before
        the B5 fix can contain repo-relative paths like
        ``downloads/bittorrent/<name>/<file>.mp4``. Later pipeline steps
        resolve those from a different CWD and silently miss the file, so we
        surface them loudly here rather than hiding the failure.
        """
        try:
            def _collect():
                with self._db_session_factory() as session:
                    from haven_cli.database.models import TorrentDownload
                    return [
                        (d.id, d.infohash, d.status, d.output_path)
                        for d in session.query(TorrentDownload).all()
                        if d.output_path
                    ]

            rows = await self._run_db_operation(_collect)
        except Exception as e:
            logger.warning(f"Could not validate stored download paths: {e}")
            return

        problems = 0
        for row_id, infohash, status, output_path in rows:
            p = Path(output_path)
            if not p.is_absolute():
                logger.error(
                    "B5: torrent_downloads id=%s infohash=%s status=%s has a "
                    "RELATIVE output_path=%r; pipeline steps will not be able "
                    "to resolve it. Re-download or fix the row.",
                    row_id, infohash, status, output_path,
                )
                problems += 1
            elif not p.exists():
                logger.error(
                    "B5: torrent_downloads id=%s infohash=%s status=%s has an "
                    "absolute but MISSING output_path=%r; the file is not on "
                    "disk.",
                    row_id, infohash, status, output_path,
                )
                problems += 1

        if problems:
            logger.error(
                "B5: %d stored torrent download path(s) are relative or "
                "missing (see errors above).",
                problems,
            )
        else:
            logger.debug("B5: all stored torrent download paths are absolute and present")

    async def _persist_seen(self) -> None:
        """Atomically write ``.bittorrent_seen.json`` (B4).

        Uses a temp file + ``os.replace`` so a crash mid-write cannot leave a
        truncated seen file. Shared by :meth:`_mark_seen` (persist as soon as
        a download is enqueued) and :meth:`shutdown`.
        """
        seen_file = self._download_dir / ".bittorrent_seen.json"

        def _save_seen() -> None:
            tmp_file = seen_file.with_name(seen_file.name + ".tmp")
            with open(tmp_file, "w") as f:
                json.dump({
                    "seen": sorted(self._seen_infohashes),
                    "archived": self._archived_torrents,
                }, f, indent=2)
            os.replace(tmp_file, seen_file)

        try:
            await self._run_in_executor(
                _save_seen,
                timeout=self.DEFAULT_FILE_IO_TIMEOUT,
            )
        except Exception as e:
            logger.warning(f"Could not save seen infohashes: {e}")

    async def _mark_seen(self, infohash: str) -> None:
        """Mark an infohash as seen and persist immediately (B4).

        Called ONLY when a download is actually enqueued/started, never from
        :meth:`discover_sources`. Persisting here (rather than only at
        shutdown) means a ``kill -9`` mid-cycle does not lose the mark. A
        discovery-only probe that never enqueues a download never reaches
        this method, so it can never burn a torrent.
        """
        self._seen_infohashes.add(infohash)
        await self._persist_seen()
        logger.debug(f"Marked infohash {infohash} as seen (download enqueued)")

    async def _unmark_seen(self, infohash: str) -> None:
        """Remove an infohash from the seen set after a failed download (B4).

        A transient download failure must not permanently wedge a torrent:
        the next scheduled discovery must re-offer it so it can be retried
        without ``--force`` (docs/PRIORITIZED_PLAN.md success criteria).
        """
        if infohash in self._seen_infohashes:
            self._seen_infohashes.discard(infohash)
            await self._persist_seen()
            logger.info(
                f"Un-marked infohash {infohash} after failed download; "
                "it will be re-discovered on the next cycle"
            )

    async def shutdown(self) -> None:
        """Shutdown the plugin and save state."""
        # Signal shutdown in progress to unblock any waiting operations
        self._shutdown_event.set()
        
        # Save resume data for active downloads before stopping
        if self._bt_config.enable_background_downloads:
            for infohash, handle in list(self._active_downloads.items()):
                try:
                    if self._session and handle.is_valid():
                        # Save resume data for potential restart
                        await asyncio.wait_for(
                            self._save_resume_data(infohash, handle),
                            timeout=10.0
                        )
                        # Mark as paused in database
                        await asyncio.wait_for(
                            self._update_download_status(infohash, "paused"),
                            timeout=10.0
                        )
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout saving resume data for {infohash}")
                except Exception as e:
                    logger.warning(f"Error saving resume data for {infohash}: {e}")
        
        # Stop all active downloads with timeout
        for infohash, handle in list(self._active_downloads.items()):
            try:
                if self._session:
                    # Use executor to avoid blocking
                    await self._run_in_executor(
                        self._session.remove_torrent,
                        handle,
                        timeout=10.0
                    )
            except asyncio.TimeoutError:
                logger.warning(f"Timeout stopping torrent {infohash}")
            except Exception as e:
                logger.warning(f"Error stopping torrent {infohash}: {e}")
        
        self._active_downloads.clear()
        
        # Clear progress adapters
        self._adapters.clear()
        
        # Shutdown sources with timeout
        for source in self._sources:
            try:
                await asyncio.wait_for(source.shutdown(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning(f"Timeout shutting down source {source.name}")
            except Exception as e:
                logger.warning(f"Error shutting down source {source.name}: {e}")
        
        self._sources.clear()
        
        # Save seen infohashes to persistent storage. B4: _mark_seen already
        # persists on every enqueue; this final write also captures the
        # _archived_torrents dict updated at download completion.
        await self._persist_seen()
        
        # Shutdown thread pool executor
        if self._executor:
            try:
                # Use wait=False for faster shutdown, threads will finish naturally
                self._executor.shutdown(wait=False)
                logger.info("Thread pool executor shut down")
            except Exception as e:
                logger.warning(f"Error shutting down executor: {e}")
            finally:
                self._executor = None
        
        self._initialized = False
    
    async def _save_resume_data(
        self,
        infohash: str,
        handle: lt.torrent_handle,
    ) -> None:
        """Save resume data for a torrent to the database.
        
        Args:
            infohash: Torrent infohash
            handle: libtorrent torrent handle
        """
        try:
            # Check if handle is valid (in executor to avoid blocking)
            def _check_valid():
                return handle.is_valid() and handle.status().has_metadata
            
            is_valid = await self._run_in_executor(
                _check_valid,
                timeout=self.DEFAULT_LIBTORRENT_TIMEOUT
            )
            
            if not is_valid:
                return
            
            # Get resume data from libtorrent (in executor)
            def _get_resume_data():
                return lt.bencode(handle.write_resume_data())
            
            resume_data = await self._run_in_executor(
                _get_resume_data,
                timeout=self.DEFAULT_LIBTORRENT_TIMEOUT
            )
            resume_b64 = base64.b64encode(resume_data).decode('utf-8')
            
            # Database operation with timeout
            def _update_resume():
                with self._db_session_factory() as session:
                    repos = RepositoryFactory(session)
                    repos.torrents.update_resume_data(infohash, resume_b64)
            
            await self._run_db_operation(_update_resume)
            logger.debug(f"Saved resume data for {infohash}")
        except asyncio.TimeoutError:
            logger.warning(f"Timeout saving resume data for {infohash}")
        except Exception as e:
            logger.warning(f"Could not save resume data: {e}")
    
    async def health_check(self) -> bool:
        """Check if the plugin is healthy.
        
        Verifies libtorrent availability and download directory accessibility.
        
        Returns:
            True if plugin is healthy and operational
        """
        if not self._initialized:
            return False
        
        try:
            # Check libtorrent availability
            if not self._session:
                logger.error("Health check failed: libtorrent session not initialized")
                return False
            
            # Check download directory exists
            if not self._download_dir.exists():
                logger.error(f"Health check failed: download directory does not exist")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
    
    async def discover_sources(self) -> List[MediaSource]:
        """Discover magnet links from all configured sources.
        
        Uses the existing forum scanning code (ForumScraperSource) to discover
        magnet links from configured forum-based torrent sites.
        
        Returns:
            List of MediaSource objects representing discovered torrents
        """
        if not self._initialized:
            logger.error("Plugin not initialized")
            return []
        
        sources: List[MediaSource] = []
        
        # Discover from all enabled sources
        for source in self._sources:
            if not source.enabled:
                continue
            
            try:
                logger.info(f"Discovering from source: {source.name}")
                
                # Use the existing forum scanning code
                if isinstance(source, ForumScraperSource):
                    magnets = await source.search("")
                else:
                    magnets = await source.search("")
                
                logger.info(f"Found {len(magnets)} magnet links from {source.name}")

                # B4: discovery is side-effect-free. Filter out infohashes
                # that were *already* marked seen (i.e. genuinely downloaded
                # in a previous cycle) but NEVER add to _seen_infohashes here
                # and NEVER persist the seen file here. A discovery-only
                # process (probe, health check) must not consume a torrent:
                # the daemon must still see and download it afterwards.
                # The mark-seen write happens only when a download is
                # actually enqueued (see _mark_seen / archive()).
                new_magnets = [
                    m for m in magnets
                    if m.infohash not in self._seen_infohashes
                ]

                # Convert to MediaSource objects
                for magnet in new_magnets:
                    sources.append(MediaSource(
                        source_id=magnet.infohash,
                        media_type="bittorrent",
                        uri=magnet.uri,
                        title=magnet.title,
                        priority="medium",
                        metadata={
                            "infohash": magnet.infohash,
                            "size": magnet.size,
                            "seeders": magnet.seeders,
                            "leechers": magnet.leechers,
                            "category": magnet.category,
                            "source_name": magnet.source_name,
                            **magnet.metadata,
                        },
                    ))
                
            except Exception as e:
                logger.error(f"Error discovering from source {source.name}: {e}")
        
        logger.info(f"Discovered {len(sources)} new torrents")
        return sources
    
    async def archive(self, source: MediaSource) -> ArchiveResult:
        """Archive a torrent by downloading it with background tracking.
        
        Downloads the torrent using libtorrent with configured settings.
        Implements file selection to prioritize video files.
        Supports background download tracking with resume capability (Option #2).
        
        Args:
            source: MediaSource to archive
            
        Returns:
            ArchiveResult with success status and file information
        """
        if not self._initialized:
            return ArchiveResult(
                success=False,
                error="Plugin not initialized"
            )
        
        if source.media_type != "bittorrent":
            return ArchiveResult(
                success=False,
                error=f"Unsupported media type: {source.media_type}"
            )
        
        infohash = source.source_id
        
        # Check if already archived
        if infohash in self._archived_torrents:
            logger.info(f"Torrent {infohash} already archived")
            archived_info = self._archived_torrents[infohash]
            cached_paths = archived_info.get("output_paths") or (
                [archived_info["output_path"]] if archived_info.get("output_path") else []
            )
            metadata = self._result_metadata(source, cached_paths,
                                             archived_info.get("output_titles"))
            return ArchiveResult(
                success=True,
                output_path=cached_paths[0] if cached_paths else "",
                file_size=archived_info.get("file_size", 0),
                metadata={"already_archived": True, **metadata},
            )
        
        if not self._session:
            return ArchiveResult(
                success=False,
                error="libtorrent session not initialized"
            )
        
        # Check for existing download in database (resume capability)
        if self._bt_config.enable_background_downloads:
            existing = await self._get_download_from_db(infohash)
            if existing:
                if existing["status"] == "completed":
                    logger.info(f"Torrent {infohash} already completed")
                    cached_paths = existing.get("output_paths") or (
                        [existing["output_path"]] if existing.get("output_path") else []
                    )
                    metadata = self._result_metadata(source, cached_paths,
                                                     existing.get("output_titles"))
                    return ArchiveResult(
                        success=True,
                        output_path=cached_paths[0] if cached_paths else "",
                        file_size=existing.get("downloaded_size", 0),
                        metadata={"already_archived": True, **metadata},
                    )
                elif existing["status"] in ["downloading", "paused"]:
                    logger.info(f"Resuming existing download for {infohash}")
                    return await self._resume_download(existing, source)
        
        try:
            # Add magnet link to session (in executor to avoid blocking)
            def _add_magnet():
                params = lt.add_torrent_params()
                params.save_path = str(self._download_dir)
                params.storage_mode = lt.storage_mode_t.storage_mode_sparse
                params.url = source.uri
                params.max_connections = self._bt_config.max_connections
                handle = self._session.add_torrent(params)
                return handle
            
            handle = await self._run_in_executor(
                _add_magnet,
                timeout=self.DEFAULT_LIBTORRENT_TIMEOUT
            )
            
            async with self._session_lock:
                self._active_downloads[infohash] = handle
            
            logger.info(f"Downloading torrent {infohash}")
            
            # Wait for metadata to be available (with retries for decentralized torrents)
            logger.info("Waiting for torrent metadata...")
            
            async def _check_metadata():
                def _has_metadata():
                    return handle.has_metadata()
                return await self._run_in_executor(
                    _has_metadata,
                    timeout=10.0
                )
            
            metadata_timeout = self._bt_config.metadata_timeout
            metadata_retries = self._bt_config.metadata_retries
            retry_count = 0
            
            while retry_count < metadata_retries:
                metadata_elapsed = 0
                
                while metadata_elapsed < metadata_timeout:
                    if await _check_metadata():
                        break
                    
                    await asyncio.sleep(1)
                    metadata_elapsed += 1
                    
                    # Check if shutdown was requested
                    if self._shutdown_event.is_set():
                        raise RuntimeError("Shutdown requested during metadata wait")
                
                # Check if we got metadata
                if await _check_metadata():
                    break
                
                retry_count += 1
                if retry_count < metadata_retries:
                    logger.warning(
                        f"Metadata timeout after {metadata_timeout}s, "
                        f"retrying ({retry_count}/{metadata_retries})..."
                    )
                    # Brief pause before retry
                    await asyncio.sleep(2)
            
            # Final check after all retries
            if not await _check_metadata():
                raise RuntimeError(
                    f"Timeout waiting for torrent metadata after {metadata_retries} attempts "
                    f"({metadata_timeout * metadata_retries}s total)"
                )
            
            # Get torrent info (in executor)
            logger.info("Metadata received. Analyzing files...")
            
            def _get_torrent_info():
                return handle.get_torrent_info()
            
            torrent_info = await self._run_in_executor(
                _get_torrent_info,
                timeout=self.DEFAULT_LIBTORRENT_TIMEOUT
            )
            files = torrent_info.files()
            
            # B10: every video file lands in the archive, not only the
            # largest.  Size limits apply per video, so one oversize
            # video no longer blocks the eligible siblings.
            video_extensions = self._bt_config.video_extensions
            min_video_size = self._bt_config.min_video_size
            max_video_size = self._bt_config.max_video_size

            # Plan item 6: the real binding limit is MAX_UPLOAD_SIZE (~1 GiB)
            # on the projected encrypted+CAR size in synapse-wrapper.ts, not
            # the static 50 GB max_video_size. Derive the largest plaintext
            # video that still fits and cap BEFORE download bandwidth is spent.
            from haven_cli.pipeline.upload_limits import (
                max_plaintext_size,
                max_upload_size,
            )
            upload_cap_plain = max_plaintext_size(encryption_enabled=True)
            effective_max_video_size = min(max_video_size, upload_cap_plain)
            if upload_cap_plain < max_video_size:
                logger.info(
                    "Applying Filecoin upload size gate: max plaintext video "
                    "%d bytes (MAX_UPLOAD_SIZE=%d), below configured "
                    "max_video_size=%d.",
                    upload_cap_plain,
                    max_upload_size(),
                    max_video_size,
                )

            selected_files, skipped_files = self._select_video_files(
                files,
                video_extensions,
                min_video_size,
                effective_max_video_size,
                mode=self._bt_config.select_mode,
            )
            all_video_files = [
                (i, path, size)
                for i, path, size in selected_files
                + [(i, path, size) for i, path, size, _ in skipped_files]
            ]
            for _i, _path, _size, _reason in skipped_files:
                logger.info(f"Skipping video {_path} ({_size} bytes): {_reason}")

            if selected_files:
                logger.info(
                    "Selected %d of %d video file(s) within size limits: %s",
                    len(selected_files),
                    len(all_video_files),
                    ", ".join(path for _, path, _ in selected_files),
                )
            elif skipped_files:
                # No eligible video — fail, but name every skip so the
                # operator sees it was per-video, not one largest pick.
                detail = "; ".join(
                    f"'{path}' ({size} bytes) {_reason}" for _, path, size, _reason in skipped_files
                )
                logger.info(
                    "No video files within size limits: %s "
                    "(effective max=%d bytes, Filecoin "
                    "MAX_UPLOAD_SIZE=%d bytes, configured "
                    "max_video_size=%d bytes)",
                    detail,
                    effective_max_video_size,
                    max_upload_size(),
                    max_video_size,
                )
                skip_reason = (
                    f"No eligible video files in torrent: {detail}. "
                    f"Found {len(all_video_files)} video file(s)."
                )
                    
                # Create a skipped record in the database for TUI visibility
                await self._create_skipped_record(
                    infohash=infohash,
                    source_id=source.source_id,
                    title=source.metadata.get("title"),
                    magnet_uri=source.uri,
                    skip_reason=skip_reason,
                    total_size=sum(size for _, _, size in all_video_files),
                )

                # Clean up the torrent handle
                async with self._session_lock:
                    try:
                        await self._run_in_executor(
                            self._session.remove_torrent,
                            handle,
                            timeout=10.0
                        )
                        if infohash in self._active_downloads:
                            del self._active_downloads[infohash]
                    except Exception:
                        pass

                return ArchiveResult(
                    success=False,
                    error=skip_reason,
                )
            
            if not selected_files:
                # No video file found — fail rather than downloading non-video archives
                # TODO: Support extracting video from archives (e.g., .rar, .zip) if
                #       the torrent contains a single archive wrapping the video file.
                logger.warning("No video file found in torrent")
                
                # Clean up the torrent handle
                async with self._session_lock:
                    try:
                        await self._run_in_executor(
                            self._session.remove_torrent,
                            handle,
                            timeout=10.0
                        )
                        if infohash in self._active_downloads:
                            del self._active_downloads[infohash]
                    except Exception:
                        pass
                
                return ArchiveResult(
                    success=False,
                    error=f"No supported video files found in torrent. "
                          f"Only video files ({', '.join(self._bt_config.video_extensions)}) are supported. "
                          f"Archive extraction (rar, zip) is not yet supported."
                )
            
            # Set file priorities (in executor): B10 — every eligible
            # video downloads, not only the largest.
            selected_indexes = [i for i, _, _ in selected_files]
            selected_total_size = sum(size for _, _, size in selected_files)
            logger.info(
                "Selecting %d file(s): %s (%d bytes total)",
                len(selected_files),
                ", ".join(path for _, path, _ in selected_files),
                selected_total_size,
            )

            def _set_priorities():
                for i in range(files.num_files()):
                    if i in selected_indexes:
                        handle.file_priority(i, 4)  # Normal priority
                    else:
                        handle.file_priority(i, 0)  # Don't download
            
            await self._run_in_executor(
                _set_priorities,
                timeout=self.DEFAULT_LIBTORRENT_TIMEOUT
            )
            
            # Create download record in database.  The single
            # ``selected_file_index`` column keeps the first index for
            # back-compat; the full list rides in metadata (B10).
            if self._bt_config.enable_background_downloads:
                await self._create_download_record(
                    infohash=infohash,
                    source_id=source.source_id,
                    title=source.metadata.get("title"),
                    magnet_uri=source.uri,
                    total_size=selected_total_size,
                    selected_file_index=selected_indexes[0],
                    metadata={
                        **dict(source.metadata or {}),
                        "selected_file_indexes": list(selected_indexes),
                    },
                )
            
            # B4: the download is now being enqueued/started, which is the
            # moment this torrent is genuinely consumed. Only here do we
            # mark the infohash seen (and persist it) — never in discovery.
            await self._mark_seen(infohash)

            # Wait for download to complete with timeout and stall detection
            logger.info("Downloading selected file(s)...")
            result = await self._download_with_tracking(
                handle=handle,
                infohash=infohash,
                torrent_info=torrent_info,
                files=files,
                selected_file_index=selected_indexes[0],
                selected_file_indexes=selected_indexes,
                source=source,
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Error during download: {e}")

            # B4: the download did not complete, so release the seen mark.
            # Otherwise the next discovery would filter this torrent out and
            # a transient failure would wedge it forever.
            try:
                await self._unmark_seen(infohash)
            except Exception as unmark_err:
                logger.warning(f"Could not un-mark seen infohash {infohash}: {unmark_err}")

            # Update database status on error
            if self._bt_config.enable_background_downloads:
                try:
                    await asyncio.wait_for(
                        self._update_download_status(infohash, "failed", str(e)),
                        timeout=10.0
                    )
                except asyncio.TimeoutError:
                    pass
            
            # Clean up on error
            if infohash in self._active_downloads:
                try:
                    if self._session:
                        await self._run_in_executor(
                            self._session.remove_torrent,
                            self._active_downloads[infohash],
                            timeout=10.0
                        )
                except Exception:
                    pass
                async with self._session_lock:
                    if infohash in self._active_downloads:
                        del self._active_downloads[infohash]
            
            return ArchiveResult(
                success=False,
                error=str(e)
            )
    
    async def _download_with_tracking(
        self,
        handle: lt.torrent_handle,
        infohash: str,
        torrent_info: lt.torrent_info,
        files: lt.file_storage,
        selected_file_index: int = 0,
        source: Optional[MediaSource] = None,
        selected_file_indexes: Optional[List[int]] = None,
    ) -> ArchiveResult:
        """Download a file with timeout, stall detection, and database tracking.
        
        This method runs the download loop with periodic yielding to prevent
        blocking the asyncio event loop. All blocking operations are executed
        in the thread pool executor.
        
        Args:
            handle: libtorrent torrent handle
            infohash: Torrent infohash
            torrent_info: Torrent info
            files: File storage
            selected_file_index: Index of the file being downloaded
            source: Original MediaSource
            
        Returns:
            ArchiveResult with download result
        """
        if source is None:
            raise ValueError("_download_with_tracking requires a source")
        # B10: multi-file torrents pass every selected index; single-file
        # callers keep the legacy ``selected_file_index`` shape.
        download_indexes = (
            list(selected_file_indexes) if selected_file_indexes
            else [selected_file_index]
        )
        start_time = asyncio.get_event_loop().time()
        last_progress_time = start_time
        last_progress = 0.0
        is_complete = False
        loop_counter = 0
        
        async def _get_status():
            """Get torrent status in executor to avoid blocking."""
            def _status():
                return handle.status()
            return await self._run_in_executor(_status, timeout=10.0)
        
        async def _is_finished():
            """Check if download is finished in executor."""
            def _finished():
                return handle.status().is_finished
            return await self._run_in_executor(_finished, timeout=10.0)
        
        while not await _is_finished():
            # Check if shutdown was requested
            if self._shutdown_event.is_set():
                logger.info(f"Shutdown requested, pausing download {infohash}")
                await self._update_download_status(infohash, "paused")
                raise RuntimeError("Shutdown requested during download")
            
            status = await _get_status()
            current_time = asyncio.get_event_loop().time()
            elapsed = current_time - start_time
            
            # Update progress in database (don't block on this)
            if self._bt_config.enable_background_downloads:
                # Use create_task to avoid blocking the loop
                asyncio.create_task(
                    self._update_download_progress(
                        infohash=infohash,
                        progress=status.progress,
                        download_rate=status.download_rate,
                        upload_rate=status.upload_rate,
                        peers=status.num_peers,
                        seeds=status.num_seeds,
                        downloaded_size=status.total_done,
                    )
                )
            
            # Report to unified progress tracker if available
            if self._progress_tracker:
                try:
                    # Lazy import to avoid circular dependency
                    from haven_tui.data.download_tracker import BitTorrentProgressAdapter
                    
                    # Get or create adapter for this torrent
                    if infohash not in self._adapters:
                        self._adapters[infohash] = BitTorrentProgressAdapter(
                            tracker=self._progress_tracker,
                            infohash=infohash,
                            magnet_uri=source.uri,
                            title=source.metadata.get("title", ""),
                        )
                    
                    adapter = self._adapters[infohash]
                    adapter.report_status(status)
                except Exception as e:
                    logger.warning(f"Error reporting to progress tracker: {e}")
            
            # Check for timeout
            if self._bt_config.max_download_time > 0:
                if elapsed > self._bt_config.max_download_time:
                    logger.warning(
                        f"Download timeout after {elapsed:.0f}s "
                        f"(max: {self._bt_config.max_download_time}s)"
                    )
                    
                    if self._bt_config.allow_partial_downloads:
                        if status.progress >= self._bt_config.min_progress_percent / 100:
                            logger.warning(
                                f"Accepting partial result ({status.progress * 100:.2f}%)"
                            )
                            is_complete = True
                            break
                    
                    await self._update_download_status(
                        infohash, "failed", f"Timeout after {elapsed:.0f}s"
                    )
                    raise RuntimeError(f"Download timeout after {elapsed:.0f}s")
            
            # Check for stall (no progress)
            if status.progress > last_progress:
                last_progress_time = current_time
                last_progress = status.progress
            else:
                stall_duration = current_time - last_progress_time
                if stall_duration > self._bt_config.stall_timeout:
                    logger.warning(
                        f"Download stalled for {stall_duration:.0f}s "
                        f"(no progress since {last_progress * 100:.2f}%)"
                    )
                    
                    if self._bt_config.allow_partial_downloads:
                        if status.progress >= self._bt_config.min_progress_percent / 100:
                            logger.warning(
                                f"Accepting partial result due to stall ({status.progress * 100:.2f}%)"
                            )
                            is_complete = True
                            break
                    
                    await self._update_download_status(
                        infohash, "stalled", f"Stalled for {stall_duration:.0f}s"
                    )
                    raise RuntimeError(f"Download stalled for {stall_duration:.0f}s")
            
            # Log progress periodically (every 6 iterations = ~30 seconds)
            loop_counter += 1
            if loop_counter % 6 == 0:
                logger.info(
                    f"Progress: {status.progress * 100:.2f}% - "
                    f"Download rate: {status.download_rate / 1000:.2f} KB/s - "
                    f"Peers: {status.num_peers} - "
                    f"Elapsed: {elapsed:.0f}s"
                )
            
            # Yield control to the event loop - use shorter sleep for more responsiveness
            await asyncio.sleep(5)
        
        logger.info("Download complete. Stopping torrent...")

        # Get the output paths for the downloaded files (B5: ABSOLUTE,
        # B10: one per selected video).  Note: files.file_path()
        # already includes the torrent name as prefix.  ``.resolve()``
        # guarantees the stored paths never depend on the process CWD,
        # so pipeline steps can open them with no path joining.
        output_paths = [
            str((self._download_dir / files.file_path(i)).resolve())
            for i in download_indexes
        ]
        output_path = output_paths[0] if output_paths else ""

        # Verify every selected file exists (in executor)
        async def _file_exists(path: str) -> bool:
            def _exists() -> bool:
                return os.path.exists(path)
            return await self._run_in_executor(_exists, timeout=self.DEFAULT_FILE_IO_TIMEOUT)

        present_paths: List[str] = []
        missing_paths: List[str] = []
        for candidate in output_paths:
            if await _file_exists(candidate):
                present_paths.append(candidate)
            else:
                missing_paths.append(candidate)

        if not present_paths:
            await self._update_download_status(
                infohash, "failed", f"File not found: {missing_paths[0] if missing_paths else output_path}"
            )
            return ArchiveResult(
                success=False,
                error=f"Download completed but file not found: {missing_paths[0] if missing_paths else output_path}"
            )

        if missing_paths:
            # Partial completion still archives what arrived; the
            # scheduler only enqueues files that actually exist.
            logger.warning(
                "Download finished with %d of %d selected file(s) on disk; "
                "missing: %s",
                len(present_paths), len(output_paths), ", ".join(missing_paths),
            )
            output_paths = present_paths
            output_path = present_paths[0]

        # Get file sizes (in executor)
        async def _get_file_size(path: str) -> int:
            def _size() -> int:
                return os.path.getsize(path)
            return await self._run_in_executor(_size, timeout=self.DEFAULT_FILE_IO_TIMEOUT)

        file_size = 0
        for candidate in output_paths:
            file_size += await _get_file_size(candidate)

        output_titles = self._torrent_titles(source, output_paths)

        # Update database as completed
        if self._bt_config.enable_background_downloads:
            await self._complete_download(
                infohash,
                output_path,
                file_size,
                output_paths=output_paths,
                output_titles=output_titles,
                selected_file_indexes=download_indexes,
            )
        
        # Remove the torrent handle to stop seeding (in executor)
        async with self._session_lock:
            try:
                await self._run_in_executor(
                    self._session.remove_torrent,
                    handle,
                    timeout=self.DEFAULT_LIBTORRENT_TIMEOUT
                )
                if infohash in self._active_downloads:
                    del self._active_downloads[infohash]
            except Exception as e:
                logger.warning(f"Error removing torrent: {e}")
        
        logger.info("Torrent stopped. Not seeding.")
        
        # Mark as archived (B10: every selected file, not only the first).
        self._archived_torrents[infohash] = {
            "infohash": infohash,
            "title": source.metadata.get("title", ""),
            "output_path": output_path,
            "output_paths": list(output_paths),
            "output_titles": dict(output_titles),
            "file_size": file_size,
            "archived_at": asyncio.get_event_loop().time(),
        }

        return ArchiveResult(
            success=True,
            output_path=output_path,
            file_size=file_size,
            metadata=self._result_metadata(source, output_paths, output_titles),
        )
    
    async def _create_download_record(
        self,
        infohash: str,
        source_id: str,
        title: Optional[str],
        magnet_uri: str,
        total_size: int,
        selected_file_index: int,
        metadata: Dict[str, Any],
    ) -> None:
        """Create a download record in the database.
        
        Args:
            infohash: Torrent infohash
            source_id: MediaSource source_id
            title: Torrent title
            magnet_uri: Magnet URI
            total_size: Total size in bytes
            selected_file_index: Index of the file being downloaded
            metadata: Additional metadata
        """
        try:
            def _create_record():
                with self._db_session_factory() as session:
                    repos = RepositoryFactory(session)
                    existing = repos.torrents.get_by_infohash(infohash)
                    if not existing:
                        repos.torrents.create(
                            infohash=infohash,
                            source_id=source_id,
                            title=title,
                            magnet_uri=magnet_uri,
                            status="downloading",
                            total_size=total_size,
                            selected_file_index=selected_file_index,
                            metadata=metadata,
                        )
                        return True
                    return False
            
            created = await self._run_db_operation(_create_record)
            if created:
                logger.debug(f"Created download record for {infohash}")
        except asyncio.TimeoutError:
            logger.warning(f"Timeout creating download record for {infohash}")
        except Exception as e:
            logger.warning(f"Could not create download record: {e}")
    
    async def _create_skipped_record(
        self,
        infohash: str,
        source_id: str,
        title: Optional[str],
        magnet_uri: str,
        skip_reason: str,
        total_size: int,
    ) -> None:
        """Create a skipped download record in the database for TUI visibility.
        
        This is called when a torrent is skipped due to size limits,
        so the TUI can display it as skipped rather than having it
        disappear entirely.
        
        Args:
            infohash: Torrent infohash
            source_id: MediaSource source_id
            title: Torrent title
            magnet_uri: Magnet URI
            skip_reason: Reason the torrent was skipped
            total_size: Total size of the selected file in bytes
        """
        try:
            def _create_record():
                with self._db_session_factory() as session:
                    repos = RepositoryFactory(session)
                    existing = repos.torrents.get_by_infohash(infohash)
                    if not existing:
                        repos.torrents.create(
                            infohash=infohash,
                            source_id=source_id,
                            title=title,
                            magnet_uri=magnet_uri,
                            status="skipped",
                            total_size=total_size,
                            error_message=skip_reason,
                            metadata={"skip_reason": skip_reason},
                        )
                        return True
                    return False
            
            created = await self._run_db_operation(_create_record)
            if created:
                logger.debug(f"Created skipped record for {infohash}: {skip_reason[:50]}...")
        except asyncio.TimeoutError:
            logger.warning(f"Timeout creating skipped record for {infohash}")
        except Exception as e:
            logger.warning(f"Could not create skipped record: {e}")
    
    async def _update_download_progress(
        self,
        infohash: str,
        progress: float,
        download_rate: int,
        upload_rate: int,
        peers: int,
        seeds: int,
        downloaded_size: int,
    ) -> None:
        """Update download progress in the database.
        
        Args:
            infohash: Torrent infohash
            progress: Download progress (0.0 - 1.0)
            download_rate: Download rate in bytes/sec
            upload_rate: Upload rate in bytes/sec
            peers: Number of connected peers
            seeds: Number of connected seeds
            downloaded_size: Downloaded size in bytes
        """
        try:
            def _update_progress():
                with self._db_session_factory() as session:
                    repos = RepositoryFactory(session)
                    repos.torrents.update_progress(
                        infohash=infohash,
                        progress=progress,
                        download_rate=download_rate,
                        upload_rate=upload_rate,
                        peers=peers,
                        seeds=seeds,
                        downloaded_size=downloaded_size,
                    )
            
            await self._run_db_operation(_update_progress)
        except asyncio.TimeoutError:
            # Don't log timeout for progress updates to avoid spam
            pass
        except Exception as e:
            logger.warning(f"Could not update download progress: {e}")
    
    async def _update_download_status(
        self,
        infohash: str,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        """Update download status in the database.
        
        Args:
            infohash: Torrent infohash
            status: New status
            error_message: Error message if status is 'failed'
        """
        try:
            def _update_status():
                with self._db_session_factory() as session:
                    repos = RepositoryFactory(session)
                    repos.torrents.update_status(
                        infohash=infohash,
                        status=status,
                        error_message=error_message,
                    )
            
            await self._run_db_operation(_update_status)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout updating download status for {infohash}")
        except Exception as e:
            logger.warning(f"Could not update download status: {e}")
    
    async def _complete_download(
        self,
        infohash: str,
        output_path: str,
        file_size: int,
        output_paths: Optional[List[str]] = None,
        output_titles: Optional[Dict[str, str]] = None,
        selected_file_indexes: Optional[List[int]] = None,
    ) -> None:
        """Mark a download as completed in the database.

        Args:
            infohash: Torrent infohash
            output_path: Path to the downloaded file (first file)
            file_size: Total size in bytes (all files)
            output_paths: All downloaded file paths (B10)
            output_titles: Per-file titles keyed by path (B10)
            selected_file_indexes: All selected torrent indexes (B10)
        """
        try:
            def _complete():
                with self._db_session_factory() as session:
                    repos = RepositoryFactory(session)
                    existing = repos.torrents.get_by_infohash(infohash)
                    metadata = dict(existing.download_metadata) if existing and existing.download_metadata else {}
                    if output_paths is not None:
                        metadata["output_paths"] = list(output_paths)
                    if output_titles:
                        metadata["output_titles"] = dict(output_titles)
                    if selected_file_indexes is not None:
                        metadata["selected_file_indexes"] = list(selected_file_indexes)
                    repos.torrents.update(
                        infohash=infohash,
                        status="completed",
                        progress=1.0,
                        output_path=output_path,
                        downloaded_size=file_size,
                        completed_at=datetime.now(timezone.utc),
                        metadata=metadata,
                    )
            
            await self._run_db_operation(_complete)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout completing download record for {infohash}")
        except Exception as e:
            logger.warning(f"Could not complete download record: {e}")
    
    async def _get_download_from_db(self, infohash: str) -> Optional[Dict[str, Any]]:
        """Get a download record from the database.
        
        Args:
            infohash: Torrent infohash
            
        Returns:
            Dictionary with download data if found, None otherwise
        """
        try:
            def _get_download():
                with self._db_session_factory() as session:
                    repos = RepositoryFactory(session)
                    download = repos.torrents.get_by_infohash(infohash)
                    if download:
                        # Return as dict to avoid detached session issues
                        meta = dict(download.download_metadata) if download.download_metadata else {}
                        indexes = meta.get("selected_file_indexes")
                        if not isinstance(indexes, list) or not indexes:
                            indexes = (
                                [download.selected_file_index]
                                if download.selected_file_index is not None else []
                            )
                        paths = meta.get("output_paths")
                        if not isinstance(paths, list) or not paths:
                            paths = (
                                [download.output_path]
                                if download.output_path else []
                            )
                        titles = meta.get("output_titles")
                        if not isinstance(titles, dict):
                            titles = {}
                        return {
                            "infohash": download.infohash,
                            "status": download.status,
                            "output_path": download.output_path,
                            "output_paths": list(paths),
                            "output_titles": dict(titles),
                            "downloaded_size": download.downloaded_size,
                            "resume_data": download.resume_data,
                            "selected_file_index": download.selected_file_index,
                            "selected_file_indexes": list(indexes),
                        }
                    return None
            
            return await self._run_db_operation(_get_download)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout getting download record for {infohash}")
            return None
        except Exception as e:
            logger.warning(f"Could not get download record: {e}")
            return None
    
    async def _restore_pending_downloads(self) -> None:
        """Restore pending downloads from database on plugin startup.
        
        This method is called during initialization to resume any downloads
        that were in progress when the daemon was stopped.
        """
        try:
            def _get_pending():
                with self._db_session_factory() as session:
                    repos = RepositoryFactory(session)
                    downloads = repos.torrents.get_active()  # Gets downloading + paused
                    # Return as list of dicts to avoid detached session issues
                    pending_rows = []
                    for d in downloads:
                        meta = d.download_metadata if isinstance(d.download_metadata, dict) else {}
                        indexes = meta.get("selected_file_indexes")
                        if not isinstance(indexes, list) or not indexes:
                            indexes = (
                                [d.selected_file_index]
                                if d.selected_file_index is not None else []
                            )
                        pending_rows.append({
                            "infohash": d.infohash,
                            "magnet_uri": d.magnet_uri,
                            "output_path": d.output_path,
                            "resume_data": d.resume_data,
                            "progress": d.progress,
                            "selected_file_index": d.selected_file_index,
                            "selected_file_indexes": list(indexes),
                        })
                    return pending_rows
            
            pending = await self._run_db_operation(_get_pending)
            
            if not pending:
                logger.info("No pending downloads to restore")
                return
            
            logger.info(f"Restoring {len(pending)} pending downloads...")
            
            for download in pending:
                try:
                    infohash = download["infohash"]
                    magnet_uri = download.get("magnet_uri")
                    
                    if not magnet_uri:
                        logger.warning(f"No magnet URI for pending download {infohash[:16]}...")
                        continue
                    
                    # Check if .parts file exists (indicates partial download)
                    parts_file = self._download_dir / f".{infohash}.parts"
                    has_parts = parts_file.exists()
                    progress = download.get('progress', 0)
                    
                    # Skip if there was progress but no parts file (inconsistent state)
                    if progress > 0 and not has_parts:
                        logger.warning(f"Progress {progress*100:.1f}% but no parts file for {infohash[:16]}..., marking as failed")
                        await self._update_download_status(infohash, "failed")
                        continue
                    
                    # Add magnet to session with resume data
                    def _add_magnet():
                        p = lt.add_torrent_params()
                        p.save_path = str(self._download_dir)
                        p.storage_mode = lt.storage_mode_t.storage_mode_sparse
                        p.url = magnet_uri
                        p.max_connections = self._bt_config.max_connections
                        
                        if download.get("resume_data"):
                            try:
                                resume_data = base64.b64decode(download["resume_data"])
                                p.resume_data = resume_data
                            except Exception as e:
                                logger.warning(f"Could not decode resume data for {infohash[:16]}...: {e}")
                        
                        return self._session.add_torrent(p)
                    
                    handle = await self._run_in_executor(
                        _add_magnet,
                        timeout=self.DEFAULT_LIBTORRENT_TIMEOUT
                    )
                    
                    async with self._session_lock:
                        self._active_downloads[infohash] = handle
                    
                    if has_parts:
                        logger.info(f"Restored download: {infohash[:16]}... (progress: {progress*100:.1f}%)")
                    else:
                        logger.info(f"Restarted download: {infohash[:16]}... (no progress)")
                    
                except Exception as e:
                    logger.error(f"Failed to restore download {download.get('infohash', 'unknown')[:16]}...: {e}")
            
            logger.info(f"Restored {len(self._active_downloads)} pending downloads")
            
        except asyncio.TimeoutError:
            logger.warning("Timeout getting pending downloads")
        except Exception as e:
            logger.error(f"Failed to restore pending downloads: {e}")
    
    async def _resume_download(
        self,
        existing: Dict[str, Any],
        source: MediaSource,
    ) -> ArchiveResult:
        """Resume an existing download from the database.
        
        Args:
            existing: Dictionary with existing download data
            source: MediaSource
            
        Returns:
            ArchiveResult with download result
        """
        infohash = existing["infohash"]
        
        try:
            # Add magnet link to session (in executor)
            def _add_magnet():
                p = lt.add_torrent_params()
                p.save_path = str(self._download_dir)
                p.storage_mode = lt.storage_mode_t.storage_mode_sparse
                p.url = source.uri
                p.max_connections = self._bt_config.max_connections
                
                # Add resume data if available
                if existing.get("resume_data"):
                    try:
                        resume_data = base64.b64decode(existing["resume_data"])
                        p.resume_data = resume_data
                    except Exception as e:
                        logger.warning(f"Could not decode resume data: {e}")
                
                return self._session.add_torrent(p)
            
            handle = await self._run_in_executor(
                _add_magnet,
                timeout=self.DEFAULT_LIBTORRENT_TIMEOUT
            )
            
            async with self._session_lock:
                self._active_downloads[infohash] = handle
            
            logger.info(f"Resuming torrent {infohash}")
            
            # Wait for metadata (with retries for decentralized torrents)
            async def _check_metadata():
                def _has_metadata():
                    return handle.has_metadata()
                return await self._run_in_executor(
                    _has_metadata,
                    timeout=10.0
                )
            
            metadata_timeout = self._bt_config.metadata_timeout
            metadata_retries = self._bt_config.metadata_retries
            retry_count = 0
            
            while retry_count < metadata_retries:
                metadata_elapsed = 0
                
                while metadata_elapsed < metadata_timeout:
                    if await _check_metadata():
                        break
                    
                    await asyncio.sleep(1)
                    metadata_elapsed += 1
                    
                    if self._shutdown_event.is_set():
                        raise RuntimeError("Shutdown requested during resume")
                
                # Check if we got metadata
                if await _check_metadata():
                    break
                
                retry_count += 1
                if retry_count < metadata_retries:
                    logger.warning(
                        f"Metadata timeout during resume after {metadata_timeout}s, "
                        f"retrying ({retry_count}/{metadata_retries})..."
                    )
                    await asyncio.sleep(2)
            
            # Final check after all retries
            if not await _check_metadata():
                raise RuntimeError(
                    f"Timeout waiting for torrent metadata during resume after {metadata_retries} attempts"
                )
            
            # Get torrent info (in executor)
            def _get_torrent_info():
                return handle.get_torrent_info()
            
            torrent_info = await self._run_in_executor(
                _get_torrent_info,
                timeout=self.DEFAULT_LIBTORRENT_TIMEOUT
            )
            files = torrent_info.files()
            
            # Re-apply file selection (in executor).  B10: resume
            # downloads every originally-selected video, falling back
            # to the legacy single index for old rows.
            resume_indexes = existing.get("selected_file_indexes")
            if not isinstance(resume_indexes, list) or not resume_indexes:
                single = existing.get("selected_file_index")
                resume_indexes = [single] if single is not None else []
            if resume_indexes:

                def _set_priorities():
                    for i in range(files.num_files()):
                        if i in resume_indexes:
                            handle.file_priority(i, 4)
                        else:
                            handle.file_priority(i, 0)

                await self._run_in_executor(
                    _set_priorities,
                    timeout=self.DEFAULT_LIBTORRENT_TIMEOUT
                )
            
            # Continue download with tracking
            return await self._download_with_tracking(
                handle=handle,
                infohash=infohash,
                torrent_info=torrent_info,
                files=files,
                selected_file_index=resume_indexes[0] if resume_indexes else 0,
                selected_file_indexes=resume_indexes or None,
                source=source,
            )
            
        except Exception as e:
            logger.error(f"Error during resume: {e}")
            await self._update_download_status(infohash, "failed", str(e))
            
            if infohash in self._active_downloads:
                try:
                    if self._session:
                        await self._run_in_executor(
                            self._session.remove_torrent,
                            self._active_downloads[infohash],
                            timeout=10.0
                        )
                except Exception:
                    pass
                async with self._session_lock:
                    if infohash in self._active_downloads:
                        del self._active_downloads[infohash]
            
            return ArchiveResult(
                success=False,
                error=str(e)
            )
    
    async def get_download_status(self, infohash: str) -> Optional[Dict[str, Any]]:
        """Get the status of a download.
        
        Args:
            infohash: Torrent infohash
            
        Returns:
            Download status as dictionary, or None if not found
        """
        if not self._bt_config.enable_background_downloads:
            return None
        
        try:
            def _get_status():
                with self._db_session_factory() as session:
                    repos = RepositoryFactory(session)
                    download = repos.torrents.get_by_infohash(infohash)
                    if download:
                        return download.to_dict()
                    return None
            
            return await self._run_db_operation(_get_status)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout getting download status for {infohash}")
            return None
        except Exception as e:
            logger.warning(f"Could not get download status: {e}")
            return None
    
    async def list_active_downloads(self) -> List[Dict[str, Any]]:
        """List all active downloads.
        
        Returns:
            List of active download status dictionaries
        """
        if not self._bt_config.enable_background_downloads:
            return []
        
        try:
            def _list_active():
                with self._db_session_factory() as session:
                    repos = RepositoryFactory(session)
                    downloads = repos.torrents.get_active()
                    return [d.to_dict() for d in downloads]
            
            return await self._run_db_operation(_list_active)
        except asyncio.TimeoutError:
            logger.warning("Timeout listing active downloads")
            return []
        except Exception as e:
            logger.warning(f"Could not list active downloads: {e}")
            return []
    
    async def get_all_downloads(
        self,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Get all downloads with optional pagination.
        
        Args:
            limit: Maximum number of downloads to return
            offset: Number of downloads to skip
            
        Returns:
            List of download status dictionaries
        """
        if not self._bt_config.enable_background_downloads:
            return []
        
        try:
            def _get_all():
                with self._db_session_factory() as session:
                    repos = RepositoryFactory(session)
                    downloads = repos.torrents.get_all(limit=limit, offset=offset)
                    return [d.to_dict() for d in downloads]
            
            return await self._run_db_operation(_get_all)
        except asyncio.TimeoutError:
            logger.warning("Timeout getting all downloads")
            return []
        except Exception as e:
            logger.warning(f"Could not get downloads: {e}")
            return []
    
    def configure(self, config: Dict[str, Any]) -> None:
        """Update plugin configuration.
        
        Args:
            config: New configuration values to merge
        """
        super().configure(config)
        self._bt_config = BitTorrentConfig.from_dict(self._config)
        # Reload sources if plugin is already initialized
        # This ensures sources are loaded when configure is called after initialization
        if self._initialized and self._executor:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._load_sources())
                else:
                    loop.run_until_complete(self._load_sources())
            except Exception as e:
                logger.warning(f"Could not reload sources after configure: {e}")
    
    async def _load_sources(self) -> None:
        """Load sources from configuration."""
        self._sources.clear()
        
        for source_config in self._bt_config.sources:
            try:
                source_type = source_config.get("type", "forum")
                
                if source_type == "forum":
                    # Create forum source using existing forum scanning code
                    forum_config = ForumSourceConfig(
                        name=source_config.get("name", "unknown"),
                        enabled=source_config.get("enabled", True),
                        domain=source_config.get("domain", ""),
                        forum_id=source_config.get("forum_id", ""),
                        max_threads=source_config.get("max_threads", 10),
                        use_rmdown=source_config.get("use_rmdown", True),
                        infohash_pattern=source_config.get("infohash_pattern", r"【特徵全碼】：([A-Fa-f0-9]{40})"),
                        size_pattern=source_config.get("size_pattern", r"【影片大小】：([\d.]+)(GB|MB|KB)"),
                    )
                    
                    source = ForumScraperSource(config=forum_config)
                    await source.initialize()
                    self._sources.append(source)
                    logger.info(f"Loaded forum source: {source.name}")
                else:
                    logger.warning(f"Unknown source type: {source_type}")
                    
            except Exception as e:
                logger.error(f"Failed to load source {source_config.get('name')}: {e}")
    
    def get_config(self) -> Dict[str, Any]:
        """Get current plugin configuration.
        
        Returns:
            Current configuration as a dictionary
        """
        return {
            "download_dir": self._bt_config.download_dir,
            "max_concurrent_downloads": self._bt_config.max_concurrent_downloads,
            "max_download_speed": self._bt_config.max_download_speed,
            "max_upload_speed": self._bt_config.max_upload_speed,
            "seed_ratio": self._bt_config.seed_ratio,
            "seed_time": self._bt_config.seed_time,
            "video_extensions": self._bt_config.video_extensions,
            "min_video_size": self._bt_config.min_video_size,
            "max_video_size": self._bt_config.max_video_size,
            "select_mode": self._bt_config.select_mode,
            "sources": self._bt_config.sources,
            "enabled": self._bt_config.enabled,
            "max_download_time": self._bt_config.max_download_time,
            "stall_timeout": self._bt_config.stall_timeout,
            "min_progress_percent": self._bt_config.min_progress_percent,
            "allow_partial_downloads": self._bt_config.allow_partial_downloads,
            "enable_background_downloads": self._bt_config.enable_background_downloads,
            "metadata_timeout": self._bt_config.metadata_timeout,
            "metadata_retries": self._bt_config.metadata_retries,
            "max_connections": self._bt_config.max_connections,
        }
