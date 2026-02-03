"""
Shared utilities for EVM-compatible blockchain operations.
Works across all EVM chains (Ethereum, Polygon, BSC, Avalanche, Arbitrum, Optimism, Base, etc.)
"""

from __future__ import annotations

import logging
from typing import Tuple, Optional
from decimal import Decimal

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
    if "arkiv" in rpc_lower or "hoodi" in rpc_lower or "mendoza" in rpc_lower:
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
