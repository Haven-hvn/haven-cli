"""Phase 3 regression tests (BATCH_SYNC_REMEDIATION_PLAN.md).

``ArkivSyncClient.batch_sync_contexts`` must short-circuit ``len(contexts) == 1``
through ``sync_context()`` so the ``find_existing_entity()`` dedup runs. The
old behavior unconditionally opened a ``BatchBuilder`` and called
``batch.create_entity(...)``, which created a duplicate Arkiv entity for any
already-archived CID — the central bug that the remediation plan addresses.

These tests use mocks at the seam ``ArkivSyncClient._get_client()`` plus
``find_existing_entity`` so we can drive both the "existing" and "new" paths
without an actual Arkiv RPC.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from haven_cli.pipeline.context import PipelineContext, UploadResult
from haven_cli.services.arkiv_sync import ArkivSyncClient, ArkivSyncConfig


def _make_config(*, enabled: bool = True) -> ArkivSyncConfig:
    return ArkivSyncConfig(
        enabled=enabled,
        private_key="0x" + "1" * 64,
        rpc_url="https://example.invalid/arkiv",
        expires_in=86400,
    )


def _make_context(*, root_cid: str = "bafy_singleton_test") -> PipelineContext:
    """A minimally-populated context that satisfies ``_build_attributes``.

    ``_build_attributes`` SHA-256s ``upload_result.root_cid`` to derive
    ``cid_hash`` (used by ``find_existing_entity``), so the upload result
    must be present.
    """
    ctx = PipelineContext(source_path=Path("/tmp/single.mp4"))
    # Filecoin piece CIDs start with ``bafkzcib`` and are at least 59
    # chars long; ``require_piece_cid()`` validates both. We pad with
    # filler hex so ``_build_payload`` doesn't raise on the N≥2 path.
    ctx.upload_result = UploadResult(
        video_path="/tmp/single.mp4",
        root_cid=root_cid,
        piece_cid="bafkzcib" + "a" * 60,
    )
    return ctx


class TestSingletonShortCircuit:
    """Phase 3: ``batch_sync_contexts([ctx])`` must delegate to ``sync_context()``."""

    def test_singleton_calls_sync_context_not_batch_builder(self):
        """A 1-item batch must NOT open ``client.arkiv.batch()``.

        The previous code path always opened a BatchBuilder, even for one
        context, and skipped find_existing_entity dedup. This test pins
        the new behavior: zero BatchBuilder activity for N=1.
        """
        client = ArkivSyncClient(_make_config())

        sync_context_mock = MagicMock(
            return_value={
                "entity_key": "0xabc",
                "transaction_hash": "0xtx",
                "is_update": True,
            }
        )

        # Mock ``_get_client`` so any accidental BatchBuilder use would
        # surface as a clear assertion failure (we record every call).
        fake_arkiv_client = MagicMock()
        with patch.object(client, "sync_context", sync_context_mock), patch.object(
            client, "_get_client", return_value=fake_arkiv_client
        ):
            results = client.batch_sync_contexts([_make_context()])

        sync_context_mock.assert_called_once()
        # The crucial assertion: BatchBuilder was never opened.
        fake_arkiv_client.arkiv.batch.assert_not_called()

        assert results == [
            {
                "entity_key": "0xabc",
                "transaction_hash": "0xtx",
                "is_update": True,
            }
        ]

    def test_singleton_returns_empty_list_when_sync_context_returns_none(self):
        """Singleton path mirrors sync_context's ``None`` return as ``[]``.

        ``sync_context`` returns ``None`` when sync is disabled. The
        wrapping must produce ``[]`` (not ``[None]``) so downstream
        ``zip(batch, results)`` consumers don't trip on a None entry.
        """
        client = ArkivSyncClient(_make_config())

        with patch.object(client, "sync_context", return_value=None), patch.object(
            client, "_get_client"
        ) as get_client_mock:
            results = client.batch_sync_contexts([_make_context()])

        assert results == []
        # Even the get_client probe should be unnecessary for singletons —
        # the short-circuit returns before touching the Arkiv client.
        get_client_mock.assert_not_called()

    def test_singleton_propagates_is_update_key(self):
        """``sync_context`` returns ``is_update``; batch path historically didn't.

        The dispatch contract with ``BatchSyncProcessor`` only reads
        ``entity_key`` from each result, so the extra ``is_update`` key on
        the singleton path is harmless. This test pins that the key is
        passed through unchanged so callers that opt in to it (the dapp's
        future re-sync logic, debug logging, etc.) see the right value.
        """
        client = ArkivSyncClient(_make_config())

        sync_context_mock = MagicMock(
            return_value={
                "entity_key": "0xkey",
                "transaction_hash": "0xtx",
                "is_update": False,  # Pretend this was a brand-new create.
            }
        )

        with patch.object(client, "sync_context", sync_context_mock), patch.object(
            client, "_get_client"
        ):
            (result,) = client.batch_sync_contexts([_make_context()])

        assert result["is_update"] is False
        assert result["entity_key"] == "0xkey"

    def test_disabled_sync_returns_empty_before_short_circuit(self):
        """``enabled=False`` short-circuits at the very top, ahead of ``len==1``.

        We don't want disabled-sync calls to even touch ``sync_context``,
        because ``sync_context`` would also bail out — but using its bail
        path costs an extra method call and a logger.info line. The
        ``not self.config.enabled`` early-return predates Phase 3 and
        must still take precedence.
        """
        client = ArkivSyncClient(_make_config(enabled=False))
        sync_context_mock = MagicMock()

        with patch.object(client, "sync_context", sync_context_mock):
            results = client.batch_sync_contexts([_make_context()])

        assert results == []
        sync_context_mock.assert_not_called()


class TestNonSingletonBehaviorUnchanged:
    """N≥2 must keep using BatchBuilder — Phase 3 doesn't change this path."""

    def test_two_contexts_open_batch_builder(self):
        """Two contexts → one BatchBuilder context manager, no sync_context()."""
        client = ArkivSyncClient(_make_config())

        # Wire up a mock BatchBuilder context manager that records
        # create_entity calls and returns a fake receipt with two creates.
        fake_batch = MagicMock()
        fake_batch.__enter__ = MagicMock(return_value=fake_batch)
        fake_batch.__exit__ = MagicMock(return_value=False)
        fake_receipt = MagicMock()
        # Two CreateEvent stand-ins — only the .key matters here.
        create_events = [MagicMock(key="0xkey1"), MagicMock(key="0xkey2")]
        for e in create_events:
            # MagicMock attribute access returns another MagicMock by
            # default; we want str() on those to give back the literal.
            type(e).__str__ = lambda self, k=e.key: k
        fake_receipt.creates = create_events
        fake_batch.receipt = fake_receipt

        fake_arkiv_client = MagicMock()
        fake_arkiv_client.arkiv.batch.return_value = fake_batch

        sync_context_mock = MagicMock()

        with patch.object(client, "sync_context", sync_context_mock), patch.object(
            client, "_get_client", return_value=fake_arkiv_client
        ), patch(
            "haven_cli.services.arkiv_sync._extract_transaction_hash",
            return_value="0xbatch_tx",
        ):
            results = client.batch_sync_contexts(
                [_make_context(root_cid="cid_a"), _make_context(root_cid="cid_b")]
            )

        # The N≥2 path must not call sync_context.
        sync_context_mock.assert_not_called()
        # And it must open exactly one BatchBuilder.
        fake_arkiv_client.arkiv.batch.assert_called_once()
        # Two create_entity calls — one per context.
        assert fake_batch.create_entity.call_count == 2
        # All results share the batch transaction hash.
        assert len(results) == 2
        for r in results:
            assert r["transaction_hash"] == "0xbatch_tx"
            assert "entity_key" in r

    def test_empty_list_returns_empty_list_without_touching_client(self):
        """An empty input list returns ``[]`` before any Arkiv call."""
        client = ArkivSyncClient(_make_config())

        with patch.object(client, "_get_client") as get_client_mock, patch.object(
            client, "sync_context"
        ) as sync_context_mock:
            results = client.batch_sync_contexts([])

        assert results == []
        get_client_mock.assert_not_called()
        sync_context_mock.assert_not_called()
