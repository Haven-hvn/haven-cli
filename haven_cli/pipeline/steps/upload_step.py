"""Upload step - Filecoin upload via Synapse SDK.

This step uploads video content to the Filecoin network using
the Synapse SDK. It:
1. Creates a CAR file from the video
2. Uploads to Filecoin via Synapse
3. Records the CID and transaction details

The step uses the JS Runtime Bridge to communicate with the
Synapse SDK running in a Deno subprocess.

The step is conditional and can be skipped via the upload_enabled option.
"""

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable, Dict, Optional

from haven_cli.config import get_config
from haven_cli.database.connection import get_db_session
from haven_cli.database.repositories import VideoRepository
from haven_cli.js_runtime.bridge import JSRuntimeBridge
from haven_cli.js_runtime.manager import JSBridgeManager
from haven_cli.pipeline.context import (
    EncryptionMetadata,
    PipelineContext,
    UploadResult,
)
from haven_cli.pipeline.events import EventType
from haven_cli.pipeline.results import ErrorCategory, StepError, StepResult
from haven_cli.pipeline.step import ConditionalStep

logger = logging.getLogger(__name__)


class UploadStep(ConditionalStep):
    """Pipeline step for Filecoin upload.
    
    This step uploads video content to the Filecoin network using
the Synapse SDK. It handles CAR file creation, upload, and
    transaction confirmation.
    
    The upload is performed via the JS Runtime Bridge, which
    communicates with the Synapse SDK running in a Deno subprocess.
    
    Emits:
        - UPLOAD_REQUESTED event when starting
        - UPLOAD_PROGRESS events during upload
        - UPLOAD_COMPLETE event on success
        - UPLOAD_FAILED event on failure
    
    Output data:
        - root_cid: Content ID of the uploaded file
        - piece_cid: Piece CID for Filecoin deals
        - transaction_hash: Blockchain transaction hash
    """
    
    @property
    def name(self) -> str:
        """Step identifier."""
        return "upload"
    
    @property
    def enabled_option(self) -> str:
        """Context option that enables this step."""
        return "upload_enabled"
    
    @property
    def default_enabled(self) -> bool:
        """Upload is enabled by default."""
        return True
    
    @property
    def max_retries(self) -> int:
        """Upload can retry on transient network errors."""
        return 3
    
    @property
    def retry_delay_seconds(self) -> float:
        """Longer delay for upload retries."""
        return 5.0
    
    async def process(self, context: PipelineContext) -> StepResult:
        """Process Filecoin upload with retry logic.
        
        Args:
            context: Pipeline context with video path
            
        Returns:
            StepResult with upload details
        """
        last_error = None
        
        for attempt in range(self.max_retries + 1):
            try:
                return await self._do_upload(context)
            except Exception as e:
                last_error = e
                category = self._categorize_error(e)
                
                if category == ErrorCategory.PERMANENT:
                    break  # Don't retry permanent errors
                
                if attempt < self.max_retries:
                    delay = self.retry_delay_seconds * (attempt + 1)
                    logger.warning(f"Upload attempt {attempt + 1} failed, retrying in {delay}s: {e}")
                    await asyncio.sleep(delay)
        
        # All retries exhausted or permanent error
        await self._emit_event(EventType.UPLOAD_FAILED, context, {
            "video_path": context.video_path,
            "error": str(last_error),
        })
        
        return StepResult.fail(
            self.name,
            StepError.from_exception(last_error, code="UPLOAD_ERROR"),
        )
    
    async def _do_upload(self, context: PipelineContext) -> StepResult:
        """Perform the actual upload.
        
        Args:
            context: Pipeline context with video path
            
        Returns:
            StepResult with upload details
        """
        video_path = context.video_path
        
        # Emit upload requested event
        await self._emit_event(EventType.UPLOAD_REQUESTED, context, {
            "video_path": video_path,
            "encrypted": context.encryption_metadata is not None,
        })
        
        # Get Filecoin configuration
        filecoin_config = self._get_filecoin_config(context)
        
        # Get JS Runtime Bridge
        bridge = await self._get_js_bridge()
        
        # Create progress callback
        async def on_progress(stage: str, percent: int) -> None:
            await self._emit_event(EventType.UPLOAD_PROGRESS, context, {
                "video_path": video_path,
                "stage": stage,
                "progress_percent": percent,
            })
        
        # Set up progress notification handler
        unregister_progress = None
        
        def handle_progress(params: dict) -> None:
            """Handle progress notifications from JS runtime."""
            percentage = params.get("percentage", 0)
            stage = params.get("stage", "uploading")
            if percentage < 100:
                # Emit pipeline event
                asyncio.create_task(
                    self._emit_event(EventType.UPLOAD_PROGRESS, context, {
                        "video_path": video_path,
                        "stage": stage,
                        "progress_percent": percentage,
                    })
                )
        
        try:
            # Register for progress notifications
            unregister_progress = bridge.on_notification(
                "synapse.uploadProgress", handle_progress
            )
            
            # Upload to Filecoin
            upload_result = await self._upload_to_filecoin(
                bridge,
                video_path,
                filecoin_config,
                context.encryption_metadata,
                on_progress,
            )
            
            # Create upload result
            result = UploadResult(
                video_path=video_path,
                root_cid=upload_result.get("root_cid", ""),
                piece_cid=upload_result.get("piece_cid", ""),
                transaction_hash=upload_result.get("transaction_hash", ""),
                encryption_metadata=context.encryption_metadata,
            )
            
            # Store in context
            context.upload_result = result
            
            # Update database
            await self._update_database(video_path, result)
            
            # Emit upload complete event
            await self._emit_event(EventType.UPLOAD_COMPLETE, context, {
                "video_path": video_path,
                "root_cid": result.root_cid,
                "piece_cid": result.piece_cid,
                "transaction_hash": result.transaction_hash,
            })
            
            return StepResult.ok(
                self.name,
                root_cid=result.root_cid,
                piece_cid=result.piece_cid,
                transaction_hash=result.transaction_hash,
                cid=result.root_cid,  # Alias for convenience
            )
            
        finally:
            # Unregister progress handler
            if unregister_progress:
                unregister_progress()
    
    def _get_filecoin_config(self, context: PipelineContext) -> Dict[str, Any]:
        """Get Filecoin configuration from context and config.
        
        Returns:
            Dictionary with Filecoin configuration
        """
        return {
            "data_set_id": context.options.get("dataset_id") or self._config.get("data_set_id", 1),
            "wait_for_deal": self._config.get("wait_for_deal", False),
        }
    
    async def _get_js_bridge(self) -> JSRuntimeBridge:
        """Get the JS Runtime Bridge for Synapse SDK communication.
        
        Uses the JSBridgeManager singleton for connection reuse and
        automatic reconnection handling.
        
        Returns:
            JSRuntimeBridge instance ready for Synapse SDK calls.
        """
        return await JSBridgeManager.get_instance().get_bridge()
    
    async def _upload_to_filecoin(
        self,
        bridge: JSRuntimeBridge,
        video_path: str,
        config: Dict[str, Any],
        encryption_metadata: Optional[EncryptionMetadata],
        on_progress: Callable[[str, int], Awaitable[None]],
    ) -> Dict[str, Any]:
        """Upload content to Filecoin via Synapse SDK.
        
        The process:
        1. Connect to Synapse
        2. Upload file to Filecoin
        3. Wait for transaction confirmation (optional)
        4. Return CIDs and transaction hash
        
        Args:
            bridge: JS Runtime Bridge instance
            video_path: Path to video file
            config: Filecoin configuration
            encryption_metadata: Encryption metadata if encrypted
            on_progress: Progress callback
            
        Returns:
            Dictionary with upload result
            
        Raises:
            RuntimeError: If upload fails
        """
        # Connect to Synapse (uses FILECOIN_RPC_URL and HAVEN_PRIVATE_KEY env vars)
        logger.info("Connecting to Synapse...")
        
        try:
            await bridge.call("synapse.connect", {})
        except Exception as e:
            logger.error(f"Failed to connect to Synapse: {e}")
            raise RuntimeError(f"Synapse connection failed: {e}") from e
        
        await on_progress("preparing", 10)
        
        # Determine file to upload (encrypted or original)
        file_to_upload = video_path
        if encryption_metadata and encryption_metadata.ciphertext:
            # Use encrypted file if available
            if os.path.exists(encryption_metadata.ciphertext):
                file_to_upload = encryption_metadata.ciphertext
                logger.info(f"Using encrypted file for upload: {file_to_upload}")
        
        # Verify file exists
        if not os.path.exists(file_to_upload):
            raise FileNotFoundError(f"File to upload not found: {file_to_upload}")
        
        # Upload to Filecoin
        await on_progress("uploading", 20)
        
        logger.info(f"Starting Filecoin upload for: {file_to_upload}")
        
        try:
            # Use a longer timeout for Filecoin upload (180 seconds)
            # Filecoin uploads typically take 60-120 seconds for small files
            result = await bridge.call(
                "synapse.upload",
                {
                    "filePath": file_to_upload,
                    "metadata": {
                        "encrypted": encryption_metadata is not None,
                        "dataSetId": config.get("data_set_id"),
                    },
                    "onProgress": True,  # Enable progress notifications
                },
                timeout=180.0,  # 3 minutes timeout for upload
            )
        except Exception as e:
            logger.error(f"Filecoin upload failed: {e}")
            raise RuntimeError(f"Upload to Filecoin failed: {e}") from e
        
        await on_progress("confirming", 90)
        
        # Wait for deal confirmation (optional)
        if config.get("wait_for_deal", False):
            logger.info("Waiting for deal confirmation...")
            try:
                status = await bridge.call("synapse.getStatus", {"cid": result["cid"]})
                max_wait_attempts = 60  # Max 5 minutes (60 * 5s)
                attempts = 0
                
                while status.get("status") == "pending" and attempts < max_wait_attempts:
                    await asyncio.sleep(5)
                    status = await bridge.call("synapse.getStatus", {"cid": result["cid"]})
                    attempts += 1
                    logger.debug(f"Deal status: {status.get('status')} (attempt {attempts})")
                
                if status.get("status") != "confirmed":
                    logger.warning(f"Deal confirmation timeout after {attempts} attempts")
                else:
                    logger.info("Deal confirmed successfully")
                    
            except Exception as e:
                # Log but don't fail - upload succeeded even if status check fails
                logger.warning(f"Could not get deal status: {e}")
        
        await on_progress("complete", 100)
        
        logger.info(f"Upload complete. CID: {result.get('cid', '')}")
        
        return {
            "root_cid": result["cid"],
            "piece_cid": result.get("pieceCid", ""),
            "deal_id": result.get("dealId", ""),
            "transaction_hash": result.get("txHash", ""),
        }
    
    def _categorize_error(self, error: Exception) -> ErrorCategory:
        """Categorize error for retry decisions.
        
        Network errors are transient and can be retried.
        Configuration errors are permanent.
        
        Args:
            error: The exception to categorize
            
        Returns:
            ErrorCategory for the error
        """
        error_str = str(error).lower()
        error_type = type(error).__name__.lower()
        
        # Permanent errors (no retry) - check first for wrapped errors
        permanent_patterns = [
            "unauthorized",
            "forbidden",
            "401",
            "403",
            "404",
            "bad request",
            "invalid api key",
            "not configured",
            "not found",
        ]
        
        for pattern in permanent_patterns:
            if pattern in error_str:
                return ErrorCategory.PERMANENT
        
        # Transient errors (retry)
        transient_patterns = [
            "timeout",
            "connection",
            "network",
            "rate limit",
            "503",
            "502",
            "504",
            "temporary",
            "unavailable",
        ]
        
        for pattern in transient_patterns:
            if pattern in error_str:
                return ErrorCategory.TRANSIENT
        
        # ValueError and TypeError are typically permanent (programming errors)
        if error_type in ("valueerror", "typeerror"):
            return ErrorCategory.PERMANENT
        
        return ErrorCategory.UNKNOWN
    
    async def _update_database(
        self,
        video_path: str,
        result: UploadResult,
    ) -> None:
        """Update database with upload result.
        
        Args:
            video_path: Path to the video file
            result: Upload result with CID information
        """
        try:
            with get_db_session() as session:
                repo = VideoRepository(session)
                video = repo.get_by_source_path(video_path)
                
                if video:
                    repo.update(
                        video,
                        cid=result.root_cid,
                        piece_cid=result.piece_cid,
                    )
                    logger.info(f"Updated database with CID for video: {video_path}")
                else:
                    logger.warning(f"Video not found in database: {video_path}")
                    
        except Exception as e:
            # Log error but don't fail the step - upload succeeded
            logger.error(f"Failed to update database with upload result: {e}")
