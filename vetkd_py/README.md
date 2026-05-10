# vetkd_py

VetKD transport-key unwrap and IBE decrypt for Haven-AOL.

This package provides Python bindings (via Rust/PyO3) for the ICP VetKD
cryptographic primitives needed by Haven-CLI's decrypt path.

## Installation

```bash
# From source (requires Rust toolchain + maturin)
pip install maturin
cd vetkd_py
maturin develop

# Or build a wheel
maturin build --release
pip install target/wheels/vetkd_py-*.whl
```

## API

### Transport Key Management

```python
import vetkd_py

# Generate ephemeral transport keypair
secret_key = vetkd_py.generate_transport_secret_key()
public_key = vetkd_py.transport_public_key_from_secret(secret_key)
```

### Key Recovery (Transport Unwrap)

```python
# After canister returns EncryptedVetKey blob:
vet_key = vetkd_py.decrypt_and_verify(
    encrypted_key_bytes=canister_response,
    transport_secret_key_bytes=secret_key,
    verification_key_bytes=verification_key,
    derivation_input=derivation_hash,
)
```

### IBE Decrypt

```python
# Recover the AES key from IBE ciphertext:
aes_key = vetkd_py.ibe_decrypt(
    ibe_ciphertext_bytes=encrypted_aes_key,
    vet_key_bytes=vet_key,
)
```

### Combined (unwrap_and_derive)

```python
# One-shot: transport unwrap + IBE decrypt
aes_key = vetkd_py.unwrap_and_derive(
    encrypted_key_bytes=canister_response,
    transport_secret_key_bytes=secret_key,
    verification_key_bytes=verification_key,
    derivation_input=derivation_hash,
    ibe_ciphertext_bytes=encrypted_aes_key,
)
```

## Environment Variables (Haven-CLI integration)

| Variable | Description |
|----------|-------------|
| `HAVEN_AOL_TRANSPORT_SECRET_KEY_B64` | Base64-encoded transport secret key |
| `HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64` | Base64-encoded transport public key |

## Error Handling

All functions raise `ValueError` with descriptive messages on failure:
- Deserialization failures (malformed blobs)
- Verification failures (key mismatch)
- Decryption failures (wrong key / corrupted ciphertext)
