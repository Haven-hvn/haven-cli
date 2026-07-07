"""Haven-AOL Protocol v3 encrypt / decrypt entry points for haven-cli.

This module is the v3 sibling of :mod:`haven_cli.crypto.haven_aol_local`. It
reuses every primitive the v1 path already owns:

  * VetKD derived public key fetched via :func:`get_vetkd_public_key_b64`,
    cached at process scope (already done by ``_get_or_cache_derived_public_key``).
  * IBE encryption / unwrapping via the same ``vetkd_py`` extension.
  * AES-GCM streaming chunk format identical to v1 (``[12-byte IV]
    [<4-byte chunk index><4-byte chunk len><ciphertext+tag>]*``).

What is new in v3:

  * **Derivation input** is the 32-byte SHA-256 of
    ``"accessol_v3:" + chain + ":" + tokenAddress + ":" + threshold + ":" + epoch``,
    computed by :func:`haven_aol.v3.compute_derivation_input_v3`. No CID.
  * **Per-file** ``current_epoch()`` recomputation. The brief is explicit:
    session-snapshotting the epoch is a bug, not an optimisation. See
    ``docs/corpus-gate-proposal-v3.md`` §1.7 scenario (C).
  * **Threshold-zero collapse** at the uploader. If ``threshold == 0`` the
    metadata records ``epoch: 0`` and the IBE wrap uses ``epoch: 0``,
    regardless of ``current_epoch()``. This matches the canister's
    server-side collapse rule (Key Design Decision §5).
  * **Decryptor cache** keyed by ``metadata.epoch`` — never ``current_epoch()``
    — and backed by the in-memory :class:`GateKeyCache` singleton. See
    ``haven_cli/crypto/gate_key_cache.py``.

v1 paths in :mod:`haven_cli.crypto.haven_aol_local` are untouched. Callers
that need to handle both versions must dispatch on ``metadata.version``
before reaching either this module or the v1 module — there is no
in-module routing here. The CLI-level helper for that is
:func:`decrypt_file_dispatch` below.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import struct
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# We deliberately import only the bits we need from the v1 module so we
# don't pull in ``GateParams`` (a v1-only dataclass). The IBE primitives
# and the chunk-IV helper are version-agnostic.
from haven_cli.crypto.haven_aol_local import (
    _derive_chunk_iv,
    _get_or_cache_derived_public_key,
    _vetkd_unwrap_aes_key,
)
from haven_cli.crypto.gate_key_cache import (
    CachedVetKey,
    GateKeyCache,
    gate_key_cache as _default_gate_key_cache,
)
from haven_cli.services.haven_aol_icp import (
    DecryptionKeyResponse,
    request_decryption_key_v3,
)
from haven_aol.v3 import (
    GATE_METADATA_VERSION_V3,
    build_gate_metadata_v3,
    compute_derivation_input_v3,
    current_epoch,
)

logger = logging.getLogger(__name__)


# ── Upload (encrypt) ─────────────────────────────────────────────────


def _ibe_encrypt_aes_key_v3(aes_key: bytes, derivation_input: bytes) -> bytes:
    """IBE-wrap an AES key using the v3 derivation input.

    Mirrors :func:`haven_cli.crypto.haven_aol_local._ibe_encrypt_aes_key` —
    the underlying ``vetkd_py.ibe_encrypt`` call is identical; only the
    32-byte ``identity_bytes`` differ (v3 uses the corpus-scoped digest,
    v1 uses the CID-bound digest).
    """
    try:
        import vetkd_py
    except ImportError as exc:
        raise RuntimeError(
            "vetkd_py package is required for Haven-AOL v3 IBE encryption. "
            "Install it from the vetkd_py/ directory: pip install ./vetkd_py"
        ) from exc

    derived_public_key = _get_or_cache_derived_public_key()
    return vetkd_py.ibe_encrypt(
        derived_public_key_bytes=derived_public_key,
        identity_bytes=derivation_input,
        plaintext=aes_key,
    )


def _resolve_effective_epoch(threshold: int, epoch: int | None) -> int:
    """v3 threshold-zero collapse + fresh-per-file wall-clock epoch."""
    if threshold < 0:
        raise ValueError("threshold must be >= 0")
    effective_epoch = 0 if threshold == 0 else (
        current_epoch() if epoch is None else int(epoch)
    )
    if effective_epoch < 0:
        raise ValueError("epoch must be >= 0")
    return effective_epoch


def _resolve_epoch_key_material(
    *,
    aes_key: bytes | None,
    encrypted_aes_key_b64: str | None,
    derivation_input: bytes,
) -> tuple[bytes, str]:
    """Return ``(raw_aes_key, encrypted_aes_key_b64)`` for a v3 encrypt.

    Three modes, listed in priority order:

      1. Both ``aes_key`` and ``encrypted_aes_key_b64`` supplied — epoch
         key reuse. Trust the caller: no fresh randomness, no fresh IBE
         wrap. This is the path the pipeline takes when it has already
         cached the epoch key via :class:`EpochAesKeyCache`. (Bug 8)
      2. Only ``aes_key`` supplied — the caller has the raw key (e.g.
         from a test fixture) but hasn't wrapped it yet. Wrap it now.
      3. Neither supplied — fresh random AES key + fresh IBE wrap.
         Pre-Sprint-4-bug-fix behaviour. Kept for tests and single-file
         encrypts where per-epoch reuse doesn't matter.
    """
    if aes_key is not None:
        if len(aes_key) != 32:
            raise ValueError(f"aes_key must be 32 bytes, got {len(aes_key)}")
        if encrypted_aes_key_b64 is not None:
            return aes_key, encrypted_aes_key_b64
        wrapped_key = _ibe_encrypt_aes_key_v3(aes_key, derivation_input)
        return aes_key, base64.b64encode(wrapped_key).decode("ascii")

    # aes_key is None. The pre-fix code accepted ``encrypted_aes_key_b64``
    # here as a test-stub, but that broke correctness (the raw key was
    # random, the ciphertext was sealed under it, and the wrapped blob
    # said something else). Reject that combination now — it's never
    # correct.
    if encrypted_aes_key_b64 is not None:
        raise ValueError(
            "encrypted_aes_key_b64 must be paired with aes_key; "
            "otherwise the ciphertext cannot be decrypted by v3 consumers"
        )

    fresh_key = os.urandom(32)
    wrapped_key = _ibe_encrypt_aes_key_v3(fresh_key, derivation_input)
    return fresh_key, base64.b64encode(wrapped_key).decode("ascii")


def encrypt_bytes_v3(
    plaintext: bytes,
    *,
    chain: str,
    token_address: str,
    threshold: int,
    cid: str,
    encrypted_aes_key_b64: str | None = None,
    aes_key: bytes | None = None,
    epoch: int | None = None,
) -> dict[str, Any]:
    """Encrypt ``plaintext`` under a v3 corpus-scoped gate.

    The function computes ``current_epoch()`` **inside** this call (after the
    caller's pre-checks). Callers MUST NOT snapshot the epoch outside and
    pass it in for batch uploads — that would let a long-running session
    use stale derivation inputs (see proposal §1.7 scenario (C)). The
    ``epoch`` parameter exists for tests and for the threshold-zero
    fast-path where the value is fixed at 0.

    Threshold-zero collapse: if ``threshold == 0`` we force ``epoch = 0``
    regardless of the caller-supplied or wall-clock value.

    Epoch-key reuse (Bug 4/8 fix): callers who want to seal N files
    under one shared per-epoch AES key can pass both ``aes_key`` (raw 32
    bytes) and ``encrypted_aes_key_b64`` (already-IBE-wrapped form).
    When supplied together, the function skips fresh randomness / IBE
    wrap and reuses the caller-supplied material. Passing only
    ``encrypted_aes_key_b64`` (without ``aes_key``) is now rejected —
    it was a test-only stub that produced undecryptable ciphertexts.

    Returns a dict with the v3 gate-metadata record under ``"gate"`` and
    the ciphertext bytes under ``"ciphertext_bytes"``. The ``"gate"`` dict
    is the canonical shape produced by
    :func:`haven_aol.v3.build_gate_metadata_v3`.
    """
    effective_epoch = _resolve_effective_epoch(threshold, epoch)

    derivation_input = compute_derivation_input_v3(
        chain=chain,
        token_address=token_address,
        threshold=threshold,
        epoch=effective_epoch,
    )
    raw_aes_key, wrapped_key_b64 = _resolve_epoch_key_material(
        aes_key=aes_key,
        encrypted_aes_key_b64=encrypted_aes_key_b64,
        derivation_input=derivation_input,
    )

    iv = os.urandom(12)
    aesgcm = AESGCM(raw_aes_key)
    ciphertext_bytes = iv + aesgcm.encrypt(iv, plaintext, None)

    gate = build_gate_metadata_v3(
        cid=cid,
        chain=chain,
        token_address=token_address,
        threshold=threshold,
        epoch=effective_epoch,
        encrypted_aes_key_b64=wrapped_key_b64,
    )

    logger.info(
        "haven-aol v3 encrypt: chain=%s threshold=%d epoch=%d cid=%s reused_key=%s",
        chain, threshold, effective_epoch, cid, aes_key is not None,
    )

    return {
        "ciphertext_bytes": ciphertext_bytes,
        "data_to_encrypt_hash": derivation_input.hex(),
        "encrypted_key_b64": wrapped_key_b64,
        "key_hash": hashlib.sha256(raw_aes_key).hexdigest(),
        "iv_b64": base64.b64encode(iv).decode("ascii"),
        "gate": gate,
    }



def encrypt_file_streaming_v3(
    input_path: str | Path,
    output_path: str | Path,
    *,
    chain: str,
    token_address: str,
    threshold: int,
    cid: str,
    chunk_size: int = 1024 * 1024,
    epoch: int | None = None,
    aes_key: bytes | None = None,
    encrypted_aes_key_b64: str | None = None,
) -> dict[str, Any]:
    """Encrypt a file with chunked AES-GCM under a v3 gate.

    Identical chunk format to v1 (see
    :func:`haven_cli.crypto.haven_aol_local.encrypt_file_streaming`). The
    only differences are the derivation input (v3, no CID) and the gate
    metadata version (3).

    The epoch is computed per-file **here**, after the file is opened and
    before the IBE wrap. Snapshotting outside is prohibited (proposal
    §1.7 scenario (C)).

    Epoch-key reuse (Bug 4 / Bug 5 / Bug 8 fix):

      * ``aes_key`` — 32 raw bytes. If supplied the file is sealed under
        this key instead of a fresh ``os.urandom(32)``. Callers who cache
        per-epoch AES keys (see :class:`haven_cli.crypto.epoch_key_cache.EpochAesKeyCache`)
        pass the cached raw bytes here.
      * ``encrypted_aes_key_b64`` — the already-IBE-wrapped base64 form of
        that same ``aes_key``. If both are supplied the function does
        NOT call IBE at all; if only ``aes_key`` is supplied the wrap is
        performed here (single-file case). Passing only
        ``encrypted_aes_key_b64`` (with ``aes_key is None``) is rejected
        because the ciphertext and metadata would disagree.

    Both new parameters default to ``None`` so all pre-fix call sites
    (including tests) remain byte-identical (fresh random key per file).
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")

    src_path = Path(input_path)
    dst_path = Path(output_path)

    # Per-file epoch recomputation. This is the line tested by
    # ``test_per_file_epoch_recomputation`` — a batch loop that calls
    # encrypt_file_streaming_v3 for two files at different wall-clock
    # times must reflect two different epochs in metadata.
    effective_epoch = _resolve_effective_epoch(threshold, epoch)

    derivation_input = compute_derivation_input_v3(
        chain=chain,
        token_address=token_address,
        threshold=threshold,
        epoch=effective_epoch,
    )
    raw_aes_key, wrapped_key_b64 = _resolve_epoch_key_material(
        aes_key=aes_key,
        encrypted_aes_key_b64=encrypted_aes_key_b64,
        derivation_input=derivation_input,
    )

    base_iv = os.urandom(12)
    aesgcm = AESGCM(raw_aes_key)

    with src_path.open("rb") as src, dst_path.open("wb") as dst:
        dst.write(base_iv)
        chunk_index = 0
        while True:
            chunk = src.read(chunk_size)
            if not chunk:
                break
            per_iv = _derive_chunk_iv(base_iv, chunk_index)
            encrypted_chunk = aesgcm.encrypt(per_iv, chunk, None)
            dst.write(struct.pack("<I", chunk_index))
            dst.write(struct.pack("<I", len(encrypted_chunk)))
            dst.write(encrypted_chunk)
            chunk_index += 1

    gate = build_gate_metadata_v3(
        cid=cid,
        chain=chain,
        token_address=token_address,
        threshold=threshold,
        epoch=effective_epoch,
        encrypted_aes_key_b64=wrapped_key_b64,
    )

    logger.info(
        "haven-aol v3 stream encrypt done chunks=%d chain=%s threshold=%d "
        "epoch=%d cid=%s reused_key=%s",
        chunk_index, chain, threshold, effective_epoch, cid, aes_key is not None,
    )

    return {
        "data_to_encrypt_hash": derivation_input.hex(),
        "encrypted_key_b64": wrapped_key_b64,
        "key_hash": hashlib.sha256(raw_aes_key).hexdigest(),
        "iv_b64": base64.b64encode(base_iv).decode("ascii"),
        "gate": gate,
    }



# ── Decrypt ──────────────────────────────────────────────────────────


def _fetch_vetkey_v3(
    *,
    chain: str,
    token_address: str,
    threshold: int,
    epoch: int,
    cache: GateKeyCache,
) -> CachedVetKey:
    """Return a cached VetKey for ``(chain, token, threshold, epoch)``.

    On miss, calls ``request_decryption_key_v3`` and installs the result.
    Cache lookup is keyed by ``epoch`` exactly as passed in — typically
    from parsed metadata. This function MUST NOT call ``current_epoch()``.
    """
    key = GateKeyCache.make_key(
        chain=chain,
        token_address=token_address,
        threshold=threshold,
        epoch=epoch,
    )
    cached = cache.get(key)
    if cached is not None:
        return cached

    logger.info(
        "haven-aol v3 cache miss; fetching VetKey chain=%s threshold=%d epoch=%d",
        chain, threshold, epoch,
    )
    response: DecryptionKeyResponse = request_decryption_key_v3(
        chain=chain,
        token_address=token_address,
        threshold=threshold,
        epoch=epoch,
    )
    cached = CachedVetKey(
        encrypted_key=bytes(response.encrypted_key),
        verification_key=bytes(response.verification_key),
    )
    cache.put(key, cached)
    return cached


def _unwrap_aes_key_v3(
    *,
    chain: str,
    token_address: str,
    threshold: int,
    epoch: int,
    encrypted_aes_key_b64: str,
    cache: GateKeyCache,
) -> bytes:
    """Common path: fetch (or cache-hit) VetKey, then derive the AES key."""
    derivation_input = compute_derivation_input_v3(
        chain=chain,
        token_address=token_address,
        threshold=threshold,
        epoch=epoch,
    )
    bundle = _fetch_vetkey_v3(
        chain=chain,
        token_address=token_address,
        threshold=threshold,
        epoch=epoch,
        cache=cache,
    )
    verification_key_b64 = base64.b64encode(bundle.verification_key).decode("ascii")
    return _vetkd_unwrap_aes_key(
        encrypted_canister_key=bundle.encrypted_key,
        verification_key_b64=verification_key_b64,
        derivation_input=derivation_input,
        encrypted_aes_key_b64=encrypted_aes_key_b64,
    )


def decrypt_bytes_v3(
    ciphertext_bytes: bytes,
    *,
    metadata: dict[str, Any],
    cache: GateKeyCache | None = None,
) -> bytes:
    """Decrypt a v3 ``[IV || ciphertext+tag]`` payload.

    ``metadata`` MUST be a v3-shaped gate-metadata dict (the result of
    :func:`haven_cli.crypto.gate_metadata.parse_gate_metadata` for a v3
    record). The function asserts ``metadata.version == 3`` defensively;
    callers should dispatch upstream.

    The cache key is built from ``metadata.epoch`` — **never** from
    ``current_epoch()``. This is the Sprint 4 brief's scenario-D
    mitigation.
    """
    if metadata.get("version") != GATE_METADATA_VERSION_V3:
        raise ValueError(
            f"decrypt_bytes_v3: expected v3 metadata, got version "
            f"{metadata.get('version')!r}"
        )
    cache = cache if cache is not None else _default_gate_key_cache

    chain = str(metadata["chain"])
    token_address = str(metadata["tokenAddress"])
    threshold = int(metadata["threshold"])
    epoch = int(metadata["epoch"])
    encrypted_aes_key_b64 = str(metadata["encryptedAesKey"])

    aes_key = _unwrap_aes_key_v3(
        chain=chain,
        token_address=token_address,
        threshold=threshold,
        epoch=epoch,
        encrypted_aes_key_b64=encrypted_aes_key_b64,
        cache=cache,
    )

    if len(ciphertext_bytes) < 12:
        raise RuntimeError("Ciphertext too short (missing IV)")
    iv = ciphertext_bytes[:12]
    ct_and_tag = ciphertext_bytes[12:]
    aesgcm = AESGCM(aes_key)
    try:
        return aesgcm.decrypt(iv, ct_and_tag, None)
    except Exception as exc:
        raise RuntimeError(
            "v3 AES-GCM decryption failed. Key mismatch or corrupted ciphertext."
        ) from exc


def decrypt_file_streaming_v3(
    input_path: str | Path,
    output_path: str | Path,
    *,
    metadata: dict[str, Any],
    cache: GateKeyCache | None = None,
) -> None:
    """Decrypt a file produced by :func:`encrypt_file_streaming_v3`.

    Reuses the v1 streaming chunk format — only the derivation input and
    the metadata version differ.
    """
    if metadata.get("version") != GATE_METADATA_VERSION_V3:
        raise ValueError(
            f"decrypt_file_streaming_v3: expected v3 metadata, got version "
            f"{metadata.get('version')!r}"
        )
    cache = cache if cache is not None else _default_gate_key_cache

    chain = str(metadata["chain"])
    token_address = str(metadata["tokenAddress"])
    threshold = int(metadata["threshold"])
    epoch = int(metadata["epoch"])
    encrypted_aes_key_b64 = str(metadata["encryptedAesKey"])

    aes_key = _unwrap_aes_key_v3(
        chain=chain,
        token_address=token_address,
        threshold=threshold,
        epoch=epoch,
        encrypted_aes_key_b64=encrypted_aes_key_b64,
        cache=cache,
    )

    src_path = Path(input_path)
    dst_path = Path(output_path)
    aesgcm = AESGCM(aes_key)

    with src_path.open("rb") as src, dst_path.open("wb") as dst:
        base_iv = src.read(12)
        if len(base_iv) != 12:
            raise RuntimeError("Encrypted file too short (missing base IV)")
        expected_chunk_index = 0
        while True:
            idx_data = src.read(4)
            if not idx_data:
                break
            if len(idx_data) != 4:
                raise RuntimeError("Truncated chunk index in encrypted file")
            chunk_index = struct.unpack("<I", idx_data)[0]
            if chunk_index != expected_chunk_index:
                raise RuntimeError(
                    f"Chunk ordering violation: expected chunk {expected_chunk_index}, "
                    f"got {chunk_index}. File may have been tampered with."
                )
            len_data = src.read(4)
            if len(len_data) != 4:
                raise RuntimeError("Truncated chunk length in encrypted file")
            chunk_len = struct.unpack("<I", len_data)[0]
            if chunk_len > 64 * 1024 * 1024:
                raise RuntimeError(
                    f"Chunk {chunk_index} claims {chunk_len} bytes — exceeds 64 MiB"
                )
            encrypted_chunk = src.read(chunk_len)
            if len(encrypted_chunk) != chunk_len:
                raise RuntimeError(
                    f"Truncated chunk data: expected {chunk_len}, got {len(encrypted_chunk)}"
                )
            per_iv = _derive_chunk_iv(base_iv, chunk_index)
            try:
                plaintext_chunk = aesgcm.decrypt(per_iv, encrypted_chunk, None)
            except Exception as exc:
                raise RuntimeError(
                    f"v3 AES-GCM decryption failed on chunk {chunk_index}."
                ) from exc
            dst.write(plaintext_chunk)
            expected_chunk_index += 1


# ── Version dispatch helpers ─────────────────────────────────────────


def decrypt_bytes_dispatch(
    ciphertext_bytes: bytes,
    *,
    metadata: dict[str, Any],
    private_key: str = "",
    cache: GateKeyCache | None = None,
) -> bytes:
    """Decrypt ``ciphertext_bytes`` routing on ``metadata.version``.

    v1 records (``version == 1``) are delegated to the existing
    :func:`haven_cli.crypto.haven_aol_local.decrypt_bytes` unchanged — same
    arguments, same behaviour. v3 records use :func:`decrypt_bytes_v3` with
    the in-memory :class:`GateKeyCache`.

    ``private_key`` is forwarded to the v1 path only; v3 derivation does
    not consume it (the transport secret comes from
    ``HAVEN_AOL_TRANSPORT_SECRET_KEY_B64``).
    """
    version = metadata.get("version") if isinstance(metadata, dict) else None
    if version == 1:
        # Import inside the branch so v3-only environments don't load v1's
        # ``cryptography`` stack at module init.
        from haven_cli.crypto.haven_aol_local import GateParams, decrypt_bytes

        gate = GateParams(
            chain=str(metadata["chain"]),
            token_address=str(metadata["tokenAddress"]),
            threshold=int(metadata["threshold"]),
            cid=str(metadata["cid"]),
        )
        return decrypt_bytes(
            ciphertext_bytes,
            private_key,
            str(metadata["encryptedAesKey"]),
            gate,
        )
    if version == GATE_METADATA_VERSION_V3:
        return decrypt_bytes_v3(ciphertext_bytes, metadata=metadata, cache=cache)
    raise ValueError(
        f"decrypt_bytes_dispatch: unknown gate metadata version {version!r}"
    )


def decrypt_file_dispatch(
    input_path: str | Path,
    output_path: str | Path,
    *,
    metadata: dict[str, Any],
    private_key: str = "",
    cache: GateKeyCache | None = None,
) -> None:
    """Decrypt a file by dispatching on ``metadata.version`` (v1 or v3)."""
    version = metadata.get("version") if isinstance(metadata, dict) else None
    if version == 1:
        from haven_cli.crypto.haven_aol_local import (
            GateParams,
            decrypt_file_streaming,
        )

        gate = GateParams(
            chain=str(metadata["chain"]),
            token_address=str(metadata["tokenAddress"]),
            threshold=int(metadata["threshold"]),
            cid=str(metadata["cid"]),
        )
        decrypt_file_streaming(
            input_path,
            output_path,
            private_key,
            str(metadata["encryptedAesKey"]),
            gate,
        )
        return
    if version == GATE_METADATA_VERSION_V3:
        decrypt_file_streaming_v3(
            input_path, output_path, metadata=metadata, cache=cache
        )
        return
    raise ValueError(
        f"decrypt_file_dispatch: unknown gate metadata version {version!r}"
    )


__all__ = [
    "encrypt_bytes_v3",
    "encrypt_file_streaming_v3",
    "decrypt_bytes_v3",
    "decrypt_file_streaming_v3",
    "decrypt_bytes_dispatch",
    "decrypt_file_dispatch",
]
