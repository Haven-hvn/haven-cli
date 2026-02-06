"""Blockchain Network Configuration.

Provides unified mainnet/testnet configuration for all blockchain integrations:
- Lit Protocol (encryption)
- Filecoin (storage via Synapse)
- Arkiv (blockchain sync)

Usage:
    # Get network configuration based on user setting
    from haven_cli.config import get_config
    from haven_cli.services.blockchain_network import get_network_config
    
    config = get_config()
    network_config = get_network_config(config.blockchain.network_mode)
    
    # Use Lit network
    lit_network = network_config.lit_network
    
    # Use Filecoin RPC
    filecoin_rpc = network_config.filecoin_rpc_url
    
    # Use Arkiv RPC
    arkiv_rpc = network_config.arkiv_rpc_url
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class NetworkMode(str, Enum):
    """Blockchain network mode - unified across all integrations."""
    
    MAINNET = "mainnet"
    TESTNET = "testnet"
    
    @classmethod
    def from_string(cls, value: str) -> NetworkMode:
        """Parse network mode from string (case-insensitive)."""
        normalized = value.lower().strip()
        if normalized in ("mainnet", "main", "production", "prod"):
            return cls.MAINNET
        elif normalized in ("testnet", "test", "dev", "development", "calibration", "hoodi"):
            return cls.TESTNET
        else:
            raise ValueError(f"Unknown network mode: {value}. Use 'mainnet' or 'testnet'.")


@dataclass(frozen=True)
class NetworkConfig:
    """Network configuration for all blockchain integrations.
    
    This dataclass contains all network-specific settings that change
    based on whether the user selects mainnet or testnet mode.
    
    Attributes:
        mode: The network mode (mainnet or testnet)
        lit_network: Lit Protocol network name
        filecoin_rpc_url: Filecoin RPC endpoint
        filecoin_chain_id: Filecoin chain ID
        arkiv_rpc_url: Arkiv RPC endpoint
        arkiv_chain_name: Human-readable chain name
        chain_for_access_control: Chain name for Lit access control conditions
    """
    
    mode: NetworkMode
    lit_network: str
    filecoin_rpc_url: str
    filecoin_chain_id: int
    arkiv_rpc_url: str
    arkiv_chain_name: str
    chain_for_access_control: str
    
    @property
    def is_mainnet(self) -> bool:
        """Check if this is mainnet configuration."""
        return self.mode == NetworkMode.MAINNET
    
    @property
    def is_testnet(self) -> bool:
        """Check if this is testnet configuration."""
        return self.mode == NetworkMode.TESTNET


# Network configuration presets
_NETWORK_PRESETS: dict[NetworkMode, NetworkConfig] = {
    NetworkMode.MAINNET: NetworkConfig(
        mode=NetworkMode.MAINNET,
        lit_network="datil",  # Lit Protocol mainnet
        filecoin_rpc_url="https://api.node.glif.io/rpc/v1",
        filecoin_chain_id=314,  # Filecoin mainnet
        arkiv_rpc_url="https://mainnet.arkiv.network/rpc",  # Arkiv mainnet
        arkiv_chain_name="Arkiv Mainnet",
        chain_for_access_control="ethereum",  # Lit uses ethereum for mainnet
    ),
    NetworkMode.TESTNET: NetworkConfig(
        mode=NetworkMode.TESTNET,
        lit_network="datil-dev",  # Lit Protocol testnet (can also use "naga" or "naga-dev")
        filecoin_rpc_url="https://api.calibration.node.glif.io/rpc/v1",
        filecoin_chain_id=314159,  # Filecoin Calibration testnet
        arkiv_rpc_url="https://mendoza.hoodi.arkiv.network/rpc",  # Arkiv on Hoodi testnet
        arkiv_chain_name="Arkiv Hoodi Testnet",
        chain_for_access_control="ethereum",  # Lit uses ethereum for testnet too
    ),
}


def get_network_config(mode: str | NetworkMode) -> NetworkConfig:
    """Get network configuration for the specified mode.
    
    Args:
        mode: Network mode ('mainnet', 'testnet', or NetworkMode enum)
        
    Returns:
        NetworkConfig with all network-specific settings
        
    Raises:
        ValueError: If mode is not recognized
        
    Example:
        >>> config = get_network_config("testnet")
        >>> config.lit_network
        'datil-dev'
        >>> config.filecoin_rpc_url
        'https://api.calibration.node.glif.io/rpc/v1'
    """
    if isinstance(mode, str):
        mode = NetworkMode.from_string(mode)
    
    return _NETWORK_PRESETS[mode]


def get_network_config_from_env() -> NetworkConfig:
    """Get network configuration from environment variable.
    
    Checks HAVEN_NETWORK_MODE environment variable, defaults to testnet.
    
    Returns:
        NetworkConfig based on environment setting
        
    Example:
        >>> import os
        >>> os.environ["HAVEN_NETWORK_MODE"] = "mainnet"
        >>> config = get_network_config_from_env()
        >>> config.is_mainnet
        True
    """
    import os
    
    mode_str = os.environ.get("HAVEN_NETWORK_MODE", "testnet")
    return get_network_config(mode_str)


def validate_network_mode(mode: str) -> tuple[bool, Optional[str]]:
    """Validate a network mode string.
    
    Args:
        mode: Network mode string to validate
        
    Returns:
        Tuple of (is_valid, error_message)
        
    Example:
        >>> validate_network_mode("mainnet")
        (True, None)
        >>> validate_network_mode("invalid")
        (False, "Unknown network mode: invalid. Use 'mainnet' or 'testnet'.")
    """
    try:
        NetworkMode.from_string(mode)
        return True, None
    except ValueError as e:
        return False, str(e)


# Convenience functions for common use cases

def get_lit_network(mode: str | NetworkMode = "testnet") -> str:
    """Get Lit Protocol network name for the specified mode.
    
    Args:
        mode: Network mode ('mainnet' or 'testnet')
        
    Returns:
        Lit Protocol network name (e.g., 'datil', 'datil-dev')
    """
    return get_network_config(mode).lit_network


def get_filecoin_rpc_url(mode: str | NetworkMode = "testnet") -> str:
    """Get Filecoin RPC URL for the specified mode.
    
    Args:
        mode: Network mode ('mainnet' or 'testnet')
        
    Returns:
        Filecoin RPC endpoint URL
    """
    return get_network_config(mode).filecoin_rpc_url


def get_arkiv_rpc_url(mode: str | NetworkMode = "testnet") -> str:
    """Get Arkiv RPC URL for the specified mode.
    
    Args:
        mode: Network mode ('mainnet' or 'testnet')
        
    Returns:
        Arkiv RPC endpoint URL
    """
    return get_network_config(mode).arkiv_rpc_url


def get_chain_for_access_control(mode: str | NetworkMode = "testnet") -> str:
    """Get chain name for Lit access control conditions.
    
    Args:
        mode: Network mode ('mainnet' or 'testnet')
        
    Returns:
        Chain name for access control conditions
    """
    return get_network_config(mode).chain_for_access_control
