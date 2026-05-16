"""Encrypt step - Haven-AOL encryption.

This step encrypts video content using the in-repo Haven-AOL implementation.
The step is conditional and can be skipped via the encrypt option.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
from concurrent.futures import Future
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from haven_cli.crypto.haven_aol_local import (
    GateParams,
    derivation_threshold_from_access_condition,
    encrypt_file_streaming,
)
from haven_cli.pipeline.context import EncryptionMetadata, PipelineContext
from haven_cli.pipeline.events import EventType
from haven_cli.pipeline.results import ErrorCategory, StepError, StepResult
from haven_cli.pipeline.step import ConditionalStep
from haven_cli.services.blockchain_network import get_network_config
from haven_cli.services.evm_utils import normalize_haven_aol_chain

logger = logging.getLogger(__name__)

# Pre-encrypt SHA-256 hashing occupies this share of the encrypt stage (0–100).
HASH_PROGRESS_WEIGHT_PERCENT = 15.0
PROGRESS_EMIT_INTERVAL_SECONDS = 1.0
PROGRESS_EMIT_MIN_DELTA_PERCENT = 1.0

# Errnos often seen for transient network / I/O issues (retry may help).
_TRANSIENT_ERRNOS: frozenset[int] = frozenset(
    e
    for e in (
        errno.ECONNABORTED,
        errno.ECONNRESET,
        errno.ECONNREFUSED,
        errno.ETIMEDOUT,
        errno.EPIPE,
        errno.EAGAIN,
        getattr(errno, "WSAETIMEDOUT", -1),
        getattr(errno, "WSAECONNRESET", -1),
    )
    if e >= 0
)


def _is_icp_transport_error(exc: BaseException) -> bool:
    """Check if *exc* is an icp-py-core TransportError.

    Uses duck-typing (checks for ``original_error`` attribute) rather than
    class name to avoid coupling to the exact icp-py-core exception hierarchy.
    """
    return hasattr(exc, "original_error")


def _classify_icp_transport_error(exc: BaseException) -> ErrorCategory:
    """Classify an ICP TransportError as TRANSIENT or PERMANENT.

    Inspects the original HTTP status code and response body from the
    boundary node to determine whether retrying might succeed.
    """
    original = getattr(exc, "original_error", None)
    if original is None:
        return ErrorCategory.TRANSIENT  # network-level, retry

    response = getattr(original, "response", None)
    if response is None:
        return ErrorCategory.TRANSIENT

    status = response.status_code

    # 429 Too Many Requests — always transient
    if status == 429:
        return ErrorCategory.TRANSIENT

    # 5xx — transient (server-side)
    if 500 <= status < 600:
        return ErrorCategory.TRANSIENT

    # 400 — inspect body for transient vs permanent
    if status == 400:
        try:
            body = (response.text or "").lower()
        except Exception:
            body = ""
        transient_signs = [
            "temporarily unavailable", "try again", "timeout",
            "overloaded", "rate limit", "throttl",
        ]
        for sign in transient_signs:
            if sign in body:
                return ErrorCategory.TRANSIENT
        # malformed CBOR, invalid sender, etc. — permanent
        permanent_signs = [
            "malformed", "invalid sender", "unknown api version",
            "bad encoding", "invalid canister", "canister_not_found",
            "specified canister does not exist", "invalid principal",
            "unauthorized", "forbidden",
        ]
        for sign in permanent_signs:
            if sign in body:
                return ErrorCategory.PERMANENT
        # Unknown 400 — treat as transient (could be a temporary gateway issue)
        return ErrorCategory.TRANSIENT

    # Other 4xx — permanent
    if 400 <= status < 500:
        return ErrorCategory.PERMANENT

    return ErrorCategory.TRANSIENT


def classify_encrypt_failure(exc: BaseException) -> ErrorCategory:
    """Classify an exception from the encrypt path for retry / reporting.

    Handles ICP ``TransportError`` (from ``icp-py-core``) by inspecting the
    original HTTP status code and response body to determine whether the error
    is transient (retry may help) or permanent (configuration / identity issue).
    """
    if isinstance(exc, (ValueError, FileNotFoundError, KeyError, TypeError)):
        return ErrorCategory.PERMANENT
    if isinstance(exc, (TimeoutError, ConnectionError, asyncio.TimeoutError, BrokenPipeError)):
        return ErrorCategory.TRANSIENT
    if isinstance(exc, OSError):
        err = getattr(exc, "errno", None)
        if err in _TRANSIENT_ERRNOS:
            return ErrorCategory.TRANSIENT
        winerr = getattr(exc, "winerror", None)
        if winerr in (10060, 10061):  # common Windows socket timeout / refused
            return ErrorCategory.TRANSIENT
    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        if any(
            s in msg
            for s in (
                "timeout",
                "temporarily unavailable",
                "connection reset",
                "connection refused",
                "econnrefused",
                "try again",
            )
        ):
            return ErrorCategory.TRANSIENT
    # ICP TransportError (icp-py-core HTTP failures) — classify by status code
    if _is_icp_transport_error(exc):
        return _classify_icp_transport_error(exc)
    return ErrorCategory.UNKNOWN


def step_error_from_encrypt_exception(exc: BaseException) -> StepError:
    """Build a StepError for encrypt failures with category-aware retry flags."""
    category = classify_encrypt_failure(exc)
    return StepError.from_exception(exc, code="ENCRYPT_ERROR", category=category)


def compute_encrypt_stage_progress(
    bytes_processed: int,
    file_size: int,
    *,
    phase: str,
    hash_weight_percent: float = HASH_PROGRESS_WEIGHT_PERCENT,
) -> float:
    """Map in-phase byte progress to overall encrypt-stage percent (0–100)."""
    if file_size <= 0:
        if phase == "hashing":
            return 0.0
        return min(100.0, hash_weight_percent)

    fraction = min(1.0, bytes_processed / file_size)
    if phase == "hashing":
        return min(hash_weight_percent, fraction * hash_weight_percent)

    encrypt_span = 100.0 - hash_weight_percent
    return min(100.0, hash_weight_percent + fraction * encrypt_span)


def should_emit_encrypt_progress(
    now: float,
    last_emit_at: float,
    last_progress_percent: float,
    progress_percent: float,
    *,
    interval_seconds: float = PROGRESS_EMIT_INTERVAL_SECONDS,
    min_delta_percent: float = PROGRESS_EMIT_MIN_DELTA_PERCENT,
    force: bool = False,
) -> bool:
    """Return True when a progress event or DB update should be emitted."""
    if force or progress_percent >= 100.0:
        return True
    if last_emit_at <= 0:
        return True
    if now - last_emit_at >= interval_seconds:
        return True
    return progress_percent - last_progress_percent >= min_delta_percent


def hash_file_sha256_with_progress(
    path: str,
    *,
    block_size: int = 1024 * 1024,
    progress_callback: Callable[[int], None] | None = None,
) -> str:
    """Hash a file with optional per-block progress callbacks (cumulative bytes read)."""
    hasher = hashlib.sha256()
    bytes_read = 0
    with open(path, "rb") as handle:
        while True:
            block = handle.read(block_size)
            if not block:
                break
            hasher.update(block)
            bytes_read += len(block)
            if progress_callback is not None:
                progress_callback(bytes_read)
    return hasher.hexdigest()


def build_encrypt_progress_payload(
    *,
    video_id: Optional[int],
    job_id: Optional[int],
    video_path: str,
    progress_percent: float,
    bytes_processed: int,
    bytes_total: int,
    phase: str,
    encrypt_speed: int,
    chunk_index: Optional[int] = None,
) -> Dict[str, Any]:
    """Build a normalized ENCRYPT_PROGRESS event payload for TUI and metrics."""
    payload: Dict[str, Any] = {
        "video_id": video_id,
        "job_id": job_id,
        "video_path": video_path,
        "progress": progress_percent,
        "progress_percent": progress_percent,
        "bytes_processed": bytes_processed,
        "bytes_total": bytes_total,
        "phase": phase,
        "encrypt_speed": encrypt_speed,
        "source_bytes_processed": bytes_processed,
    }
    if chunk_index is not None:
        payload["chunk_index"] = chunk_index
    return payload


class EncryptStep(ConditionalStep):
    """Pipeline step for Haven-AOL encryption.

    Supports multiple access control patterns:
    - owner_only: Only the wallet owner can decrypt
    - nft_gated: Only NFT holders can decrypt
    - token_gated: Only token holders can decrypt
    - custom: Explicit access conditions provided in context

    Note: The ``public`` pattern is NOT supported. The Haven-AOL canister
    requires ``threshold > 0`` and performs a real token balance check, so
    there is no way to create a universally-decryptable gate without a
    canister-level protocol change.

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
        self._progress_file_size: int = 0
        self._last_progress_emit_at: float = 0.0
        self._last_reported_progress: float = -1.0
        self._last_speed_bytes: int = 0
        self._last_speed_at: float = 0.0
        self._last_progress_phase: str = ""

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
        self._reset_progress_tracking()

        file_size = self._resolve_source_file_size(context, video_path)
        self._progress_file_size = file_size

        # Create EncryptionJob record for tracking
        if context.video_id:
            self._job_id = await self._create_encryption_job(context.video_id, file_size)
            await self._update_pipeline_snapshot(context.video_id, "encrypt", 0)

        # Emit encrypt requested event
        await self._emit_event(EventType.ENCRYPT_REQUESTED, context, {
            "video_path": video_path,
            "video_id": context.video_id,
            "job_id": self._job_id,
        })

        try:
            # Get access conditions from config or context
            access_conditions = self._get_access_conditions(context)
            pattern = context.options.get("access_pattern") or self._config.get(
                "access_pattern", "owner_only"
            )
            chain = self._get_chain(context)
            logger.info(
                "Encrypt starting video_id=%s path=%s chain=%s pattern=%s",
                context.video_id,
                video_path,
                chain,
                pattern,
            )

            # Encrypt via standalone Haven-AOL implementation (runs in a thread
            # so progress events can be scheduled from chunk callbacks).
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
                "video_id": context.video_id,
                "job_id": self._job_id,
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
            logger.error(
                "Encryption failed video_id=%s path=%s: %s",
                context.video_id,
                video_path,
                e,
                exc_info=True,
            )

            # Mark job as failed
            error_msg = str(e)
            if self._job_id and context.video_id:
                await self._fail_encryption_job(self._job_id, error_msg)
                await self._update_pipeline_snapshot(
                    context.video_id, "encrypt", 0, status="failed", error=error_msg
                )

            return StepResult.fail(self.name, step_error_from_encrypt_exception(e))

    def _reset_progress_tracking(self) -> None:
        """Reset per-run progress throttling and speed measurement state."""
        self._progress_file_size = 0
        self._last_progress_emit_at = 0.0
        self._last_reported_progress = -1.0
        self._last_speed_bytes = 0
        self._last_speed_at = 0.0
        self._last_progress_phase = ""

    @staticmethod
    def _resolve_source_file_size(context: PipelineContext, video_path: str) -> int:
        """Resolve source byte count for progress (metadata, then stat)."""
        if context.video_metadata and context.video_metadata.file_size:
            return int(context.video_metadata.file_size)
        return os.path.getsize(video_path)

    async def _report_encrypt_progress(
        self,
        context: PipelineContext,
        video_path: str,
        bytes_processed: int,
        file_size: int,
        phase: str,
        *,
        chunk_index: Optional[int] = None,
        force: bool = False,
    ) -> None:
        """Emit ENCRYPT_PROGRESS and persist job/snapshot updates (throttled)."""
        progress_percent = compute_encrypt_stage_progress(
            bytes_processed,
            file_size,
            phase=phase,
        )
        now = time.monotonic()
        if phase != self._last_progress_phase:
            self._last_speed_bytes = bytes_processed
            self._last_speed_at = now
            self._last_progress_phase = phase

        if not should_emit_encrypt_progress(
            now,
            self._last_progress_emit_at,
            self._last_reported_progress,
            progress_percent,
            force=force,
        ):
            return

        encrypt_speed = 0
        if self._last_speed_at > 0 and now > self._last_speed_at:
            elapsed = now - self._last_speed_at
            delta_bytes = max(0, bytes_processed - self._last_speed_bytes)
            encrypt_speed = int(delta_bytes / elapsed) if elapsed > 0 else 0

        self._last_progress_emit_at = now
        self._last_reported_progress = progress_percent
        self._last_speed_bytes = bytes_processed
        self._last_speed_at = now

        payload = build_encrypt_progress_payload(
            video_id=context.video_id,
            job_id=self._job_id,
            video_path=video_path,
            progress_percent=progress_percent,
            bytes_processed=bytes_processed,
            bytes_total=file_size,
            phase=phase,
            encrypt_speed=encrypt_speed,
            chunk_index=chunk_index,
        )
        await self._emit_event(EventType.ENCRYPT_PROGRESS, context, payload)

        if context.video_id and file_size > 0:
            effective_bytes = int(file_size * progress_percent / 100)
            await self._update_job_progress(
                context.video_id,
                effective_bytes,
                progress_percent,
                file_size,
                encrypt_speed,
            )

    def _schedule_encrypt_progress(
        self,
        loop: asyncio.AbstractEventLoop,
        progress_futures: list[Future[None]],
        context: PipelineContext,
        video_path: str,
        bytes_processed: int,
        file_size: int,
        phase: str,
        *,
        chunk_index: Optional[int] = None,
        force: bool = False,
    ) -> None:
        """Schedule async progress reporting from a worker thread."""
        fut = asyncio.run_coroutine_threadsafe(
            self._report_encrypt_progress(
                context,
                video_path,
                bytes_processed,
                file_size,
                phase,
                chunk_index=chunk_index,
                force=force,
            ),
            loop,
        )
        progress_futures.append(fut)

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

        gate_condition = access_conditions[0] if access_conditions else {}
        token_address = str(gate_condition.get("contractAddress", "")).strip()
        if not token_address:
            raise ValueError(
                "token_contract/contractAddress is required for Haven-AOL encryption; it is not hard-coded."
            )

        threshold = derivation_threshold_from_access_condition(gate_condition)

        chain = self._get_chain(context)
        file_size = self._progress_file_size or os.path.getsize(video_path)
        self._progress_file_size = file_size

        loop = asyncio.get_running_loop()
        progress_futures: list[Future[None]] = []

        def on_hash_progress(bytes_read: int) -> None:
            self._schedule_encrypt_progress(
                loop,
                progress_futures,
                context,
                video_path,
                bytes_read,
                file_size,
                "hashing",
                force=bytes_read >= file_size,
            )

        def _run_hash() -> str:
            return hash_file_sha256_with_progress(
                video_path,
                progress_callback=on_hash_progress,
            )

        original_hash = await asyncio.to_thread(_run_hash)
        for fut in progress_futures:
            await asyncio.wrap_future(fut)
        progress_futures.clear()

        await self._report_encrypt_progress(
            context,
            video_path,
            file_size,
            file_size,
            "hashing",
            force=True,
        )

        cid_value = str(context.options.get("cid", "")).strip()
        if not cid_value:
            # Upload CID is unknown pre-upload; derive a deterministic local CID key.
            cid_value = f"sha256:{original_hash}"
        gate_condition["cid"] = cid_value

        chunk_size_raw = context.options.get("encrypt_chunk_size") or self._config.get(
            "encrypt_chunk_size",
            1024 * 1024,
        )
        chunk_size = int(chunk_size_raw)
        if chunk_size <= 0:
            raise ValueError("encrypt_chunk_size must be > 0")

        encrypted_path = f"{video_path}.encrypted"
        gate = GateParams(
            chain=chain,
            token_address=token_address,
            threshold=threshold,
            cid=cid_value,
        )

        def on_encrypt_progress(chunk_index: int, source_bytes_processed: int) -> None:
            self._schedule_encrypt_progress(
                loop,
                progress_futures,
                context,
                video_path,
                source_bytes_processed,
                file_size,
                "encrypting",
                chunk_index=chunk_index,
                force=source_bytes_processed >= file_size,
            )

        def _run_encrypt() -> Dict[str, Any]:
            return encrypt_file_streaming(
                input_path=video_path,
                output_path=encrypted_path,
                private_key="",
                gate=gate,
                chunk_size=chunk_size,
                progress_callback=on_encrypt_progress,
            )

        encrypted = await asyncio.to_thread(_run_encrypt)
        for fut in progress_futures:
            await asyncio.wrap_future(fut)

        await self._report_encrypt_progress(
            context,
            video_path,
            file_size,
            file_size,
            "encrypting",
            force=True,
        )

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

        Uses a token contract with threshold=1 to create a valid derivation
        input, while recording the owner wallet for downstream reference.
        The token_contract field provides a real ERC-20/721 address for the
        Haven-AOL derivation preimage (which requires a valid 0x address).

        Args:
            context: Pipeline context with owner_wallet option and evm_chain

        Returns:
            Access control conditions for owner-only access

        Raises:
            ValueError: If owner_wallet or token_contract not configured
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

        # token_contract provides the valid 0x address for derivation input
        config_token_contract = (
            getattr(pipeline_cfg, "token_contract", None)
            if pipeline_cfg is not None
            else self._config.get("token_contract")
        )
        contract = context.options.get("token_contract") or config_token_contract
        if not contract:
            raise ValueError(
                "token_contract required for owner_only pattern "
                "(provides the contract address for Haven-AOL derivation)."
            )

        chain = self._get_chain(context)

        return [{
            "contractAddress": contract,
            "standardContractType": "ERC20",
            "chain": chain,
            "method": "balanceOf",
            "parameters": [":userAddress"],
            "returnValueTest": {
                "comparator": ">=",
                "value": "1",
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

        NOT SUPPORTED: The Haven-AOL canister requires threshold > 0 and
        performs a real ERC-20/721 balanceOf check against the token contract.
        There is no "public" mode in the canister — threshold=0 is rejected
        with #InvalidThreshold, and any non-zero threshold against the zero
        address will fail the balance check with #InsufficientBalance.

        This pattern requires a canister-level protocol change to support
        truly public access (e.g., a dedicated public gate flow that skips
        the balance check).

        Raises:
            ValueError: Always — public pattern is not supported by the canister.
        """
        raise ValueError(
            "The 'public' access pattern is not supported by the Haven-AOL canister. "
            "The canister requires threshold > 0 and performs a real token balance check, "
            "so there is no way to create a universally-decryptable gate. "
            "Use 'token_gated' with a widely-held token and threshold=1 as an alternative, "
            "or request a canister protocol change to add a public-access mode."
        )

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
            logger.error(
                "Failed to save encryption metadata to database: %s",
                e,
                exc_info=True,
            )

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
        # Failure is already logged with traceback in process(); avoid duplicate ERROR lines.
        logger.debug(
            "Encrypt on_error hook video_id=%s: %s",
            context.video_id,
            error.message if error else "Unknown error",
        )

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