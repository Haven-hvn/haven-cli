"""Tests for the daemon provider/dataset health loop (plan item 9, 2nd half)."""

import asyncio
import logging
from datetime import UTC, datetime

import pytest

from haven_cli.daemon.provider_health import (
    STATUS_DEGRADED,
    STATUS_ERROR,
    STATUS_LIVE,
    STATUS_UNKNOWN,
    HealthDeps,
    LatestPiece,
    ProviderHealthStatus,
    format_health_line,
    provider_health_loop,
    run_provider_health_check,
)


def _piece(**overrides):
    fields = dict(
        piece_cid="bafk-piece",
        root_cid="bafy-root",
        dataset_id="52242",
    )
    fields.update(overrides)
    return LatestPiece(**fields)


class TestRunCheck:
    @pytest.mark.asyncio
    async def test_live_when_retrievable(self):
        seen = {}

        async def probe(params):
            seen.update(params)
            return {"retrievable": True, "status": "active"}

        status = await run_provider_health_check(HealthDeps(
            lookup_latest=lambda: _piece(), probe=probe, provider_ids=(9,),
        ))
        assert status.status == STATUS_LIVE
        assert status.dataset_id == "52242"
        assert status.provider_ids == (9,)
        assert status.retrievable is True
        assert seen["pieceCid"] == "bafk-piece"

    @pytest.mark.asyncio
    async def test_degraded_when_not_retrievable(self):
        async def probe(params):
            return {"retrievable": False, "status": "pending"}

        status = await run_provider_health_check(HealthDeps(
            lookup_latest=lambda: _piece(), probe=probe, provider_ids=(9,),
        ))
        assert status.status == STATUS_DEGRADED
        assert status.retrievable is False

    @pytest.mark.asyncio
    async def test_unknown_when_no_piece(self):
        def probe(params):
            raise AssertionError("must not probe without a piece")

        status = await run_provider_health_check(HealthDeps(
            lookup_latest=lambda: None, probe=probe, provider_ids=(9,),
        ))
        assert status.status == STATUS_UNKNOWN

    @pytest.mark.asyncio
    async def test_error_when_db_lookup_fails(self):
        def lookup():
            raise RuntimeError("db down")

        status = await run_provider_health_check(HealthDeps(
            lookup_latest=lookup, probe=lambda p: {}, provider_ids=(9,),
        ))
        assert status.status == STATUS_ERROR
        assert "db down" in status.detail

    @pytest.mark.asyncio
    async def test_error_when_probe_fails(self):
        async def probe(params):
            raise RuntimeError("bridge down")

        status = await run_provider_health_check(HealthDeps(
            lookup_latest=lambda: _piece(), probe=probe, provider_ids=(9,),
        ))
        assert status.status == STATUS_ERROR
        assert status.piece_cid == "bafk-piece"

    @pytest.mark.asyncio
    async def test_never_raises(self):
        def lookup():
            raise RuntimeError("boom")

        try:
            await run_provider_health_check(HealthDeps(lookup_latest=lookup))
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"health check raised: {exc}")


class TestFormat:
    def test_line_names_dataset_and_provider(self):
        status = ProviderHealthStatus(
            status=STATUS_LIVE, dataset_id="52242", provider_ids=(9,),
            piece_cid="bafk-x", retrievable=True, detail="ok",
            checked_at=datetime(2026, 11, 10, tzinfo=UTC),
        )
        line = format_health_line(status)
        assert line.startswith("Provider health:")
        assert "dataset=52242" in line
        assert "providers=[9]" in line
        assert "status=live" in line
        assert "piece=bafk-x" in line


class TestLoop:
    @pytest.mark.asyncio
    async def test_loop_logs_and_stops(self, caplog):
        caplog.set_level(logging.INFO, logger="haven_cli.daemon.provider_health")
        async def probe(params):
            return {"retrievable": True, "status": "active"}

        deps = HealthDeps(
            lookup_latest=lambda: _piece(), probe=probe, provider_ids=(9,),
        )
        stop = asyncio.Event()
        task = asyncio.create_task(provider_health_loop(
            deps, interval_seconds=0.05, stop_event=stop, initial_delay=0.01,
        ))
        await asyncio.sleep(0.12)
        stop.set()
        await asyncio.wait_for(task, timeout=2)
        assert "Provider health: dataset=52242" in caplog.text
        assert "status=live" in caplog.text

    @pytest.mark.asyncio
    async def test_loop_disabled_when_interval_zero(self, caplog):
        caplog.set_level(logging.INFO, logger="haven_cli.daemon.provider_health")
        deps = HealthDeps(lookup_latest=lambda: None)
        await provider_health_loop(deps, interval_seconds=0)
        assert "disabled" in caplog.text
