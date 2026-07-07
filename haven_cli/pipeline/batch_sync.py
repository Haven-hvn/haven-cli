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

        # ── Gate: only sync contexts whose upload actually completed ──
        # The scheduler only feeds us contexts from pipelines that reported
        # success, but "success" is derived from the in-memory
        # ``context.upload_result`` / the ``videos`` row's CID, which can
        # persist from a *previous* successful run. A fresh re-upload
        # attempt can fail (e.g. Synapse connection timeout) while a stale
        # CID lingers, so the pipeline still looks successful and the
        # context reaches this processor — and we'd create an Arkiv entity
        # that references a file that is not reliably retrievable
        # off-cluster. An Arkiv entity is only meaningful if the upload for
        # *this* attempt completed, so we require an authoritative
        # completed UploadJob (status='completed' with a recorded remote
        # CID) plus a populated upload_result. Contexts that fail this gate
        # are dropped (fall through) so the rest of the batch still syncs —
        # one bad item must never stall or abort the batch.
        ready, skipped = self._partition_by_upload_status(batch)
        for ctx in skipped:
            logger.warning(
                "Skipping Arkiv sync for video %s: upload not completed "
                "(entity would reference an inaccessible file)",
                getattr(ctx, "video_id", "?"),
            )
        if not ready:
            logger.info(
                "Batch sync: no contexts with a completed upload; nothing to sync"
            )
            return
        batch = ready

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

    def _partition_by_upload_status(
        self, batch: list[PipelineContext]
    ) -> tuple[list[PipelineContext], list[PipelineContext]]:
        """Split a batch into contexts safe to sync vs. not.

        A context is syncable only if its upload for this attempt
        authoritatively completed: a populated ``upload_result`` (root CID
        + piece CID) AND a ``completed`` ``UploadJob`` row carrying a
        remote CID for the video. Anything else (failed upload, stalled
        job, stale CID from a prior run) is returned in the ``skipped``
        bucket so the caller can drop it without aborting the batch.
        """
        ready: list[PipelineContext] = []
        skipped: list[PipelineContext] = []
        for ctx in batch:
            if self._upload_completed(ctx):
                ready.append(ctx)
            else:
                skipped.append(ctx)
        return ready, skipped

    def _upload_completed(self, ctx: PipelineContext) -> bool:
        """Return True iff the upload for ``ctx`` authoritatively completed.

        The in-memory ``upload_result`` is the first gate: if the upload
        step failed this run, ``upload_result`` is never set and we refuse
        to sync. When a ``video_id`` is available we additionally consult
        the authoritative ``upload_jobs`` table — a ``completed`` job with a
        recorded remote CID is the only reliable proof the file landed on
        Filecoin and is retrievable off-cluster.

        If the DB is unreachable we cannot verify, so we trust the
        in-memory result rather than stall the whole batch on a hiccup.
        """
        upload_result = getattr(ctx, "upload_result", None)
        if (
            not upload_result
            or not getattr(upload_result, "root_cid", None)
            or not getattr(upload_result, "piece_cid", None)
        ):
            return False

        video_id = getattr(ctx, "video_id", None)
        if video_id is None:
            # No video row to verify against; trust the in-memory result.
            return True

        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import UploadJobRepository

            with get_db_session() as session:
                jobs = UploadJobRepository(session).get_by_video_id(video_id)
            return any(
                job.status == "completed" and bool(job.remote_cid)
                for job in jobs
            )
        except Exception as exc:  # Defensive: never let a DB blip stall the batch
            logger.warning(
                "Could not verify upload completion for video %s; "
                "trusting in-memory upload result to avoid stalling batch: %s",
                video_id, exc,
            )
            return True

    @property
    def total_entities_created(self) -> int:
        return self._total_entities_created

    def _partition_gated(
        self, batch: list[PipelineContext]
    ) -> tuple[list[PipelineContext], list[PipelineContext]]:
        """Split batch into gated (needs attestation) and non-gated.

        Uses :func:`is_gate_metadata_any` so v3 corpus-scoped contexts are
        recognized as gated (Bug 2 fix). Under the pre-fix behaviour every
        v3 context fell into the non-gated bucket and skipped attestation.
        """
        from haven_cli.crypto.gate_metadata import is_gate_metadata_any

        gated: list[PipelineContext] = []
        non_gated: list[PipelineContext] = []

        for ctx in batch:
            if (
                ctx.encryption_metadata
                and is_gate_metadata_any(ctx.encryption_metadata.gate)
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
                    cid_hashes=cid_hashes,           # submission order
                    evm_address=evm_address,
                )
                # v2 (Merkle root signing): canister returns one attestation dict per
                # submitted cidHash, in submission order. Defense-in-depth assertions
                # below catch any future canister bug before context.attestation is set.
                if len(attestations) != len(chunk):
                    raise PermanentError(
                        f"batch_attest_holding returned {len(attestations)} attestations "
                        f"for {len(chunk)} contexts"
                    )
                for ctx, att, expected_hash in zip(chunk, attestations, cid_hashes):
                    if att.get("cidHash") != expected_hash:
                        raise PermanentError(
                            f"Attestation cidHash mismatch — got {att.get('cidHash')!r}, "
                            f"expected {expected_hash!r}"
                        )
                    ctx.attestation = att
            except ValueError as exc:
                raise PermanentError(f"Attestation config error: {exc}") from exc
            except PermanentError:
                # Don't swallow PermanentError — propagate so the batch fails loudly.
                raise
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
