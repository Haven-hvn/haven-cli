"""
Shared utilities for EVM-compatible blockchain operations.
Works across all EVM chains (Ethereum, Polygon, BSC, Avalanche, Arbitrum, Optimism, Base, etc.)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional, Tuple, TypedDict

logger = logging.getLogger(__name__)


class InsufficientGasError(Exception):
    """
    Raised when blockchain transaction fails due to insufficient gas funds.
    Works across all EVM-compatible chains (Ethereum, Polygon, BSC, Avalanche, etc.).
    """
    def __init__(
        self, 
        message: str, 
        wallet_address: str, 
        original_error: Exception,
        chain_name: str | None = None,
        native_token_symbol: str | None = None
    ):
        super().__init__(message)
        self.wallet_address = wallet_address
        self.original_error = original_error
        self.chain_name = chain_name
        self.native_token_symbol = native_token_symbol or "gas tokens"


_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

# Chains where Haven-AOL access-control conditions are enforced (token/NFT balances).
# Haven-AOL itself runs on Internet Computer mainnet; this list is only the EVM
# network that holds the gated asset referenced in encryption metadata.
SUPPORTED_HAVEN_AOL_CHAINS: tuple[str, ...] = (
    "EthMainnet",
    "EthSepolia",
    "ArbitrumOne",
    "BaseMainnet",
    "OptimismMainnet",
)

_HAVEN_AOL_CHAIN_ALIASES: dict[str, str] = {
    "ethmainnet": "EthMainnet",
    "ethereum": "EthMainnet",
    "eth-mainnet": "EthMainnet",
    "ethsepolia": "EthSepolia",
    "eth-sepolia": "EthSepolia",
    "sepolia": "EthSepolia",
    "arbitrumone": "ArbitrumOne",
    "arbitrum-one": "ArbitrumOne",
    "arbitrum": "ArbitrumOne",
    "basemainnet": "BaseMainnet",
    "base-mainnet": "BaseMainnet",
    "base": "BaseMainnet",
    "optimismmainnet": "OptimismMainnet",
    "optimism-mainnet": "OptimismMainnet",
    "optimism": "OptimismMainnet",
}


def normalize_haven_aol_chain(chain: str) -> str:
    """Normalize config/CLI input to a supported access-control asset chain name."""
    normalized = _HAVEN_AOL_CHAIN_ALIASES.get(chain.strip().lower())
    if normalized is None:
        valid = ", ".join(SUPPORTED_HAVEN_AOL_CHAINS)
        raise ValueError(
            f"Unsupported access-control asset chain {chain!r}. "
            f"Supported values: {valid}"
        )
    return normalized


class Eip712TypedDataDomain(TypedDict):
    name: str
    chainId: int
    verifyingContract: str


class Eip712TypedDataField(TypedDict):
    name: str
    type: str


class Eip712TypedDataMessage(TypedDict):
    evmAddress: str
    transportPublicKey: str
    nonce: int


class Eip712GateRequestTypedData(TypedDict):
    types: dict[str, list[Eip712TypedDataField]]
    primaryType: str
    domain: Eip712TypedDataDomain
    message: Eip712TypedDataMessage


@dataclass(frozen=True)
class GateRequestProof:
    """Signed EIP-712 proof payload for requestDecryptionKey."""

    evm_address: str
    nonce: int
    signature_hex: str
    eip712_chain_id: int
    eip712_verifying_contract: str
    typed_data: Eip712GateRequestTypedData


def _normalize_evm_address(address: str) -> str:
    candidate = address.strip()
    if not _EVM_ADDRESS_RE.fullmatch(candidate):
        raise ValueError(f"Invalid EVM address: {address!r}")
    return candidate


def _bytes_to_hex_prefixed(raw: bytes) -> str:
    return "0x" + raw.hex()


def build_gate_request_typed_data(
    evm_address: str,
    transport_public_key: bytes,
    nonce: int,
    chain_id: int,
    verifying_contract: str,
    app_name: str = "HavenAOL",
) -> Eip712GateRequestTypedData:
    """Build EIP-712 typed data for the GateRequest primary type."""
    if nonce < 0:
        raise ValueError("nonce must be >= 0")
    if chain_id <= 0:
        raise ValueError("chain_id must be > 0")
    if len(transport_public_key) == 0:
        raise ValueError("transport_public_key must not be empty")

    normalized_evm = _normalize_evm_address(evm_address)
    normalized_contract = _normalize_evm_address(verifying_contract)

    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "GateRequest": [
                {"name": "evmAddress", "type": "address"},
                {"name": "transportPublicKey", "type": "bytes"},
                {"name": "nonce", "type": "uint256"},
            ],
        },
        "primaryType": "GateRequest",
        "domain": {
            "name": app_name,
            "chainId": chain_id,
            "verifyingContract": normalized_contract,
        },
        "message": {
            "evmAddress": normalized_evm,
            "transportPublicKey": _bytes_to_hex_prefixed(transport_public_key),
            "nonce": nonce,
        },
    }


def sign_gate_request_typed_data(
    private_key: str,
    transport_public_key: bytes,
    nonce: int,
    chain_id: int,
    verifying_contract: str,
    app_name: str = "HavenAOL",
) -> GateRequestProof:
    """Create and sign a GateRequest EIP-712 payload using the wallet private key."""
    try:
        from eth_account import Account
        from eth_account.messages import encode_typed_data
    except ImportError as exc:
        raise RuntimeError(
            "eth-account is required for EIP-712 signing. Install haven-cli[blockchain]."
        ) from exc

    normalized_key = private_key.strip()
    if not normalized_key.startswith("0x"):
        normalized_key = f"0x{normalized_key}"

    signer = Account.from_key(normalized_key)
    typed_data = build_gate_request_typed_data(
        evm_address=signer.address,
        transport_public_key=transport_public_key,
        nonce=nonce,
        chain_id=chain_id,
        verifying_contract=verifying_contract,
        app_name=app_name,
    )
    signable_message = encode_typed_data(full_message=typed_data)
    signature = Account.sign_message(signable_message, normalized_key)
    signature_hex = signature.signature.hex()
    if not signature_hex.startswith("0x"):
        signature_hex = "0x" + signature_hex

    return GateRequestProof(
        evm_address=signer.address,
        nonce=nonce,
        signature_hex=signature_hex,
        eip712_chain_id=chain_id,
        eip712_verifying_contract=_normalize_evm_address(verifying_contract),
        typed_data=typed_data,
    )


def get_wallet_address_from_private_key(private_key: str) -> str:
    """
    Get the EVM wallet address from a private key.
    Works for all EVM-compatible chains (Ethereum, Polygon, BSC, Avalanche, etc.)
    since they all use the same address format (0x...).
    
    Args:
        private_key: The private key string (with or without 0x prefix)
        
    Returns:
        The EVM-compatible address (checksummed) or "unknown" on error
    """
    try:
        # Try to import eth_account
        from eth_account import Account
        
        # Normalize private key - ensure it has 0x prefix
        normalized_key = private_key.strip()
        if not normalized_key.startswith('0x'):
            normalized_key = f'0x{normalized_key}'
        
        # Create account from private key
        # eth_account works for all EVM chains since they share the same address derivation
        account = Account.from_key(normalized_key)
        return account.address
    except ImportError:
        logger.warning("eth_account not installed, cannot derive wallet address")
        return "unknown"
    except Exception as e:
        logger.warning("Failed to derive wallet address from private key: %s", e)
        return "unknown"


def detect_chain_from_rpc_url(rpc_url: str) -> Tuple[str, str]:
    """
    Detect blockchain network and native token from RPC URL.
    
    Args:
        rpc_url: The RPC URL string
        
    Returns:
        Tuple of (chain_name, native_token_symbol)
    """
    rpc_lower = rpc_url.lower()
    
    # Ethereum networks
    if "ethereum" in rpc_lower or "mainnet" in rpc_lower or "eth" in rpc_lower:
        if "sepolia" in rpc_lower or "goerli" in rpc_lower:
            return ("Ethereum Testnet", "ETH")
        return ("Ethereum", "ETH")
    
    # Polygon networks
    if "polygon" in rpc_lower or "matic" in rpc_lower:
        if "mumbai" in rpc_lower or "testnet" in rpc_lower:
            return ("Polygon Testnet", "MATIC")
        return ("Polygon", "MATIC")
    
    # Binance Smart Chain
    if "bsc" in rpc_lower or "binance" in rpc_lower:
        if "testnet" in rpc_lower:
            return ("BSC Testnet", "BNB")
        return ("BSC", "BNB")
    
    # Avalanche
    if "avalanche" in rpc_lower or "avax" in rpc_lower:
        if "fuji" in rpc_lower or "testnet" in rpc_lower:
            return ("Avalanche Testnet", "AVAX")
        return ("Avalanche", "AVAX")
    
    # Arbitrum
    if "arbitrum" in rpc_lower:
        if "goerli" in rpc_lower or "testnet" in rpc_lower:
            return ("Arbitrum Testnet", "ETH")
        return ("Arbitrum", "ETH")
    
    # Optimism
    if "optimism" in rpc_lower or "optimistic" in rpc_lower:
        if "goerli" in rpc_lower or "testnet" in rpc_lower:
            return ("Optimism Testnet", "ETH")
        return ("Optimism", "ETH")
    
    # Base
    if "base" in rpc_lower:
        if "goerli" in rpc_lower or "sepolia" in rpc_lower or "testnet" in rpc_lower:
            return ("Base Testnet", "ETH")
        return ("Base", "ETH")
    
    # Filecoin (EVM-compatible)
    if "filecoin" in rpc_lower or "fil" in rpc_lower:
        if "calibration" in rpc_lower or "testnet" in rpc_lower:
            return ("Filecoin Calibration", "tFIL")
        return ("Filecoin", "FIL")
    
    # Arkiv (uses GLM as gas token)
    if (
        "arkiv" in rpc_lower
        or "hoodi" in rpc_lower
        or "mendoza" in rpc_lower
        or "braga" in rpc_lower
    ):
        return ("Arkiv", "GLM")
    
    # Local/unknown
    if "localhost" in rpc_lower or "127.0.0.1" in rpc_lower:
        return ("Local Network", "ETH")
    
    # Default fallback
    return ("EVM Chain", "gas tokens")


def is_insufficient_funds_error(error: Exception) -> bool:
    """
    Check if an error indicates insufficient funds for gas.
    Works across different EVM RPC providers and error message formats.
    
    Args:
        error: The exception to check
        
    Returns:
        True if the error indicates insufficient funds
    """
    error_str = str(error).lower()
    error_message = ""
    
    # Extract error message from Web3RPCError
    if hasattr(error, 'args') and error.args:
        error_data = error.args[0] if error.args else {}
        if isinstance(error_data, dict):
            error_message = error_data.get('message', '').lower()
        else:
            error_message = str(error_data).lower()
    
    # Check for common insufficient funds error patterns across EVM chains
    insufficient_funds_patterns = [
        'insufficient funds',
        'insufficient balance',
        'not enough funds',
        'insufficient gas',
        'gas required exceeds allowance',
        'execution reverted: insufficient',
        'out of gas',
        'balance too low',
    ]
    
    combined_error = f"{error_str} {error_message}".lower()
    return any(pattern in combined_error for pattern in insufficient_funds_patterns)


def handle_evm_gas_error(
    error: Exception,
    private_key: str | None,
    rpc_url: str,
    context: str = "blockchain operation"
) -> InsufficientGasError:
    """
    Handle EVM gas errors by extracting wallet address and chain information.
    Works across all EVM-compatible chains.
    
    Args:
        error: The exception that occurred
        private_key: The private key to derive wallet address from
        rpc_url: The RPC URL to detect chain from
        context: Context string for logging (e.g., "Arkiv sync", "Filecoin upload")
        
    Returns:
        InsufficientGasError with wallet address and chain info
        
    Raises:
        ValueError: If the error is not an insufficient funds error
    """
    if not is_insufficient_funds_error(error):
        raise ValueError("Error is not an insufficient funds error")
    
    # Get wallet address from private key (works for all EVM chains)
    wallet_address = get_wallet_address_from_private_key(private_key) if private_key else "unknown"
    
    # Detect chain and token from RPC URL
    chain_name, token_symbol = detect_chain_from_rpc_url(rpc_url)
    
    # Extract error message for logging
    error_message = ""
    if hasattr(error, 'args') and error.args:
        error_data = error.args[0] if error.args else {}
        if isinstance(error_data, dict):
            error_message = error_data.get('message', '')
        else:
            error_message = str(error_data)
    
    logger.error(
        "❌ %s failed due to insufficient gas funds | "
        "Chain: %s | "
        "Wallet Address: %s | "
        "Please send %s to this address | "
        "Error: %s",
        context,
        chain_name,
        wallet_address,
        token_symbol,
        error_message,
        exc_info=True
    )
    
    # Create and return error with context
    return InsufficientGasError(
        f"Insufficient {token_symbol} for gas. Please send {token_symbol} to address: {wallet_address}",
        wallet_address=wallet_address,
        original_error=error,
        chain_name=chain_name,
        native_token_symbol=token_symbol
    )


def validate_evm_config(private_key: str | None, rpc_url: str) -> Tuple[str, str, str]:
    """
    Validate EVM configuration and return wallet address and chain info.
    Useful for configuration validation before enabling blockchain features.
    
    Args:
        private_key: The private key to validate
        rpc_url: The RPC URL to detect chain from
        
    Returns:
        Tuple of (wallet_address, chain_name, native_token_symbol)
        
    Raises:
        ValueError: If private key is missing or invalid
    """
    if not private_key:
        raise ValueError("Private key is required for EVM operations")
    
    wallet_address = get_wallet_address_from_private_key(private_key)
    chain_name, token_symbol = detect_chain_from_rpc_url(rpc_url)
    
    return (wallet_address, chain_name, token_symbol)
