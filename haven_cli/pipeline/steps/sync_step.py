"""Sync step - Arkiv blockchain synchronization.

This step synchronizes video metadata to the Arkiv blockchain,
creating a permanent, queryable record of the archived content.
It:
1. Builds the Arkiv entity payload
2. Checks for existing entities (update vs create)
3. Submits transaction to Arkiv
4. Records the entity key

The step is conditional and can be skipped via the arkiv_sync_enabled option.
"""

from typing import Any, Dict, Optional

from haven_cli.database.connection import get_db_session
from haven_cli.database.repositories import VideoRepository
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
    """
    
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
        """Arkiv sync is enabled by default."""
        return True
    
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
        
        Args:
            context: Pipeline context with upload result
            
        Returns:
            StepResult with sync details
        """
        video_path = context.video_path
        
        # Emit sync requested event
        await self._emit_event(EventType.SYNC_REQUESTED, context, {
            "video_path": video_path,
            "cid": context.cid,
        })
        
        try:
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
            
            context.arkiv_entity_key = entity_key
            
            # Update database with entity key
            await self._update_database(context, entity_key)
            
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
            return StepResult.fail(self.name, error)
            
        except Exception as e:
            category = self._categorize_error(e)
            
            return StepResult.fail(
                self.name,
                StepError.from_exception(e, code="SYNC_ERROR", category=category),
            )
    
    def _get_arkiv_config(self) -> ArkivSyncConfig:
        """Get Arkiv configuration from config and environment.
        
        Priority:
        1. Explicit config values from self._config
        2. Environment variables (via build_arkiv_config)
        
        Returns:
            ArkivSyncConfig instance
        """
        # Get values from config if available
        private_key = self._config.get("arkiv_private_key")
        rpc_url = self._config.get("arkiv_rpc_url")
        enabled = self._config.get("arkiv_sync_enabled")
        expires_in = self._config.get("arkiv_expiration_seconds")
        
        # Build config (will use environment variables as fallback)
        return build_arkiv_config(
            private_key=private_key,
            rpc_url=rpc_url,
            enabled=enabled,
            expires_in=expires_in,
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
                import logging
                logger = logging.getLogger(__name__)
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
            import logging
            logger = logging.getLogger(__name__)
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
        import logging
        logger = logging.getLogger(__name__)
        logger.info("Sync step skipped: %s", reason)
