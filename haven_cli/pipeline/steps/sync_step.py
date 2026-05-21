"""Sync step - Arkiv blockchain synchronization.

This step synchronizes video metadata to the Arkiv blockchain,
creating a permanent, queryable record of the archived content.
It:
1. Requests attestation from Haven-AOL canister (for gated content)
2. Builds the Arkiv entity payload
3. Checks for existing entities (update vs create)
4. Submits transaction to Arkiv
5. Records the entity key

The step is conditional and can be skipped via the arkiv_sync_enabled option.

Task 12: Writes progress to SyncJob and PipelineSnapshot tables.
"""

import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from haven_cli.database.connection import get_db_session
from haven_cli.database.repositories import VideoRepository

logger = logging.getLogger(__name__)
from haven_cli.pipeline.context import PipelineContext
from haven_cli.pipeline.events import EventType
from haven_cli.pipeline.results import ErrorCategory, StepError, StepResult
from haven_cli.pipeline.step import ConditionalStep
from haven_cli.services.arkiv_sync import (
    ArkivSyncClient,
    ArkivSyncConfig,
    InsufficientGasError,
    build_arkiv_config,
)
from haven_cli.services.blockchain_network import get_network_config
from haven_cli.services.evm_utils import is_non_golem_base_transaction_error


class SyncStep(ConditionalStep):
    """Pipeline step for Arkiv blockchain synchronization.
    
    This step creates or updates an entity on the Arkiv blockchain
    with the video's metadata, enabling decentralized discovery
    and verification of archived content.
    
    Emits:
        - SYNC_REQUESTED event when starting
        - SYNC_COMPLETE event on success
    
    Output data:
        - entity_key: Arkiv entity key
        - transaction_hash: Blockchain transaction hash
        - is_update: Whether this was an update to existing entity
    
    Task 12: Creates/updates SyncJob and PipelineSnapshot records.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the sync step.
        
        Args:
            config: Step configuration (passed to base class)
        """
        super().__init__(config=config)
        self._job_id: Optional[int] = None
        self._start_time: Optional[float] = None
    
    @property
    def name(self) -> str:
        """Step identifier."""
        return "sync"
    
    @property
    def enabled_option(self) -> str:
        """Context option that enables this step."""
        return "arkiv_sync_enabled"
    
    @property
    def default_enabled(self) -> bool:
        """Arkiv sync is disabled by default."""
        return False
    
    @property
    def max_retries(self) -> int:
        """Blockchain operations can retry on transient errors."""
        return 3
    
    async def should_skip(self, context: PipelineContext) -> bool:
        """Skip if sync is disabled or no upload result available."""
        # Check if sync is enabled
        if await super().should_skip(context):
            return True
        
        # Skip if no upload result (nothing to sync)
        if context.upload_result is None:
            return True
        
        # Skip if no CID (nothing to reference on-chain)
        if not context.upload_result.root_cid:
            return True
        
        return False
    
    async def _get_skip_reason(self, context: PipelineContext) -> str:
        """Provide specific skip reason."""
        if context.upload_result is None:
            return "No upload result to sync"
        if not context.upload_result.root_cid:
            return "No root CID available for sync"
        return await super()._get_skip_reason(context)
    
    async def process(self, context: PipelineContext) -> StepResult:
        """Process Arkiv synchronization.
        
        Task 12: Creates SyncJob and updates PipelineSnapshot.
        
        Args:
            context: Pipeline context with upload result
            
        Returns:
            StepResult with sync details
        """
        video_path = context.video_path
        self._start_time = time.time()
        
        # Create SyncJob record for tracking
        if context.video_id:
            self._job_id = await self._create_sync_job(context.video_id)
            await self._update_pipeline_snapshot(context.video_id, "sync", 0)
        
        # Emit sync requested event
        await self._emit_event(EventType.SYNC_REQUESTED, context, {
            "video_path": video_path,
            "cid": context.cid,
        })
        
        try:
            # ── Attestation: request canister-signed proof for gated content ──
            await self._request_attestation(context)
            
            # Get Arkiv configuration
            arkiv_config = self._get_arkiv_config()
            
            # Initialize Arkiv client
            client = ArkivSyncClient(arkiv_config)
            
            # Sync to Arkiv
            result = client.sync_context(context)
            
            if result is None:
                # Sync is disabled or no result
                return StepResult.ok(
                    self.name,
                    skipped=True,
                    reason="Arkiv sync disabled",
                )
            
            # Store entity key in context
            entity_key = result["entity_key"]
            transaction_hash = result.get("transaction_hash", "")
            is_update = result.get("is_update", False)
            block_number = result.get("block_number")
            gas_used = result.get("gas_used")
            
            context.arkiv_entity_key = entity_key
            
            # Update database with entity key
            await self._update_database(context, entity_key)
            
            # Mark job as completed
            if self._job_id and context.video_id:
                await self._complete_sync_job(
                    self._job_id, transaction_hash, block_number, gas_used
                )
                await self._update_pipeline_snapshot(context.video_id, "sync", 100, status="completed")
            
            # Emit sync complete event
            await self._emit_event(EventType.SYNC_COMPLETE, context, {
                "video_path": video_path,
                "entity_key": entity_key,
                "transaction_hash": transaction_hash,
                "is_update": is_update,
            })
            
            return StepResult.ok(
                self.name,
                entity_key=entity_key,
                transaction_hash=transaction_hash,
                is_update=is_update,
            )
            
        except InsufficientGasError as e:
            # Special handling for gas errors
            error = StepError(
                code="INSUFFICIENT_GAS",
                message=str(e),
                category=ErrorCategory.PERMANENT,
                details={
                    "wallet_address": e.wallet_address,
                    "chain_name": e.chain_name,
                    "token_symbol": e.native_token_symbol,
                }
            )
            
            # Mark job as failed
            if self._job_id and context.video_id:
                await self._fail_sync_job(self._job_id, str(e))
                await self._update_pipeline_snapshot(
                    context.video_id, "sync", 0, status="failed", error=str(e)
                )
            
            return StepResult.fail(self.name, error)
            
        except Exception as e:
            category = self._categorize_error(e)
            error_msg = str(e)
            
            # Mark job as failed
            if self._job_id and context.video_id:
                await self._fail_sync_job(self._job_id, error_msg)
                await self._update_pipeline_snapshot(
                    context.video_id, "sync", 0, status="failed", error=error_msg
                )
            
            return StepResult.fail(
                self.name,
                StepError.from_exception(e, code="SYNC_ERROR", category=category),
            )
    
    def _get_arkiv_config(self) -> ArkivSyncConfig:
        """Get Arkiv configuration from config and environment.
        
        Priority:
        1. Explicit config values from self._config
        2. Network mode defaults
        3. Environment variables (via build_arkiv_config)
        
        Returns:
            ArkivSyncConfig instance
        """
        arkiv_mode = self._config.get(
            "arkiv_network_mode",
            self._config.get("network_mode", "testnet"),
        )
        network_config = get_network_config(arkiv_mode)
        
        # Get values from config if available
        private_key = self._config.get("arkiv_private_key")
        
        # Use config value, network default, or environment variable
        rpc_url = self._config.get("arkiv_rpc_url") or network_config.arkiv_rpc_url
        
        enabled = self._config.get("arkiv_sync_enabled")
        expires_in = self._config.get("arkiv_expiration_seconds")
        
        # Build config (will use environment variables as fallback)
        return build_arkiv_config(
            private_key=private_key,
            rpc_url=rpc_url,
            enabled=enabled,
            expires_in=expires_in,
            network_mode=arkiv_mode,
        )
    
    async def _request_attestation(self, context: PipelineContext) -> None:
        """Request a canister-signed attestation for gated content.

        The attestation proves the uploader held the required token at upload time.
        It is stored in ``context.attestation`` and included in the Arkiv payload.

        Attestation is non-blocking: failure logs an error (with traceback) but
        does not abort the sync step. The upload proceeds without attestation in
        that case.
        """
        # Only attest gated (encrypted) content
        if not context.encryption_metadata:
            return

        from haven_cli.crypto.gate_metadata import is_gate_metadata

        gate = context.encryption_metadata.gate
        if not is_gate_metadata(gate):
            return

        # Need root CID to compute cid_hash
        if not context.upload_result or not context.upload_result.root_cid:
            logger.warning("Cannot attest: no root CID available yet")
            return

        # Need private key to sign EIP-712
        private_key = os.environ.get("HAVEN_PRIVATE_KEY", "").strip()
        if not private_key:
            logger.warning("Cannot attest: HAVEN_PRIVATE_KEY not set")
            return

        cid_hash = hashlib.sha256(
            context.upload_result.root_cid.encode()
        ).hexdigest()

        try:
            threshold = int(gate.get("threshold", "1"))
        except (TypeError, ValueError) as exc:
            logger.error(
                "Attestation skipped: invalid gate threshold %r (%s)",
                gate.get("threshold"),
                exc,
            )
            return

        if threshold <= 0:
            # Canister rejects threshold == 0 with InvalidThreshold; skip rather
            # than make a doomed request.
            logger.warning(
                "Attestation skipped: gate threshold must be > 0 (got %d)",
                threshold,
            )
            return

        try:
            from haven_cli.services.evm_utils import get_wallet_address_from_private_key
            from haven_cli.services.haven_aol_icp import attest_holding

            evm_address = get_wallet_address_from_private_key(private_key)
            if evm_address == "unknown":
                logger.error(
                    "Attestation skipped: could not derive EVM address from HAVEN_PRIVATE_KEY"
                )
                return

            logger.info(
                "Requesting attestation: chain=%s token=%s threshold=%d cidHash=%s evmAddress=%s",
                gate["chain"],
                gate["tokenAddress"],
                threshold,
                cid_hash,
                evm_address,
            )

            attestation = attest_holding(
                private_key=private_key,
                chain=gate["chain"],
                token_address=gate["tokenAddress"],
                threshold=threshold,
                cid_hash=cid_hash,
                evm_address=evm_address,
            )

            context.attestation = attestation
            logger.info(
                "Got attestation for gate_token=%s balance=%s",
                gate["tokenAddress"],
                attestation.get("balanceAtCheck"),
            )

        except Exception as exc:
            # Attestation failure should NOT block upload — but it should be
            # loudly visible. Previously this used logger.warning without
            # exc_info, which made canister rejections nearly invisible.
            logger.error(
                "Attestation failed (upload will proceed without it): %s",
                exc,
                exc_info=True,
            )

    async def _update_database(
        self,
        context: PipelineContext,
        entity_key: str,
    ) -> None:
        """Update database with entity key.
        
        Args:
            context: Pipeline context with video_id
            entity_key: Arkiv entity key to store
        """
        # Need video_id in context to update
        if context.video_id is None:
            # Try to find by source path
            try:
                with get_db_session() as session:
                    repo = VideoRepository(session)
                    video = repo.get_by_source_path(context.video_path)
                    if video:
                        repo.update(video, arkiv_entity_key=entity_key)
            except Exception as e:
                # Log but don't fail the step
                logger.warning("Failed to update database with Arkiv entity key: %s", e)
            return
        
        try:
            with get_db_session() as session:
                repo = VideoRepository(session)
                video = repo.get_by_id(context.video_id)
                if video:
                    repo.update(video, arkiv_entity_key=entity_key)
        except Exception as e:
            # Log but don't fail the step
            logger.warning("Failed to update database with Arkiv entity key: %s", e)
    
    def _categorize_error(self, error: Exception) -> ErrorCategory:
        """Categorize error for retry decisions.
        
        Args:
            error: The exception to categorize
            
        Returns:
            ErrorCategory for retry logic
        """
        error_str = str(error).lower()
        error_type = type(error).__name__.lower()
        
        # Transient errors - can retry
        transient_patterns = [
            "timeout",
            "connection",
            "network",
            "temporarily",
            "rate limit",
            "too many requests",
        ]
        if any(p in error_str for p in transient_patterns):
            return ErrorCategory.TRANSIENT

        if is_non_golem_base_transaction_error(error):
            return ErrorCategory.PERMANENT
        
        # Configuration errors - permanent
        permanent_patterns = [
            "invalid",
            "unauthorized",
            "forbidden",
            "not found",
            "private key",
            "missing",
        ]
        if any(p in error_str for p in permanent_patterns):
            return ErrorCategory.PERMANENT
        
        # Web3/EVM errors
        if "web3" in error_type or "rpc" in error_type:
            # RPC errors might be transient
            return ErrorCategory.TRANSIENT
        
        return ErrorCategory.UNKNOWN
    
    async def on_skip(self, context: PipelineContext, reason: str) -> None:
        """Handle step skip.
        
        Args:
            context: Pipeline context
            reason: Skip reason
        """
        logger.info("Sync step skipped: %s", reason)
        
        # Create a skipped SyncJob record so TUI shows correct status
        if context.video_id:
            await self._create_skipped_sync_job(context.video_id, reason)
    
    # =========================================================================
    # Task 12: Job tracking helper methods
    # =========================================================================
    
    async def _create_sync_job(self, video_id: int) -> Optional[int]:
        """Create a SyncJob record for tracking.
        
        Args:
            video_id: Video ID
            
        Returns:
            Job ID or None if creation failed
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import SyncJobRepository
            
            with get_db_session() as session:
                repo = SyncJobRepository(session)
                job = repo.create(
                    video_id=video_id,
                    status="syncing",
                )
                logger.debug(f"Created SyncJob {job.id} for video {video_id}")
                return job.id
        except Exception as e:
            logger.warning(f"Failed to create SyncJob: {e}")
            return None
    
    async def _create_skipped_sync_job(
        self,
        video_id: int,
        reason: str,
    ) -> Optional[int]:
        """Create a SyncJob record marked as skipped.
        
        This is called when sync is skipped due to configuration
        (arkiv_sync_enabled=false) so the TUI correctly shows sync as skipped
        rather than pending.
        
        Args:
            video_id: Video ID
            reason: Reason for skipping (e.g., "arkiv_sync_enabled is disabled")
            
        Returns:
            Job ID or None if creation failed
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import SyncJobRepository
            
            with get_db_session() as session:
                repo = SyncJobRepository(session)
                job = repo.create(
                    video_id=video_id,
                    status="skipped",
                )
                # Update with skip reason
                job.error_message = reason
                session.commit()
                logger.debug(f"Created skipped SyncJob {job.id} for video {video_id}")
                return job.id
        except Exception as e:
            logger.warning(f"Failed to create skipped SyncJob: {e}")
            return None
    
    async def _complete_sync_job(
        self,
        job_id: int,
        tx_hash: str,
        block_number: Optional[int] = None,
        gas_used: Optional[int] = None,
    ) -> None:
        """Mark SyncJob as completed.
        
        Args:
            job_id: Job ID
            tx_hash: Transaction hash
            block_number: Block number (if available)
            gas_used: Gas used (if available)
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import SyncJobRepository
            
            with get_db_session() as session:
                repo = SyncJobRepository(session)
                repo.complete_sync(job_id, tx_hash, block_number or 0, gas_used)
                logger.debug(f"Completed SyncJob {job_id}")
        except Exception as e:
            logger.warning(f"Failed to complete SyncJob: {e}")
    
    async def _fail_sync_job(self, job_id: int, error_message: str) -> None:
        """Mark SyncJob as failed.
        
        Args:
            job_id: Job ID
            error_message: Error description
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.models import SyncJob
            
            with get_db_session() as session:
                job = session.query(SyncJob).filter(SyncJob.id == job_id).first()
                if job:
                    job.status = "failed"
                    job.error_message = error_message
                    session.commit()
                logger.debug(f"Failed SyncJob {job_id}: {error_message}")
        except Exception as e:
            logger.warning(f"Failed to mark SyncJob as failed: {e}")
    
    async def _update_pipeline_snapshot(
        self,
        video_id: int,
        stage: str,
        progress_percent: float,
        status: str = "active",
        error: Optional[str] = None,
    ) -> None:
        """Update PipelineSnapshot for TUI dashboard.
        
        Args:
            video_id: Video ID
            stage: Current stage name
            progress_percent: Stage progress (0-100)
            status: Overall status
            error: Error message if failed
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import PipelineSnapshotRepository
            
            with get_db_session() as session:
                repo = PipelineSnapshotRepository(session)
                
                if status == "failed" and error:
                    repo.mark_error(video_id, stage, error)
                elif status == "completed":
                    repo.mark_completed(video_id)
                else:
                    repo.update_stage(
                        video_id=video_id,
                        stage=stage,
                        status=status,
                        progress_percent=progress_percent,
                    )
        except Exception as e:
            logger.debug(f"Failed to update PipelineSnapshot: {e}")
