"""Arkiv blockchain synchronization service for Haven CLI.

This module provides functionality to sync video metadata to the Arkiv blockchain,
creating permanent, queryable records of archived content.

Adapted from backend/app/services/arkiv_sync.py for CLI usage.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

from haven_cli.crypto.gate_metadata import (
    GATE_METADATA_VERSION_V3,
    gate_metadata_any_to_json,
    is_gate_metadata_any,
)

from haven_cli.pipeline.context import PipelineContext
from haven_cli.services.piece_cid import require_piece_cid
from haven_cli.services.evm_utils import (
    InsufficientGasError,
    handle_evm_gas_error,
    is_legacy_kaolin_arkiv_rpc_url,
    is_non_golem_base_transaction_error,
    validate_evm_config,
)

# Minimum arkiv-sdk for Braga (RLP storage transactions via eth_sendTransaction).
MIN_ARKIV_SDK_VERSION = "1.0.0b2"

# ── ARKIV_FORMAT 2.0.0 wire tables ──────────────────────────────────────
#
# The Python SDK (arkiv-sdk 1.0.0b2) only expresses two annotation types:
# ``str`` and ``int`` (``Attributes = dict[str, str | int]``). The spec's
# ``addr``/``bytes32``/``i32`` tags are therefore realized as:
#   * gate_token / sha256_ct → lowercase hex ``str`` (≤128 B: 42 / 64 chars)
#   * gate_type / gate_chain / gate_threshold / gate_epoch / mime / dur_s → ``int``
# The chain stores one numeric annotation kind, so Python-written ints match
# JS ``i32(…)`` queries on the same attribute. Revisit if the SDK gains
# tagged constructors.

#: Usenet-style group taxonomy (replaces project/type/category/tags).
ARKIV_GROUP_VIDEO_FULL = "haven.video.full"

#: Haven-AOL chain variant → EIP-155 id (replaces "EthMainnet"-style strings).
CHAIN_VARIANT_TO_EIP155: dict[str, int] = {
    "EthMainnet": 1,
    "EthSepolia": 11155111,
    "ArbitrumOne": 42161,
    "BaseMainnet": 8453,
    "OptimismMainnet": 10,
}

#: Shared MIME enum (spec §MIME enum). Extend by appending, never renumber.
MIME_TO_ENUM: dict[str, int] = {
    "video/mp4": 1,
    "video/webm": 2,
    "video/quicktime": 3,
    "audio/mpeg": 4,
    "audio/wav": 5,
    "audio/ogg": 6,
    "image/png": 7,
    "image/jpeg": 8,
    "image/webp": 9,
    "image/gif": 10,
    "image/svg+xml": 11,
    "text/plain": 12,
    "text/markdown": 13,
    "application/pdf": 14,
}

#: Arkiv ``str`` slots are 128 bytes — truncate titles at a UTF-8 boundary.
TITLE_MAX_BYTES = 128


def _truncate_title(title: str) -> str:
    """Truncate *title* to ``TITLE_MAX_BYTES`` UTF-8 bytes (never split a char)."""
    raw = title.encode("utf-8")[:TITLE_MAX_BYTES]
    # Drop a trailing partial sequence (decode back-off).
    while raw:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw[:-1]
    return ""


def _mime_to_enum(mime_type: str | None) -> int | None:
    """Map a MIME string to the shared enum; ``None`` when unmapped/empty."""
    if not mime_type:
        return None
    return MIME_TO_ENUM.get(mime_type.split(";")[0].strip().lower())


logger = logging.getLogger(__name__)


@dataclass
class ArkivSyncConfig:
    """Configuration for Arkiv blockchain sync."""
    enabled: bool
    private_key: str | None
    rpc_url: str
    expires_in: int = 4 * 7 * 24 * 60 * 60  # Default: 4 weeks in seconds


class ArkivEntityClient(Protocol):
    """Protocol for Arkiv entity client operations."""
    
    def create_entity(
        self,
        payload: bytes,
        content_type: str,
        attributes: Any,  # Attributes type from arkiv
        expires_in: int,
    ) -> tuple[Any, object]:  # EntityKey, receipt
        ...

    def update_entity(
        self,
        key: Any,  # EntityKey type from arkiv
        payload: bytes,
        content_type: str,
        attributes: Any,  # Attributes type from arkiv
        expires_in: int,
    ) -> object:  # Returns TransactionReceipt
        ...
    
    def query_entities(
        self,
        query: str,
        options: Any | None = None,
    ) -> Any:  # Returns iterator of entities
        ...


class ArkivClientProtocol(Protocol):
    """Protocol for Arkiv client."""
    arkiv: ArkivEntityClient


def build_arkiv_config(
    private_key: str | None = None,
    rpc_url: str | None = None,
    enabled: bool | None = None,
    expires_in: int | None = None,
    network_mode: str = "testnet",
) -> ArkivSyncConfig:
    """
    Build Arkiv sync config from environment variables or explicit parameters.
    
    Args:
        private_key: Optional private key (defaults to HAVEN_PRIVATE_KEY env var)
        rpc_url: Optional RPC URL (defaults to ARKIV_RPC_URL env var or network_mode default)
        enabled: Optional enabled flag (defaults to ARKIV_SYNC_ENABLED env var)
        expires_in: Optional expiration in seconds (defaults to ARKIV_EXPIRATION_WEEKS env var)
        network_mode: Network mode ('mainnet' or 'testnet') for default RPC selection
        
    Returns:
        ArkivSyncConfig instance
    """
    # Import here to avoid circular imports
    from haven_cli.services.blockchain_network import get_network_config
    
    # Get network configuration for defaults
    network_config = get_network_config(network_mode)
    
    # Get private key from HAVEN_PRIVATE_KEY env var
    final_private_key = private_key or os.getenv("HAVEN_PRIVATE_KEY")
    
    # RPC URL priority: explicit > env var > network_mode default
    final_rpc_url = rpc_url or os.getenv("ARKIV_RPC_URL") or network_config.arkiv_rpc_url
    
    # Check if sync is enabled
    if enabled is not None:
        sync_enabled = enabled
    else:
        sync_enabled_str = os.getenv("ARKIV_SYNC_ENABLED", "false").lower()
        sync_enabled = sync_enabled_str in ("true", "1", "yes")
    
    # Read expiration weeks from environment variable
    if expires_in is not None:
        final_expires_in = expires_in
    else:
        expiration_weeks_str = os.getenv("ARKIV_EXPIRATION_WEEKS", "4")
        try:
            expiration_weeks = int(expiration_weeks_str)
            if expiration_weeks < 1:
                logger.warning("ARKIV_EXPIRATION_WEEKS must be at least 1, using default of 4 weeks")
                expiration_weeks = 4
        except ValueError:
            logger.warning("Invalid ARKIV_EXPIRATION_WEEKS value '%s', using default of 4 weeks", expiration_weeks_str)
            expiration_weeks = 4
        
        # Convert weeks to seconds
        final_expires_in = expiration_weeks * 7 * 24 * 60 * 60
    
    # Arkiv is enabled only if both: user toggle is on AND private key exists
    final_enabled = bool(final_private_key) and sync_enabled
    
    # Validate EVM config and log wallet info when enabled
    if is_legacy_kaolin_arkiv_rpc_url(final_rpc_url):
        logger.warning(
            "⚠️  Arkiv RPC URL targets legacy Kaolin testnet (%s). "
            "Kaolin was sunset; use Braga: https://braga.hoodi.arkiv.network/rpc",
            final_rpc_url,
        )

    if final_enabled and final_private_key:
        try:
            wallet_address, chain_name, token_symbol = validate_evm_config(final_private_key, final_rpc_url)
            network_indicator = "🟢 MAINNET" if network_mode == "mainnet" else "🟡 TESTNET"
            logger.info(
                "✅ Arkiv sync enabled | "
                "%s | "
                "RPC: %s | "
                "Chain: %s | "
                "Wallet Address: %s | "
                "Ensure you have %s for gas fees | "
                "Requires arkiv-sdk>=%s",
                network_indicator,
                final_rpc_url,
                chain_name,
                wallet_address,
                token_symbol,
                MIN_ARKIV_SDK_VERSION,
            )
            if network_mode == "mainnet":
                logger.warning("⚠️  Arkiv is configured for MAINNET - real tokens will be used!")
        except Exception as e:
            logger.warning("Failed to validate Arkiv EVM config: %s", e)
    
    if final_private_key and not sync_enabled:
        logger.info("🔒 Arkiv sync is disabled by user setting (ARKIV_SYNC_ENABLED=false)")
    elif not final_private_key:
        logger.info("🔑 Arkiv sync is disabled: no private key configured")
    
    if final_enabled:
        expiration_weeks = final_expires_in // (7 * 24 * 60 * 60)
        logger.info(
            "⏰ Arkiv expiration configured | "
            "Expiration: %d weeks (%d seconds)",
            expiration_weeks,
            final_expires_in
        )
    
    return ArkivSyncConfig(
        enabled=final_enabled,
        private_key=final_private_key,
        rpc_url=final_rpc_url,
        expires_in=final_expires_in
    )


def _extract_transaction_hash(receipt: Any) -> str | None:
    """
    Extract transaction hash from Arkiv SDK receipt object.
    
    The receipt object structure may vary, but typically contains:
    - receipt.transactionHash
    - receipt.hash
    - receipt.txHash
    - receipt.tx_hash (TransactionReceipt from web3)
    - receipt.transaction_hash
    - Or nested in receipt.receipt.transactionHash
    
    Returns the transaction hash as a string, or None if not found.
    """
    if not receipt:
        return None
    
    # Try common attribute names (arkiv-sdk uses tx_hash)
    for attr_name in ['tx_hash', 'transactionHash', 'hash', 'txHash', 'transaction_hash']:
        if hasattr(receipt, attr_name):
            try:
                value = getattr(receipt, attr_name)
                if value:
                    return str(value)
            except Exception:
                continue
    
    # Try dictionary access if receipt is dict-like
    if isinstance(receipt, dict):
        for key in ['transactionHash', 'hash', 'txHash', 'tx_hash', 'transaction_hash']:
            if key in receipt and receipt[key]:
                return str(receipt[key])
    
    # Try nested receipt object
    if hasattr(receipt, 'receipt'):
        try:
            nested_receipt = receipt.receipt
            for attr_name in ['transactionHash', 'hash', 'txHash', 'tx_hash', 'transaction_hash']:
                if hasattr(nested_receipt, attr_name):
                    value = getattr(nested_receipt, attr_name)
                    if value:
                        return str(value)
        except Exception:
            pass
    
    return None


def _log_transaction_info(
    receipt: Any,
    rpc_url: str,
    operation: str,
    entity_key: str | None = None
) -> None:
    """
    Log transaction information for developers to check on block explorer.
    
    Args:
        receipt: The transaction receipt from Arkiv SDK
        rpc_url: The RPC URL used for the transaction (helps identify the network)
        operation: Either "create" or "update"
        entity_key: The Arkiv entity key if available
    """
    transaction_hash = _extract_transaction_hash(receipt)
    
    # If extraction failed, try to extract from string representation
    if not transaction_hash and receipt:
        import re
        receipt_str = str(receipt)
        # Look for transaction hash pattern in string (0x followed by 64 hex chars)
        tx_hash_match = re.search(r'[\'"]?((?:0x)?[0-9a-fA-F]{64})[\'"]?', receipt_str)
        if tx_hash_match:
            transaction_hash = tx_hash_match.group(1)
            if not transaction_hash.startswith('0x'):
                transaction_hash = '0x' + transaction_hash
    
    if transaction_hash:
        # Determine network from RPC URL for helpful logging
        from haven_cli.services.evm_utils import detect_chain_from_rpc_url
        chain_name, _ = detect_chain_from_rpc_url(rpc_url)
        
        logger.info(
            "✅ Arkiv %s transaction confirmed | "
            "Transaction Hash: %s | "
            "Network: %s | "
            "Entity Key: %s",
            operation,
            transaction_hash,
            chain_name,
            entity_key or "N/A"
        )
    else:
        logger.warning(
            "⚠️ Arkiv %s transaction completed but could not extract transaction hash",
            operation
        )


def _build_attributes(context: PipelineContext) -> dict[str, str | int]:
    """
    Build public attributes for Arkiv entity indexing (ARKIV_FORMAT 2.0.0).

    Max 10 attributes for ``haven.video.full``. Attributes are indexed and
    queryable on-chain; every key below carries the query pattern that pays
    for its ~192 bytes. Cross-application contract: shared spec
    ``docs/entities/MEDIA_CONTENT_SPEC.md``.

    Tag mapping (Python SDK expresses str|int only): ``gate_token`` is a
    lowercase hex ``str`` (spec ``addr``), ``sha256_ct`` a 64-hex ``str``
    (spec ``bytes32``); all gate/enum facts are ``int``.

    Args:
        context: Pipeline context with video metadata

    Returns:
        Dictionary of attributes for Arkiv
    """
    attributes: dict[str, str | int] = {}

    # ── Group taxonomy + title (list display + prefix search) ──
    attributes["grp"] = ARKIV_GROUP_VIDEO_FULL
    title = context.title or ""
    if title:
        attributes["title"] = _truncate_title(title)

    # Get video metadata if available
    video_metadata = context.video_metadata

    # ── Gate corpus (enables community feed queries without payload fetch) ──
    #
    # ``is_gate_metadata_any`` accepts both v1 and v3 records. ``gate_type``
    # (int 1|3) replaces ``gate_version`` with no fallback; ``gate_type`` ==
    # gate["version"] numerically. ``gate_chain`` is the EIP-155 id.
    if context.encryption_metadata:
        gate = context.encryption_metadata.gate
        if is_gate_metadata_any(gate):
            attributes["gate_type"] = int(gate.get("version", 1))
            attributes["gate_token"] = str(gate["tokenAddress"]).lower()
            chain_id = CHAIN_VARIANT_TO_EIP155.get(str(gate["chain"]))
            if chain_id is None:
                logger.warning(
                    "Unknown gate chain variant %r — omitting gate_chain "
                    "(known: %s)",
                    gate["chain"],
                    sorted(CHAIN_VARIANT_TO_EIP155),
                )
            else:
                attributes["gate_chain"] = chain_id
            attributes["gate_threshold"] = int(gate.get("threshold", "1"))
            if gate.get("version") == GATE_METADATA_VERSION_V3:
                attributes["gate_epoch"] = int(gate["epoch"])

    # ── Ciphertext locator + dedup key ──
    #
    # ``sha256_ct`` is the hex digest of the record's root locator string
    # (the same computation the old ``cid_hash`` used, renamed — it never
    # hashed raw bytes). Stable, computable at sync time, near-unique:
    # ``find_existing_entity`` queries ``sha256_ct = "<hex>"``.
    if context.upload_result and context.upload_result.root_cid:
        attributes["sha256_ct"] = hashlib.sha256(
            context.upload_result.root_cid.encode()
        ).hexdigest()

    # ── Viewer dispatch + display/sort without payload fetch ──
    mime_enum = _mime_to_enum(video_metadata.mime_type if video_metadata else None)
    if mime_enum is not None:
        attributes["mime"] = mime_enum
    if video_metadata and video_metadata.duration and video_metadata.duration > 0:
        attributes["dur_s"] = int(video_metadata.duration)

    return attributes


def _build_payload(context: PipelineContext) -> dict[str, Any]:
    """
    Build the entity payload for Arkiv (ARKIV_FORMAT 2.0.0).

    Short snake_case keys, zero mirrors of attributes (attrs are always
    readable alongside payload, so mirrors only duplicate bytes).
    One locator per record class: encrypted records carry ``piece``,
    clear records carry ``fcid`` — never both.

    Args:
        context: Pipeline context with video metadata

    Returns:
        Dictionary payload for Arkiv entity
    """
    payload: dict[str, Any] = {}

    video_metadata = context.video_metadata

    if context.encryption_metadata:
        # ── Gated record ──
        # ``is_gate_metadata_any`` / ``gate_metadata_any_to_json`` accept
        # both v1 and v3 records. The frozen gate spellings
        # (chain: "EthMainnet", string threshold) stay inside the blob;
        # compact forms live in attributes.
        if (
            context.cid_encryption_metadata
            and context.encrypted_cid
            and is_gate_metadata_any(context.cid_encryption_metadata.gate)
        ):
            payload["cid_gate"] = gate_metadata_any_to_json(
                context.cid_encryption_metadata.gate
            )

        if is_gate_metadata_any(context.encryption_metadata.gate):
            payload["gate"] = gate_metadata_any_to_json(
                context.encryption_metadata.gate
            )

            if video_metadata and video_metadata.file_size:
                payload["size"] = video_metadata.file_size

            original_hash = context.get_step_data("encrypt", "original_hash")
            if original_hash:
                payload["pt_hash"] = original_hash

        # Encrypted locator (required for haven-dapp Synapse download).
        if context.upload_result and context.upload_result.root_cid:
            payload["piece"] = require_piece_cid(
                context.upload_result.piece_cid,
                context="Arkiv payload",
            )
    else:
        # ── Clear record: Filecoin locator only ──
        if context.upload_result and context.upload_result.root_cid:
            payload["fcid"] = context.upload_result.root_cid
            if video_metadata and video_metadata.file_size:
                payload["size"] = video_metadata.file_size

    # VLM analysis reference (primary VLM archival method).
    if context.upload_result and context.upload_result.vlm_json_cid:
        payload["vlm"] = context.upload_result.vlm_json_cid
    if context.analysis_result and context.analysis_result.analysis_model:
        payload["vlm_model"] = context.analysis_result.analysis_model

    # Provenance (payload-only in 2.0.0 — off the indexed surface).
    if video_metadata and video_metadata.source_uri:
        payload["src"] = video_metadata.source_uri
    if video_metadata and video_metadata.creator_handle:
        payload["creator"] = video_metadata.creator_handle
    if video_metadata and video_metadata.phash:
        payload["phash"] = video_metadata.phash
    if video_metadata and video_metadata.codec:
        payload["codecs"] = [video_metadata.codec]

    # Segment metadata for multi-segment recordings.
    if context.segment_metadata:
        segment_data: dict[str, Any] = {
            "segment_index": context.segment_metadata.segment_index,
        }
        if context.segment_metadata.start_timestamp:
            segment_data["start_timestamp"] = context.segment_metadata.start_timestamp
        if context.segment_metadata.end_timestamp:
            segment_data["end_timestamp"] = context.segment_metadata.end_timestamp
        if context.segment_metadata.mint_id:
            segment_data["mint_id"] = context.segment_metadata.mint_id
        if context.segment_metadata.recording_session_id:
            segment_data["recording_session_id"] = context.segment_metadata.recording_session_id

        payload["seg"] = segment_data

    # ── Attestation (canister-signed holding proof) ──
    #
    # Two payload shapes are emitted, distinguished by presence of "merkleProof":
    #
    #   • Single-CID  (sync_step._request_attestation → attest_holding):
    #         { …shared fields…, cidHash, signature }
    #
    #   • Merkle batch (batch_sync._attest_batch → batch_attest_holding, v2):
    #         { …shared fields…, cidCount, cidHash, merkleProof, merkleRoot,
    #           rootSignature }
    #
    # The dapp distinguishes the two via `isMerkleAttestation` (presence of
    # `merkleProof`); both shapes are accepted. See
    # docs/ipld-batch-attestation-proposal-v2.md §3.
    if context.attestation:
        a = context.attestation
        attestation_payload: dict[str, Any] = {
            "evmAddress":     a["evmAddress"],
            "chain":          a["chain"],
            "tokenAddress":   a["tokenAddress"],
            "threshold":      a["threshold"],
            "balanceAtCheck": a["balanceAtCheck"],
            "cidHash":        a["cidHash"],
            "timestamp":      a["timestamp"],
        }
        if "merkleProof" in a:
            # v2 Merkle batch attestation.
            attestation_payload["cidCount"]      = a["cidCount"]
            attestation_payload["merkleProof"]   = a["merkleProof"]
            attestation_payload["merkleRoot"]    = a["merkleRoot"]
            attestation_payload["rootSignature"] = a["rootSignature"]
        else:
            # Legacy single-CID attestation from attest_holding().
            attestation_payload["signature"] = a["signature"]
        payload["attn"] = attestation_payload

    return payload


def _is_413_error(exc: Exception) -> bool:
    """
    Check if an exception is an HTTP 413 Request Entity Too Large error.
    
    Args:
        exc: Exception to check
        
    Returns:
        True if the error is a 413 error
    """
    # Check the exception itself
    try:
        from requests.exceptions import HTTPError
        if isinstance(exc, HTTPError):
            if hasattr(exc, 'response') and exc.response is not None:
                return exc.response.status_code == 413
    except ImportError:
        pass
    
    # Check the exception chain
    current = exc
    checked = set()
    while current is not None and id(current) not in checked:
        checked.add(id(current))
        try:
            from requests.exceptions import HTTPError
            if isinstance(current, HTTPError):
                if hasattr(current, 'response') and current.response is not None:
                    if current.response.status_code == 413:
                        return True
        except ImportError:
            pass
        
        # Check if the error message contains 413
        error_str = str(current)
        if "413" in error_str and ("Request Entity Too Large" in error_str or "Entity Too Large" in error_str):
            return True
        
        # Move to the next exception in the chain
        current = getattr(current, '__cause__', None) or getattr(current, '__context__', None)
    
    return False


class ArkivSyncClient:
    """
    Handles pushing video metadata to Arkiv using the Arkiv SDK.
    
    Network calls are skipped when disabled or missing key.
    """

    def __init__(
        self,
        config: ArkivSyncConfig,
    ) -> None:
        """
        Initialize the Arkiv sync client.
        
        Args:
            config: Arkiv sync configuration
        """
        self.config = config
        self._client: ArkivClientProtocol | None = None

    def _get_client(self) -> ArkivClientProtocol:
        """Get or create the Arkiv client."""
        if self._client is None:
            if not self.config.private_key:
                raise ValueError("Arkiv private key missing")
            
            try:
                from arkiv import Arkiv
                from arkiv.account import NamedAccount
                from arkiv.provider import ProviderBuilder
                
                provider = ProviderBuilder().custom(self.config.rpc_url).build()
                account = NamedAccount.from_private_key("haven-cli", self.config.private_key)
                self._client = Arkiv(provider=provider, account=account)
            except ImportError:
                raise ImportError(
                    "arkiv package is required for blockchain sync. "
                    "Install with: pip install arkiv"
                )
        
        return self._client

    def find_existing_entity(
        self,
        sha256_ct: str,
    ) -> dict[str, Any] | None:
        """
        Find an existing entity by content locator hash.

        Args:
            sha256_ct: The ``sha256_ct`` hex digest to search for

        Returns:
            Existing entity dict with 'entity_key' if found, None otherwise
        """
        if not self.config.enabled:
            return None

        try:
            from arkiv.types import KEY, ATTRIBUTES, PAYLOAD, CONTENT_TYPE, OWNER, CREATED_AT, QueryOptions

            client = self._get_client()

            # Build query for sha256_ct attribute
            query = f'sha256_ct = "{sha256_ct}"'
            
            # Select only necessary fields
            required_fields = KEY | ATTRIBUTES | PAYLOAD | CONTENT_TYPE | OWNER | CREATED_AT
            query_options = QueryOptions(
                attributes=required_fields,
                max_results_per_page=10,
            )
            
            # Query entities
            entities = list(client.arkiv.query_entities(query=query, options=query_options))
            
            if entities:
                entity = entities[0]  # Take first match
                logger.info("Found existing Arkiv entity for sha256_ct: %s", sha256_ct)
                return {
                    "entity_key": str(entity.key) if hasattr(entity, "key") else None,
                    "entity": entity,
                }
            
            return None
            
        except Exception as exc:
            logger.warning("Failed to find existing Arkiv entity: %s", exc)
            return None

    def sync_context(
        self,
        context: PipelineContext,
    ) -> dict[str, Any] | None:
        """
        Sync a pipeline context to Arkiv.

        Creates a new entity or updates an existing one based on locator hash.
        
        Args:
            context: Pipeline context with video metadata
            
        Returns:
            Dictionary with entity_key and transaction_hash if successful,
            None if sync is disabled
            
        Raises:
            InsufficientGasError: If the wallet has insufficient gas funds
            Exception: For other sync errors
        """
        if not self.config.enabled:
            logger.info("Arkiv sync is disabled, skipping")
            return None
        
        # Build payload and attributes
        payload = _build_payload(context)
        attributes = _build_attributes(context)
        
        # Convert payload to bytes
        payload_bytes = json.dumps(payload).encode("utf-8")
        
        # Get sha256_ct for duplicate detection
        sha256_ct = attributes.get("sha256_ct", "")

        # Check for existing entity
        existing = self.find_existing_entity(sha256_ct)
        
        try:
            from arkiv.types import Attributes, EntityKey
            
            client = self._get_client()
            
            if existing and existing.get("entity_key"):
                # Update existing entity
                entity_key = EntityKey(existing["entity_key"])
                
                receipt = client.arkiv.update_entity(
                    entity_key,
                    payload=payload_bytes,
                    content_type="application/json",
                    attributes=Attributes(attributes),
                    expires_in=self.config.expires_in,
                )
                
                _log_transaction_info(receipt, self.config.rpc_url, "update", str(entity_key))
                
                transaction_hash = _extract_transaction_hash(receipt)
                
                logger.info("✅ Updated Arkiv entity: %s", entity_key)
                
                return {
                    "entity_key": str(entity_key),
                    "transaction_hash": transaction_hash or "",
                    "is_update": True,
                }
            
            else:
                # Create new entity
                entity_key, receipt = client.arkiv.create_entity(
                    payload=payload_bytes,
                    content_type="application/json",
                    attributes=Attributes(attributes),
                    expires_in=self.config.expires_in,
                )
                
                _log_transaction_info(receipt, self.config.rpc_url, "create", str(entity_key))
                
                transaction_hash = _extract_transaction_hash(receipt)
                
                logger.info("✅ Created Arkiv entity: %s", entity_key)
                
                return {
                    "entity_key": str(entity_key),
                    "transaction_hash": transaction_hash or "",
                    "is_update": False,
                }
                
        except Exception as exc:
            # Check for insufficient gas error
            if isinstance(exc, Exception) and is_insufficient_funds_error(exc):
                raise handle_evm_gas_error(
                    exc,
                    self.config.private_key,
                    self.config.rpc_url,
                    context="Arkiv sync"
                )
            
            # Check for 413 error (payload too large)
            if _is_413_error(exc):
                logger.error(
                    "❌ Arkiv sync failed: Payload too large (413). "
                    "The video metadata is too large for the Arkiv contract limits. "
                    "Error: %s",
                    exc
                )
            elif is_non_golem_base_transaction_error(exc):
                logger.error(
                    "❌ Arkiv sync failed: RPC rejected transaction (not Golem Base encoded). "
                    "Upgrade arkiv-sdk to >=%s for Braga testnet. "
                    "Current installs must use RLP storage transactions, not legacy "
                    "contract.execute().transact. Error: %s",
                    MIN_ARKIV_SDK_VERSION,
                    exc,
                )

            logger.error("❌ Arkiv sync failed: %s", exc, exc_info=True)
            raise


    def batch_sync_contexts(
        self,
        contexts: list[PipelineContext],
    ) -> list[dict[str, Any]]:
        """Create multiple Arkiv entities in a single execute() transaction.

        Args:
            contexts: List of PipelineContexts with upload_result populated.

        Returns:
            List of dicts with entity_key + transaction_hash, one per context.
            All share the same transaction_hash (single tx).

        Raises:
            InsufficientGasError: If wallet has insufficient gas
            Exception: For other sync errors
        """
        if not self.config.enabled:
            logger.info("Arkiv sync is disabled, skipping batch")
            return []

        if not contexts:
            return []

        # Phase 3 (BATCH_SYNC_REMEDIATION_PLAN.md): singleton short-circuit.
        # ``BatchBuilder`` only amortizes when there are ≥2 entities to
        # commit — a single ``create_entity`` call inside a batch costs the
        # same one transaction as calling it directly, but the batch path
        # historically skipped the ``find_existing_entity()`` dedup that
        # ``sync_context()`` performs. So a daemon flushing a singleton
        # for an already-archived CID would create a duplicate Arkiv
        # entity. Delegating to ``sync_context()`` for ``len == 1``
        # restores dedup parity at zero cost.
        #
        # Note: ``BatchSyncProcessor.__call__`` reads only ``entity_key``
        # from each result dict, so the extra ``is_update`` key on the
        # singleton path is harmless (no contract break).
        if len(contexts) == 1:
            logger.debug(
                "batch_sync_contexts: singleton — delegating to sync_context() "
                "for find_existing_entity() dedup parity"
            )
            result = self.sync_context(contexts[0])
            return [result] if result is not None else []

        try:
            from arkiv.types import Attributes

            client = self._get_client()

            # Build and execute batch via BatchBuilder (arkiv-sdk >= 1.0.0b3)
            with client.arkiv.batch() as batch:
                for ctx in contexts:
                    payload = _build_payload(ctx)
                    attributes = _build_attributes(ctx)
                    payload_bytes = json.dumps(payload).encode("utf-8")
                    batch.create_entity(
                        payload=payload_bytes,
                        content_type="application/json",
                        attributes=Attributes(attributes),
                        expires_in=self.config.expires_in,
                    )

            # Receipt contains .creates list of CreateEvent(key, owner, expiration)
            receipt = batch.receipt
            if receipt is None:
                raise RuntimeError("Batch execute returned no receipt")

            transaction_hash = _extract_transaction_hash(receipt) or ""

            results: list[dict[str, Any]] = []
            for create_event in receipt.creates:
                results.append({
                    "entity_key": str(create_event.key),
                    "transaction_hash": transaction_hash,
                })

            logger.info(
                "✅ Batch created %d Arkiv entities in 1 transaction (tx=%s)",
                len(results),
                transaction_hash or "unknown",
            )
            return results

        except Exception as exc:
            if isinstance(exc, Exception) and is_insufficient_funds_error(exc):
                raise handle_evm_gas_error(
                    exc,
                    self.config.private_key,
                    self.config.rpc_url,
                    context="Arkiv batch sync"
                )
            elif is_non_golem_base_transaction_error(exc):
                logger.error(
                    "❌ Arkiv batch sync failed: RPC rejected transaction (not Golem Base encoded). "
                    "Upgrade arkiv-sdk to >=%s for Braga testnet. "
                    "Current installs must use RLP storage transactions, not legacy "
                    "contract.execute().transact. Error: %s",
                    MIN_ARKIV_SDK_VERSION,
                    exc,
                )
            logger.error("❌ Arkiv batch sync failed: %s", exc, exc_info=True)
            raise


def is_insufficient_funds_error(error: Exception) -> bool:
    """
    Check if an error indicates insufficient funds for gas.
    
    Args:
        error: The exception to check
        
    Returns:
        True if the error indicates insufficient funds
    """
    from haven_cli.services.evm_utils import is_insufficient_funds_error as _check
    return _check(error)
