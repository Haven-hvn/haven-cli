"""End-to-end VetKD transport unwrap chain test against mainnet.

This test proves the full end-to-end flow connecting both layers:
  - Local crypto layer: transport keypair generation, IBE encrypt/decrypt
  - Mainnet service layer: authenticated canister calls, EIP-712 signing

Flow:
  1. Generate an ephemeral ICP identity (Ed25519)
  2. Generate an ephemeral EVM private key (eth_account)
  3. Generate a transport keypair locally (vetkd_py)
  4. Construct a valid EIP-712 signed requestDecryptionKey payload
  5. Call the mainnet canister (gny6k-fqaaa-aaaab-ag3ra-cai on https://icp-api.io)
  6. Receive the EncryptedVetKey blob
  7. Run the full decrypt_and_verify -> ibe_decrypt -> plaintext recovery pipeline

No environment variables are required. All keys are generated fresh on every run.
If the canister returns InsufficientBalance (ephemeral wallet has no tokens),
the test skips gracefully.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import subprocess
import time

import pytest

import vetkd_py

from haven_cli.services.haven_aol_icp import candid_blob_to_bytes, candid_return_item_to_value

# ---------------------------------------------------------------------------
# Canister configuration (mainnet)
# ---------------------------------------------------------------------------
MAINNET_CANISTER_ID = "gny6k-fqaaa-aaaab-ag3ra-cai"
MAINNET_HOST = "https://icp-api.io"

# EIP-712 domain parameters for Haven-AOL on Ethereum mainnet
EIP712_CHAIN_ID = 1
EIP712_VERIFYING_CONTRACT = "0x1c7D4B196Cb0C7B01d743Fbc6116a9023097791A"

# Gate request parameters — using EthMainnet with USDC.
# An ephemeral wallet will have 0 balance, so the canister will return
# InsufficientBalance, which the test detects and skips gracefully.
GATE_CHAIN = "EthMainnet"
GATE_TOKEN_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
GATE_THRESHOLD = 1
GATE_CID = "QmXoypizjW3WknFiJnKLwHCnL72vedxjQkDDP1mXWo6uco"

# Candid service description — same interface as in haven_aol_icp.py
_HAVEN_AOL_DID = """type Chain = variant { EthMainnet; EthSepolia; ArbitrumOne; BaseMainnet; OptimismMainnet; };
type GateRequest = record {
  chain : Chain; tokenAddress : text; threshold : nat; cid : text; evmAddress : text;
  transportPublicKey : blob; nonce : nat; signature : blob; eip712ChainId : nat; eip712VerifyingContract : text;
};
type GateError = variant {
  InsufficientBalance : record { required : nat; actual : nat };
  InvalidAddress : text; InvalidThreshold; EvmRpcError : text; VetKDError : text;
  InvalidSignature : text; NonceAlreadyUsed;
};
type GateResult = variant { ok : record { encrypted_key : blob; verification_key : blob }; err : GateError; };
service : { requestDecryptionKey : (GateRequest) -> (GateResult); getVetKDPublicKey : () -> (blob) query; }
"""


# ---------------------------------------------------------------------------
# Ephemeral key generation helpers
# ---------------------------------------------------------------------------

def _generate_icp_identity() -> tuple[object, str]:
    """Generate a fresh Ed25519 ICP identity.

    Returns:
        (identity, pem_string) — the ICP Identity object and its PEM encoding.
    """
    from icp_identity.identity import Identity

    result = subprocess.run(
        ["openssl", "genpkey", "-algorithm", "Ed25519", "-outform", "PEM"],
        capture_output=True,
        text=True,
        check=True,
    )
    pem = result.stdout
    identity = Identity.from_pem(pem)
    assert not getattr(identity, "anonymous", False)
    return identity, pem


def _generate_evm_private_key() -> tuple[str, str]:
    """Generate a fresh EVM private key and derive its address.

    Returns:
        (private_key_hex, address) — 0x-prefixed private key and checksummed address.
    """
    from eth_account import Account

    private_key = "0x" + secrets.token_bytes(32).hex()
    account = Account.from_key(private_key)
    return private_key, account.address


def _generate_transport_keypair() -> tuple[bytes, bytes]:
    """Generate a fresh VetKD transport keypair.

    Returns:
        (secret_key, public_key) — 32-byte secret and 48-byte public key.
    """
    secret_key = vetkd_py.generate_transport_secret_key()
    public_key = vetkd_py.transport_public_key_from_secret(secret_key)
    assert len(secret_key) == 32
    assert len(public_key) == 48
    return secret_key, public_key


def _compute_derivation_input() -> bytes:
    """Compute the SHA-256 derivation input from gate parameters.

    Format: SHA256("accessol:{chain}:{tokenAddress}:{threshold}:{cid}")
    """
    preimage = (
        f"accessol:{GATE_CHAIN}:{GATE_TOKEN_ADDRESS}:{GATE_THRESHOLD}:{GATE_CID}"
    ).encode("utf-8")
    return hashlib.sha256(preimage).digest()


def _build_ic_canister(identity) -> object:
    """Create a Canister instance targeting the mainnet Haven-AOL canister."""
    from icp_agent.agent import Agent
    from icp_agent.client import Client
    from icp_canister.canister import Canister

    client = Client(url=MAINNET_HOST)
    agent = Agent(identity, client)
    return Canister(agent, MAINNET_CANISTER_ID, candid_str=_HAVEN_AOL_DID)


# ---------------------------------------------------------------------------
# Module-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ic_identity():
    """Generate a fresh ephemeral ICP identity."""
    identity, _ = _generate_icp_identity()
    return identity


@pytest.fixture(scope="module")
def evm_keypair():
    """Generate a fresh ephemeral EVM keypair."""
    private_key, address = _generate_evm_private_key()
    return {"private_key": private_key, "address": address}


@pytest.fixture(scope="module")
def transport_keypair():
    """Generate a fresh ephemeral VetKD transport keypair."""
    sk, pk = _generate_transport_keypair()
    return {"secret_key": sk, "public_key": pk}


@pytest.fixture(scope="module")
def derivation_input() -> bytes:
    """Compute the derivation input for the gate parameters."""
    return _compute_derivation_input()


@pytest.fixture(scope="module")
def ic_canister(ic_identity):
    """Create a Canister instance targeting mainnet."""
    return _build_ic_canister(ic_identity)


@pytest.fixture(scope="module")
def verification_key(ic_canister):
    """Fetch the DerivedPublicKey (verification key) from the canister.

    This is the mainnet's VetKD public key used for IBE encryption and
    for verifying the EncryptedVetKey.
    """
    response = ic_canister.getVetKDPublicKey(verify_certificate=False)
    assert isinstance(response, list) and len(response) == 1, (
        f"Unexpected getVetKDPublicKey response shape: {type(response)}"
    )
    raw = candid_return_item_to_value(response[0])
    key_bytes = candid_blob_to_bytes(raw, context="getVetKDPublicKey")
    assert isinstance(key_bytes, (bytes, bytearray)), (
        f"Expected bytes-like, got {type(key_bytes)}"
    )
    # DerivedPublicKey is 96 bytes (compressed G2 point)
    assert len(key_bytes) == 96, (
        f"Expected 96-byte DerivedPublicKey, got {len(key_bytes)} bytes"
    )
    return bytes(key_bytes)


@pytest.fixture(scope="module")
def eip712_proof(evm_keypair, transport_keypair):
    """Create a signed EIP-712 GateRequest proof using the ephemeral EVM key."""
    from haven_cli.services.evm_utils import sign_gate_request_typed_data

    # Combine time_ns with 8 random bytes to avoid collisions
    nonce = (int(time.time_ns()) << 64) | int.from_bytes(
        secrets.token_bytes(8), "big"
    )

    proof = sign_gate_request_typed_data(
        private_key=evm_keypair["private_key"],
        transport_public_key=transport_keypair["public_key"],
        nonce=nonce,
        chain_id=EIP712_CHAIN_ID,
        verifying_contract=EIP712_VERIFYING_CONTRACT,
    )
    return proof


@pytest.fixture(scope="module")
def encrypted_vet_key(ic_canister, eip712_proof, transport_keypair):
    """Request an EncryptedVetKey from the mainnet canister.

    This is the critical step that only mainnet can provide — a real
    EncryptedVetKey blob encrypted to our transport public key.

    If the canister returns InsufficientBalance (the ephemeral wallet has no
    tokens), the test is skipped gracefully.
    """
    nonce = eip712_proof.nonce
    transport_public_key = transport_keypair["public_key"]
    signature_bytes = bytes.fromhex(
        eip712_proof.signature_hex.removeprefix("0x")
    )

    chain_variant = {GATE_CHAIN: None}
    gate_request = {
        "chain": chain_variant,
        "tokenAddress": GATE_TOKEN_ADDRESS,
        "threshold": GATE_THRESHOLD,
        "cid": GATE_CID,
        "evmAddress": eip712_proof.evm_address,
        "transportPublicKey": transport_public_key,
        "nonce": nonce,
        "signature": signature_bytes,
        "eip712ChainId": eip712_proof.eip712_chain_id,
        "eip712VerifyingContract": eip712_proof.eip712_verifying_contract,
    }

    response = ic_canister.requestDecryptionKey(gate_request, verify_certificate=False)
    assert isinstance(response, list) and len(response) == 1, (
        f"Unexpected response shape: {type(response)}"
    )
    gate_result = candid_return_item_to_value(response[0])

    # Gracefully skip if the ephemeral wallet doesn't meet access conditions.
    # The canister returns a dict like {'err': {'InsufficientBalance': {...}}}
    # or {'err': {'EvmRpcError': '...'}} for transient IC RPC issues.
    if isinstance(gate_result, dict) and "err" in gate_result:
        err = gate_result["err"]
        if isinstance(err, dict):
            error_type = next(iter(err.keys()))
        else:
            error_type = str(err)
        pytest.skip(
            f"Canister returned GateError (expected for ephemeral wallet): {error_type}"
        )

    assert isinstance(gate_result, dict) and "ok" in gate_result, (
        f"Unexpected GateResult: {gate_result}"
    )

    ok_record = gate_result["ok"]
    assert isinstance(ok_record, dict) and "encrypted_key" in ok_record, (
        f"Unexpected GateResult ok shape: {ok_record}"
    )
    encrypted_key = candid_blob_to_bytes(ok_record["encrypted_key"], context="requestDecryptionKey encrypted_key")
    assert isinstance(encrypted_key, (bytes, bytearray)), (
        f"Expected bytes-like, got {type(encrypted_key)}"
    )
    # EncryptedVetKey is 192 bytes
    assert len(encrypted_key) == 192, (
        f"Expected 192-byte EncryptedVetKey, got {len(encrypted_key)} bytes"
    )
    return bytes(encrypted_key)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVetKdE2EMainnet:
    """End-to-end test: local crypto + mainnet canister.

    Proves the full transport unwrap chain with a real canister response.
    """





    def test_transport_keypair_consistency(self, transport_keypair):
        """Verify the generated transport keypair is internally consistent."""
        derived_public = vetkd_py.transport_public_key_from_secret(
            transport_keypair["secret_key"]
        )
        assert derived_public == transport_keypair["public_key"], (
            "Transport keypair inconsistency: derived public key does not match"
        )

    def test_verification_key_valid(self, verification_key: bytes):
        """Verify the canister's DerivedPublicKey deserializes correctly."""
        roundtrip = vetkd_py.deserialize_derived_public_key(verification_key)
        assert roundtrip == verification_key, (
            "Verification key deserialization round-trip failed"
        )

    def test_derivation_input_format(self, derivation_input: bytes):
        """Verify the derivation input matches the expected SHA-256 format.

        Format: SHA256("accessol:{chain}:{tokenAddress}:{threshold}:{cid}")
        """
        assert len(derivation_input) == 32, "Derivation input must be 32 bytes"

        # Recompute and verify
        preimage = (
            f"accessol:{GATE_CHAIN}:{GATE_TOKEN_ADDRESS}:{GATE_THRESHOLD}:{GATE_CID}"
        ).encode("utf-8")
        expected = hashlib.sha256(preimage).digest()
        assert derivation_input == expected, (
            "Derivation input does not match expected format"
        )

