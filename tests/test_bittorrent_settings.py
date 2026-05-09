"""Tests for BitTorrent plugin config helpers."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

import haven_cli.bittorrent_plugin_init as bittorrent_plugin_init
from haven_cli.bittorrent_plugin_init import (
    BITTORRENT_PLUGIN_NAME,
    BitTorrentJobInitSpec,
    build_bittorrent_plugin_settings,
    create_bittorrent_scheduled_job_if_absent,
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


def test_bit_torrent_job_init_spec_defaults() -> None:
    spec = BitTorrentJobInitSpec(schedule="0 * * * *")
    assert spec.schedule == "0 * * * *"
    assert spec.on_success == "archive_new"


def test_create_bittorrent_scheduled_job_if_absent_propagates_cron_validation_error() -> None:
    with patch.object(
        bittorrent_plugin_init,
        "_validate_cron",
        side_effect=ValueError("bad cron"),
    ):
        with pytest.raises(ValueError, match="bad cron"):
            create_bittorrent_scheduled_job_if_absent("0 * * * *")


def test_validate_cron_rejects_invalid_expression() -> None:
    pytest.importorskip("croniter")
    with pytest.raises(ValueError):
        bittorrent_plugin_init._validate_cron("not-a-valid-cron")
    bittorrent_plugin_init._validate_cron("0 * * * *")


def test_create_bittorrent_scheduled_job_if_absent_invalid_on_success() -> None:
    with patch.object(bittorrent_plugin_init, "_validate_cron", lambda _s: None):
        with pytest.raises(ValueError, match="Invalid on_success"):
            create_bittorrent_scheduled_job_if_absent(
                "0 * * * *",
                on_success="invalid_action",
            )


def test_create_bittorrent_scheduled_job_if_absent_skips_when_exists() -> None:
    existing = MagicMock()
    existing.plugin_name = BITTORRENT_PLUGIN_NAME
    mock_repo = MagicMock()
    mock_repo.get_all.return_value = [existing]

    @contextmanager
    def _sess() -> MagicMock:
        yield MagicMock()

    with patch.object(bittorrent_plugin_init, "_validate_cron", lambda _s: None):
        with patch.object(bittorrent_plugin_init, "_persist_new_bittorrent_job") as mock_persist:
            with patch("haven_cli.database.connection.get_db_session", _sess):
                with patch("haven_cli.database.repositories.JobRepository", return_value=mock_repo):
                    assert create_bittorrent_scheduled_job_if_absent("0 * * * *") == "skipped_exists"
    mock_persist.assert_not_called()


def test_create_bittorrent_scheduled_job_if_absent_creates_when_absent() -> None:
    mock_repo = MagicMock()
    mock_repo.get_all.return_value = []

    @contextmanager
    def _sess() -> MagicMock:
        yield MagicMock()

    with patch.object(bittorrent_plugin_init, "_validate_cron", lambda _s: None):
        with patch.object(bittorrent_plugin_init, "_persist_new_bittorrent_job") as mock_persist:
            with patch("haven_cli.database.connection.get_db_session", _sess):
                with patch("haven_cli.database.repositories.JobRepository", return_value=mock_repo):
                    assert create_bittorrent_scheduled_job_if_absent("0 * * * *") == "created"
    mock_persist.assert_called_once_with("0 * * * *", "archive_new")
