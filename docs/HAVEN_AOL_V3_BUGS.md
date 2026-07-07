# Haven-AOL v3 Implementation Bugs

Discovered during v3 integration testing. Entity referenced:
`0xccf08a8e386b3207b091c334a11450078eadd47212a4f5285c0e8a4ccaa6d632`

## Bug 1 — v3→v1 metadata downgrade

**File:** `haven_cli/crypto/gate_metadata.py:79-90`
**Caller:** `haven_cli/pipeline/steps/encrypt_step.py:749`

`encrypt_file_streaming_v3()` produces proper v3 gate metadata (`version:3`, `epoch`, no `cid` in
derivation). But `encrypt_step.py` calls `merge_encrypt_result_gate()` which unconditionally routes
through `build_gate_metadata()` — the **v1 builder** — stripping `epoch` and setting `version:1`.

```python
def merge_encrypt_result_gate(gate_partial, encrypted_aes_key_b64):
    return build_gate_metadata(          # <-- always v1
        cid=str(gate_partial["cid"]),
        chain=str(gate_partial["chain"]),
        token_address=str(gate_partial["tokenAddress"]),
        threshold=str(gate_partial.get("threshold", "1")),
        encrypted_aes_key_b64=encrypted_aes_key_b64,
    )
```

**Impact:** Every entity created with `encryption_version=3` stores v1 metadata on-chain
(`"version":1`, no `epoch`, CID-bound derivation). The v3 IBE ciphertext in `encryptedAesKey` was
produced with the v3 derivation input (`"accessol_v3:…:epoch"`) but the stored metadata says
`version:1` — a v3-aware decryptor would see v1 and try the wrong (v1/CID-based) derivation,
making the content **undecryptable**.

---

## Bug 2 — `is_gate_metadata()` v1-only

**File:** `haven_cli/crypto/gate_metadata.py:93-99`

```python
GATE_METADATA_VERSION = 1
REQUIRED_GATE_KEYS = frozenset({"version", "cid", "chain", "tokenAddress", "threshold", "encryptedAesKey"})

def is_gate_metadata(data: Any) -> bool:
    if data.get("version") != GATE_METADATA_VERSION:  # rejects version=3
        return False
    return REQUIRED_GATE_KEYS.issubset(data.keys())
```

No `is_gate_metadata_v3()` exists. No dispatching variant.

**Impact sites (6 call sites, all silently drop v3 contexts):**

| Location | Line | What gets skipped |
|---|---|---|
| `arkiv_sync.py:_build_attributes()` | 363 | Gate info in entity attributes |
| `arkiv_sync.py:_build_payload()` | 405 | `cid_encryption_metadata` in entity payload |
| `arkiv_sync.py:_build_payload()` | 411 | `encryption_metadata` in entity payload |
| `batch_sync.py:_partition_gated()` | 109 | v3 contexts treated as non-gated (no attestation) |
| `sync_step.py:_request_attestation()` | 300 | Per-file attestation skipped |
| `arkiv_sync.py:_write_encmeta_v2()` | (see source) | Sidecar metadata dropped |

---

## Bug 3 — `gate_metadata_to_json()` v1-only

**File:** `haven_cli/crypto/gate_metadata.py:160-164`

```python
def gate_metadata_to_json(gate: dict[str, Any]) -> str:
    if not is_gate_metadata(gate):       # rejects v3
        raise ValueError("Invalid gate metadata: expected version 1 with all required fields")
    return json.dumps(gate, separators=(",", ":"))
```

No v3-aware serialization in the payload builder. A separate `gate_metadata_v3_to_json()` exists in
the SDK (`haven_aol.v3`) but is never called from `_build_payload()`.

**Impact:** Would raise `ValueError` if a v3 gate metadata object reached this function. Currently
masked by Bug 2 (v3 metadata never makes it past `is_gate_metadata`).

---

## Bug 4 — Per-file AES key in v3 (should be per-epoch)

**File:** `haven_cli/crypto/haven_aol_v3.py:136`

```python
aes_key = os.urandom(32)  # new random key per file — even in v3
```

Config at `config.py:179` documents: `"1 = AES key per file (v1, default), 3 = AES key per epoch
(v3)"`. The code ignores this — every `encrypt_file_streaming_v3()` call generates a fresh 32-byte
key regardless of version.

**What v3 should do:**
- One AES key per `(chain, token, threshold, epoch)` bucket
- IBE-encrypt that single key once; reuse `encryptedAesKey` across all files in the epoch
- Ideally the AES key should come from the canister (VetKD symmetric derivation) rather than local
  `os.urandom`

**Impact:**
- Metadata bloat — each file's on-chain entity payload carries a unique `encryptedAesKey`
- No single epoch key to manage/rotate
- Defeats the v3 efficiency promise (one canister round-trip → decrypt all files in epoch)

---

## Bug 5 — Missing epoch-key injection in pipeline caller

**File:** `haven_cli/pipeline/steps/encrypt_step.py:693-702`

`encrypt_file_streaming_v3()` has an `encrypted_aes_key_b64` parameter specifically for reusing a
pre-computed IBE-wrapped key. The pipeline caller never passes it:

```python
encrypted = await asyncio.to_thread(lambda: encrypt_file_streaming_v3(
    input_path=video_path,
    output_path=encrypted_path,
    chain=chain,
    token_address=token_address,
    threshold=threshold,
    cid=cid_value,
    chunk_size=chunk_size,
    # encrypted_aes_key_b64=...  # never set — per-epoch reuse impossible
))
```

Even if Bug 4 were fixed at the crypto layer, the caller has no logic to cache and inject the epoch
AES key across files.

---

## Bug 6 — No encrypt-side epoch cache

**File:** `haven_cli/crypto/gate_key_cache.py` (decrypt only)

A `GateKeyCache` exists but is only used on the **decrypt** path
(`haven_aol_v3.py:_unwrap_aes_key_v3()`). There is no equivalent cache on the encrypt side to
remember the epoch AES key between `encrypt_file_streaming_v3()` calls.

**Impact:** Even if Bug 4's per-file `os.urandom(32)` is fixed, the system has nowhere to store the
epoch key between encryption calls. Each file in a batch would need to regenerate/refetch it.

---

## Bug 7 — No top-level `epoch` in entity payload

**File:** `haven_cli/services/arkiv_sync.py:380-502`

`_build_payload()` does not include an `epoch` field. For v3 the epoch is embedded inside the
`encryption_metadata` JSON string, but:

- Consumers (dapp, indexer) must parse the JSON to extract it
- No query/filter path exists for epoch-based queries on the entity
- Risk of consumer reliance on fragile JSON parsing of an opaque string field

---

## Bug 8 — `encrypt_file_streaming_v3` lacks raw-key injection

**File:** `haven_cli/crypto/haven_aol_v3.py:178-264`

The function accepts `encrypted_aes_key_b64` (the already-IBE-wrapped key) but not the **raw** AES
key. For per-epoch key reuse the caller needs to supply the raw 32-byte key (for AES-GCM encrypt)
and separately the IBE-wrapped form (for metadata). The signature only supports the latter.

---

## Summary Impact Matrix

| Bug | Category | Severity | Affected Flow |
|---|---|---|---|
| 1. v3→v1 downgrade | Data corruption | **Critical** — content undecryptable | Encrypt → Arkiv payload |
| 2. `is_gate_metadata` v1-only | Logic bug | **High** — v3 attestation skipped | Attestation, payload, attributes |
| 3. `gate_metadata_to_json` v1-only | Logic bug | **High** — would crash on v3 | Arkiv payload serialization |
| 4. Per-file AES key | Design gap | **High** — defeats v3 optimization | Encrypt |
| 5. Missing injection at call site | Design gap | **Medium** — blocks per-epoch fix | Pipeline → Encrypt |
| 6. No encrypt-side cache | Design gap | **Medium** — no epoch state between calls | Encrypt |
| 7. No top-level `epoch` | Queryability | **Low** — dapp can parse metadata | Entity payload |
| 8. Missing raw-key param | API gap | **Medium** — blocks per-epoch fix | Crypto API |

## Current v3 GateKeyCache (decrypt side only)

**File:** `haven_cli/crypto/gate_key_cache.py`

The `GateKeyCache` IS correctly implemented for decryption:

- Keyed on `(chain, token_address, threshold, epoch)` — natural rotation when epoch advances
- Caches the `CachedVetKey` bundle (encrypted key + verification key from canister)
- Used in `_fetch_vetkey_v3()` / `_unwrap_aes_key_v3()` to skip redundant canister calls
- Process-lifetime only, no disk persistence (by design)
- Thread-safe via `threading.Lock`

This cache works correctly — it just has no encrypt-side counterpart.
