"""Phase 4 + 4b regression tests (BATCH_SYNC_REMEDIATION_PLAN.md).

These tests pin the config-plumbing contract for ``create_batched_pipeline``:

- **Phase 4** — ``flush_timeout`` and ``max_pending`` are accepted as
  parameters and threaded into the constructed ``BatchAccumulator``. The
  previous implementation only read ``batch_size`` and silently ignored
  the user's ``HAVEN_BATCH_SYNC_FLUSH_TIMEOUT`` / ``HAVEN_BATCH_SYNC_MAX_PENDING``
  values.

- **Phase 4b** — when ``flush_timeout`` is omitted, the new module-level
  default of 18000.0s (30 min) takes effect, not the historical 30s. The
  ``PipelineConfig.batch_sync_flush_timeout`` field default tracks the
  same value.
"""

from __future__ import annotations

import importlib

import pytest

from haven_cli.config import PipelineConfig
from haven_cli.pipeline.batch_accumulator import (
    BATCH_FLUSH_TIMEOUT_SECONDS,
    BatchAccumulator,
)
from haven_cli.pipeline.manager import create_batched_pipeline


class TestCreateBatchedPipelineParameters:
    """Phase 4: ``flush_timeout`` and ``max_pending`` thread through to accumulator."""

    def test_flush_timeout_threaded_to_accumulator(self):
        """A non-default ``flush_timeout`` must override the module default.

        Regression test for the silent-ignore bug: the user could set
        ``batch_sync_flush_timeout = 5.0`` in config, the wizard would
        echo it back, but the actual accumulator would still use the
        module default. Phase 4 fixes the wiring.
        """
        _, accumulator, _ = create_batched_pipeline(
            batch_size=4,
            flush_timeout=7.5,
            max_pending=11,
        )
        assert isinstance(accumulator, BatchAccumulator)
        assert accumulator._flush_timeout == 7.5
        assert accumulator._max_pending == 11
        assert accumulator._batch_size == 4

    def test_flush_timeout_omitted_uses_module_default(self):
        """Omitting ``flush_timeout`` must fall back to ``BATCH_FLUSH_TIMEOUT_SECONDS``.

        This is the contract that lets older callers (or external
        integrators not yet on Phase 4) keep working without breakage.
        After Phase 4b that default is 18000s, so the assertion here is
        also a Phase 4b invariant.
        """
        _, accumulator, _ = create_batched_pipeline(batch_size=2)
        assert accumulator._flush_timeout == BATCH_FLUSH_TIMEOUT_SECONDS
        # Cross-pin Phase 4b's specific value so a regression that
        # changes only the constant fails this test loudly rather than
        # silently slipping through.
        assert accumulator._flush_timeout == 18000.0

    def test_max_pending_omitted_uses_module_default(self):
        """Omitting ``max_pending`` falls back to ``BatchAccumulator``'s default of 50."""
        _, accumulator, _ = create_batched_pipeline(batch_size=2)
        assert accumulator._max_pending == 50

    def test_explicit_none_treated_same_as_omitted(self):
        """Passing ``flush_timeout=None`` is equivalent to omitting it.

        The new signature uses ``Optional[float]`` so callers that
        sometimes have a value and sometimes don't can pass ``None``
        without special-casing.
        """
        _, accumulator, _ = create_batched_pipeline(
            batch_size=2,
            flush_timeout=None,
            max_pending=None,
        )
        assert accumulator._flush_timeout == BATCH_FLUSH_TIMEOUT_SECONDS
        assert accumulator._max_pending == 50


class TestPipelineConfigDefaults:
    """Phase 4b: ``PipelineConfig`` field defaults must match the module constant."""

    def test_batch_sync_flush_timeout_default_is_18000(self):
        """``PipelineConfig().batch_sync_flush_timeout`` is 18000.0 (30 min).

        These two locations — the module-level constant in
        ``batch_accumulator.py`` and the dataclass default in
        ``config.py`` — must stay in sync. Drift between them is
        exactly the silent-misconfiguration bug Phase 4b is fixing.
        """
        config = PipelineConfig()
        assert config.batch_sync_flush_timeout == 18000.0
        # And — critically — match the module constant.
        assert config.batch_sync_flush_timeout == BATCH_FLUSH_TIMEOUT_SECONDS

    def test_batch_sync_size_default_is_10(self):
        """``batch_sync_size`` default unchanged at 10 across all phases."""
        config = PipelineConfig()
        assert config.batch_sync_size == 10

    def test_batch_sync_max_pending_default_is_50(self):
        """``batch_sync_max_pending`` default unchanged at 50 across all phases."""
        config = PipelineConfig()
        assert config.batch_sync_max_pending == 50

    def test_batch_sync_enabled_default_is_false(self):
        """Batch sync is opt-in. Default deployments stay on the inline path."""
        config = PipelineConfig()
        assert config.batch_sync_enabled is False


class TestEnvVarOverrideEndToEnd:
    """Phase 4: ``HAVEN_BATCH_SYNC_*`` env vars must reach the accumulator.

    This is the bug-of-record symptom: an operator sets
    ``HAVEN_BATCH_SYNC_FLUSH_TIMEOUT=5`` to make a slow CI environment
    flush singletons quickly, sees the value in ``haven config show``,
    and the daemon still sits on partial batches for the module default.
    """

    def test_env_var_round_trip_through_config_and_pipeline(self, monkeypatch):
        """Setting the env var → load_config → create_batched_pipeline preserves the value.

        We don't go through ``haven_cli.daemon.service`` here because
        wiring up a full daemon for one assertion would dwarf the test
        in setup. Instead we assemble the same chain: env var →
        ``load_config`` → ``getattr(config.pipeline, …)`` →
        ``create_batched_pipeline`` keyword.
        """
        # Need to reload the config module so the env var is picked up
        # on a fresh load. ``load_config`` re-reads ``os.environ`` each
        # call, so a simple monkeypatch + load is enough.
        monkeypatch.setenv("HAVEN_BATCH_SYNC_FLUSH_TIMEOUT", "12.5")
        monkeypatch.setenv("HAVEN_BATCH_SYNC_MAX_PENDING", "77")

        # Re-import to ensure no cached module-level config interferes.
        from haven_cli.config import load_config

        config = load_config()
        assert config.pipeline.batch_sync_flush_timeout == 12.5
        assert config.pipeline.batch_sync_max_pending == 77

        # Mirror what daemon/service.py + cli/jobs.py do.
        _, accumulator, _ = create_batched_pipeline(
            batch_size=config.pipeline.batch_sync_size,
            flush_timeout=config.pipeline.batch_sync_flush_timeout,
            max_pending=config.pipeline.batch_sync_max_pending,
        )
        assert accumulator._flush_timeout == 12.5
        assert accumulator._max_pending == 77
