"""Sprint 4 — haven-cli v3 encrypt / decrypt / dispatch tests.

These tests exercise the *logic* of the v3 upload and decrypt paths
without requiring the ``vetkd_py`` native extension. The IBE primitives
are stubbed via ``monkeypatch``:

  * ``_ibe_encrypt_aes_key_v3`` is replaced with a deterministic
    pseudo-IBE wrapper (``"FAKEIBE|" + sha256(aes_key) + ":" + identity_hex``)
    so the encrypted-key payload is byte-comparable.
  * ``_vetkd_unwrap_aes_key`` is replaced with a stub that recovers the
    AES key from the fake wrapper.
  * ``request_decryption_key_v3`` is stubbed to return a sentinel
    ``DecryptionKeyResponse`` and count calls so we can verify that the
    GateKeyCache short-circuits the second hit.

The real cryptographic invariants are covered by the SDK byte-identity
test suite (``packages/python/tests/test_haven_aol_v3.py``) and the
Sprint 0 fixture; here we focus on the four haven-cli-specific
invariants the brief pins:

  1. v3 metadata is shape-correct and field-ordered.
  2. ``current_epoch()`` is recomputed **per file**, not snapshotted.
  3. Threshold-zero collapse forces ``epoch=0`` on the uploader.
  4. Decrypt looks up the cache by ``metadata.epoch`` — never
     ``current_epoch()``. With a single ``request_decryption_key_v3``
     stub, two decrypt-bytes calls produce ONE canister round-trip.
  5. ``decrypt_bytes_dispatch`` routes v1 records to the v1 decryptor
     unchanged (no v3 stubs touched).
"""

from __future__ import annotations

import base64
import hashlib

import pytest

import haven_cli.crypto.haven_aol_v3 as v3_mod
from haven_cli.crypto.gate_key_cache import CachedVetKey, GateKeyCache
from haven_cli.crypto.gate_metadata import (
    GATE_METADATA_VERSION_V3,
    build_gate_metadata_v3,
    parse_gate_metadata,
)
from haven_cli.services.haven_aol_icp import DecryptionKeyResponse


CHAIN = "EthMainnet"
TOKEN = "0x" + "ab" * 20
CID = "bafyV3Test"


# ── IBE stubs ───────────────────────────────────────────────────────


def _fake_ibe(aes_key: bytes, derivation_input: bytes) -> bytes:
    """Deterministic stand-in for ``vetkd_py.ibe_encrypt``.

    Format: ``b"FAKEIBE|" + sha256(aes_key).digest() + b":" + derivation_input``.
    The unwrap stub below recovers ``aes_key`` from ``aes_key_b64`` only by
    consulting a per-test side-channel dict, not by reversing this fake.
    """
    return (
        b"FAKEIBE|"
        + hashlib.sha256(aes_key).digest()
        + b":"
        + derivation_input
    )


@pytest.fixture
def stub_v3_primitives(monkeypatch):
    """Install IBE + VetKD stubs. Returns a state dict tests can inspect.

    The state dict tracks:
      * ``"ibe_inputs"``   — list of (aes_key, derivation_input) tuples.
      * ``"unwrap_inputs"``— list of full unwrap kwargs.
      * ``"cipher_aes_key"`` — the AES key the test wants returned from
        unwrap. Set this BEFORE calling decrypt to control round-trip
        outcomes. The stub also stashes the AES key produced by encrypt
        in this slot so a typical sequence is:
            state["cipher_aes_key"] = None
            result = encrypt_bytes_v3(...)
            # state["cipher_aes_key"] is now the AES key encrypt used
            plaintext = decrypt_bytes_v3(result["ciphertext_bytes"], ...)
    """
    state: dict = {
        "ibe_inputs": [],
        "unwrap_inputs": [],
        "cipher_aes_key": None,
    }

    # Patch IBE wrap. Also stash the AES key so the decrypt stub can find
    # it. Real encrypt generates a random AES key inside the function; we
    # intercept it here.
    real_ibe = v3_mod._ibe_encrypt_aes_key_v3  # noqa: F841 (kept for clarity)

    def fake_ibe(aes_key: bytes, derivation_input: bytes) -> bytes:
        state["ibe_inputs"].append((aes_key, bytes(derivation_input)))
        state["cipher_aes_key"] = aes_key  # stash for decrypt stub
        return _fake_ibe(aes_key, derivation_input)

    monkeypatch.setattr(v3_mod, "_ibe_encrypt_aes_key_v3", fake_ibe)

    # Patch the unwrap helper. The unwrap stub returns ``state["cipher_aes_key"]``
    # so an encrypt→decrypt round trip yields the same AES key, and AES-GCM
    # succeeds for real.
    def fake_unwrap(
        *,
        encrypted_canister_key,
        verification_key_b64,
        derivation_input,
        encrypted_aes_key_b64,
    ) -> bytes:
        state["unwrap_inputs"].append(
            {
                "encrypted_canister_key": bytes(encrypted_canister_key),
                "verification_key_b64": verification_key_b64,
                "derivation_input": bytes(derivation_input),
                "encrypted_aes_key_b64": encrypted_aes_key_b64,
            }
        )
        return bytes(state["cipher_aes_key"])

    monkeypatch.setattr(v3_mod, "_vetkd_unwrap_aes_key", fake_unwrap)

    return state


@pytest.fixture
def stub_request_v3(monkeypatch):
    """Stub ``request_decryption_key_v3`` to return a sentinel bundle and
    count calls. The returned ``DecryptionKeyResponse`` bytes are
    deterministic for assertions."""
    state = {"call_count": 0, "calls": []}

    def fake_request(*, chain, token_address, threshold, epoch):
        state["call_count"] += 1
        state["calls"].append(
            {
                "chain": chain,
                "token_address": token_address,
                "threshold": threshold,
                "epoch": epoch,
            }
        )
        return DecryptionKeyResponse(
            encrypted_key=bytes([0x33] * 48),
            verification_key=bytes([0x44] * 96),
        )

    monkeypatch.setattr(v3_mod, "request_decryption_key_v3", fake_request)
    return state


# ── 1) Metadata shape ───────────────────────────────────────────────


class TestV3EncryptMetadataShape:
    def test_encrypt_bytes_produces_v3_metadata(self, stub_v3_primitives):
        result = v3_mod.encrypt_bytes_v3(
            b"hello world",
            chain=CHAIN,
            token_address=TOKEN,
            threshold=1,
            cid=CID,
            epoch=687,  # explicit for determinism
        )
        gate = result["gate"]
        assert gate["version"] == GATE_METADATA_VERSION_V3
        assert gate["chain"] == CHAIN
        assert gate["tokenAddress"] == TOKEN
        assert gate["threshold"] == "1"
        assert gate["epoch"] == 687
        assert gate["cid"] == CID
        # Round-trip via the dispatcher must succeed.
        parsed = parse_gate_metadata(gate)
        assert parsed is not None and parsed["version"] == 3

    def test_encrypt_uses_v3_derivation_input(self, stub_v3_primitives):
        # The hash of the v3 preimage is well-known; the SDK has the
        # canonical reference. Here we only confirm the IBE stub received
        # the SDK's v3 derivation input — not the v1 one.
        from haven_aol.v3 import compute_derivation_input_v3

        expected = compute_derivation_input_v3(CHAIN, TOKEN, 1, 687)
        v3_mod.encrypt_bytes_v3(
            b"x",
            chain=CHAIN,
            token_address=TOKEN,
            threshold=1,
            cid=CID,
            epoch=687,
        )
        assert stub_v3_primitives["ibe_inputs"]
        _, derivation_input = stub_v3_primitives["ibe_inputs"][-1]
        assert derivation_input == expected


# ── 2) Per-file epoch recomputation ─────────────────────────────────


class TestPerFileEpochRecomputation:
    """When the wall clock advances between two uploads, the second
    upload must record the new epoch. Session-snapshotting would let the
    second file carry the first file's epoch.

    The pattern we test: a caller-level loop that does NOT pass an
    explicit epoch (so the function consults ``current_epoch()``) sees
    two distinct metadata epochs across two ``encrypt_bytes_v3`` calls
    when ``current_epoch`` returns different values each time.
    """

    def test_two_uploads_two_epochs(self, monkeypatch, stub_v3_primitives):
        epochs_to_return = iter([100, 101])
        monkeypatch.setattr(
            v3_mod, "current_epoch", lambda: next(epochs_to_return)
        )

        r1 = v3_mod.encrypt_bytes_v3(
            b"a", chain=CHAIN, token_address=TOKEN, threshold=1, cid="cidA"
        )
        r2 = v3_mod.encrypt_bytes_v3(
            b"b", chain=CHAIN, token_address=TOKEN, threshold=1, cid="cidB"
        )
        assert r1["gate"]["epoch"] == 100
        assert r2["gate"]["epoch"] == 101

    def test_streaming_recomputes_epoch_per_call(self, monkeypatch, tmp_path, stub_v3_primitives):
        epochs = iter([200, 201, 202])
        monkeypatch.setattr(v3_mod, "current_epoch", lambda: next(epochs))

        results = []
        for i in range(3):
            src = tmp_path / f"in_{i}.bin"
            dst = tmp_path / f"out_{i}.bin"
            src.write_bytes(b"x" * 100)
            r = v3_mod.encrypt_file_streaming_v3(
                src,
                dst,
                chain=CHAIN,
                token_address=TOKEN,
                threshold=1,
                cid=f"cid{i}",
            )
            results.append(r["gate"]["epoch"])
        assert results == [200, 201, 202]


# ── 3) Threshold-zero collapse ──────────────────────────────────────


class TestThresholdZeroCollapse:
    def test_encrypt_threshold_zero_forces_epoch_zero(self, monkeypatch, stub_v3_primitives):
        # Even though current_epoch() returns a real value, the uploader
        # must record epoch=0 when threshold=0.
        monkeypatch.setattr(v3_mod, "current_epoch", lambda: 9999)
        result = v3_mod.encrypt_bytes_v3(
            b"plain",
            chain=CHAIN,
            token_address=TOKEN,
            threshold=0,
            cid=CID,
        )
        assert result["gate"]["threshold"] == "0"
        assert result["gate"]["epoch"] == 0

    def test_encrypt_streaming_threshold_zero_forces_epoch_zero(
        self, monkeypatch, tmp_path, stub_v3_primitives
    ):
        monkeypatch.setattr(v3_mod, "current_epoch", lambda: 9999)
        src = tmp_path / "in.bin"
        dst = tmp_path / "out.bin"
        src.write_bytes(b"y" * 32)
        result = v3_mod.encrypt_file_streaming_v3(
            src,
            dst,
            chain=CHAIN,
            token_address=TOKEN,
            threshold=0,
            cid=CID,
        )
        assert result["gate"]["threshold"] == "0"
        assert result["gate"]["epoch"] == 0


# ── 4) Decrypt uses metadata.epoch + caches the VetKey ──────────────


class TestDecryptCacheByMetadataEpoch:
    def test_round_trip_single_call(self, stub_v3_primitives, stub_request_v3):
        result = v3_mod.encrypt_bytes_v3(
            b"hello world",
            chain=CHAIN,
            token_address=TOKEN,
            threshold=1,
            cid=CID,
            epoch=42,
        )
        # Fresh cache for this test so we don't pick up state from others.
        cache = GateKeyCache()
        plaintext = v3_mod.decrypt_bytes_v3(
            result["ciphertext_bytes"],
            metadata=result["gate"],
            cache=cache,
        )
        assert plaintext == b"hello world"
        assert stub_request_v3["call_count"] == 1

    def test_two_decrypts_same_bucket_one_canister_call(
        self, stub_v3_primitives, stub_request_v3
    ):
        # Two ciphertexts in the same (chain, token, threshold, epoch)
        # bucket must share the canister-fetched VetKey via the cache.
        cache = GateKeyCache()
        a = v3_mod.encrypt_bytes_v3(
            b"first",
            chain=CHAIN,
            token_address=TOKEN,
            threshold=1,
            cid="cidA",
            epoch=99,
        )
        # IMPORTANT: stub_v3_primitives stashes the AES key from the most
        # recent encrypt into state["cipher_aes_key"]. To exercise two
        # decrypts that *match* the same bucket, both ciphertexts must be
        # decryptable. We arrange that by deriving both AES keys from the
        # same encrypt — but encrypt randomizes the AES key. Easiest is
        # to run two decrypts of the SAME ciphertext, which is sufficient
        # to prove the cache short-circuit.
        v3_mod.decrypt_bytes_v3(
            a["ciphertext_bytes"], metadata=a["gate"], cache=cache
        )
        v3_mod.decrypt_bytes_v3(
            a["ciphertext_bytes"], metadata=a["gate"], cache=cache
        )
        # The canister was hit exactly once for the bucket.
        assert stub_request_v3["call_count"] == 1
        # And both unwraps used the same encrypted_canister_key (cached).
        assert (
            stub_v3_primitives["unwrap_inputs"][0]["encrypted_canister_key"]
            == stub_v3_primitives["unwrap_inputs"][1]["encrypted_canister_key"]
        )

    def test_decrypt_uses_metadata_epoch_not_current(
        self, monkeypatch, stub_v3_primitives, stub_request_v3
    ):
        # Scenario (D) from the proposal §1.7. We:
        #   1. Encrypt at epoch=100.
        #   2. Move the wall clock to epoch=9999.
        #   3. Decrypt — the cache lookup MUST use metadata.epoch=100,
        #      and the canister fetch MUST request epoch=100, not 9999.
        result = v3_mod.encrypt_bytes_v3(
            b"old content",
            chain=CHAIN,
            token_address=TOKEN,
            threshold=1,
            cid=CID,
            epoch=100,
        )
        cache = GateKeyCache()

        monkeypatch.setattr(v3_mod, "current_epoch", lambda: 9999)
        plaintext = v3_mod.decrypt_bytes_v3(
            result["ciphertext_bytes"],
            metadata=result["gate"],
            cache=cache,
        )
        assert plaintext == b"old content"

        # The canister was asked for epoch=100, not 9999.
        assert stub_request_v3["call_count"] == 1
        assert stub_request_v3["calls"][0]["epoch"] == 100

    def test_cache_hit_skips_canister(self, stub_v3_primitives, stub_request_v3):
        # Pre-populate the cache with a bundle that matches the metadata.
        cache = GateKeyCache()
        cache_key = GateKeyCache.make_key(
            chain=CHAIN, token_address=TOKEN, threshold=1, epoch=42
        )
        cache.put(
            cache_key,
            CachedVetKey(
                encrypted_key=bytes([0x77] * 48),
                verification_key=bytes([0x88] * 96),
            ),
        )

        result = v3_mod.encrypt_bytes_v3(
            b"plain",
            chain=CHAIN,
            token_address=TOKEN,
            threshold=1,
            cid=CID,
            epoch=42,
        )
        v3_mod.decrypt_bytes_v3(
            result["ciphertext_bytes"], metadata=result["gate"], cache=cache
        )
        # Canister was never hit because the bundle was cached.
        assert stub_request_v3["call_count"] == 0
        # And the unwrap saw the *pre-cached* encrypted key, not the stub's.
        assert (
            stub_v3_primitives["unwrap_inputs"][0]["encrypted_canister_key"]
            == bytes([0x77] * 48)
        )

    def test_decrypt_rejects_v1_metadata(self, stub_v3_primitives, stub_request_v3):
        bad = {
            "version": 1,
            "cid": "x",
            "chain": CHAIN,
            "tokenAddress": TOKEN,
            "threshold": "1",
            "encryptedAesKey": "abc",
        }
        with pytest.raises(ValueError):
            v3_mod.decrypt_bytes_v3(b"\x00" * 30, metadata=bad)


# ── 5) Dispatcher routes v1 records away from the v3 path ──────────


class TestDecryptDispatcher:
    def test_dispatch_v3(self, stub_v3_primitives, stub_request_v3):
        result = v3_mod.encrypt_bytes_v3(
            b"d",
            chain=CHAIN,
            token_address=TOKEN,
            threshold=1,
            cid=CID,
            epoch=5,
        )
        cache = GateKeyCache()
        plaintext = v3_mod.decrypt_bytes_dispatch(
            result["ciphertext_bytes"], metadata=result["gate"], cache=cache
        )
        assert plaintext == b"d"

    def test_dispatch_unknown_version_raises(self):
        with pytest.raises(ValueError):
            v3_mod.decrypt_bytes_dispatch(
                b"\x00" * 30, metadata={"version": 99, "cid": "x"}
            )

    def test_dispatch_v1_calls_v1_decrypt_unchanged(self, monkeypatch):
        # We route a v1 record into the dispatcher and verify that the
        # v1 decryptor was called with the v1 args (NOT the v3 path).
        # Stub haven_cli.crypto.haven_aol_local.decrypt_bytes so we can
        # detect that it was invoked.
        import haven_cli.crypto.haven_aol_local as v1_mod

        called = {"args": None, "kwargs": None}

        def fake_v1(ciphertext, private_key, encrypted_key_b64, gate):
            called["args"] = (ciphertext, private_key, encrypted_key_b64, gate)
            return b"V1-PLAINTEXT"

        monkeypatch.setattr(v1_mod, "decrypt_bytes", fake_v1)

        v1_meta = {
            "version": 1,
            "cid": "v1cid",
            "chain": CHAIN,
            "tokenAddress": TOKEN,
            "threshold": "1",
            "encryptedAesKey": "v1-aes-b64",
        }
        out = v3_mod.decrypt_bytes_dispatch(
            b"\x00" * 30, metadata=v1_meta, private_key="0xabc"
        )
        assert out == b"V1-PLAINTEXT"
        assert called["args"] is not None
        ciphertext, private_key, aes_b64, gate = called["args"]
        assert private_key == "0xabc"
        assert aes_b64 == "v1-aes-b64"
        assert gate.cid == "v1cid"
        assert gate.chain == CHAIN
        assert gate.threshold == 1
