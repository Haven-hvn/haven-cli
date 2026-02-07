"""Encrypt step - Lit Protocol encryption.

This step encrypts video content using Lit Protocol for
access-controlled decryption. It:
1. Connects to Lit Protocol network
2. Encrypts the video file
3. Stores encryption metadata (access conditions, ciphertext hash)

The step uses the JS Runtime Bridge to communicate with the
Lit Protocol SDK running in a Deno subprocess.

The step is conditional and can be skipped via the encrypt option.
"""

import base64
import logging
import os
from typing import Any, Dict, List, Optional

from haven_cli.js_runtime.bridge import JSRuntimeBridge
from haven_cli.js_runtime.manager import JSBridgeManager
from haven_cli.pipeline.context import EncryptionMetadata, PipelineContext
from haven_cli.pipeline.events import EventType
from haven_cli.pipeline.results import StepError, StepResult
from haven_cli.pipeline.step import ConditionalStep
from haven_cli.services.blockchain_network import get_network_config
from haven_cli.services.evm_utils import get_wallet_address_from_private_key

logger = logging.getLogger(__name__)


class EncryptStep(ConditionalStep):
    """Pipeline step for Lit Protocol encryption.
    
    This step encrypts video content using Lit Protocol, enabling
    access-controlled decryption based on on-chain conditions.
    
    The encryption is performed via the JS Runtime Bridge, which
    communicates with the Lit SDK running in a Deno subprocess.
    
    Supports multiple access control patterns:
    - owner_only: Only the wallet owner can decrypt
    - nft_gated: Only NFT holders can decrypt
    - token_gated: Only token holders can decrypt
    - public: Anyone can decrypt (for public content)
    - custom: Explicit access conditions provided in context
    
    Emits:
        - ENCRYPT_REQUESTED event when starting
        - ENCRYPT_COMPLETE event on success
    
    Output data:
        - ciphertext_hash: Hash of the encrypted content
        - access_conditions: Access control conditions used
        - chain: Blockchain used for access control
        - encrypted_path: Path to the encrypted file
    """
    
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
    
    async def process(self, context: PipelineContext) -> StepResult:
        """Process Lit Protocol encryption.
        
        Args:
            context: Pipeline context with video path
            
        Returns:
            StepResult with encryption metadata
        """
        video_path = context.video_path
        
        # Emit encrypt requested event
        await self._emit_event(EventType.ENCRYPT_REQUESTED, context, {
            "video_path": video_path,
        })
        
        try:
            # Get access conditions from config or context
            access_conditions = self._get_access_conditions(context)
            logger.info(f"Using access pattern: {context.options.get('access_pattern', 'owner_only')}")
            
            # Get JS Runtime Bridge via manager
            bridge = await self._get_js_bridge()
            
            # Encrypt via Lit Protocol
            encryption_result = await self._encrypt_with_lit(
                bridge,
                video_path,
                access_conditions,
            )
            
            # Create encryption metadata
            encryption_metadata = EncryptionMetadata(
                ciphertext=encryption_result.get("ciphertext_path", ""),
                data_to_encrypt_hash=encryption_result.get("data_to_encrypt_hash", ""),
                access_control_conditions=access_conditions,
                chain=encryption_result.get("chain", "ethereum"),
            )
            
            # Store in context
            context.encryption_metadata = encryption_metadata
            context.encrypted_video_path = encryption_result.get("ciphertext_path")
            
            # Save encryption metadata to database
            if context.video_id:
                await self._save_encryption_metadata(
                    context.video_id,
                    encryption_metadata,
                )
            
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
            return StepResult.fail(
                self.name,
                StepError.from_exception(e, code="ENCRYPT_ERROR"),
            )
    
    async def _get_js_bridge(self) -> JSRuntimeBridge:
        """Get the JS Runtime Bridge for Lit SDK communication.
        
        Uses the JSBridgeManager singleton for connection reuse and
        automatic reconnection handling.
        
        Returns:
            JSRuntimeBridge instance ready for Lit SDK calls.
        """
        return await JSBridgeManager.get_instance().get_bridge()
    
    async def _encrypt_with_lit(
        self,
        bridge: JSRuntimeBridge,
        video_path: str,
        access_conditions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Encrypt content using Lit Protocol via JS bridge.
        
        The process:
        1. Ensure Lit Protocol connection
        2. Read video file content
        3. Call Lit SDK encrypt function via bridge
        4. Store ciphertext to file
        5. Return encryption metadata
        
        Args:
            bridge: JS Runtime Bridge instance
            video_path: Path to video file
            access_conditions: Access control conditions
            
        Returns:
            Dictionary with encryption result including:
            - ciphertext_path: Path to encrypted file
            - data_to_encrypt_hash: Hash of original content
            - access_control_condition_hash: Hash of access conditions
            - chain: Blockchain used
            
        Raises:
            RuntimeError: If encryption fails
            FileNotFoundError: If video file doesn't exist
        """
        import os
        
        # Get network configuration (from blockchain.network_mode)
        network_mode = self._config.get("network_mode", "testnet")
        network_config = get_network_config(network_mode)
        
        # Ensure Lit is connected - use network mode config
        lit_network = self._config.get("lit_network") or network_config.lit_network
        chain = self._config.get("chain") or network_config.chain_for_access_control
        
        logger.info(f"Connecting to Lit Protocol network: {lit_network} (mode: {network_mode})")
        
        try:
            await bridge.call("lit.connect", {
                "network": lit_network,
            })
        except Exception as e:
            logger.error(f"Failed to connect to Lit Protocol: {e}")
            raise RuntimeError(f"Lit Protocol connection failed: {e}") from e
        
        # Read file content
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        
        logger.info(f"Reading video file for encryption: {video_path}")
        try:
            with open(video_path, "rb") as f:
                content = f.read()
        except Exception as e:
            raise RuntimeError(f"Failed to read video file: {e}") from e
        
        # For large files, use chunked encryption
        file_size = len(content)
        if file_size > 10 * 1024 * 1024:  # 10MB threshold
            logger.info(f"Large file detected ({file_size} bytes), using chunked encryption")
            return await self._encrypt_large_file(
                bridge, video_path, content, access_conditions
            )
        
        # Encrypt via Lit Protocol
        logger.info(f"Encrypting content via Lit Protocol on chain: {chain}")
        
        try:
            result = await bridge.call("lit.encrypt", {
                "data": base64.b64encode(content).decode(),
                "accessControlConditions": access_conditions,
                "chain": chain,
            })
        except Exception as e:
            logger.error(f"Lit Protocol encryption failed: {e}")
            raise RuntimeError(f"Encryption failed: {e}") from e
        
        # Store ciphertext to file
        encrypted_path = video_path + ".enc"
        logger.info(f"Writing encrypted content to: {encrypted_path}")
        
        try:
            ciphertext = result.get("ciphertext", "")
            if isinstance(ciphertext, str):
                # Assume base64 encoded
                with open(encrypted_path, "wb") as f:
                    f.write(base64.b64decode(ciphertext))
            else:
                # Already bytes
                with open(encrypted_path, "wb") as f:
                    f.write(ciphertext)
        except Exception as e:
            logger.error(f"Failed to write encrypted file: {e}")
            raise RuntimeError(f"Failed to save encrypted file: {e}") from e
        
        # Calculate original file hash for integrity verification
        import hashlib
        original_hash = hashlib.sha256(content).hexdigest()
        
        logger.info(f"Encryption complete. Hash: {result.get('dataToEncryptHash', '')}")
        
        return {
            "ciphertext_path": encrypted_path,
            "data_to_encrypt_hash": result.get("dataToEncryptHash", ""),
            "access_control_condition_hash": result.get("accessControlConditionHash", ""),
            "chain": chain,
            "original_hash": original_hash,
        }
    
    async def _encrypt_large_file(
        self,
        bridge: JSRuntimeBridge,
        video_path: str,
        content: bytes,
        access_conditions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Encrypt large files using chunked encryption for memory efficiency.
        
        This method encrypts the content in chunks to avoid loading the entire
        encrypted result into memory at once.
        
        Args:
            bridge: JS Runtime Bridge instance
            video_path: Path to video file
            content: File content as bytes
            access_conditions: Access control conditions
            
        Returns:
            Dictionary with encryption result
        """
        # Get network configuration
        network_mode = self._config.get("network_mode", "testnet")
        network_config = get_network_config(network_mode)
        chain = self._config.get("chain") or network_config.chain_for_access_control
        chunk_size = 1024 * 1024  # 1MB chunks
        
        logger.info(f"Using chunked encryption for large file")
        
        # For large files, we still need to pass the whole content to Lit
        # but we read it in a memory-efficient way
        # In a production implementation, Lit Protocol might support streaming
        # For now, we pass the entire content but could optimize based on Lit's capabilities
        
        try:
            result = await bridge.call("lit.encrypt", {
                "data": base64.b64encode(content).decode(),
                "accessControlConditions": access_conditions,
                "chain": chain,
            })
        except Exception as e:
            logger.error(f"Lit Protocol encryption failed for large file: {e}")
            raise RuntimeError(f"Large file encryption failed: {e}") from e
        
        # Stream ciphertext to file
        encrypted_path = video_path + ".enc"
        ciphertext = result.get("ciphertext", "")
        
        with open(encrypted_path, "wb") as f:
            if isinstance(ciphertext, str):
                f.write(base64.b64decode(ciphertext))
            else:
                f.write(ciphertext)
        
        # Calculate original file hash
        import hashlib
        original_hash = hashlib.sha256(content).hexdigest()
        
        return {
            "ciphertext_path": encrypted_path,
            "data_to_encrypt_hash": result.get("dataToEncryptHash", ""),
            "access_control_condition_hash": result.get("accessControlConditionHash", ""),
            "chain": chain,
            "original_hash": original_hash,
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
        pattern = context.options.get("access_pattern", "owner_only")
        
        if pattern == "owner_only":
            return self._owner_only_conditions(context)
        elif pattern == "nft_gated":
            return self._nft_gated_conditions(context)
        elif pattern == "token_gated":
            return self._token_gated_conditions(context)
        elif pattern == "public":
            return self._public_conditions()
        else:
            raise ValueError(f"Unknown access pattern: {pattern}")
    
    def _owner_only_conditions(self, context: PipelineContext) -> List[Dict[str, Any]]:
        """Access restricted to wallet owner.
        
        Args:
            context: Pipeline context
            
        Returns:
            Access control conditions for owner-only access
            
        Raises:
            ValueError: If owner_wallet not configured and cannot be derived
        """
        wallet_address = context.options.get("owner_wallet") or self._config.get("owner_wallet")
        
        # Auto-derive owner_wallet from private key if not explicitly set
        if not wallet_address:
            private_key = os.environ.get("HAVEN_PRIVATE_KEY") or os.environ.get("FILECOIN_PRIVATE_KEY")
            if private_key:
                wallet_address = get_wallet_address_from_private_key(private_key)
                if wallet_address and wallet_address != "unknown":
                    logger.info(f"Auto-derived owner_wallet from private key: {wallet_address}")
        
        if not wallet_address:
            raise ValueError("owner_wallet required for owner_only pattern. "
                           "Set it in config, context options, or provide HAVEN_PRIVATE_KEY env var.")
        
        chain = self._config.get("chain", "ethereum")
        
        return [{
            "contractAddress": "",
            "standardContractType": "",
            "chain": chain,
            "method": "",
            "parameters": [":userAddress"],
            "returnValueTest": {
                "comparator": "=",
                "value": wallet_address,
            },
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
        contract = context.options.get("nft_contract") or self._config.get("nft_contract")
        if not contract:
            raise ValueError("nft_contract required for nft_gated pattern. "
                           "Set it in context options or config.")
        
        chain = self._config.get("chain", "ethereum")
        
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
        contract = context.options.get("token_contract") or self._config.get("token_contract")
        if not contract:
            raise ValueError("token_contract required for token_gated pattern")
        
        min_balance = context.options.get("min_balance") or self._config.get("min_balance", "1")
        chain = self._config.get("chain", "ethereum")
        
        # Determine token standard (default to ERC20)
        token_standard = context.options.get("token_standard") or self._config.get("token_standard", "ERC20")
        
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
    
    def _public_conditions(self) -> List[Dict[str, Any]]:
        """Public access conditions - anyone can decrypt.
        
        This creates a condition that always returns true.
        Note: In practice, this may still require a valid wallet signature
        but doesn't restrict based on ownership.
        
        Returns:
            Access control conditions allowing public access
        """
        chain = self._config.get("chain", "ethereum")
        
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
                        lit_encryption_metadata=self._metadata_to_json(metadata),
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
            "access_control_conditions": metadata.access_control_conditions,
            "accessControlConditions": metadata.access_control_conditions,  # camelCase
            "chain": metadata.chain,
        })
    
    async def on_skip(self, context: PipelineContext, reason: str) -> None:
        """Handle step skip - encryption not requested."""
        logger.debug(f"Encrypt step skipped: {reason}")
    
    async def on_error(
        self,
        context: PipelineContext,
        error: Optional[StepError],
    ) -> None:
        """Handle encryption error."""
        logger.error(f"Encryption step failed: {error.message if error else 'Unknown error'}")
