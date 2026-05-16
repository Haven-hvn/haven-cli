"""Upload step - Filecoin upload via Synapse SDK.

This step uploads video content to the Filecoin network using
the Synapse SDK. It:
1. Creates a CAR file from the video
2. Uploads to Filecoin via Synapse
3. Records the CID and transaction details

The step uses the JS Runtime Bridge to communicate with the
Synapse SDK running in a Deno subprocess.

The step is conditional and can be skipped via the upload_enabled option.

Task 12: Writes progress to UploadJob and PipelineSnapshot tables.
"""

import asyncio
import base64
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from haven_cli.config import get_config
from haven_cli.crypto.haven_aol_local import (
    GateParams,
    derivation_threshold_from_access_condition,
    encrypt_bytes,
)
from haven_cli.database.connection import get_db_session
from haven_cli.database.repositories import VideoRepository
from haven_cli.js_runtime.bridge import JSRuntimeBridge
from haven_cli.js_runtime.manager import JSBridgeManager
from haven_cli.js_runtime.protocol import JSONRPCError, JSONRPCErrorCode
from haven_cli.pipeline.context import (
    CidEncryptionMetadata,
    EncryptionMetadata,
    PipelineContext,
    UploadResult,
)
from haven_cli.pipeline.events import EventType
from haven_cli.pipeline.results import ErrorCategory, StepError, StepResult
from haven_cli.pipeline.step import ConditionalStep
from haven_cli.services.blockchain_network import get_network_config
from haven_cli.services.evm_utils import normalize_haven_aol_chain

logger = logging.getLogger(__name__)


# ============================================================================
# Progress Stage Constants
# ============================================================================
# These constants define the upload progress stages and their percentage ranges.
# The Synapse SDK reports progress from 0-100, but not all percentages
# represent actual network upload activity.
#
# Progress mapping from synapse-wrapper.ts:
#   0-10%   = Connecting to Synapse SDK
#   20%     = CAR file created (local preparation, no network activity)
#   25-35%  = Payment/validation checks
#   80-100% = Actual network upload to Filecoin
#
# Therefore:
#   - bytes_uploaded = 0 during preparation phase (< 80%)
#   - upload_speed = 0 during preparation phase (< 80%)
#   - Real bytes/speed only calculated during network phase (>= 80%)

# Stage names for UploadJob.stage and PipelineSnapshot tracking
STAGE_CONNECTING = "connecting"   # 0-10%: Initial connection
STAGE_PREPARING = "preparing"     # 20%: CAR file creation
STAGE_UPLOADING = "uploading"     # 80-99%: Actual network upload
STAGE_CONFIRMING = "confirming"   # 90%: Transaction confirmation
STAGE_COMPLETE = "complete"       # 100%: Upload finished

# Progress thresholds
NETWORK_UPLOAD_START_PERCENT = 80  # Actual network upload starts at 80%
CAR_CREATION_PERCENT = 20          # CAR file created at 20%
CONNECTION_PERCENT = 10            # Connected at 10%
CONFIRMATION_PERCENT = 90          # Confirming at 90%
COMPLETION_PERCENT = 100           # Complete at 100%


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
    
    Task 12: Creates/updates UploadJob and PipelineSnapshot records.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the upload step.
        
        Args:
            config: Step configuration (passed to base class)
        """
        super().__init__(config=config)
        self._job_id: Optional[int] = None
        self._start_time: Optional[float] = None
    
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
    
    async def should_skip(self, context: PipelineContext) -> bool:
        """Skip if the step is not enabled in context options.
        
        Also skips if encryption was requested but failed (security measure).
        This prevents uploading unencrypted files when encryption fails.
        """
        # Check if upload is disabled
        enabled = context.options.get(self.enabled_option, self.default_enabled)
        if not enabled:
            self._skip_reason = "upload_enabled is disabled"
            return True
        
        # SECURITY CHECK: If encryption was requested but failed, skip upload
        # This prevents "fail open" behavior where unencrypted files could be uploaded
        if context.options.get("encrypt", False):
            # Encryption was requested - check if it succeeded
            if context.encryption_metadata is None:
                # No encryption metadata means encryption failed or did not complete
                logger.warning(
                    "Skipping upload: encryption was requested but failed or did not complete. "
                    "This is a security measure to prevent uploading unencrypted content."
                )
                self._skip_reason = "encryption was requested but failed or did not complete"
                return True
            
            # Verify encrypted file exists
            if context.encrypted_video_path:
                if not os.path.exists(context.encrypted_video_path):
                    logger.error(
                        f"Skipping upload: encrypted file not found at {context.encrypted_video_path}"
                    )
                    self._skip_reason = f"encrypted file not found at {context.encrypted_video_path}"
                    return True
        
        return False
    
    async def _get_skip_reason(self, context: PipelineContext) -> str:
        """Get the reason for skipping this step."""
        return getattr(self, '_skip_reason', f"{self.enabled_option} is disabled")
    
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
        # Format user-friendly error message for insufficient balance
        error_message = str(last_error)
        error_details: Dict[str, Any] = {}
        error_code = "UPLOAD_ERROR"
        
        # Check if this is an insufficient balance error
        if self._is_insufficient_balance_error(last_error):
            error_code = "INSUFFICIENT_BALANCE"
            # Try to extract balance details from JSON-RPC error data
            available, required = self._extract_balance_details(last_error)
            if available and required:
                error_message = (
                    f"Insufficient funds for transaction. "
                    f"Required: {required} FIL, "
                    f"Available: {available} FIL. "
                    f"Please fund your wallet to proceed."
                )
                error_details = {
                    "available_fil": available,
                    "required_fil": required,
                    "shortfall_fil": str(float(required) - float(available)),
                }
            else:
                error_message = (
                    f"Insufficient funds for transaction. "
                    f"Please fund your wallet to proceed. "
                    f"Original error: {error_message}"
                )
        
        await self._emit_event(EventType.UPLOAD_FAILED, context, {
            "video_path": context.video_path,
            "error": error_message,
            "error_code": error_code,
            **error_details,
        })
        
        step_error = StepError.from_exception(
            last_error, 
            code=error_code,
            category=ErrorCategory.PERMANENT if error_code == "INSUFFICIENT_BALANCE" else ErrorCategory.UNKNOWN,
        )
        step_error.message = error_message  # Use formatted message
        step_error.details.update(error_details)
        
        return StepResult.fail(self.name, step_error)
    
    def _is_insufficient_balance_error(self, error: Exception) -> bool:
        """Check if the error is an insufficient balance error.
        
        Args:
            error: The exception to check
            
        Returns:
            True if this is an insufficient balance error
        """
        error_str = str(error).lower()
        error_type = type(error).__name__.lower()
        
        # Check for JSON-RPC error with specific code
        if isinstance(error, JSONRPCError):
            if error.code == JSONRPCErrorCode.INSUFFICIENT_BALANCE:
                return True
        
        # Check for balance error patterns in message
        balance_patterns = [
            "insufficient balance",
            "insufficient funds",
            "actor balance less than needed",
            "syserrsenderstateinvalid",
            "retcode=2",
            "not enough funds",
            "sender has insufficient funds",
            "insufficient usdfc",
        ]
        for pattern in balance_patterns:
            if pattern in error_str:
                return True
        
        return False
    
    def _extract_balance_details(self, error: Exception) -> tuple[str | None, str | None]:
        """Extract available and required balance from error.
        
        Args:
            error: The exception to extract from
            
        Returns:
            Tuple of (available, required) as strings, or (None, None) if not found
        """
        # Try to extract from JSON-RPC error data
        if isinstance(error, JSONRPCError) and error.data:
            data = error.data
            if isinstance(data, dict):
                available = data.get("available")
                required = data.get("required")
                if available and required:
                    # Convert from wei/attoFIL to FIL if they look like big integers
                    try:
                        available_fil = float(available) / 1e18
                        required_fil = float(required) / 1e18
                        return f"{available_fil:.6f}", f"{required_fil:.6f}"
                    except (ValueError, TypeError):
                        return str(available), str(required)
        
        # Try to parse from error message
        import re
        error_message = str(error)
        
        # Pattern: "Actor balance less than needed 0.002286105823689615 < 0.069999999883052615"
        match = re.search(
            r"(?:balance|needed)[\s:=]*([\d.]+)\s*[<:+-]?\s*(?:need|required|than)?[\s:=]*([\d.]+)",
            error_message,
            re.IGNORECASE
        )
        if match:
            return match.group(1), match.group(2)
        
        return None, None
    
    async def _do_upload(self, context: PipelineContext) -> StepResult:
        """Perform the actual upload.
        
        Task 12: Creates UploadJob and updates PipelineSnapshot.
        
        Args:
            context: Pipeline context with video path
            
        Returns:
            StepResult with upload details
        """
        video_path = context.video_path
        self._start_time = time.time()
        
        # Create UploadJob record for tracking
        if context.video_id:
            file_size = context.video_metadata.file_size if context.video_metadata else 0
            self._job_id = await self._create_upload_job(context.video_id, file_size)
            await self._update_pipeline_snapshot(context.video_id, "upload", 0)
        
        # Emit upload requested event
        await self._emit_event(EventType.UPLOAD_REQUESTED, context, {
            "video_path": video_path,
            "encrypted": context.encryption_metadata is not None,
        })
        
        # Get Filecoin configuration
        filecoin_config = self._get_filecoin_config(context)
        
        # Get JS Runtime Bridge for progress notifications
        # Note: Actual JS calls use _js_call_with_retry for resilience
        bridge = await self._get_js_bridge()
        
        # Track actual network upload start time and bytes
        self._network_upload_started = False
        self._network_upload_start_time: Optional[float] = None
        self._actual_bytes_uploaded = 0
        
        # Create progress callback
        async def on_progress(stage: str, percent: int, bytes_uploaded: int = 0, total_bytes: int = 0) -> None:
            # Update job progress with accurate data
            if self._job_id and context.video_id:
                file_size = context.video_metadata.file_size if context.video_metadata else 0
                
                # Determine if we're in preparation or actual network upload phase
                # Based on synapse-wrapper.ts stages:
                # 0-35% = Preparation (CAR creation, payment check)
                # 80%+ = Actual network upload
                is_preparation = percent < NETWORK_UPLOAD_START_PERCENT
                
                if is_preparation:
                    # During preparation, no actual network bytes have been transferred
                    display_bytes_uploaded = 0
                    upload_speed = 0
                else:
                    # Track network upload start
                    if not self._network_upload_started:
                        self._network_upload_started = True
                        self._network_upload_start_time = time.time()
                        self._actual_bytes_uploaded = bytes_uploaded if bytes_uploaded > 0 else file_size
                    
                    # Use actual bytes from callback, or calculate from percentage of file size
                    display_bytes_uploaded = bytes_uploaded if bytes_uploaded > 0 else int(
                        file_size * (percent - NETWORK_UPLOAD_START_PERCENT) / (COMPLETION_PERCENT - NETWORK_UPLOAD_START_PERCENT)
                    )
                    self._actual_bytes_uploaded = display_bytes_uploaded
                    
                    # Calculate speed only during actual network upload
                    if self._network_upload_start_time:
                        network_elapsed = time.time() - self._network_upload_start_time
                        upload_speed = int(display_bytes_uploaded / network_elapsed) if network_elapsed > 0 else 0
                    else:
                        upload_speed = 0
                
                await self._update_job_progress(
                    context.video_id, 
                    display_bytes_uploaded, 
                    percent, 
                    upload_speed,
                    stage=stage  # Pass actual stage to database
                )
            
            await self._emit_event(EventType.UPLOAD_PROGRESS, context, {
                "video_path": video_path,
                "video_id": context.video_id,
                "job_id": self._job_id,
                "stage": stage,
                "progress_percent": percent,
                "bytes_uploaded": bytes_uploaded if bytes_uploaded > 0 else None,
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
                        "video_id": context.video_id,
                        "job_id": self._job_id,
                        "stage": stage,
                        "progress_percent": percentage,
                    })
                )
        
        try:
            # Register for progress notifications
            unregister_progress = bridge.on_notification(
                "synapse.uploadProgress", handle_progress
            )
            
            # Upload to Filecoin (uses _js_call_with_retry internally)
            upload_result = await self._upload_to_filecoin(
                video_path,
                filecoin_config,
                context.encryption_metadata,
                on_progress,
            )
            
            # Upload VLM AI.json file if available
            vlm_json_cid = None
            if context.analysis_result and context.analysis_result.ai_json_path:
                ai_json_path = context.analysis_result.ai_json_path
                if os.path.exists(ai_json_path):
                    try:
                        logger.info(f"Uploading VLM AI.json file: {ai_json_path}")
                        vlm_json_result = await self._upload_vlm_json(
                            ai_json_path,
                            filecoin_config,
                            upload_result.get("root_cid", ""),  # Parent CID
                        )
                        vlm_json_cid = vlm_json_result.get("root_cid")
                        logger.info(f"VLM AI.json uploaded with CID: {vlm_json_cid}")
                    except Exception as e:
                        # Log but don't fail - video upload succeeded
                        logger.warning(f"Failed to upload VLM AI.json file: {e}")
            
            # Encrypt CID for Arkiv sync (if encryption is enabled)
            cid_encryption_metadata = None
            cid_encryption_result = None
            if context.encryption_metadata and upload_result.get("root_cid"):
                try:
                    cid_encryption_result = await self._encrypt_cid(
                        upload_result.get("root_cid"),
                        context.encryption_metadata.access_control_conditions,
                        context,
                    )
                    # Create CidEncryptionMetadata from result (excluding encrypted_cid)
                    cid_encryption_metadata = CidEncryptionMetadata(
                        encrypted_key=cid_encryption_result.get("encryptedKey", ""),
                        key_hash=cid_encryption_result.get("keyHash", ""),
                        iv=cid_encryption_result.get("iv", ""),
                        access_control_conditions=cid_encryption_result.get("accessControlConditions", []),
                        chain=cid_encryption_result["chain"],
                    )
                    logger.info(f"CID encrypted for Arkiv sync")
                except Exception as e:
                    raise RuntimeError(f"Failed to encrypt CID for Arkiv sync: {e}") from e
            
            # Create upload result
            result = UploadResult(
                video_path=video_path,
                root_cid=upload_result.get("root_cid", ""),
                piece_cid=upload_result.get("piece_cid", ""),
                transaction_hash=upload_result.get("transaction_hash", ""),
                encryption_metadata=context.encryption_metadata,
                vlm_json_cid=vlm_json_cid,
            )
            
            # Store in context
            context.upload_result = result
            
            # Store CID encryption metadata for Arkiv sync
            if cid_encryption_metadata:
                context.cid_encryption_metadata = cid_encryption_metadata
            if cid_encryption_result:
                context.encrypted_cid = cid_encryption_result.get("encryptedCid")
            
            # Update database
            await self._update_database(video_path, result)
            
            # Mark job as completed
            if self._job_id and context.video_id:
                await self._complete_upload_job(
                    self._job_id, result.root_cid, result.piece_cid
                )
                await self._update_pipeline_snapshot(context.video_id, "upload", 100, status="completed")
            
            # Emit upload complete event
            await self._emit_event(EventType.UPLOAD_COMPLETE, context, {
                "video_path": video_path,
                "root_cid": result.root_cid,
                "piece_cid": result.piece_cid,
                "transaction_hash": result.transaction_hash,
                "vlm_json_cid": result.vlm_json_cid,
            })
            
            return StepResult.ok(
                self.name,
                root_cid=result.root_cid,
                piece_cid=result.piece_cid,
                transaction_hash=result.transaction_hash,
                vlm_json_cid=result.vlm_json_cid,
                cid=result.root_cid,  # Alias for convenience
            )
            
        except Exception as e:
            # Mark job as failed
            error_msg = str(e)
            if self._job_id and context.video_id:
                await self._fail_upload_job(self._job_id, error_msg)
                await self._update_pipeline_snapshot(
                    context.video_id, "upload", 0, status="failed", error=error_msg
                )
            raise
            
        finally:
            # Unregister progress handler
            if unregister_progress:
                unregister_progress()
    
    def _get_filecoin_config(self, context: PipelineContext) -> Dict[str, Any]:
        """Get Filecoin configuration from context and config.
        
        Returns:
            Dictionary with Filecoin configuration
        """
        filecoin_mode = self._config.get(
            "filecoin_network_mode",
            self._config.get("network_mode", "testnet"),
        )
        rpc_url = self._config.get("filecoin_rpc_url")
        if not rpc_url:
            rpc_url = get_network_config(filecoin_mode).filecoin_rpc_url
        
        return {
            "data_set_id": context.options.get("dataset_id") or self._config.get("data_set_id", 1),
            "wait_for_deal": self._config.get("wait_for_deal", False),
            "rpc_url": rpc_url,
            "network_mode": filecoin_mode,
        }
    
    async def _get_js_bridge(self) -> JSRuntimeBridge:
        """Get the JS Runtime Bridge for Synapse SDK communication.
        
        Uses the JSBridgeManager singleton for connection reuse and
        automatic reconnection handling.
        
        Returns:
            JSRuntimeBridge instance ready for Synapse SDK calls.
        """
        return await JSBridgeManager.get_instance().get_bridge()
    
    async def _js_call_with_retry(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        max_retries: int = 3,
    ) -> Any:
        """Call JS runtime method with automatic retry on bridge failure.
        
        This method wraps JSBridgeManager.call_with_retry() to handle
        bridge restart scenarios gracefully. When the bridge is stopped
        (e.g., due to health check failure or concurrent operation timeout),
        this method will automatically retry with a fresh bridge.
        
        Args:
            method: The method name to call
            params: Optional parameters for the method
            timeout: Optional timeout override
            max_retries: Maximum retry attempts (default 3)
            
        Returns:
            The result from the JS runtime
            
        Raises:
            RuntimeError: If all retry attempts fail
        """
        return await JSBridgeManager.get_instance().call_with_retry(
            method, params, max_retries=max_retries, timeout=timeout
        )
    
    async def _upload_to_filecoin(
        self,
        video_path: str,
        config: Dict[str, Any],
        encryption_metadata: Optional[EncryptionMetadata],
        on_progress: Callable[[str, int, int, int], Awaitable[None]],
    ) -> Dict[str, Any]:
        """Upload content to Filecoin via Synapse SDK.
        
        The process:
        1. Connect to Synapse
        2. Upload file to Filecoin
        3. Wait for transaction confirmation (optional)
        4. Return CIDs and transaction hash
        
        Uses _js_call_with_retry for all JS runtime calls to handle
        bridge restart scenarios gracefully during concurrent uploads.
        
        Args:
            video_path: Path to video file
            config: Filecoin configuration
            encryption_metadata: Encryption metadata if encrypted
            on_progress: Progress callback
            
        Returns:
            Dictionary with upload result
            
        Raises:
            RuntimeError: If upload fails
        """
        # Connect to Synapse with network configuration
        logger.info("Connecting to Synapse...")
        
        filecoin_mode = self._config.get(
            "filecoin_network_mode",
            self._config.get("network_mode", "testnet"),
        )
        rpc_url = self._config.get("filecoin_rpc_url")
        if not rpc_url:
            rpc_url = get_network_config(filecoin_mode).filecoin_rpc_url
        
        try:
            # Use longer timeout for Synapse connection (testnet can be slow)
            await self._js_call_with_retry("synapse.connect", {
                "rpcUrl": rpc_url,
                "networkMode": filecoin_mode,
            }, timeout=120.0)  # 2 minutes for connection
        except Exception as e:
            logger.error(f"Failed to connect to Synapse: {e}")
            raise RuntimeError(f"Synapse connection failed: {e}") from e
        
        await on_progress(STAGE_CONNECTING, CONNECTION_PERCENT, 0, 0)
        
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
        # Note: Progress 20% from synapse means CAR file created, not actual upload started
        await on_progress(STAGE_PREPARING, CAR_CREATION_PERCENT, 0, 0)
        
        logger.info(f"Starting Filecoin upload for: {file_to_upload}")
        
        try:
            # Use a longer timeout for Filecoin upload (6000 seconds)
            # Filecoin uploads typically take 60-120 seconds for small files
            # Use max_retries=1 for upload since it's a long-running operation
            result = await self._js_call_with_retry(
                "synapse.upload",
                {
                    "filePath": file_to_upload,
                    "metadata": {
                        "encrypted": encryption_metadata is not None,
                        "dataSetId": config.get("data_set_id"),
                    },
                    "onProgress": True,  # Enable progress notifications
                },
                timeout=6000.0,  # 100 minutes timeout for upload
                max_retries=1,  # Only 1 retry for long uploads
            )
        except Exception as e:
            logger.error(f"Filecoin upload failed: {e}")
            raise RuntimeError(f"Upload to Filecoin failed: {e}") from e
        
        await on_progress(STAGE_CONFIRMING, CONFIRMATION_PERCENT, 0, 0)
        
        # Wait for deal confirmation (optional)
        if config.get("wait_for_deal", False):
            logger.info("Waiting for deal confirmation...")
            try:
                status = await self._js_call_with_retry("synapse.getStatus", {"cid": result["cid"]})
                max_wait_attempts = 60  # Max 5 minutes (60 * 5s)
                attempts = 0
                
                while status.get("status") == "pending" and attempts < max_wait_attempts:
                    await asyncio.sleep(5)
                    status = await self._js_call_with_retry("synapse.getStatus", {"cid": result["cid"]})
                    attempts += 1
                    logger.debug(f"Deal status: {status.get('status')} (attempt {attempts})")
                
                if status.get("status") != "confirmed":
                    logger.warning(f"Deal confirmation timeout after {attempts} attempts")
                else:
                    logger.info("Deal confirmed successfully")
                    
            except Exception as e:
                # Log but don't fail - upload succeeded even if status check fails
                logger.warning(f"Could not get deal status: {e}")
        
        await on_progress(STAGE_COMPLETE, COMPLETION_PERCENT, 0, 0)
        
        logger.info(f"Upload complete. CID: {result.get('cid', '')}")
        
        return {
            "root_cid": result["cid"],
            "piece_cid": result.get("pieceCid", ""),
            "deal_id": result.get("dealId", ""),
            "transaction_hash": result.get("txHash", ""),
        }
    
    async def _upload_vlm_json(
        self,
        ai_json_path: str,
        config: Dict[str, Any],
        parent_cid: str,
    ) -> Dict[str, Any]:
        """Upload VLM AI.json file to Filecoin via Synapse SDK.
        
        This uploads the AI analysis JSON file separately from the video,
        allowing the backend to reference it by CID for Arkiv sync.
        
        Uses _js_call_with_retry for resilience during concurrent operations.
        
        Args:
            ai_json_path: Path to the AI.json file
            config: Filecoin configuration
            parent_cid: CID of the parent video file (for metadata linking)
            
        Returns:
            Dictionary with upload result
            
        Raises:
            RuntimeError: If upload fails
        """
        logger.info(f"Uploading VLM AI.json to Filecoin: {ai_json_path}")
        
        # Verify file exists
        if not os.path.exists(ai_json_path):
            raise FileNotFoundError(f"AI.json file not found: {ai_json_path}")
        
        try:
            result = await self._js_call_with_retry(
                "synapse.upload",
                {
                    "filePath": ai_json_path,
                    "metadata": {
                        "type": "vlm_analysis",
                        "parentCid": parent_cid,
                        "dataSetId": config.get("data_set_id"),
                    },
                    "onProgress": False,  # Disable progress for secondary upload
                },
                timeout=60.0,  # Shorter timeout for smaller JSON files
            )
        except Exception as e:
            logger.error(f"VLM AI.json upload failed: {e}")
            raise RuntimeError(f"Upload of VLM AI.json to Filecoin failed: {e}") from e
        
        logger.info(f"VLM AI.json upload complete. CID: {result.get('cid', '')}")
        
        return {
            "root_cid": result["cid"],
            "piece_cid": result.get("pieceCid", ""),
            "deal_id": result.get("dealId", ""),
            "transaction_hash": result.get("txHash", ""),
        }
    
    async def _encrypt_cid(
        self,
        cid: str,
        access_control_conditions: List[Dict[str, Any]],
        context: PipelineContext,
    ) -> Dict[str, Any]:
        """Encrypt the CID with local Haven-AOL logic for Arkiv sync."""
        logger.info(f"Encrypting CID for Arkiv sync: {cid[:30]}...")

        gate_condition = access_control_conditions[0] if access_control_conditions else {}
        token_address = str(gate_condition.get("contractAddress", "")).strip()
        if not token_address:
            raise RuntimeError("token_contract/contractAddress required for CID encryption")
        threshold = derivation_threshold_from_access_condition(gate_condition)
        pipeline_cfg = self._config.get("pipeline")
        config_chain = getattr(pipeline_cfg, "evm_chain", None) if pipeline_cfg is not None else self._config.get("evm_chain")
        configured_chain = context.options.get("evm_chain") or config_chain
        if not configured_chain:
            raise RuntimeError(
                "evm_chain is required for CID encryption "
                "(EVM chain where access-control assets live). "
                "Set --evm-chain or pipeline.evm_chain."
            )
        chain = normalize_haven_aol_chain(str(configured_chain))

        try:
            encrypted = encrypt_bytes(
                plaintext=cid.encode("utf-8"),
                private_key="",
                gate=GateParams(
                    chain=chain,
                    token_address=token_address,
                    threshold=threshold,
                    cid=cid,
                ),
            )
            logger.info(f"CID encrypted successfully")
            return {
                "encryptedCid": base64.b64encode(encrypted["ciphertext_bytes"]).decode("ascii"),
                "encryptedKey": encrypted["encrypted_key_b64"],
                "keyHash": encrypted["key_hash"],
                "iv": encrypted["iv_b64"],
                "accessControlConditions": access_control_conditions,
                "chain": chain,
            }
        except Exception as e:
            logger.error(f"CID encryption failed: {e}")
            raise RuntimeError(f"CID encryption failed: {e}") from e

    def _categorize_error(self, error: Exception) -> ErrorCategory:
        """Categorize error for retry decisions.
        
        Network errors are transient and can be retried.
        Configuration errors are permanent.
        Insufficient balance errors are permanent (user must fund wallet).
        
        Args:
            error: The exception to categorize
            
        Returns:
            ErrorCategory for the error
        """
        error_str = str(error).lower()
        error_type = type(error).__name__.lower()
        
        # CRITICAL: Insufficient balance errors are PERMANENT - don't retry
        # These require user action (funding wallet) and retrying wastes time
        balance_error_patterns = [
            "insufficient balance",
            "insufficient funds",
            "actor balance less than needed",
            "syserrsenderstateinvalid",
            "retcode=2",
            "not enough funds",
            "sender has insufficient funds",
            "insufficient usdfc",
        ]
        for pattern in balance_error_patterns:
            if pattern in error_str:
                logger.warning(f"Detected insufficient balance error (non-retryable): {error}")
                return ErrorCategory.PERMANENT
        
        # Check for JSON-RPC insufficient balance error code
        if isinstance(error, JSONRPCError):
            if error.code == JSONRPCErrorCode.INSUFFICIENT_BALANCE:
                logger.warning(f"Detected JSON-RPC insufficient balance error (non-retryable): {error}")
                return ErrorCategory.PERMANENT
        
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
                    update_kwargs = {
                        "cid": result.root_cid,
                        "piece_cid": result.piece_cid,
                    }
                    # Include vlm_json_cid if available
                    if result.vlm_json_cid:
                        update_kwargs["vlm_json_cid"] = result.vlm_json_cid
                    
                    repo.update(video, **update_kwargs)
                    logger.info(
                        f"Updated database with CID for video: {video_path} "
                        f"(vlm_json_cid: {result.vlm_json_cid or 'N/A'})"
                    )
                else:
                    logger.warning(f"Video not found in database: {video_path}")
                    
        except Exception as e:
            # Log error but don't fail the step - upload succeeded
            logger.error(f"Failed to update database with upload result: {e}")
    
    # =========================================================================
    # Task 12: Job tracking helper methods
    # =========================================================================
    
    async def _create_upload_job(
        self,
        video_id: int,
        bytes_total: int,
    ) -> Optional[int]:
        """Create an UploadJob record for tracking.
        
        Args:
            video_id: Video ID
            bytes_total: Total bytes to upload
            
        Returns:
            Job ID or None if creation failed
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import UploadJobRepository
            
            with get_db_session() as session:
                repo = UploadJobRepository(session)
                job = repo.create(
                    video_id=video_id,
                    target="ipfs",
                    status="uploading",
                    bytes_total=bytes_total,
                )
                logger.debug(f"Created UploadJob {job.id} for video {video_id}")
                return job.id
        except Exception as e:
            logger.warning(f"Failed to create UploadJob: {e}")
            return None
    
    async def _update_job_progress(
        self,
        video_id: int,
        bytes_uploaded: int,
        progress_percent: float,
        upload_speed: int = 0,
        stage: str = "uploading",
    ) -> None:
        """Update UploadJob progress.
        
        Args:
            video_id: Video ID
            bytes_uploaded: Bytes uploaded so far (actual network bytes)
            progress_percent: Progress percentage (0-100)
            upload_speed: Upload speed in bytes/sec (only during network upload)
            stage: Current upload stage (e.g., 'preparing', 'uploading', 'confirming')
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import UploadJobRepository, PipelineSnapshotRepository
            
            with get_db_session() as session:
                job_repo = UploadJobRepository(session)
                if self._job_id:
                    job_repo.update_progress(self._job_id, bytes_uploaded, upload_speed, stage)
                
                # Also update pipeline snapshot with accurate stage info
                snapshot_repo = PipelineSnapshotRepository(session)
                # Store detailed stage in status field for visibility
                # Format: "upload:stage_name" to indicate upload sub-stage
                stage_detail = f"upload:{stage}" if stage != "uploading" else "upload"
                snapshot_repo.update_stage(
                    video_id=video_id,
                    stage=stage_detail,
                    status="active",
                    progress_percent=progress_percent,
                    stage_speed=upload_speed,
                )
                snapshot_repo.update_bytes_metrics(
                    video_id=video_id,
                    uploaded_bytes=bytes_uploaded,
                )
        except Exception as e:
            logger.debug(f"Failed to update UploadJob progress: {e}")
    
    async def _complete_upload_job(
        self,
        job_id: int,
        remote_cid: str,
        piece_cid: Optional[str] = None,
    ) -> None:
        """Mark UploadJob as completed.
        
        Args:
            job_id: Job ID
            remote_cid: Remote CID
            piece_cid: Piece CID (if available)
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import UploadJobRepository
            
            with get_db_session() as session:
                repo = UploadJobRepository(session)
                repo.complete_upload(job_id, remote_cid)
                logger.debug(f"Completed UploadJob {job_id}")
        except Exception as e:
            logger.warning(f"Failed to complete UploadJob: {e}")
    
    async def _fail_upload_job(self, job_id: int, error_message: str) -> None:
        """Mark UploadJob as failed.
        
        Args:
            job_id: Job ID
            error_message: Error description
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.models import UploadJob
            
            with get_db_session() as session:
                job = session.query(UploadJob).filter(UploadJob.id == job_id).first()
                if job:
                    job.status = "failed"
                    job.error_message = error_message
                    session.commit()
                logger.debug(f"Failed UploadJob {job_id}: {error_message}")
        except Exception as e:
            logger.warning(f"Failed to mark UploadJob as failed: {e}")
    
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
