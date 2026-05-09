"""Encrypt step - Haven-AOL encryption.

This step encrypts video content using the in-repo Haven-AOL implementation.
The step is conditional and can be skipped via the encrypt option.
"""

import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from haven_cli.crypto.haven_aol_local import GateParams, encrypt_bytes
from haven_cli.pipeline.context import EncryptionMetadata, PipelineContext
from haven_cli.pipeline.events import EventType
from haven_cli.pipeline.results import StepError, StepResult
from haven_cli.pipeline.step import ConditionalStep
from haven_cli.services.blockchain_network import get_network_config
from haven_cli.services.evm_utils import normalize_haven_aol_chain

logger = logging.getLogger(__name__)


class EncryptStep(ConditionalStep):
    """Pipeline step for Haven-AOL encryption.

    Supports multiple access control patterns:
    - owner_only: Only the wallet owner can decrypt
    - nft_gated: Only NFT holders can decrypt
    - token_gated: Only token holders can decrypt
    - public: Anyone can decrypt (for public content)
    - custom: Explicit access conditions provided in context

    Emits:
        - ENCRYPT_REQUESTED event when starting
        - ENCRYPT_PROGRESS events during encryption
        - ENCRYPT_COMPLETE event on success

    Output data:
        - ciphertext_hash: Hash of the encrypted content
        - access_conditions: Access control conditions used
        - chain: Blockchain used for access control
        - encrypted_path: Path to the encrypted file

    Task 12: Creates/updates EncryptionJob and PipelineSnapshot records.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the encrypt step.

        Args:
            config: Step configuration (passed to base class)
        """
        super().__init__(config=config)
        self._job_id: Optional[int] = None
        self._start_time: Optional[float] = None

    @property
    def name(self) -> str:
        """Step identifier."""
        return "encrypt"

    @property
    def enabled_option(self) -> str:
        """Context option that enables this step."""
        return "encrypt"

    @property
    def default_enabled(self) -> bool:
        """Encryption is disabled by default."""
        return False

    @property
    def max_retries(self) -> int:
        """Maximum retry attempts for transient errors."""
        return 3

    def _get_chain(self, context: PipelineContext) -> str:
        """Resolve the EVM chain from context options or config.

        Checks context options first (e.g., CLI --evm-chain flag),
        then falls back to step config.

        Args:
            context: Pipeline context with options

        Returns:
            Normalized chain name

        Raises:
            ValueError: If no chain is configured
        """
        pipeline_cfg = self._config.get("pipeline")
        config_chain = (
            getattr(pipeline_cfg, "evm_chain", None)
            if pipeline_cfg is not None
            else self._config.get("evm_chain")
        )
        configured_chain = context.options.get("evm_chain") or config_chain
        if not configured_chain:
            raise ValueError(
                "evm_chain is required for Haven-AOL encryption. "
                "Set --evm-chain or pipeline.evm_chain."
            )
        return normalize_haven_aol_chain(str(configured_chain))

    async def process(self, context: PipelineContext) -> StepResult:
        """Process Haven-AOL encryption.

        Args:
            context: Pipeline context with video path

        Returns:
            StepResult with encryption metadata
        """
        video_path = context.video_path
        self._start_time = time.time()

        # Create EncryptionJob record for tracking
        if context.video_id:
            file_size = context.video_metadata.file_size if context.video_metadata else 0
            self._job_id = await self._create_encryption_job(context.video_id, file_size)
            await self._update_pipeline_snapshot(context.video_id, "encrypt", 0)

        # Emit encrypt requested event
        await self._emit_event(EventType.ENCRYPT_REQUESTED, context, {
            "video_path": video_path,
        })

        try:
            # Get access conditions from config or context
            access_conditions = self._get_access_conditions(context)
            logger.info(f"Using access pattern: {context.options.get('access_pattern', 'owner_only')}")

            # Encrypt via standalone Haven-AOL implementation
            # Uses _js_call_with_retry internally for resilience
            encryption_result = await self._encrypt_with_haven_aol(
                video_path,
                access_conditions,
                context,
            )

            # Create encryption metadata
            encryption_metadata = EncryptionMetadata(
                ciphertext=encryption_result.get("ciphertext_path", ""),
                data_to_encrypt_hash=encryption_result.get("data_to_encrypt_hash", ""),
                encrypted_key=encryption_result.get("encrypted_key", ""),
                key_hash=encryption_result.get("key_hash", ""),
                iv=encryption_result.get("iv", ""),
                access_control_conditions=access_conditions,
                chain=encryption_result["chain"],
            )

            # Store in context
            context.encryption_metadata = encryption_metadata
            context.encrypted_video_path = encryption_result.get("ciphertext_path")

            # Store metadata path for cleanup step
            if encryption_result.get("metadata_path"):
                context.set_step_data("encrypt", "metadata_path", encryption_result.get("metadata_path"))

            # Store original hash for encryption_metadata
            if encryption_result.get("original_hash"):
                context.set_step_data("encrypt", "original_hash", encryption_result.get("original_hash"))

            # Save encryption metadata to database
            if context.video_id:
                await self._save_encryption_metadata(
                    context.video_id,
                    encryption_metadata,
                )

            # Mark job as completed
            if self._job_id and context.video_id:
                await self._complete_encryption_job(self._job_id, encryption_metadata.data_to_encrypt_hash)
                await self._update_pipeline_snapshot(context.video_id, "encrypt", 100, status="completed")

            # Emit encrypt complete event
            await self._emit_event(EventType.ENCRYPT_COMPLETE, context, {
                "video_path": video_path,
                "encrypted_path": encryption_result.get("ciphertext_path"),
                "data_to_encrypt_hash": encryption_metadata.data_to_encrypt_hash,
                "chain": encryption_metadata.chain,
            })

            return StepResult.ok(
                self.name,
                ciphertext_hash=encryption_metadata.data_to_encrypt_hash,
                access_conditions=access_conditions,
                chain=encryption_metadata.chain,
                encrypted_path=encryption_result.get("ciphertext_path"),
            )

        except Exception as e:
            logger.error(f"Encryption failed: {e}")

            # Mark job as failed
            error_msg = str(e)
            if self._job_id and context.video_id:
                await self._fail_encryption_job(self._job_id, error_msg)
                await self._update_pipeline_snapshot(
                    context.video_id, "encrypt", 0, status="failed", error=error_msg
                )

            return StepResult.fail(
                self.name,
                StepError.from_exception(e, code="ENCRYPT_ERROR"),
            )

    async def _encrypt_with_haven_aol(
        self,
        video_path: str,
        access_conditions: List[Dict[str, Any]],
        context: PipelineContext,
    ) -> Dict[str, Any]:
        """Encrypt content with standalone Haven-AOL logic.

        This uses the Haven-AOL implementation for file encryption while
        preserving the step output contract expected by downstream steps.
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        private_key = os.environ.get("HAVEN_PRIVATE_KEY") or os.environ.get("PRIVATE_KEY")
        if not private_key:
            raise RuntimeError(
                "Private key required for encryption. Set HAVEN_PRIVATE_KEY environment variable."
            )

        gate_condition = access_conditions[0] if access_conditions else {}
        token_address = str(gate_condition.get("contractAddress", "")).strip()
        if not token_address:
            raise ValueError(
                "token_contract/contractAddress is required for Haven-AOL encryption; it is not hard-coded."
            )

        threshold_raw = gate_condition.get("returnValueTest", {}).get("value", "1")
        try:
            threshold = int(str(threshold_raw))
        except ValueError:
            # For comparator-based conditions (e.g., owner_wallet comparison),
            # the value may be an address string, not a number. Default to 1.
            threshold = 1

        chain = self._get_chain(context)

        with open(video_path, "rb") as f:
            plaintext = f.read()

        original_hash = hashlib.sha256(plaintext).hexdigest()
        cid_value = str(context.options.get("cid", "")).strip()
        if not cid_value:
            # Upload CID is unknown pre-upload; derive a deterministic local CID key.
            cid_value = f"sha256:{original_hash}"
        gate_condition["cid"] = cid_value

        encrypted = encrypt_bytes(
            plaintext=plaintext,
            private_key=private_key,
            gate=GateParams(
                chain=chain,
                token_address=token_address,
                threshold=threshold,
                cid=cid_value,
            ),
        )

        encrypted_path = f"{video_path}.encrypted"
        with open(encrypted_path, "wb") as f:
            f.write(encrypted["ciphertext_bytes"])

        return {
            "ciphertext_path": encrypted_path,
            "data_to_encrypt_hash": encrypted["data_to_encrypt_hash"],
            "access_control_condition_hash": "",
            "chain": chain,
            "original_hash": original_hash,
            "metadata_path": "",
            "encrypted_key": encrypted["encrypted_key_b64"],
            "key_hash": encrypted["key_hash"],
            "iv": encrypted["iv_b64"],
        }

    def _get_access_conditions(
        self,
        context: PipelineContext,
    ) -> List[Dict[str, Any]]:
        """Get access control conditions for encryption.

        Access conditions define who can decrypt the content.
        They can be based on:
        - Wallet address ownership (owner_only)
        - NFT ownership (nft_gated)
        - Token balance (token_gated)
        - Public access (public)
        - Custom conditions provided in context

        Args:
            context: Pipeline context with options

        Returns:
            List of access control condition dictionaries

        Raises:
            ValueError: If unknown access pattern or missing required options
        """
        # Check for explicit conditions in context options
        if "access_conditions" in context.options:
            return context.options["access_conditions"]

        # Check for preset patterns
        pipeline_cfg = self._config.get("pipeline")
        config_pattern = getattr(pipeline_cfg, "access_pattern", None) if pipeline_cfg is not None else self._config.get("access_pattern")
        pattern = context.options.get("access_pattern") or config_pattern
        if not pattern:
            raise ValueError(
                "access_pattern is required for Haven-AOL encryption. "
                "Set --access-pattern or pipeline.access_pattern."
            )

        if pattern == "owner_only":
            return self._owner_only_conditions(context)
        elif pattern == "nft_gated":
            return self._nft_gated_conditions(context)
        elif pattern == "token_gated":
            return self._token_gated_conditions(context)
        elif pattern == "public":
            return self._public_conditions(context)
        else:
            raise ValueError(f"Unknown access pattern: {pattern}")

    def _owner_only_conditions(self, context: PipelineContext) -> List[Dict[str, Any]]:
        """Access restricted to wallet owner.

        Uses wallet signature as the access gate, consistent with the
        Haven-AOL owner_only specification.

        Args:
            context: Pipeline context with owner_wallet option and evm_chain

        Returns:
            Access control conditions for owner-only access

        Raises:
            ValueError: If owner_wallet not configured and cannot be derived
        """
        pipeline_cfg = self._config.get("pipeline")
        config_owner_wallet = (
            getattr(pipeline_cfg, "owner_wallet", None)
            if pipeline_cfg is not None
            else self._config.get("owner_wallet")
        )
        wallet_address = context.options.get("owner_wallet") or config_owner_wallet

        if not wallet_address:
            raise ValueError(
                "owner_wallet required for owner_only pattern. "
                "Set it in config or context options."
            )

        chain = self._get_chain(context)

        return [{
            "contractAddress": wallet_address,
            "standardContractType": "",
            "chain": chain,
            "method": "",
            "parameters": [],
            "returnValueTest": {
                "comparator": "=",
                "value": wallet_address,
            },
            "ownerWallet": wallet_address,
        }]

    def _nft_gated_conditions(self, context: PipelineContext) -> List[Dict[str, Any]]:
        """Access restricted to NFT holders.

        Args:
            context: Pipeline context with nft_contract option

        Returns:
            Access control conditions for NFT-gated access

        Raises:
            ValueError: If nft_contract not provided
        """
        pipeline_cfg = self._config.get("pipeline")
        config_nft_contract = (
            getattr(pipeline_cfg, "nft_contract", None)
            if pipeline_cfg is not None
            else self._config.get("nft_contract")
        )
        contract = context.options.get("nft_contract") or config_nft_contract
        if not contract:
            raise ValueError("nft_contract required for nft_gated pattern. "
                           "Set it in context options or config.")

        chain = self._get_chain(context)

        return [{
            "contractAddress": contract,
            "standardContractType": "ERC721",
            "chain": chain,
            "method": "balanceOf",
            "parameters": [":userAddress"],
            "returnValueTest": {
                "comparator": ">",
                "value": "0",
            },
        }]

    def _token_gated_conditions(self, context: PipelineContext) -> List[Dict[str, Any]]:
        """Access restricted to token holders.

        Requires a minimum token balance to decrypt.

        Args:
            context: Pipeline context with token_contract and min_balance options

        Returns:
            Access control conditions for token-gated access

        Raises:
            ValueError: If token_contract or min_balance not provided
        """
        pipeline_cfg = self._config.get("pipeline")
        config_token_contract = (
            getattr(pipeline_cfg, "token_contract", None)
            if pipeline_cfg is not None
            else self._config.get("token_contract")
        )
        contract = context.options.get("token_contract") or config_token_contract
        if not contract:
            raise ValueError("token_contract required for token_gated pattern")

        config_min_balance = (
            getattr(pipeline_cfg, "min_balance", None)
            if pipeline_cfg is not None
            else self._config.get("min_balance")
        )
        min_balance = context.options.get("min_balance") or config_min_balance
        if min_balance is None:
            raise ValueError("min_balance required for token_gated pattern")
        chain = self._get_chain(context)

        # Determine token standard
        config_token_standard = (
            getattr(pipeline_cfg, "token_standard", None)
            if pipeline_cfg is not None
            else self._config.get("token_standard")
        )
        token_standard = context.options.get("token_standard") or config_token_standard
        if token_standard is None:
            raise ValueError("token_standard required for token_gated pattern")

        if token_standard == "ERC20":
            return [{
                "contractAddress": contract,
                "standardContractType": "ERC20",
                "chain": chain,
                "method": "balanceOf",
                "parameters": [":userAddress"],
                "returnValueTest": {
                    "comparator": ">=",
                    "value": str(min_balance),
                },
            }]
        elif token_standard == "ERC721":
            # For ERC721, use balanceOf like NFT gating
            return [{
                "contractAddress": contract,
                "standardContractType": "ERC721",
                "chain": chain,
                "method": "balanceOf",
                "parameters": [":userAddress"],
                "returnValueTest": {
                    "comparator": ">=",
                    "value": str(min_balance),
                },
            }]
        else:
            raise ValueError(f"Unsupported token standard: {token_standard}")

    def _public_conditions(self, context: PipelineContext) -> List[Dict[str, Any]]:
        """Public access conditions - anyone can decrypt.

        This creates a condition that always returns true.
        Note: In practice, this may still require a valid wallet signature
        but doesn't restrict based on ownership.

        Args:
            context: Pipeline context with evm_chain option

        Returns:
            Access control conditions allowing public access
        """
        chain = self._get_chain(context)

        return [{
            "contractAddress": "",
            "standardContractType": "",
            "chain": chain,
            "method": "",
            "parameters": [],
            "returnValueTest": {
                "comparator": "=",
                "value": "true",
            },
        }]

    async def _save_encryption_metadata(
        self,
        video_id: int,
        metadata: EncryptionMetadata,
    ) -> None:
        """Save encryption metadata to database.

        Args:
            video_id: ID of the video record
            metadata: Encryption metadata to save
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import VideoRepository

            with get_db_session() as session:
                repo = VideoRepository(session)
                video = repo.get_by_id(video_id)

                if video:
                    repo.update(
                        video,
                        encrypted=True,
                        encryption_metadata=self._metadata_to_json(metadata),
                    )
                    logger.info(f"Saved encryption metadata for video {video_id}")
                else:
                    logger.warning(f"Video {video_id} not found, cannot save encryption metadata")

        except Exception as e:
            # Log error but don't fail the step - encryption succeeded
            logger.error(f"Failed to save encryption metadata to database: {e}")

    def _metadata_to_json(self, metadata: EncryptionMetadata) -> str:
        """Convert encryption metadata to JSON string.

        Args:
            metadata: Encryption metadata

        Returns:
            JSON string representation
        """
        import json
        return json.dumps({
            "ciphertext": metadata.ciphertext,
            "data_to_encrypt_hash": metadata.data_to_encrypt_hash,
            "dataToEncryptHash": metadata.data_to_encrypt_hash,  # camelCase for JS compatibility
            "encrypted_key": metadata.encrypted_key,
            "encryptedKey": metadata.encrypted_key,  # camelCase for JS compatibility
            "key_hash": metadata.key_hash,
            "keyHash": metadata.key_hash,  # camelCase for JS compatibility
            "iv": metadata.iv,
            "access_control_conditions": metadata.access_control_conditions,
            "accessControlConditions": metadata.access_control_conditions,  # camelCase
            "chain": metadata.chain,
        })

    async def on_skip(self, context: PipelineContext, reason: str) -> None:
        """Handle step skip - encryption not requested."""
        logger.debug(f"Encrypt step skipped: {reason}")

        # Create a skipped EncryptionJob record so TUI shows correct status
        if context.video_id:
            await self._create_skipped_encryption_job(context.video_id, reason)

    async def on_error(
        self,
        context: PipelineContext,
        error: Optional[StepError],
    ) -> None:
        """Handle encryption error."""
        logger.error(f"Encryption step failed: {error.message if error else 'Unknown error'}")

    # =========================================================================
    # Task 12: Job tracking helper methods
    # =========================================================================

    async def _create_encryption_job(
        self,
        video_id: int,
        bytes_total: int,
    ) -> Optional[int]:
        """Create an EncryptionJob record for tracking.

        Args:
            video_id: Video ID
            bytes_total: Total bytes to encrypt

        Returns:
            Job ID or None if creation failed
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import EncryptionJobRepository

            with get_db_session() as session:
                repo = EncryptionJobRepository(session)
                job = repo.create(
                    video_id=video_id,
                    status="encrypting",
                    bytes_total=bytes_total,
                )
                logger.debug(f"Created EncryptionJob {job.id} for video {video_id}")
                return job.id
        except Exception as e:
            logger.warning(f"Failed to create EncryptionJob: {e}")
            return None

    async def _create_skipped_encryption_job(
        self,
        video_id: int,
        reason: str,
    ) -> Optional[int]:
        """Create an EncryptionJob record marked as skipped.

        This is called when encryption is skipped due to configuration
        (encrypt=false) so the TUI correctly shows encryption as skipped
        rather than pending.

        Args:
            video_id: Video ID
            reason: Reason for skipping (e.g., "encryption disabled")

        Returns:
            Job ID or None if creation failed
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import EncryptionJobRepository

            with get_db_session() as session:
                repo = EncryptionJobRepository(session)
                job = repo.create(
                    video_id=video_id,
                    status="skipped",
                    bytes_total=0,
                )
                # Update with skip reason
                job.error_message = reason
                session.commit()
                logger.debug(f"Created skipped EncryptionJob {job.id} for video {video_id}")
                return job.id
        except Exception as e:
            logger.warning(f"Failed to create skipped EncryptionJob: {e}")
            return None

    async def _update_job_progress(
        self,
        video_id: int,
        bytes_processed: int,
        progress_percent: float,
        bytes_total: int,
        encrypt_speed: int = 0,
    ) -> None:
        """Update EncryptionJob progress.

        Args:
            video_id: Video ID
            bytes_processed: Bytes encrypted so far
            progress_percent: Progress percentage (0-100)
            bytes_total: Total bytes to encrypt
            encrypt_speed: Encryption speed in bytes/sec
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import EncryptionJobRepository, PipelineSnapshotRepository

            with get_db_session() as session:
                job_repo = EncryptionJobRepository(session)
                if self._job_id:
                    job_repo.update_progress(self._job_id, bytes_processed, encrypt_speed)

                # Also update pipeline snapshot
                snapshot_repo = PipelineSnapshotRepository(session)
                snapshot_repo.update_stage(
                    video_id=video_id,
                    stage="encrypt",
                    status="active",
                    progress_percent=progress_percent,
                    stage_speed=encrypt_speed,
                )
                snapshot_repo.update_bytes_metrics(
                    video_id=video_id,
                    encrypted_bytes=bytes_processed,
                )
        except Exception as e:
            logger.debug(f"Failed to update EncryptionJob progress: {e}")

    async def _complete_encryption_job(
        self,
        job_id: int,
        encrypted_ref: Optional[str] = None,
    ) -> None:
        """Mark EncryptionJob as completed.

        Args:
            job_id: Job ID
            encrypted_ref: Optional encrypted reference/hash
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import EncryptionJobRepository

            with get_db_session() as session:
                repo = EncryptionJobRepository(session)
                repo.update_status(job_id, "completed", encrypted_ref=encrypted_ref)
                logger.debug(f"Completed EncryptionJob {job_id}")
        except Exception as e:
            logger.warning(f"Failed to complete EncryptionJob: {e}")

    async def _fail_encryption_job(self, job_id: int, error_message: str) -> None:
        """Mark EncryptionJob as failed.

        Args:
            job_id: Job ID
            error_message: Error description
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import EncryptionJobRepository

            with get_db_session() as session:
                repo = EncryptionJobRepository(session)
                repo.update_status(job_id, "failed", error_message=error_message)
                logger.debug(f"Failed EncryptionJob {job_id}: {error_message}")
        except Exception as e:
            logger.warning(f"Failed to mark EncryptionJob as failed: {e}")

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