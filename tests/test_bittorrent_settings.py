"""Tests for BitTorrent plugin config helpers."""

from __future__ import annotations

from haven_cli.bittorrent_plugin_init import (
    BITTORRENT_PLUGIN_NAME,
    build_bittorrent_plugin_settings,
    sync_bittorrent_plugin_lists,
)


def test_build_bittorrent_plugin_settings_disabled_clears_sources() -> None:
    settings = build_bittorrent_plugin_settings(
        enabled=False,
        download_dir="downloads/bittorrent",
        max_concurrent_downloads=3,
        sources=[{"name": "x", "type": "forum", "domain": "a.com", "forum_id": "1"}],
    )
    assert settings["enabled"] is False
    assert settings["sources"] == []


def test_build_bittorrent_plugin_settings_enabled_empty_sources() -> None:
    settings = build_bittorrent_plugin_settings(
        enabled=True,
        download_dir="/tmp",
        max_concurrent_downloads=2,
        sources=None,
    )
    assert settings["sources"] == []


def test_build_bittorrent_plugin_settings_enabled_with_sources() -> None:
    src = [
        {
            "name": "my_forum",
            "type": "forum",
            "domain": "example.com",
            "forum_id": "26",
            "max_threads": 5,
            "enabled": True,
        }
    ]
    settings = build_bittorrent_plugin_settings(
        enabled=True,
        download_dir="/data/bt",
        max_concurrent_downloads=4,
        sources=src,
    )
    assert settings["download_dir"] == "/data/bt"
    assert settings["max_concurrent_downloads"] == 4
    assert len(settings["sources"]) == 1
    assert settings["sources"][0]["domain"] == "example.com"
    assert settings["sources"][0] is not src[0]


def test_sync_bittorrent_plugin_lists_enables() -> None:
    enabled: list[str] = ["youtube"]
    disabled: list[str] = [BITTORRENT_PLUGIN_NAME]
    sync_bittorrent_plugin_lists(enabled, disabled, bittorrent_enabled=True)
    assert BITTORRENT_PLUGIN_NAME in enabled
    assert BITTORRENT_PLUGIN_NAME not in disabled


def test_sync_bittorrent_plugin_lists_disables() -> None:
    enabled: list[str] = ["youtube", BITTORRENT_PLUGIN_NAME]
    disabled: list[str] = []
    sync_bittorrent_plugin_lists(enabled, disabled, bittorrent_enabled=False)
    assert BITTORRENT_PLUGIN_NAME not in enabled
    assert BITTORRENT_PLUGIN_NAME in disabled
