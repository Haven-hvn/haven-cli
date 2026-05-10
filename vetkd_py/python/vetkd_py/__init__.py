"""VetKD transport-key unwrap and IBE decrypt for Haven-AOL.

This package provides Python bindings (via Rust/PyO3) for the ICP VetKD
cryptographic primitives needed to complete the Haven-AOL decrypt chain:

    EncryptedVetKey -> VetKey (transport unwrap)
    IbeCiphertext + VetKey -> AES key (IBE decrypt)
    AES-GCM(key, ciphertext) -> plaintext

Usage:
    import vetkd_py

    # Generate transport keypair (done once, stored in env)
    secret = vetkd_py.generate_transport_secret_key()
    public = vetkd_py.transport_public_key_from_secret(secret)

    # After receiving encrypted key from canister:
    aes_key = vetkd_py.unwrap_and_derive(
        encrypted_key_bytes=canister_response,
        transport_secret_key_bytes=secret,
        verification_key_bytes=verification_key,
        derivation_input=derivation_hash,
        ibe_ciphertext_bytes=encrypted_aes_key,
    )
"""

from vetkd_py.vetkd_py import (  # type: ignore[attr-defined]
    generate_transport_secret_key,
    transport_public_key_from_secret,
    decrypt_and_verify,
    ibe_decrypt,
    derive_verification_key,
    deserialize_derived_public_key,
    ibe_encrypt,
    unwrap_and_derive,
)

__all__ = [
    "generate_transport_secret_key",
    "transport_public_key_from_secret",
    "decrypt_and_verify",
    "ibe_decrypt",
    "derive_verification_key",
    "deserialize_derived_public_key",
    "ibe_encrypt",
    "unwrap_and_derive",
]

__version__ = "0.1.0"
