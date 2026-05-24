"""Batch sync processor — orchestrates batch attestation + entity creation.

Takes a batch of PipelineContext objects (from BatchAccumulator via FlushQueue),
runs batch attestation for gated content, then creates all entities in a single
Arkiv transaction.
"""

import hashlib
import logging
import os
from typing import Any

from haven_cli.pipeline.context import PipelineContext
from haven_cli.pipeline.flush_queue import PermanentError
from haven_cli.services.arkiv_sync import ArkivSyncClient, ArkivSyncConfig
from haven_cli.services.evm_utils import get_wallet_address_from_private_key
from haven_cli.services.haven_aol_icp import batch_attest_holding

logger = logging.getLogger(__name__)

HAVEN_AOL_MAX_PER_CALL = 20


class BatchSyncProcessor:
    """Processes a batch of pipeline contexts through attestation + entity creation.

    This is the 'processor' callable passed to FlushQueue[list[PipelineContext]].
    """

    def __init__(
        self,
        arkiv_config: ArkivSyncConfig,
        private_key: str | None = None,
    ) -> None:
        self._arkiv_config = arkiv_config
        self._private_key = private_key or os.environ.get("HAVEN_PRIVATE_KEY", "").strip()
        self._processed_batches = 0
        self._total_entities_created = 0

    async def __call__(self, batch: list[PipelineContext]) -> None:
        """Process a batch: attest (if gated) → create entities.

        Raises:
            PermanentError: On unrecoverable failures (bad config, invalid data)
            Exception: On transient failures (network, timeout) — will be retried
        """
        if not batch:
            return

        if not self._arkiv_config.enabled:
            raise PermanentError("Arkiv sync is disabled")

        if not self._arkiv_config.private_key:
            raise PermanentError("No private key configured for Arkiv sync")

        # Separate gated (needs attestation) from non-gated contexts
        gated, non_gated = self._partition_gated(batch)

        # Attest gated content in chunks of HAVEN_AOL_MAX_PER_CALL
        if gated:
            await self._attest_batch(gated)

        # Create all entities in a single Arkiv transaction
        try:
            results = self._create_entities(batch)
        except PermanentError:
            raise
        except Exception as exc:
            error_str = str(exc).lower()
            if any(p in error_str for p in ("invalid", "unauthorized", "missing")):
                raise PermanentError(f"Entity creation config error: {exc}") from exc
            raise

        # Update contexts and database
        for ctx, result in zip(batch, results):
            ctx.arkiv_entity_key = result["entity_key"]
            ctx.touch()
            await self._update_database(ctx, result["entity_key"])

        self._processed_batches += 1
        self._total_entities_created += len(results)

        logger.info(
            "Batch sync complete: %d entities created (%d attested)",
            len(results),
            len(gated),
        )

    @property
    def processed_batches(self) -> int:
        return self._processed_batches

    @property
    def total_entities_created(self) -> int:
        return self._total_entities_created

    def _partition_gated(
        self, batch: list[PipelineContext]
    ) -> tuple[list[PipelineContext], list[PipelineContext]]:
        """Split batch into gated (needs attestation) and non-gated."""
        from haven_cli.crypto.gate_metadata import is_gate_metadata

        gated: list[PipelineContext] = []
        non_gated: list[PipelineContext] = []

        for ctx in batch:
            if (
                ctx.encryption_metadata
                and is_gate_metadata(ctx.encryption_metadata.gate)
                and ctx.upload_result
                and ctx.upload_result.root_cid
            ):
                gated.append(ctx)
            else:
                non_gated.append(ctx)

        return gated, non_gated

    async def _attest_batch(self, gated: list[PipelineContext]) -> None:
        """Run batch attestation, chunking if > HAVEN_AOL_MAX_PER_CALL."""
        if not self._private_key:
            logger.warning("Cannot attest: no private key available")
            return

        evm_address = get_wallet_address_from_private_key(self._private_key)
        if evm_address == "unknown":
            logger.error("Cannot attest: could not derive EVM address")
            return

        # Process in chunks
        for i in range(0, len(gated), HAVEN_AOL_MAX_PER_CALL):
            chunk = gated[i : i + HAVEN_AOL_MAX_PER_CALL]
            cid_hashes: list[str] = []
            for ctx in chunk:
                cid_hash = hashlib.sha256(
                    ctx.upload_result.root_cid.encode()  # type: ignore[union-attr]
                ).hexdigest()
                cid_hashes.append(cid_hash)

            # Extract gate params from first context (all share same gate)
            gate = chunk[0].encryption_metadata.gate  # type: ignore[union-attr]
            try:
                threshold = int(gate.get("threshold", "1"))
            except (TypeError, ValueError):
                logger.error("Invalid gate threshold, skipping attestation chunk")
                continue

            if threshold <= 0:
                logger.warning("Gate threshold must be > 0, skipping attestation chunk")
                continue

            try:
                attestations = batch_attest_holding(
                    private_key=self._private_key,
                    chain=gate["chain"],
                    token_address=gate["tokenAddress"],
                    threshold=threshold,
                    cid_hashes=cid_hashes,
                    evm_address=evm_address,
                )
                # Assign attestations back to contexts
                for ctx, att in zip(chunk, attestations):
                    ctx.attestation = att
            except ValueError as exc:
                raise PermanentError(f"Attestation config error: {exc}") from exc
            except Exception as exc:
                # Attestation failure should NOT block entity creation.
                # Log and continue — entities will be created without attestation.
                logger.error(
                    "Batch attestation failed for chunk (skipped): %s", exc, exc_info=True,
                )

    def _create_entities(self, batch: list[PipelineContext]) -> list[dict[str, Any]]:
        """Create Arkiv entities for all contexts in the batch."""
        client = ArkivSyncClient(self._arkiv_config)
        return client.batch_sync_contexts(batch)

    async def _update_database(self, context: PipelineContext, entity_key: str) -> None:
        """Update database with entity key."""
        if context.video_id is None:
            return
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import VideoRepository

            with get_db_session() as session:
                repo = VideoRepository(session)
                video = repo.get_by_id(context.video_id)
                if video:
                    repo.update(video, arkiv_entity_key=entity_key)
        except Exception as e:
            logger.warning("Failed to update database with entity key: %s", e)
