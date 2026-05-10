//! VetKD transport-key unwrap and IBE decrypt bindings for Python.
//!
//! This crate exposes the VetKD key-recovery and IBE-decrypt primitives
//! needed by Haven-CLI's decrypt path, via PyO3.

use ic_vetkeys::{
    DerivedPublicKey, EncryptedVetKey, IbeCiphertext, IbeIdentity, IbeSeed,
    MasterPublicKey, TransportSecretKey, VetKey,
};
use ic_cdk::management_canister::{VetKDCurve, VetKDKeyId};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rand::Rng;

/// Generate a random transport secret key (serialized scalar, 32 bytes).
///
/// This generates a random 32-byte seed, derives a BLS12-381 scalar from it
/// via ChaCha20Rng, and returns the serialized scalar bytes. Store these bytes
/// securely — they are needed for transport-key decryption.
///
/// Returns the serialized transport secret key (32 bytes).
#[pyfunction]
fn generate_transport_secret_key() -> PyResult<Vec<u8>> {
    let seed: [u8; 32] = rand::thread_rng().gen();
    let tsk = TransportSecretKey::from_seed(seed.to_vec())
        .map_err(|e| PyValueError::new_err(format!("Failed to generate transport key: {e}")))?;
    Ok(tsk.serialize())
}

/// Derive the transport public key from a transport secret key.
///
/// Args:
///     secret_key_bytes: Serialized transport secret key (from generate_transport_secret_key).
///                       Must be exactly 32 bytes (a valid BLS12-381 scalar).
///
/// Returns:
///     Public key bytes (48 bytes, compressed G1 point) suitable for sending to the canister.
#[pyfunction]
fn transport_public_key_from_secret(secret_key_bytes: &[u8]) -> PyResult<Vec<u8>> {
    let tsk = TransportSecretKey::deserialize(secret_key_bytes)
        .map_err(|e| PyValueError::new_err(format!("Invalid transport secret key bytes: {e}")))?;
    Ok(tsk.public_key())
}

/// Decrypt and verify an encrypted VetKD key blob returned by the canister.
///
/// This performs the core "transport unwrap" operation:
///   EncryptedVetKey + TransportSecretKey + VerificationKey + derivation_input -> VetKey
///
/// Args:
///     encrypted_key_bytes: Serialized EncryptedVetKey from the canister (192 bytes).
///     transport_secret_key_bytes: Serialized transport secret key (32 bytes).
///     verification_key_bytes: Serialized DerivedPublicKey (96 bytes, compressed G2).
///     derivation_input: 32-byte SHA-256 derivation hash (IBE identity).
///
/// Returns:
///     Serialized VetKey bytes (48 bytes, compressed G1 point — used for IBE decryption).
///
/// Raises:
///     ValueError: If deserialization or verification fails.
#[pyfunction]
fn decrypt_and_verify(
    encrypted_key_bytes: &[u8],
    transport_secret_key_bytes: &[u8],
    verification_key_bytes: &[u8],
    derivation_input: &[u8],
) -> PyResult<Vec<u8>> {
    let encrypted_vet_key = EncryptedVetKey::deserialize(encrypted_key_bytes)
        .map_err(|e| PyValueError::new_err(format!("Failed to deserialize EncryptedVetKey: {e}")))?;

    let tsk = TransportSecretKey::deserialize(transport_secret_key_bytes)
        .map_err(|e| PyValueError::new_err(format!("Invalid transport secret key bytes: {e}")))?;

    let dpk = DerivedPublicKey::deserialize(verification_key_bytes)
        .map_err(|_| PyValueError::new_err("Invalid verification key (DerivedPublicKey) bytes"))?;

    let vet_key = encrypted_vet_key
        .decrypt_and_verify(&tsk, &dpk, derivation_input)
        .map_err(|e| PyValueError::new_err(format!("VetKey decryption/verification failed: {e}")))?;

    Ok(vet_key.serialize().to_vec())
}

/// IBE-decrypt a ciphertext using a VetKey to recover the plaintext (e.g. AES key).
///
/// Args:
///     ibe_ciphertext_bytes: Serialized IbeCiphertext (header + G2 + masked_seed + masked_msg).
///     vet_key_bytes: Serialized VetKey from decrypt_and_verify (48 bytes).
///
/// Returns:
///     Decrypted plaintext bytes (typically 32-byte AES key).
///
/// Raises:
///     ValueError: If deserialization or decryption fails.
#[pyfunction]
fn ibe_decrypt(
    ibe_ciphertext_bytes: &[u8],
    vet_key_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    let ibe_ct = IbeCiphertext::deserialize(ibe_ciphertext_bytes)
        .map_err(|e| PyValueError::new_err(format!("Failed to deserialize IbeCiphertext: {e}")))?;

    let vet_key = VetKey::deserialize(vet_key_bytes)
        .map_err(|e| PyValueError::new_err(format!("Failed to deserialize VetKey: {e}")))?;

    let plaintext = ibe_ct
        .decrypt(&vet_key)
        .map_err(|e| PyValueError::new_err(format!("IBE decryption failed: {e}")))?;

    Ok(plaintext)
}

/// Derive the VetKD verification key offline from the mainnet master public key.
///
/// Only supports key names recognized by the IC mainnet:
///   - "key_1" — production mainnet key
///   - "test_key_1" — testing key on mainnet
///
/// For other key names (e.g. "insecure_test_key_1" on local dfx), the verification
/// key must be fetched from the canister directly via getVetKDPublicKey.
///
/// Args:
///     key_name: Master key name (e.g. "key_1" or "test_key_1").
///     canister_id_bytes: Raw canister principal bytes.
///     context: Domain separator bytes (e.g. b"accessol_v1").
///
/// Returns:
///     Serialized DerivedPublicKey bytes (96 bytes, compressed G2 point).
#[pyfunction]
fn derive_verification_key(
    key_name: &str,
    canister_id_bytes: &[u8],
    context: &[u8],
) -> PyResult<Vec<u8>> {
    let key_id = VetKDKeyId {
        curve: VetKDCurve::Bls12_381_G2,
        name: key_name.to_string(),
    };
    let master_key = MasterPublicKey::for_mainnet_key(&key_id)
        .ok_or_else(|| PyValueError::new_err(format!(
            "Unknown key name: {key_name}. Only 'key_1' and 'test_key_1' are supported \
             for offline derivation. For other keys, fetch from canister."
        )))?;
    let canister_key = master_key.derive_canister_key(canister_id_bytes);
    let derived_key = canister_key.derive_sub_key(context);
    Ok(derived_key.serialize())
}

/// Deserialize a DerivedPublicKey from bytes (validation only).
///
/// Args:
///     bytes: Serialized DerivedPublicKey (must be exactly 96 bytes, valid compressed G2).
///
/// Returns:
///     Re-serialized bytes (round-trip validation).
#[pyfunction]
fn deserialize_derived_public_key(bytes: &[u8]) -> PyResult<Vec<u8>> {
    let dpk = DerivedPublicKey::deserialize(bytes)
        .map_err(|_| PyValueError::new_err("Invalid DerivedPublicKey bytes"))?;
    Ok(dpk.serialize())
}

/// IBE-encrypt plaintext using a derived public key and identity.
///
/// Args:
///     derived_public_key_bytes: Serialized DerivedPublicKey (96 bytes).
///     identity_bytes: Raw hash bytes used as IBE identity (typically 32-byte SHA-256).
///     plaintext: Data to encrypt (typically 32-byte AES key).
///
/// Returns:
///     Serialized IbeCiphertext bytes (8-byte header + 96-byte G2 + 32-byte seed + N-byte msg).
#[pyfunction]
fn ibe_encrypt(
    derived_public_key_bytes: &[u8],
    identity_bytes: &[u8],
    plaintext: &[u8],
) -> PyResult<Vec<u8>> {
    let derived_key = DerivedPublicKey::deserialize(derived_public_key_bytes)
        .map_err(|_| PyValueError::new_err("Invalid DerivedPublicKey bytes"))?;
    let identity = IbeIdentity::from_bytes(identity_bytes);
    let seed = IbeSeed::random(&mut rand::thread_rng());
    let ciphertext = IbeCiphertext::encrypt(&derived_key, &identity, plaintext, &seed);
    Ok(ciphertext.serialize())
}

/// Combined unwrap-and-derive convenience function.
///
/// This function combines:
///   1. Transport-key decrypt + verify (EncryptedVetKey -> VetKey)
///   2. IBE decrypt (IbeCiphertext + VetKey -> plaintext AES key)
///
/// Args:
///     encrypted_key_bytes: Serialized EncryptedVetKey from canister (192 bytes).
///     transport_secret_key_bytes: Serialized transport secret key (32 bytes).
///     verification_key_bytes: Serialized DerivedPublicKey (96 bytes).
///     derivation_input: 32-byte derivation hash.
///     ibe_ciphertext_bytes: Serialized IbeCiphertext (encrypted AES key).
///
/// Returns:
///     Decrypted AES key bytes (32 bytes).
///
/// Raises:
///     ValueError: On any decode/verify/decrypt failure.
#[pyfunction]
fn unwrap_and_derive(
    encrypted_key_bytes: &[u8],
    transport_secret_key_bytes: &[u8],
    verification_key_bytes: &[u8],
    derivation_input: &[u8],
    ibe_ciphertext_bytes: &[u8],
) -> PyResult<Vec<u8>> {
    // Step 1: Transport unwrap
    let encrypted_vet_key = EncryptedVetKey::deserialize(encrypted_key_bytes)
        .map_err(|e| PyValueError::new_err(format!("Failed to deserialize EncryptedVetKey: {e}")))?;

    let tsk = TransportSecretKey::deserialize(transport_secret_key_bytes)
        .map_err(|e| PyValueError::new_err(format!("Invalid transport secret key bytes: {e}")))?;

    let dpk = DerivedPublicKey::deserialize(verification_key_bytes)
        .map_err(|_| PyValueError::new_err("Invalid verification key (DerivedPublicKey) bytes"))?;

    let vet_key = encrypted_vet_key
        .decrypt_and_verify(&tsk, &dpk, derivation_input)
        .map_err(|e| PyValueError::new_err(format!("VetKey decryption/verification failed: {e}")))?;

    // Step 2: IBE decrypt
    let ibe_ct = IbeCiphertext::deserialize(ibe_ciphertext_bytes)
        .map_err(|e| PyValueError::new_err(format!("Failed to deserialize IbeCiphertext: {e}")))?;

    let plaintext = ibe_ct
        .decrypt(&vet_key)
        .map_err(|e| PyValueError::new_err(format!("IBE decryption failed: {e}")))?;

    Ok(plaintext)
}

/// Python module definition.
#[pymodule]
fn vetkd_py(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(generate_transport_secret_key, m)?)?;
    m.add_function(wrap_pyfunction!(transport_public_key_from_secret, m)?)?;
    m.add_function(wrap_pyfunction!(decrypt_and_verify, m)?)?;
    m.add_function(wrap_pyfunction!(ibe_decrypt, m)?)?;
    m.add_function(wrap_pyfunction!(derive_verification_key, m)?)?;
    m.add_function(wrap_pyfunction!(deserialize_derived_public_key, m)?)?;
    m.add_function(wrap_pyfunction!(ibe_encrypt, m)?)?;
    m.add_function(wrap_pyfunction!(unwrap_and_derive, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use ic_cdk::management_canister::{VetKDCurve, VetKDKeyId};

    #[test]
    fn master_public_key_for_mainnet_key_1() {
        let key_id = VetKDKeyId {
            curve: VetKDCurve::Bls12_381_G2,
            name: "key_1".into(),
        };
        assert!(MasterPublicKey::for_mainnet_key(&key_id).is_some());
    }

    #[test]
    fn master_public_key_for_mainnet_test_key_1() {
        let key_id = VetKDKeyId {
            curve: VetKDCurve::Bls12_381_G2,
            name: "test_key_1".into(),
        };
        assert!(MasterPublicKey::for_mainnet_key(&key_id).is_some());
    }

    #[test]
    fn master_public_key_unknown_name_is_none() {
        let key_id = VetKDKeyId {
            curve: VetKDCurve::Bls12_381_G2,
            name: "unknown_key".into(),
        };
        assert!(MasterPublicKey::for_mainnet_key(&key_id).is_none());
    }

    #[test]
    fn transport_secret_key_from_random_seed_round_trips() {
        let seed: [u8; 32] = rand::thread_rng().gen();
        let tsk = TransportSecretKey::from_seed(seed.to_vec()).expect("valid seed");
        let bytes = tsk.serialize();
        let again = TransportSecretKey::deserialize(&bytes).expect("deserialize");
        assert_eq!(again.serialize(), bytes);
        assert_eq!(bytes.len(), 32);
        assert_eq!(again.public_key().len(), 48);
    }

    #[test]
    fn derived_public_key_round_trip() {
        let key_id = VetKDKeyId {
            curve: VetKDCurve::Bls12_381_G2,
            name: "key_1".into(),
        };
        let master = MasterPublicKey::for_mainnet_key(&key_id).expect("key_1");
        let canister_id = [1u8; 29];
        let context = b"ctx";
        let derived = master
            .derive_canister_key(&canister_id)
            .derive_sub_key(context);
        let raw = derived.serialize();
        let dpk = DerivedPublicKey::deserialize(&raw).expect("deserialize DPK");
        assert_eq!(dpk.serialize(), raw);
        assert_eq!(raw.len(), 96);
    }
}
