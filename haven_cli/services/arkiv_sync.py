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
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Protocol

from haven_cli.pipeline.context import PipelineContext
from haven_cli.services.evm_utils import (
    InsufficientGasError,
    handle_evm_gas_error,
    validate_evm_config,
)

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
        private_key: Optional private key (defaults to FILECOIN_PRIVATE_KEY or ARKIV_PRIVATE_KEY env var)
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
    
    # Get shared key from Filecoin or legacy Arkiv key
    shared_key = private_key or os.getenv("FILECOIN_PRIVATE_KEY")
    legacy_override = os.getenv("ARKIV_PRIVATE_KEY")
    final_private_key = shared_key or legacy_override
    
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
    if final_enabled and final_private_key:
        try:
            wallet_address, chain_name, token_symbol = validate_evm_config(final_private_key, final_rpc_url)
            network_indicator = "🟢 MAINNET" if network_mode == "mainnet" else "🟡 TESTNET"
            logger.info(
                "✅ Arkiv sync enabled | "
                "%s | "
                "Chain: %s | "
                "Wallet Address: %s | "
                "Ensure you have %s for gas fees",
                network_indicator,
                chain_name,
                wallet_address,
                token_symbol
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
    Build public attributes for Arkiv entity indexing.
    
    Attributes are indexed and queryable on-chain for searching
    and duplicate detection.
    
    Args:
        context: Pipeline context with video metadata
        
    Returns:
        Dictionary of attributes for Arkiv
    """
    attributes: dict[str, str | int] = {}
    
    # Get video metadata if available
    video_metadata = context.video_metadata
    
    # Title
    if video_metadata and video_metadata.title:
        attributes["title"] = video_metadata.title
    else:
        # Use filename as fallback
        attributes["title"] = context.title
    
    # Creator handle
    if video_metadata and video_metadata.creator_handle:
        attributes["creator_handle"] = video_metadata.creator_handle
    
    # Source URI for provenance
    if video_metadata and video_metadata.source_uri:
        attributes["source_uri"] = video_metadata.source_uri
    
    # pHash for content matching
    if video_metadata and video_metadata.phash:
        attributes["phash"] = video_metadata.phash
    
    # CID hash for duplicate detection
    if context.upload_result and context.upload_result.root_cid:
        cid_hash = hashlib.sha256(
            context.upload_result.root_cid.encode()
        ).hexdigest()
        attributes["cid_hash"] = cid_hash
        attributes["root_cid"] = context.upload_result.root_cid
    
    # MIME type
    if video_metadata and video_metadata.mime_type:
        attributes["mime_type"] = video_metadata.mime_type
    
    # Encryption status
    if context.encryption_metadata:
        attributes["is_encrypted"] = 1
    
    # Created timestamp
    attributes["created_at"] = datetime.now(timezone.utc).isoformat()
    
    return attributes


def _build_payload(context: PipelineContext) -> dict[str, Any]:
    """
    Build the entity payload for Arkiv.
    
    The payload contains the video metadata that will be stored on-chain.
    It includes only essential data that cannot be recalculated or is
    needed for restoration.
    
    Args:
        context: Pipeline context with video metadata
        
    Returns:
        Dictionary payload for Arkiv entity
    """
    payload: dict[str, Any] = {
        "version": "1.0",
        "type": "video",
        "archived_at": datetime.now(timezone.utc).isoformat(),
    }
    
    # Video metadata
    video_metadata = context.video_metadata
    if video_metadata:
        payload["duration"] = video_metadata.duration
        payload["file_size"] = video_metadata.file_size
        if video_metadata.codec:
            payload["codec"] = video_metadata.codec
    
    # Upload result - Filecoin CIDs
    if context.upload_result:
        payload["root_cid"] = context.upload_result.root_cid
        if context.upload_result.piece_cid:
            payload["piece_cid"] = context.upload_result.piece_cid
    
    # Analysis result
    if context.analysis_result:
        payload["has_ai_data"] = True
        payload["tag_count"] = len(context.analysis_result.tags)
        payload["timestamp_count"] = len(context.analysis_result.timestamps)
        if context.analysis_result.confidence > 0:
            payload["analysis_confidence"] = context.analysis_result.confidence
    
    # Encryption info
    if context.encryption_metadata:
        payload["encrypted"] = True
        payload["encryption_chain"] = context.encryption_metadata.chain
        if context.encryption_metadata.ciphertext:
            payload["encryption_ciphertext"] = context.encryption_metadata.ciphertext
        if context.encryption_metadata.data_to_encrypt_hash:
            payload["encryption_data_hash"] = context.encryption_metadata.data_to_encrypt_hash
    
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
        cid_hash: str,
    ) -> dict[str, Any] | None:
        """
        Find an existing entity by CID hash.
        
        Args:
            cid_hash: The CID hash to search for
            
        Returns:
            Existing entity dict with 'entity_key' if found, None otherwise
        """
        if not self.config.enabled:
            return None
        
        try:
            from arkiv.types import KEY, ATTRIBUTES, PAYLOAD, CONTENT_TYPE, OWNER, CREATED_AT, QueryOptions
            
            client = self._get_client()
            
            # Build query for cid_hash attribute
            query = f'cid_hash = "{cid_hash}"'
            
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
                logger.info("Found existing Arkiv entity for cid_hash: %s", cid_hash)
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
        
        Creates a new entity or updates an existing one based on CID hash.
        
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
        
        # Get CID hash for duplicate detection
        cid_hash = attributes.get("cid_hash", "")
        
        # Check for existing entity
        existing = self.find_existing_entity(cid_hash)
        
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
            
            logger.error("❌ Arkiv sync failed: %s", exc, exc_info=True)
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
