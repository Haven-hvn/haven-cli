"""Tests for Haven-AOL EIP-712 gate request signing helpers."""

from __future__ import annotations

import pytest

from haven_cli.services.evm_utils import (
    build_gate_request_typed_data,
    normalize_haven_aol_chain,
    sign_gate_request_typed_data,
)


def test_build_gate_request_typed_data_has_expected_shape() -> None:
    typed_data = build_gate_request_typed_data(
        evm_address="0x1111111111111111111111111111111111111111",
        transport_public_key=b"\x04\x00",
        nonce=42,
        chain_id=1,
        verifying_contract="0x2222222222222222222222222222222222222222",
    )

    assert typed_data["primaryType"] == "GateRequest"
    assert typed_data["domain"]["name"] == "HavenAOL"
    assert typed_data["domain"]["chainId"] == 1
    assert typed_data["message"]["transportPublicKey"] == "0x0400"
    assert typed_data["message"]["nonce"] == 42


def test_build_gate_request_typed_data_rejects_invalid_addresses() -> None:
    with pytest.raises(ValueError, match="Invalid EVM address"):
        build_gate_request_typed_data(
            evm_address="0x123",
            transport_public_key=b"\x04\x00",
            nonce=1,
            chain_id=1,
            verifying_contract="0x2222222222222222222222222222222222222222",
        )


def test_sign_gate_request_typed_data_recovers_signer_address() -> None:
    account_mod = pytest.importorskip("eth_account")
    messages_mod = pytest.importorskip("eth_account.messages")

    account = account_mod.Account.create()
    proof = sign_gate_request_typed_data(
        private_key=account.key.hex(),
        transport_public_key=b"\x04\x00",
        nonce=999,
        chain_id=1,
        verifying_contract="0x3333333333333333333333333333333333333333",
    )

    signable = messages_mod.encode_typed_data(full_message=proof.typed_data)
    recovered = account_mod.Account.recover_message(signable, signature=proof.signature_hex)
    assert recovered.lower() == proof.evm_address.lower()


def test_normalize_haven_aol_chain_accepts_supported_aliases() -> None:
    assert normalize_haven_aol_chain("ethereum") == "EthMainnet"
    assert normalize_haven_aol_chain("eth-sepolia") == "EthSepolia"
    assert normalize_haven_aol_chain("arbitrum") == "ArbitrumOne"
    assert normalize_haven_aol_chain("base-mainnet") == "BaseMainnet"
    assert normalize_haven_aol_chain("optimism") == "OptimismMainnet"


def test_normalize_haven_aol_chain_rejects_unsupported_chain() -> None:
    with pytest.raises(ValueError, match="Unsupported EVM chain"):
        normalize_haven_aol_chain("polygon")

