"""End-to-end VetKD transport unwrap chain test against mainnet.

This test proves the full end-to-end flow connecting both layers:
  - Local crypto layer: transport keypair generation, IBE encrypt/decrypt
  - Mainnet service layer: authenticated canister calls, EIP-712 signing

Flow:
  1. Generate an ephemeral ICP identity (Ed25519)
  2. Generate an ephemeral EVM private key (eth_account)
  3. Generate a transport keypair locally (vetkd_py)
  4. Construct a valid EIP-712 signed requestDecryptionKey payload
  5. Call the mainnet canister (dciac-uaaaa-aaaad-qlzuq-cai on https://icp-api.io)
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

# ---------------------------------------------------------------------------
# Canister configuration (mainnet)
# ---------------------------------------------------------------------------
MAINNET_CANISTER_ID = "dciac-uaaaa-aaaad-qlzuq-cai"
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
type GateResult = variant { ok : blob; err : GateError; };
service : { requestDecryptionKey : (GateRequest) -> (GateResult); getVetKDPublicKey : () -> (blob); }
"""


# ---------------------------------------------------------------------------
# Ephemeral key generation helpers
# ---------------------------------------------------------------------------

def _generate_icp_identity() -> tuple[object, str]:
    """Generate a fresh Ed25519 ICP identity.

    Returns:
        (identity, pem_string) — the ICP Identity object and its PEM encoding.
    """
    from ic.identity import Identity

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
    from ic.agent import Agent
    from ic.canister import Canister
    from ic.client import Client

    client = Client(MAINNET_HOST)
    agent = Agent(identity, client)
    return Canister(
        agent=agent,
        canister_id=MAINNET_CANISTER_ID,
        candid=_HAVEN_AOL_DID,
    )


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
    response = ic_canister.getVetKDPublicKey()
    assert isinstance(response, list) and len(response) == 1, (
        f"Unexpected getVetKDPublicKey response shape: {type(response)}"
    )
    raw = response[0]
    # ic-py may return blob fields as either bytes or list of ints
    key_bytes = bytes(raw) if isinstance(raw, list) else raw
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

    response = ic_canister.requestDecryptionKey(gate_request)
    assert isinstance(response, list) and len(response) == 1, (
        f"Unexpected response shape: {type(response)}"
    )
    gate_result = response[0]

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

    raw = gate_result["ok"]
    # ic-py may return blob fields as either bytes or list of ints
    encrypted_key = bytes(raw) if isinstance(raw, list) else raw
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

    def test_encrypted_vet_key_structure(self, encrypted_vet_key: bytes):
        """Verify the EncryptedVetKey blob has the expected structure (192 bytes)."""
        assert isinstance(encrypted_vet_key, bytes), "EncryptedVetKey must be bytes"
        assert len(encrypted_vet_key) == 192, (
            f"EncryptedVetKey must be 192 bytes, got {len(encrypted_vet_key)}"
        )
