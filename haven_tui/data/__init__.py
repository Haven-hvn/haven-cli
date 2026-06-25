"""Data access layer for Haven TUI.

Repository pattern implementation for querying pipeline data and
real-time download/torrent progress tracking. Cross-process pipeline
events arrive via ``haven_tui.data.sqlite_event_consumer.SqliteEventConsumer``
(wired in ``haven_tui.app``), which republishes onto the in-process
EventBus that ``haven_tui.core.state_manager.StateManager`` is
subscribed to.
"""

from haven_tui.data.repositories import (
    PipelineSnapshotRepository,
    DownloadRepository,
    JobHistoryRepository,
    SpeedHistoryRepository,
    AnalyticsRepository,
)
from haven_tui.data.download_tracker import (
    DownloadStatus,
    DownloadProgress,
    DownloadProgressTracker,
    YouTubeProgressAdapter,
    BitTorrentProgressAdapter,
    get_download_tracker,
    reset_download_tracker,
    format_bytes,
    format_duration,
)
from haven_tui.data.torrent_bridge import (
    BitTorrentProgressBridge,
)
from haven_tui.data.speed_aggregator import (
    SpeedAggregator,
    SpeedSample,
    SpeedAggregate,
)

__all__ = [
    "PipelineSnapshotRepository",
    "DownloadRepository",
    "JobHistoryRepository",
    "SpeedHistoryRepository",
    "AnalyticsRepository",
    "DownloadStatus",
    "DownloadProgress",
    "DownloadProgressTracker",
    "YouTubeProgressAdapter",
    "BitTorrentProgressAdapter",
    "BitTorrentProgressBridge",
    "get_download_tracker",
    "reset_download_tracker",
    "format_bytes",
    "format_duration",
    "SpeedAggregator",
    "SpeedSample",
    "SpeedAggregate",
]
