"""Type stubs for the vetkd_py native extension module."""

def generate_transport_secret_key() -> bytes:
    """Generate a random transport secret key (serialized BLS12-381 scalar).

    Generates a cryptographically random 32-byte seed, uses it to derive a
    BLS12-381 scalar via ChaCha20Rng, then returns the serialized scalar (32 bytes).

    IMPORTANT: The returned bytes are the *serialized scalar*, not the seed.
    Store these bytes in HAVEN_AOL_TRANSPORT_SECRET_KEY_B64 (base64-encoded).
    To reconstruct the key later, use TransportSecretKey::deserialize (not from_seed).

    Returns:
        Serialized transport secret key (32 bytes). Store securely, never transmit.

    Raises:
        ValueError: If key generation fails (should not happen with valid RNG).
    """
    ...

def transport_public_key_from_secret(secret_key_bytes: bytes) -> bytes:
    """Derive the transport public key from a secret key.

    Args:
        secret_key_bytes: Raw secret key bytes from generate_transport_secret_key().

    Returns:
        Public key bytes to send to the canister.

    Raises:
        ValueError: If secret_key_bytes is invalid.
    """
    ...

def decrypt_and_verify(
    encrypted_key_bytes: bytes,
    transport_secret_key_bytes: bytes,
    verification_key_bytes: bytes,
    derivation_input: bytes,
) -> bytes:
    """Decrypt and verify an EncryptedVetKey using the transport secret key.

    Args:
        encrypted_key_bytes: Serialized EncryptedVetKey from the canister.
        transport_secret_key_bytes: Local transport secret key.
        verification_key_bytes: Serialized DerivedPublicKey (verification key).
        derivation_input: 32-byte SHA-256 derivation hash.

    Returns:
        Serialized VetKey bytes.

    Raises:
        ValueError: On deserialization or verification failure.
    """
    ...

def ibe_decrypt(
    ibe_ciphertext_bytes: bytes,
    vet_key_bytes: bytes,
) -> bytes:
    """IBE-decrypt a ciphertext using a VetKey.

    Args:
        ibe_ciphertext_bytes: Serialized IbeCiphertext.
        vet_key_bytes: Serialized VetKey from decrypt_and_verify.

    Returns:
        Decrypted plaintext bytes (typically 32-byte AES key).

    Raises:
        ValueError: On deserialization or decryption failure.
    """
    ...

def derive_verification_key(
    key_name: str,
    canister_id_bytes: bytes,
    context: bytes,
) -> bytes:
    """Derive the VetKD verification key offline from the mainnet master key.

    Args:
        key_name: Master key name (e.g. "key_1").
        canister_id_bytes: Raw canister principal bytes.
        context: Domain separator bytes (e.g. b"accessol_v1").

    Returns:
        Serialized DerivedPublicKey bytes (96 bytes).

    Raises:
        ValueError: If the key name is unknown.
    """
    ...

def deserialize_derived_public_key(bytes: bytes) -> bytes:
    """Validate and re-serialize a DerivedPublicKey.

    Args:
        bytes: Serialized DerivedPublicKey bytes.

    Returns:
        Validated serialized bytes.

    Raises:
        ValueError: If bytes are invalid.
    """
    ...

def ibe_encrypt(
    derived_public_key_bytes: bytes,
    identity_bytes: bytes,
    plaintext: bytes,
) -> bytes:
    """IBE-encrypt plaintext using a derived public key and identity.

    Args:
        derived_public_key_bytes: Serialized DerivedPublicKey (96 bytes).
        identity_bytes: Raw hash bytes used as IBE identity (32 bytes).
        plaintext: Data to encrypt.

    Returns:
        Serialized IbeCiphertext bytes.

    Raises:
        ValueError: If the public key is invalid.
    """
    ...

def unwrap_and_derive(
    encrypted_key_bytes: bytes,
    transport_secret_key_bytes: bytes,
    verification_key_bytes: bytes,
    derivation_input: bytes,
    ibe_ciphertext_bytes: bytes,
) -> bytes:
    """Combined transport-unwrap + IBE-decrypt in one call.

    Equivalent to calling decrypt_and_verify then ibe_decrypt, but
    avoids intermediate serialization.

    Args:
        encrypted_key_bytes: Serialized EncryptedVetKey from canister.
        transport_secret_key_bytes: Local transport secret key.
        verification_key_bytes: Serialized DerivedPublicKey.
        derivation_input: 32-byte derivation hash.
        ibe_ciphertext_bytes: Serialized IbeCiphertext (encrypted AES key).

    Returns:
        Decrypted AES key bytes (32 bytes).

    Raises:
        ValueError: On any decode/verify/decrypt failure.
    """
    ...
