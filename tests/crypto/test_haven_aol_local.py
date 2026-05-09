"""Tests for standalone Haven-AOL crypto helpers."""

from haven_cli.crypto.haven_aol_local import (
    GateParams,
    compute_derivation_input,
    decrypt_bytes,
    encrypt_bytes,
)


def test_compute_derivation_input_is_stable() -> None:
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1_000_000,
        cid="QmXoypizjW3WknFiJnKLwHCnL72vedxjQkDDP1mXWo6uco",
    )
    digest = compute_derivation_input(gate)
    assert digest.hex() == "e16d8738a6ea707f75e887fd3fce3e96d2fe061d075c5fe2821e94b2c9ad3b17"


def test_encrypt_then_decrypt_round_trip() -> None:
    gate = GateParams(
        chain="EthMainnet",
        token_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        threshold=1_000_000,
        cid="QmRoundTripCid",
    )
    private_key = "0x" + ("12" * 32)
    plaintext = b"haven-cli aol encryption test"

    encrypted = encrypt_bytes(plaintext=plaintext, private_key=private_key, gate=gate)
    decrypted = decrypt_bytes(
        ciphertext_bytes=encrypted["ciphertext_bytes"],
        private_key=private_key,
        encrypted_key_b64=encrypted["encrypted_key_b64"],
        gate=gate,
    )
    assert decrypted == plaintext
