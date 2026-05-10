"""End-to-end VetKD transport unwrap chain test against mainnet.

This test proves the full end-to-end flow connecting both layers:
  - Local crypto layer: transport keypair generation, IBE encrypt/decrypt
  - Mainnet service layer: authenticated canister calls, EIP-712 signing

Flow:
  1. Generate a transport keypair locally (vetkd_py)
  2. Construct a valid EIP-712 signed requestDecryptionKey payload
  3. Call the mainnet canister (dciacuaaaaaaaadqlzuqcai on https://icp-api.io)
  4. Receive the EncryptedVetKey blob
  5. Run the full decrypt_and_verify -> ibe_decrypt -> AES key recovery pipeline

Skipped unless all required environment variables are set. This makes the test
safe for CI while runnable by an agent with mainnet credentials.

Required environment variables:
  HAVEN_ICP_IDENTITY_PEM_PATH       — Path to ICP identity PEM file
  HAVEN_PRIVATE_KEY                 — EVM private key for EIP-712 signing
  HAVEN_AOL_EIP712_VERIFYING_CONTRACT — EIP-712 verifying contract address
  HAVEN_AOL_TEST_CID                — CID to use in the gate request
  HAVEN_AOL_TEST_TOKEN_ADDRESS      — Token address for the gate
  HAVEN_AOL_TEST_CHAIN              — Chain name (default: EthMainnet)
  HAVEN_AOL_TEST_THRESHOLD          — Token threshold (default: 1)
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time

import pytest

import vetkd_py

# ---------------------------------------------------------------------------
# Canister configuration (mainnet)
# ---------------------------------------------------------------------------
MAINNET_CANISTER_ID = "dciacuaaaaaaaadqlzuqcai"
MAINNET_HOST = "https://icp-api.io"

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
type GateResult = variant { ok : blob; err : GateError; };
service : { requestDecryptionKey : (GateRequest) -> (GateResult); getVetKDPublicKey : () -> (blob); }
"""


def _check_required_env() -> dict[str, str] | None:
    """Check that all required env vars are set. Return config or None."""
    required = {
        "HAVEN_ICP_IDENTITY_PEM_PATH": os.environ.get("HAVEN_ICP_IDENTITY_PEM_PATH", "").strip(),
        "HAVEN_PRIVATE_KEY": os.environ.get("HAVEN_PRIVATE_KEY", "").strip(),
        "HAVEN_AOL_EIP712_VERIFYING_CONTRACT": os.environ.get(
            "HAVEN_AOL_EIP712_VERIFYING_CONTRACT", ""
        ).strip(),
        "HAVEN_AOL_TEST_CID": os.environ.get("HAVEN_AOL_TEST_CID", "").strip(),
        "HAVEN_AOL_TEST_TOKEN_ADDRESS": os.environ.get(
            "HAVEN_AOL_TEST_TOKEN_ADDRESS", ""
        ).strip(),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        return None
    # Add optional defaults
    required["HAVEN_AOL_TEST_CHAIN"] = os.environ.get(
        "HAVEN_AOL_TEST_CHAIN", "EthMainnet"
    ).strip()
    required["HAVEN_AOL_TEST_THRESHOLD"] = os.environ.get(
        "HAVEN_AOL_TEST_THRESHOLD", "1"
    ).strip()
    return required


# Skip all tests in this module if env vars are not configured
pytestmark = pytest.mark.skipif(
    _check_required_env() is None,
    reason=(
        "E2E mainnet test requires: HAVEN_ICP_IDENTITY_PEM_PATH, "
        "HAVEN_PRIVATE_KEY, HAVEN_AOL_EIP712_VERIFYING_CONTRACT, "
        "HAVEN_AOL_TEST_CID, HAVEN_AOL_TEST_TOKEN_ADDRESS"
    ),
)


@pytest.fixture(scope="module")
def env_config() -> dict[str, str]:
    """Validated environment configuration for the e2e test."""
    cfg = _check_required_env()
    assert cfg is not None, "Env config should be validated by skipif"
    return cfg


@pytest.fixture(scope="module")
def ic_identity(env_config):
    """Load ICP identity from PEM file."""
    from ic.identity import Identity

    pem_path = env_config["HAVEN_ICP_IDENTITY_PEM_PATH"]
    with open(pem_path, "r", encoding="utf-8") as f:
        pem = f.read()
    identity = Identity.from_pem(pem)
    assert not getattr(identity, "anonymous", False), "Anonymous identity not allowed"
    return identity


@pytest.fixture(scope="module")
def ic_canister(ic_identity):
    """Create a Canister instance targeting the mainnet Haven-AOL canister."""
    from ic.agent import Agent
    from ic.canister import Canister
    from ic.client import Client

    client = Client(MAINNET_HOST)
    agent = Agent(ic_identity, client)
    return Canister(
        agent=agent,
        canister_id=MAINNET_CANISTER_ID,
        candid=_HAVEN_AOL_DID,
    )


@pytest.fixture(scope="module")
def transport_keypair():
    """Generate a fresh transport keypair for this test run.

    In production, the secret key would be stored in
    HAVEN_AOL_TRANSPORT_SECRET_KEY_B64 and persisted across runs.
    For the e2e test, we generate a fresh one to prove the full flow.
    """
    secret_key = vetkd_py.generate_transport_secret_key()
    public_key = vetkd_py.transport_public_key_from_secret(secret_key)
    return {
        "secret_key": secret_key,
        "secret_key_b64": base64.b64encode(secret_key).decode("ascii"),
        "public_key": public_key,
        "public_key_b64": base64.b64encode(public_key).decode("ascii"),
    }


@pytest.fixture(scope="module")
def gate_params(env_config) -> dict:
    """Gate parameters for the canister request."""
    return {
        "chain": env_config["HAVEN_AOL_TEST_CHAIN"],
        "token_address": env_config["HAVEN_AOL_TEST_TOKEN_ADDRESS"],
        "threshold": int(env_config["HAVEN_AOL_TEST_THRESHOLD"]),
        "cid": env_config["HAVEN_AOL_TEST_CID"],
    }


@pytest.fixture(scope="module")
def derivation_input(gate_params) -> bytes:
    """Compute the SHA-256 derivation input from gate parameters.

    Format: SHA256("accessol:{chain}:{tokenAddress}:{threshold}:{cid}")
    """
    preimage = (
        f"accessol:{gate_params['chain']}:{gate_params['token_address']}:"
        f"{gate_params['threshold']}:{gate_params['cid']}"
    ).encode("utf-8")
    return hashlib.sha256(preimage).digest()


@pytest.fixture(scope="module")
def eip712_proof(env_config, transport_keypair):
    """Create a signed EIP-712 GateRequest proof."""
    from haven_cli.services.evm_utils import sign_gate_request_typed_data

    private_key = env_config["HAVEN_PRIVATE_KEY"]
    verifying_contract = env_config["HAVEN_AOL_EIP712_VERIFYING_CONTRACT"]
    transport_public_key = transport_keypair["public_key"]

    # Combine time_ns with 8 random bytes to avoid collisions
    nonce = (int(time.time_ns()) << 64) | int.from_bytes(secrets.token_bytes(8), "big")

    proof = sign_gate_request_typed_data(
        private_key=private_key,
        transport_public_key=transport_public_key,
        nonce=nonce,
        chain_id=1,
        verifying_contract=verifying_contract,
    )
    return proof


@pytest.fixture(scope="module")
def encrypted_vet_key(ic_canister, eip712_proof, gate_params, transport_keypair):
    """Request an EncryptedVetKey from the mainnet canister.

    This is the critical step that only mainnet can provide — a real
    EncryptedVetKey blob encrypted to our transport public key.
    """
    nonce = eip712_proof.nonce
    transport_public_key = transport_keypair["public_key"]
    signature_bytes = bytes.fromhex(
        eip712_proof.signature_hex.removeprefix("0x")
    )

    chain_variant = {gate_params["chain"]: None}
    gate_request = {
        "chain": chain_variant,
        "tokenAddress": gate_params["token_address"],
        "threshold": gate_params["threshold"],
        "cid": gate_params["cid"],
        "evmAddress": eip712_proof.evm_address,
        "transportPublicKey": transport_public_key,
        "nonce": nonce,
        "signature": signature_bytes,
        "eip712ChainId": eip712_proof.eip712_chain_id,
        "eip712VerifyingContract": eip712_proof.eip712_verifying_contract,
    }

    response = ic_canister.requestDecryptionKey(gate_request)
    assert isinstance(response, list) and len(response) == 1, (
        f"Unexpected response shape: {type(response)}"
    )
    gate_result = response[0]

    if isinstance(gate_result, dict) and "err" in gate_result:
        raise RuntimeError(
            f"Canister returned GateError: {gate_result['err']}"
        )
    assert isinstance(gate_result, dict) and "ok" in gate_result, (
        f"Unexpected GateResult: {gate_result}"
    )

    encrypted_key = gate_result["ok"]
    assert isinstance(encrypted_key, (bytes, bytearray)), (
        f"Expected bytes, got {type(encrypted_key)}"
    )
    # EncryptedVetKey is 192 bytes
    assert len(encrypted_key) == 192, (
        f"Expected 192-byte EncryptedVetKey, got {len(encrypted_key)} bytes"
    )
    return bytes(encrypted_key)


@pytest.fixture(scope="module")
def verification_key(ic_canister):
    """Fetch the DerivedPublicKey (verification key) from the canister."""
    response = ic_canister.getVetKDPublicKey()
    assert isinstance(response, list) and len(response) == 1, (
        f"Unexpected getVetKDPublicKey response shape: {type(response)}"
    )
    key_bytes = response[0]
    assert isinstance(key_bytes, (bytes, bytearray)), (
        f"Expected bytes, got {type(key_bytes)}"
    )
    # DerivedPublicKey is 96 bytes (compressed G2 point)
    assert len(key_bytes) == 96, (
        f"Expected 96-byte DerivedPublicKey, got {len(key_bytes)} bytes"
    )
    return bytes(key_bytes)


class TestVetKdE2EMainnet:
    """End-to-end test: local crypto + mainnet canister.

    Proves the full transport unwrap chain with a real canister response.
    """

    def test_full_transport_unwrap_chain(
        self,
        encrypted_vet_key: bytes,
        verification_key: bytes,
        derivation_input: bytes,
        transport_keypair: dict,
    ):
        """Generate keypair → sign EIP-712 → call canister → unwrap → verify.

        This is the single most important test in the suite — it proves that:
        - Locally generated transport keypairs are accepted by mainnet
        - EIP-712 signatures are valid per the canister's verification
        - The EncryptedVetKey blob can be decrypted with vetkd_py
        - The full unwrap_and_derive pipeline produces the correct AES key
        """
        # Step 1: Encrypt a random AES key locally using IBE
        # This simulates what the encrypt path does: generate an AES key and
        # IBE-wrap it using the verification key + derivation input.
        original_aes_key = os.urandom(32)
        ibe_ciphertext = vetkd_py.ibe_encrypt(
            derived_public_key_bytes=verification_key,
            identity_bytes=derivation_input,
            plaintext=original_aes_key,
        )
        assert len(ibe_ciphertext) == 168, (
            f"Expected 168-byte IBE ciphertext for 32-byte key, got {len(ibe_ciphertext)}"
        )

        # Step 2: Run the full VetKD transport unwrap chain
        # This is the core operation that was previously untested with real
        # canister responses. It combines:
        #   a. EncryptedVetKey::decrypt_and_verify(tsk, dpk, di) -> VetKey
        #   b. IbeCiphertext::decrypt(vet_key) -> AES key
        recovered_aes_key = vetkd_py.unwrap_and_derive(
            encrypted_key_bytes=encrypted_vet_key,
            transport_secret_key_bytes=transport_keypair["secret_key"],
            verification_key_bytes=verification_key,
            derivation_input=derivation_input,
            ibe_ciphertext_bytes=ibe_ciphertext,
        )

        # Step 3: Verify the recovered AES key matches the original
        assert len(recovered_aes_key) == 32, (
            f"Expected 32-byte AES key, got {len(recovered_aes_key)} bytes"
        )
        assert recovered_aes_key == original_aes_key, (
            "AES key mismatch! The full transport unwrap chain failed to recover "
            "the original key. This indicates a protocol mismatch between the "
            "canister's EncryptedVetKey and vetkd_py's unwrap_and_derive."
        )

    def test_decrypt_and_verify_produces_valid_vetkey(
        self,
        encrypted_vet_key: bytes,
        verification_key: bytes,
        derivation_input: bytes,
        transport_keypair: dict,
    ):
        """Verify that decrypt_and_verify produces a valid VetKey (48 bytes).

        This tests the first half of the transport unwrap chain independently:
        EncryptedVetKey + TransportSecretKey + VerificationKey + derivation -> VetKey
        """
        vet_key = vetkd_py.decrypt_and_verify(
            encrypted_key_bytes=encrypted_vet_key,
            transport_secret_key_bytes=transport_keypair["secret_key"],
            verification_key_bytes=verification_key,
            derivation_input=derivation_input,
        )

        # VetKey is a compressed BLS12-381 G1 point: 48 bytes
        assert len(vet_key) == 48, (
            f"Expected 48-byte VetKey, got {len(vet_key)} bytes"
        )

    def test_ibe_decrypt_with_recovered_vetkey(
        self,
        encrypted_vet_key: bytes,
        verification_key: bytes,
        derivation_input: bytes,
        transport_keypair: dict,
    ):
        """Verify the two-step chain: decrypt_and_verify -> ibe_decrypt.

        This tests the full pipeline as two explicit steps, confirming
        that the VetKey from step 1 can be used for IBE decryption.
        """
        # Step 1: Transport unwrap -> VetKey
        vet_key = vetkd_py.decrypt_and_verify(
            encrypted_key_bytes=encrypted_vet_key,
            transport_secret_key_bytes=transport_keypair["secret_key"],
            verification_key_bytes=verification_key,
            derivation_input=derivation_input,
        )

        # Step 2: IBE encrypt a known plaintext, then decrypt with the VetKey
        original_plaintext = b"e2e test payload for IBE round-trip!!"  # 32 bytes
        ibe_ciphertext = vetkd_py.ibe_encrypt(
            derived_public_key_bytes=verification_key,
            identity_bytes=derivation_input,
            plaintext=original_plaintext,
        )

        # Step 3: IBE decrypt using the recovered VetKey
        decrypted_plaintext = vetkd_py.ibe_decrypt(
            ibe_ciphertext_bytes=ibe_ciphertext,
            vet_key_bytes=vet_key,
        )

        assert decrypted_plaintext == original_plaintext, (
            "IBE decrypt with recovered VetKey failed to produce original plaintext"
        )

    def test_e2e_aes_gcm_round_trip(
        self,
        encrypted_vet_key: bytes,
        verification_key: bytes,
        derivation_input: bytes,
        transport_keypair: dict,
    ):
        """Full e2e: IBE-wrap AES key -> unwrap -> AES-GCM decrypt.

        This simulates the complete application-level encrypt/decrypt cycle:
        1. Generate random AES key + IV
        2. AES-GCM encrypt a plaintext
        3. IBE-wrap the AES key
        4. (Canister provides EncryptedVetKey — already done in fixture)
        5. Unwrap the AES key via VetKD transport unwrap
        6. AES-GCM decrypt the ciphertext
        """
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        # Encrypt a test payload with a random AES key
        aes_key = os.urandom(32)
        iv = os.urandom(12)
        plaintext = b"end-to-end test: Haven-AOL VetKD transport unwrap chain works!"
        aesgcm = AESGCM(aes_key)
        ciphertext = aesgcm.encrypt(iv, plaintext, None)

        # IBE-wrap the AES key
        ibe_ciphertext = vetkd_py.ibe_encrypt(
            derived_public_key_bytes=verification_key,
            identity_bytes=derivation_input,
            plaintext=aes_key,
        )

        # Unwrap the AES key using the full VetKD chain
        recovered_key = vetkd_py.unwrap_and_derive(
            encrypted_key_bytes=encrypted_vet_key,
            transport_secret_key_bytes=transport_keypair["secret_key"],
            verification_key_bytes=verification_key,
            derivation_input=derivation_input,
            ibe_ciphertext_bytes=ibe_ciphertext,
        )

        # Decrypt the payload with the recovered key
        recovered_aesgcm = AESGCM(recovered_key)
        decrypted = recovered_aesgcm.decrypt(iv, ciphertext, None)

        assert decrypted == plaintext, (
            "AES-GCM round-trip with VetKD-unwrapped key failed"
        )

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

    def test_derivation_input_format(self, derivation_input: bytes, gate_params: dict):
        """Verify the derivation input matches the expected SHA-256 format.

        Format: SHA256("accessol:{chain}:{tokenAddress}:{threshold}:{cid}")
        """
        assert len(derivation_input) == 32, "Derivation input must be 32 bytes"

        # Recompute and verify
        preimage = (
            f"accessol:{gate_params['chain']}:{gate_params['token_address']}:"
            f"{gate_params['threshold']}:{gate_params['cid']}"
        ).encode("utf-8")
        expected = hashlib.sha256(preimage).digest()
        assert derivation_input == expected, "Derivation input does not match expected format"

    def test_encrypted_vet_key_structure(self, encrypted_vet_key: bytes):
        """Verify the EncryptedVetKey blob has the expected structure (192 bytes)."""
        assert isinstance(encrypted_vet_key, bytes), "EncryptedVetKey must be bytes"
        assert len(encrypted_vet_key) == 192, (
            f"EncryptedVetKey must be 192 bytes, got {len(encrypted_vet_key)}"
        )
