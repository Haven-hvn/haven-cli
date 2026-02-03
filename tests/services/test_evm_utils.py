"""Tests for EVM utilities.

Tests the EVM utility functions including:
- Wallet address derivation from private key
- Chain detection from RPC URL
- Insufficient funds error detection
- Gas error handling
"""

import pytest
from unittest.mock import MagicMock, patch

from haven_cli.services.evm_utils import (
    InsufficientGasError,
    detect_chain_from_rpc_url,
    is_insufficient_funds_error,
    validate_evm_config,
)


class TestDetectChain:
    """Tests for detect_chain_from_rpc_url."""
    
    def test_ethereum_mainnet(self):
        """Test Ethereum mainnet detection."""
        chain, token = detect_chain_from_rpc_url("https://ethereum.infura.io/v3/key")
        assert "Ethereum" in chain or "EVM" in chain
        assert token == "ETH" or token == "gas tokens"
    
    def test_polygon_mainnet(self):
        """Test Polygon mainnet detection."""
        chain, token = detect_chain_from_rpc_url("https://polygon-rpc.com")
        assert chain == "Polygon"
        assert token == "MATIC"
    
    def test_polygon_mumbai(self):
        """Test Polygon Mumbai testnet detection."""
        chain, token = detect_chain_from_rpc_url("https://polygon-mumbai.infura.io/v3/key")
        assert "Polygon" in chain
        assert token == "MATIC"
    
    def test_arkiv_network(self):
        """Test Arkiv network detection."""
        chain, token = detect_chain_from_rpc_url("https://mendoza.hoodi.arkiv.network/rpc")
        assert chain == "Arkiv"
        assert token == "GLM"
    
    def test_arkiv_hoodi(self):
        """Test Arkiv Hoodi network detection."""
        chain, token = detect_chain_from_rpc_url("https://hoodi.arkiv.network/rpc")
        assert chain == "Arkiv"
        assert token == "GLM"
    
    def test_local_network(self):
        """Test local network detection."""
        chain, token = detect_chain_from_rpc_url("http://localhost:8545")
        assert chain == "Local Network"
        assert token == "ETH"
    
    def test_local_network_127(self):
        """Test local network detection with 127.0.0.1."""
        chain, token = detect_chain_from_rpc_url("http://127.0.0.1:8545")
        assert chain == "Local Network"
        assert token == "ETH"
    
    def test_unknown_network(self):
        """Test unknown network fallback."""
        chain, token = detect_chain_from_rpc_url("https://unknown.rpc.example.com")
        assert chain == "EVM Chain"
        assert token == "gas tokens"


class TestInsufficientFundsError:
    """Tests for is_insufficient_funds_error."""
    
    def test_insufficient_funds_message(self):
        """Test detection of 'insufficient funds' message."""
        error = Exception("insufficient funds for gas")
        assert is_insufficient_funds_error(error) is True
    
    def test_insufficient_balance_message(self):
        """Test detection of 'insufficient balance' message."""
        error = Exception("insufficient balance")
        assert is_insufficient_funds_error(error) is True
    
    def test_not_enough_funds_message(self):
        """Test detection of 'not enough funds' message."""
        error = Exception("not enough funds for transaction")
        assert is_insufficient_funds_error(error) is True
    
    def test_gas_required_exceeds(self):
        """Test detection of 'gas required exceeds allowance' message."""
        error = Exception("gas required exceeds allowance")
        assert is_insufficient_funds_error(error) is True
    
    def test_execution_reverted(self):
        """Test detection of 'execution reverted: insufficient' message."""
        error = Exception("execution reverted: insufficient balance")
        assert is_insufficient_funds_error(error) is True
    
    def test_balance_too_low(self):
        """Test detection of 'balance too low' message."""
        error = Exception("balance too low")
        assert is_insufficient_funds_error(error) is True
    
    def test_wrong_error(self):
        """Test that unrelated errors return False."""
        error = Exception("network timeout")
        assert is_insufficient_funds_error(error) is False
    
    def test_web3_rpc_error(self):
        """Test detection with Web3RPCError style error."""
        error = MagicMock()
        error.args = [{"message": "insufficient funds for gas"}]
        
        assert is_insufficient_funds_error(error) is True
    
    def test_case_insensitive(self):
        """Test that detection is case insensitive."""
        error = Exception("INSUFFICIENT FUNDS FOR GAS")
        assert is_insufficient_funds_error(error) is True


class TestValidateEvmConfig:
    """Tests for validate_evm_config."""
    
    def test_missing_private_key(self):
        """Test validation with missing private key."""
        with pytest.raises(ValueError, match="Private key is required"):
            validate_evm_config(None, "https://arkiv.rpc")
    
    def test_empty_private_key(self):
        """Test validation with empty private key."""
        with pytest.raises(ValueError, match="Private key is required"):
            validate_evm_config("", "https://arkiv.rpc")


class TestInsufficientGasErrorClass:
    """Tests for the InsufficientGasError exception class."""
    
    def test_error_creation(self):
        """Test creating InsufficientGasError."""
        original_error = Exception("insufficient funds")
        error = InsufficientGasError(
            message="Gas required",
            wallet_address="0x123",
            original_error=original_error,
            chain_name="Arkiv",
            native_token_symbol="GLM"
        )
        
        assert str(error) == "Gas required"
        assert error.wallet_address == "0x123"
        assert error.original_error == original_error
        assert error.chain_name == "Arkiv"
        assert error.native_token_symbol == "GLM"
    
    def test_error_with_defaults(self):
        """Test creating InsufficientGasError with minimal params."""
        original_error = Exception("insufficient funds")
        error = InsufficientGasError(
            message="Gas required",
            wallet_address="0x123",
            original_error=original_error
        )
        
        assert str(error) == "Gas required"
        assert error.wallet_address == "0x123"
        assert error.original_error == original_error
        assert error.chain_name is None
        assert error.native_token_symbol == "gas tokens"


class TestEvmUtilsIntegration:
    """Integration-style tests that don't require mocking internal imports."""
    
    def test_detect_arkiv_default_rpc(self):
        """Test that the default Arkiv RPC URL is detected correctly."""
        rpc_url = "https://mendoza.hoodi.arkiv.network/rpc"
        chain, token = detect_chain_from_rpc_url(rpc_url)
        
        assert chain == "Arkiv"
        assert token == "GLM"
    
    def test_is_insufficient_funds_comprehensive(self):
        """Test various insufficient funds error patterns."""
        patterns = [
            "insufficient funds for gas",
            "insufficient balance",
            "not enough funds",
            "insufficient gas",
            "gas required exceeds allowance",
            "execution reverted: insufficient",
            "out of gas",
            "balance too low",
        ]
        
        for pattern in patterns:
            error = Exception(pattern)
            assert is_insufficient_funds_error(error) is True, f"Failed for: {pattern}"
    
    def test_is_insufficient_funds_negative_cases(self):
        """Test that non-fund errors return False."""
        patterns = [
            "network timeout",
            "connection refused",
            "invalid parameters",
            "not found",
            "server error",
        ]
        
        for pattern in patterns:
            error = Exception(pattern)
            assert is_insufficient_funds_error(error) is False, f"Failed for: {pattern}"
