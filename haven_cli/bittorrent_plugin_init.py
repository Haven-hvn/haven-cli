"""Helpers for persisting BitTorrent plugin defaults (e.g. ``haven config init``)."""

from __future__ import annotations

from typing import Any

BITTORRENT_PLUGIN_NAME = "bittorrent"


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
