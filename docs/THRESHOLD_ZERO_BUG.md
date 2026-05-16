# Bug: `threshold=0` in Derivation Input Makes Files Undecryptable

**Severity:** HIGH — Existing encrypted files on Filecoin/Arkiv cannot be decrypted  
**Status:** Fixed  
**Discovered:** 2026-05-16

## Summary

Files encrypted with the `nft_gated` (or any pattern using `returnValueTest.value: "0"`)
produce a `GateParams(threshold=0)` for the VetKD derivation input. The Haven-AOL
canister rejects `threshold=0` with `#InvalidThreshold` on the decrypt path
(`requestDecryptionKey`), making these files **permanently undecryptable**.

## Root Cause

Two separate concepts are conflated:

1. **On-chain access condition** (`returnValueTest.value`): `"0"` means
   `balanceOf > 0` — the user must hold ≥ 1 NFT. This is the condition checked
   by the canister's RPC call to the NFT contract.

2. **Derivation input threshold** (`GateParams.threshold`): Used in the VetKD
   derivation preimage:
   ```
   SHA-256("accessol:{chain}:{tokenAddress}:{threshold}:{cid}")
   ```
   The Haven-AOL canister requires `threshold >= 1` and rejects `threshold=0`
   with `#InvalidThreshold`.

The encrypt step at `encrypt_step.py:600` reads `returnValueTest.value` directly
and passes it as the derivation threshold:
```python
threshold_raw = gate_condition.get("returnValueTest", {}).get("value", "1")
threshold = int(str(threshold_raw))  # "0" → 0
```

For `nft_gated`, the condition is:
```python
{"comparator": ">", "value": "0"}  # balanceOf > 0
```

This produces `threshold=0` in the derivation input. The encrypt side works
because `ibe_encrypt` (called locally via `vetkd_py`) does not validate the
threshold. But the decrypt side calls `requestDecryptionKey` on the canister,
which **rejects** `threshold=0`.

## Canister Code

From `VETKD_FUNCTIONALITY_REVIEW.md` (Finding F1):

```motoko
if (req.threshold == 0) return #err(#InvalidThreshold);
```

This guard was intended to block the `public` access pattern (which would allow
anyone to request decryption keys without holding any token). However, it also
blocks legitimate `nft_gated` and `token_gated` patterns that use
`returnValueTest.value: "0"`.

## Impact

- **All files encrypted with `nft_gated` pattern are undecryptable** via the
  standard decrypt flow (both `haven download --decrypt` and
  `haven download decrypt-file`).
- The `returnValueTest.value: "0"` is stored in Arkiv entity payloads and in
  the local DB `encryption_metadata` field.
- The canister has **always** rejected `threshold=0` — this is not a recent
  regression.

## Evidence

Local DB query on existing videos shows:
```
Video 1: returnValueTest.value = "0", threshold used in derivation = 0
Video 2: returnValueTest.value = "0", threshold used in derivation = 0
```

Attempting `request_decryption_key(threshold=0)` returns:
```
{'InvalidThreshold': None}
```

Attempting `request_decryption_key(threshold=1)` returns:
```
IBE decryption failed: decryption failed
```
(because the derivation input doesn't match — encryption used `threshold=0`,
decryption tries `threshold=1`, producing a different derived key).

## Fix Applied

### 1. Shared helper (`haven_aol_local.py`)

Added `derivation_threshold_from_access_condition()` to parse
`returnValueTest.value` and clamp the VetKD derivation threshold to `>= 1`.
On-chain access conditions (e.g. `balanceOf > 0` with value `"0"`) are unchanged.

### 2. Encrypt / decrypt / upload paths

`encrypt_step.py`, `download.py`, and `upload_step.py` call the shared helper so
encrypt, decrypt, and CID encryption all use the same derivation threshold.

### 3. Canister client validation (`haven_aol_icp.py`)

Relaxed the client-side check from `threshold < 1` to `threshold < 0`. The
canister itself enforces `threshold != 0`; the client-side check was overly
strict and redundant.

## Remediation for Existing Files

Files already encrypted with `threshold=0` in the derivation input **cannot be
decrypted** with the current canister code. Options:

1. **Canister upgrade**: Modify the canister to allow `threshold=0` when the
   access control conditions include a non-trivial balance check (i.e., the
   `public` pattern is the only one that should be blocked, not all
   zero-threshold gates).

2. **Re-encrypt**: Re-upload the files with the fixed encrypt step
   (`threshold=1` in derivation, `returnValueTest.value: "0"` for on-chain).

3. **Canister-side threshold mapping**: Change the canister to treat
   `threshold=0` as `threshold=1` for derivation purposes while still
   performing the on-chain balance check.

## Files Changed

| File | Change |
|------|--------|
| `haven_cli/crypto/haven_aol_local.py` | `derivation_threshold_from_access_condition()` |
| `haven_cli/pipeline/steps/encrypt_step.py` | Use shared derivation threshold helper |
| `haven_cli/cli/download.py` | Use shared derivation threshold helper |
| `haven_cli/pipeline/steps/upload_step.py` | Use shared derivation threshold helper |
| `haven_cli/services/haven_aol_icp.py` | Relaxed client check from `< 1` to `< 0` |

## Related

- `docs/VETKD_FUNCTIONALITY_REVIEW.md` — Finding F1: Canister Rejects `threshold == 0`
- `docs/VETKD_IMPLEMENTATION_SECURITY_REVIEW.md` — Documents the `InvalidThreshold` guard
