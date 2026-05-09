"""Helpers for persisting BitTorrent plugin defaults (e.g. ``haven config init``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

BITTORRENT_PLUGIN_NAME = "bittorrent"

_ALLOWED_ON_SUCCESS: frozenset[str] = frozenset(
    ("archive_new", "archive_all", "log_only"),
)

BitTorrentJobCreateResult = Literal["created", "skipped_exists"]


@dataclass(frozen=True)
class BitTorrentJobInitSpec:
    """User choices from ``haven config init`` for a recurring BitTorrent poll job."""

    schedule: str
    on_success: str = "archive_new"


def _validate_cron(schedule: str) -> None:
    from croniter import croniter

    croniter(schedule)


def _get_scheduler() -> Any:
    """Return :func:`~haven_cli.scheduler.job_scheduler.get_scheduler` (indirection for tests)."""
    from haven_cli.scheduler.job_scheduler import get_scheduler as _get

    return _get


def _persist_new_bittorrent_job(schedule: str, on_success: str) -> None:
    """Create and persist a recurring BitTorrent job (scheduler must not already have one)."""
    from haven_cli.scheduler.job_scheduler import OnSuccessAction, RecurringJob

    action = OnSuccessAction(on_success)
    scheduler = _get_scheduler()(load_jobs=True)
    job = RecurringJob(
        name="BitTorrent poll",
        plugin_name=BITTORRENT_PLUGIN_NAME,
        schedule=schedule,
        on_success=action,
    )
    scheduler.add_job(job)


def create_bittorrent_scheduled_job_if_absent(
    schedule: str,
    on_success: str = "archive_new",
) -> BitTorrentJobCreateResult:
    """Persist a recurring BitTorrent job if none exists for :data:`BITTORRENT_PLUGIN_NAME`.

    Args:
        schedule: Cron expression (five-field).
        on_success: ``archive_new``, ``archive_all``, or ``log_only``.

    Returns:
        ``created`` if a new job was stored, ``skipped_exists`` if one already exists.

    Raises:
        ValueError: Invalid cron or ``on_success`` value.
    """
    sched = schedule.strip()
    _validate_cron(sched)
    if on_success not in _ALLOWED_ON_SUCCESS:
        raise ValueError(f"Invalid on_success value: {on_success!r}")

    from haven_cli.database.connection import get_db_session
    from haven_cli.database.repositories import JobRepository

    with get_db_session() as session:
        job_repo = JobRepository(session)
        for row in job_repo.get_all():
            if row.plugin_name == BITTORRENT_PLUGIN_NAME:
                return "skipped_exists"

    _persist_new_bittorrent_job(sched, on_success)
    return "created"


def build_bittorrent_plugin_settings(
    *,
    enabled: bool,
    download_dir: str,
    max_concurrent_downloads: int,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the dict stored under ``[plugins.settings.bittorrent]`` in config TOML.

    Args:
        enabled: Whether the plugin should run scheduled BitTorrent jobs.
        download_dir: Directory for completed downloads (string path).
        max_concurrent_downloads: libtorrent session concurrency limit.
        sources: Forum / scraper source dicts; ignored when ``enabled`` is False.
    """
    src: list[dict[str, Any]] = []
    if enabled and sources:
        src = [dict(item) for item in sources]
    return {
        "enabled": enabled,
        "download_dir": download_dir,
        "max_concurrent_downloads": max_concurrent_downloads,
        "sources": src,
    }


def sync_bittorrent_plugin_lists(
    enabled_plugins: list[str],
    disabled_plugins: list[str],
    *,
    bittorrent_enabled: bool,
) -> None:
    """Keep ``enabled_plugins`` / ``disabled_plugins`` consistent with BitTorrent toggle.

    Mutates both lists in place.
    """
    if bittorrent_enabled:
        if BITTORRENT_PLUGIN_NAME in disabled_plugins:
            disabled_plugins[:] = [
                p for p in disabled_plugins if p != BITTORRENT_PLUGIN_NAME
            ]
        if BITTORRENT_PLUGIN_NAME not in enabled_plugins:
            enabled_plugins.append(BITTORRENT_PLUGIN_NAME)
    else:
        if BITTORRENT_PLUGIN_NAME in enabled_plugins:
            enabled_plugins[:] = [
                p for p in enabled_plugins if p != BITTORRENT_PLUGIN_NAME
            ]
        if BITTORRENT_PLUGIN_NAME not in disabled_plugins:
            disabled_plugins.append(BITTORRENT_PLUGIN_NAME)


__all__ = [
    "BITTORRENT_PLUGIN_NAME",
    "BitTorrentJobCreateResult",
    "BitTorrentJobInitSpec",
    "build_bittorrent_plugin_settings",
    "create_bittorrent_scheduled_job_if_absent",
    "sync_bittorrent_plugin_lists",
]
