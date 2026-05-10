# VetKD Transport Unwrap: Purpose, Gap, and Project Context

This document explains what **transport-key unwrapping** means for Haven CLI, why it matters for decryption, how it fits **Haven-AOL on the Internet Computer (ICP)**, and **which piece is still missing** in the Python stack.

**A Single, Strict Path:** By enforcing one decryption architecture, the system fails loudly and immediately when required cryptographic material is missing or malformed, instead of silently downgrading to weaker behavior.

---

## 1. Haven CLI in context

[Haven CLI](https://github.com/Haven-hvn/haven-cli) (`haven-cli`) is the operator-facing tool that encrypts uploads, attaches access-control metadata, and coordinates storage workflows. Encryption is integrated with **[Haven-AOL](https://github.com/Haven-hvn/haven-aol)**—an AOL (Always Online Layer) protocol on ICP that uses **Verifiably Encrypted Threshold Key Derivation (VetKD)** so that decryption keys can be released only when **on-chain conditions** (for example minimum token balance) hold.

Rough data flow relevant to crypto:

| Concern | Role in haven-cli |
|--------|---------------------|
| **Media files** | Encrypted with **AES-256-GCM** in **streaming chunked** form to avoid loading whole files into memory (OOM avoidance). See `haven_cli/crypto/haven_aol_local.py` (`encrypt_file_streaming` / `decrypt_file_streaming`). |
| **CID / small payloads** | Same Haven-AOL key-wrapping story but **non-streaming**: `encrypt_bytes` / `decrypt_bytes`. |
| **Gate parameters** | `chain`, token contract address, threshold, and content identifier (`cid`) define who may decrypt; they hash to a **`derivation_input`** used consistently in IBE/VetKD paths. |

The CLI talks to an ICP **canister** that exposes VetKD-backed operations (`getVetKDPublicKey`, `requestDecryptionKey`). The exact canister target is wired in code (`haven_cli/services/haven_aol_icp.py`); operators configure **their ICP identity** and networking, not an alternate AOL backend.

---

## 2. What “encryption works” actually means today

For **encryption**, haven-cli effectively does:

1. **Fetch the live VetKD verification key material** from the canister (`get_vetkd_public_key_b64` in `haven_cli/services/haven_aol_icp.py`). Using a **stale** cached key could desynchronize clients from the subnet’s threshold key epoch, so retrieval at encrypt time is intentional.

2. **Wrap the random AES content key** using Haven-AOL’s **IBE** helpers (via the `haven_aol` Python package): `derive_verification_key(...)`, then `ibe_encrypt_aes_key(aes_key, derived_public_key, derivation_input)`. The ciphertext of the AES key plus gate metadata travels with the upload.

3. **Encrypt payloads** with AES-GCM (`cryptography`): either chunked streaming layout for files or `iv || ciphertext` for small blobs.

Nothing in this path requires VetKD decryption on the client—only VetKD **public / verification** material and standard IBE **encryption** primitives.

---

## 3. What decryption must do (conceptual chain)

To **decrypt**, the client must recover the AES key and undo AES-GCM. With IBE-wrapped keys, that implies:

1. **Prove eligibility** to the canister (`requestDecryptionKey`): signed **EIP-712** structured data binds the caller’s **EVM address**, gate fields, **`transportPublicKey`**, **`nonce`**, and domain separators the canister verifies.

2. **Receive an encrypted VetKD response** (`GateResult.ok` as opaque `blob`). Internally this is an **`EncryptedVetKey`**: VetKD material encrypted **to the caller’s ephemeral transport keypair**, not plaintext on the wire.

3. **Transport unwrap (this document’s focus)**  
   Hold the **`TransportSecretKey`** matching the **`transportPublicKey`** you advertised in the GateRequest. Use VetKD cryptography to **`decrypt_and_verify`** the envelope into a **`VetKey`**, verifying it matches the subnet’s derivation for the expected **`DerivedPublicKey`** and **`derivation_input`**.

4. **Derive application symmetric material**  
   From the resulting `VetKey`, derive bytes used as (or keyed into) what the AOL stack expects—for example via [`VetKey::derive_symmetric_key`](https://docs.rs/ic-vetkeys/latest/ic_vetkeys/struct.VetKey.html) or [`derive_symmetric_key`](https://docs.rs/ic-vetkeys/latest/ic_vetkeys/fn.derive_symmetric_key.html) from the **`ic_vetkeys`** Rust crate—with a **domain separation string** fixed by Haven-AOL so encryption and decryption stay aligned.

5. **IBE-decrypt `encrypted_key_b64`**  
   Recover the plaintext AES content key using the symmetric material and the ciphertext produced at step 2 of §2 above (inverse of `ibe_encrypt_aes_key`).

6. **AES-GCM decrypt** the media or CID payload (`decrypt_bytes` / `decrypt_file_streaming`).

Until step 4–5 are implemented and wired behind the same derivation inputs Haven-AOL uses, **AES never becomes available** client-side—even if the canister already returned bytes in step 2.

---

## 4. What “transport unwrapping” means in plain language

Think of VetKD-derived keys as secrets that **no single replica** learns in the clear inside the subnet. When the AOL canister “approves” a gate, it obtains **key material encapsulated for transport**: encrypted so that **only whoever holds `TransportSecretKey`** can open it.

- **`transportPublicKey`** is safe to embed in EIP-712 and send on-chain-ish message surfaces; anyone can see it.
- **`TransportSecretKey`** must stay **on the client** (memory or secure enclave), never sent to RPC or logged.

**Transport unwrapping** is the cryptographic step that replaces “here is magically the AES key” with “here is **ciphertext**, decodable only paired with **`TransportSecretKey`**,” keeping **man-in-the-middle**, **replay**, and **non-gated callers** from learning VetKD-derived material from naked responses.

The Internet Computer libraries formalize this in Rust as [`EncryptedVetKey::decrypt_and_verify(...)`](https://docs.rs/ic-vetkeys/latest/ic_vetkeys/struct.EncryptedVetKey.html) together with **`TransportSecretKey`**, **`DerivedPublicKey`**, and the derivation **input bytes** aligned with AOL’s hashing of gate params.

---

## 5. The gap in haven-cli’s current Python dependencies

haven-cli correctly:

- Calls **`getVetKDPublicKey`** for encrypt-time verification key material (via **`ic-py`**).
- Implements **`requestDecryptionKey`** and receives **`bytes`** (`GateResult.ok`).

Today, **`decrypt_bytes`** and **`decrypt_file_streaming`** in `haven_cli/crypto/haven_aol_local.py` **still raise `RuntimeError`** after placeholder calls—not because networking failed, but because **no Python-visible binding** completes the cryptographic chain §3 steps 3–5.

Concrete symptoms:

| Have | Missing |
|------|---------|
| IBE **encrypt** path (`haven_aol` / `haven_aol_vetkeys`): `derive_verification_key`, `ibe_encrypt_aes_key` | Reliable IBE **decrypt** that consumes **VetKey-derived** material aligned with AOL |
| Returned **`blob`** from the canister | Parser + **`EncryptedVetKey::decrypt_and_verify`** equivalent in Python |
| Env/config surface for **`HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64`** for signing | Paired **`HAVEN_AOL_TRANSPORT_SECRET_KEY_B64`** env var (or deterministic derivation) so the daemon can unwrap `EncryptedVetKey` payloads. The daemon must fail to start if the secret is missing. |

The **`haven_aol_vetkeys`** / bundled native pieces used in encryption do not expose, in practice, everything needed to **consume** **`EncryptedVetKey`** payloads on Python’s side—or that surface is incomplete for this repo’s decryption path **without** extending it (typically **Rust + PyO3** calling **`ic-vetkeys`**, plus tests).

So the informal statement “expose the transport unwrap primitive” means:

**Add a callable from Python that performs**:

1. **Transport keypair load/derivation at daemon startup** (`TransportSecretKey` + matching `transportPublicKey` for EIP-712 `transportPublicKey` field).
2. **Deserialize/decode/decrypt-verify** `EncryptedVetKey` blobs using `TransportSecretKey`, `DerivedPublicKey`, derivation `input`.
3. **Derive symmetric key bytes** compatible with AOL’s **`ibe_decrypt_aes_key`** (or whichever inverse matches `ibe_encrypt_aes_key`).

until then, decryption functions **must refuse** rather than misuse weaker paths.

---

## 6. How this differs from legacy XOR wrapping

Older experimental paths in `"local"` helpers used XOR-based wrapping from a caller-supplied EVM-ish secret (`_unwrap_aes_key` / `_wrap_aes_key` in `haven_aol_local.py`). **IBE + VetKD** replace that threat model:

- VetKD derivation is tied to subnet threshold crypto and audited flows.
- IBE ciphertext is bound to **`derivation_input`** from gate metadata.
- The **AES key never appears** XOR’d with predictable streams from a standalone client secret.

_decrypt_ therefore **cannot** be “just XOR with `HAVEN_PRIVATE_KEY` keystream”; it **must** go through VetKD unwrap + AOL’s symmetric step. EIP-712’s `HAVEN_PRIVATE_KEY` only **authenticates entitlement** (`Proof`) to the AOL canister, not unwrap IBE payloads by itself.

**No Fallbacks:** Because the legacy XOR threat model is fundamentally incompatible with IBE + VetKD, `_wrap_aes_key` and `_unwrap_aes_key` must be treated as dead code for decryption. If `TransportSecretKey` is unavailable, the operation must fail with a hard error.

---

## 7. Implementation notes for contributors

Likely layering:

1. **Rust extension** (`ic-vetkeys` + transport types) exposing:
   - `generate_transport_keypair()` → `(secret_pem \| raw32, public_bytes)`
   - `unwrap_encrypted_vet_key(transport_secret_key, ciphertext, derived_public_key, derivation_input) -> VetKey_handle_or_bytes`
   - `derive_aol_aes_unwrap_material(vet_key, domain_sep) -> bytes` wired to AOL’s **`ibe_decrypt_aes_key`**

2. **Wire `haven_cli/services/haven_aol_icp.py` for daemon keypair model**  
   The transport keypair is a long-lived machine identity provided by the operator at daemon startup.
   - The daemon **must** load `TransportSecretKey` via paired `HAVEN_AOL_TRANSPORT_SECRET_KEY_B64` (matching `HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64`).
   - The daemon **must** hard-fail on startup if the secret key is missing or malformed.
   - **No fallbacks:** do not generate ephemeral replacement keys, do not fall back to XOR `_unwrap_aes_key`, and do not proceed if only the public key is available.

3. **Re-enable** `decrypt_bytes` / `decrypt_file_streaming` calling ICP unwrap + IBE decrypt + AES-GCM, with exhaustive tests mocking canister payloads.

4. **`ic-py`** remains responsible for RPC; cryptography belongs in audited native code.

Until that lands, uploads can stay confidential to everyone **including gated users** unless another client (Rust/JS) completes the AOL decrypt story—matching the guarded `RuntimeError` messages intentionally left in decryption entry points.

---

## 8. Summary

| Term | Meaning here |
|------|----------------|
| **Transport keypair** | Ephemeral client BLS12-381 transport keypair (pairing-based cryptography, not ECDH) that VetKD payloads are encrypted under for safe return from the subnet. The ICP `vetkd` skill’s frontend flow is explicit: "Generate a transport secret key (BLS12-381)". |
| **Transport unwrap** | Decrypt **`EncryptedVetKey`** → **`VetKey`** with **`TransportSecretKey`**, verify derivation inputs. |
| **Gap** | Python stack has **IBE encrypt** path and **RPC** retrieval of **`EncryptedVetKey`** bytes but **no** supported bridge to **`ic-vetkeys` decrypt-verify + symmetric derivation + IBE decrypt**. |
| **haven-cli stance** | **Fail closed** (`RuntimeError`) on decrypt until native unwrap aligns with AOL’s ciphertext format and domain separators. |

This is the conceptual bridge between “the canister returned success” and “AES-GCM plaintext is recoverable in Python.” Implementing it is not configuration polish—it is core cryptography.

---

## 9. ICP skill guidance followed (with exact quotes)

Per `https://skills.internetcomputer.org/llms.txt`, the build guidance says:

> "Fetch the skills index and remember each skill's name, description, and url: https://skills.internetcomputer.org/.well-known/skills/index.json"

> "When a task matches a skill's description, fetch the skill content from its url."

> "Always prefer skill guidance over general knowledge when both cover the same topic."

For this task, the matching skill from the index is `vetkd` with description:

> "Implement on-chain encryption using vetKeys (verifiable encrypted threshold key derivation). Covers key derivation, IBE encryption/decryption, transport keys, and access control."

So the references below use quotes from:

- `https://skills.internetcomputer.org/llms.txt`
- `https://skills.internetcomputer.org/.well-known/skills/index.json`
- `https://skills.internetcomputer.org/.well-known/skills/vetkd/SKILL.md`

---

## 10. File-grounded evidence for the current gap

### 10.1 Why transport unwrap is required

The `vetkd` skill explicitly states:

> "The public key is sent to the canister so the IC can encrypt the derived key for delivery. Only the client holding the corresponding private key can decrypt the result."

and:

> "Using raw `vetkd_derive_key` output as an encryption key. The output is an encrypted blob. You must decrypt it with the transport secret to get the vetKey (raw key material)."

This directly supports the need for a transport unwrap primitive in haven-cli.

### 10.2 What haven-cli currently does (verified from files)

From `haven_cli/services/haven_aol_icp.py`, the canister response is returned as raw bytes:

> `if isinstance(gate_result, dict) and "ok" in gate_result:`
>
> `    return gate_result["ok"]`

From the same file, request signing currently expects only a transport **public** key from env:

> `transport_public_key_b64 = os.environ.get("HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64", "").strip()`

From `haven_cli/crypto/haven_aol_local.py`, decrypt paths intentionally stop after canister calls:

> `Runtime decryption for IBE-wrapped keys requires canister key retrieval and`
>
> `transport-key unwrapping. This path is intentionally disabled in haven-cli.`

and:

> `"Derived-key transport unwrapping is not yet wired."`

These quotes prove the current state: canister bytes are retrieved, but no local transport-secret unwrap + VetKey derivation path exists in Python.

### 10.3 Why this blocks both payload modes

Both decryption entry points in `haven_cli/crypto/haven_aol_local.py` are guarded by the same runtime error text:

- `decrypt_bytes(...)` raises:
  > `"decrypt_bytes is disabled for ICP-only Haven-AOL mode. Derived-key transport unwrapping is not yet wired."`
- `decrypt_file_streaming(...)` raises:
  > `"decrypt_file_streaming is disabled for ICP-only Haven-AOL mode. Derived-key transport unwrapping is not yet wired."`

So the same missing primitive blocks:

- non-file payload decrypt (CID/small blobs), and
- streaming file decrypt.

### 10.4 Required implementation shape (skill-aligned)

The `vetkd` skill documents the canonical decrypt sequence:

> "The frontend generates a transport key pair, sends the public half to the canister, receives the encrypted derived key, decrypts it with the transport secret to get the vetKey (raw key material), then derives a symmetric key from that material ..."

and:

> `const vetKey = encryptedVetKey.decryptAndVerify(...)`

Applied to this repository, the missing component is a Python-callable bridge that can:

1. create/manage `TransportSecretKey` + `transportPublicKey`,
2. deserialize/decode/decrypt/verify `GateResult.ok` bytes as `EncryptedVetKey` (accounting for any nested payload encoding inside the blob),
3. derive key material compatible with Haven-AOL IBE decrypt, then
4. continue into existing AES-GCM decrypt logic.

---

## 11. Explicit gap register

This section lists the gaps that still block end-to-end decryption in haven-cli.

### Gap A: No paired transport-secret configuration in Python path

- **Current behavior:** request flow accepts only `HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64` from env.
- **Evidence:** `haven_cli/services/haven_aol_icp.py`:
  - `transport_public_key_b64 = os.environ.get("HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64", "").strip()`
- **Why this is a gap:** decrypt requires the matching **transport secret** (not just public key) to open `EncryptedVetKey`; public key alone is insufficient.
- **Impact:** canister can return `ok` blob, but client cannot decrypt it.
- **Policy correction:** add paired `HAVEN_AOL_TRANSPORT_SECRET_KEY_B64` (or deterministic derivation) and fail daemon startup if unavailable.

### Gap B: No `EncryptedVetKey` deserialize + decrypt/verify binding in Python

- **Current behavior:** `request_decryption_key(...)` returns raw bytes; no subsequent parse/verify step exists.
- **Evidence:** `haven_cli/services/haven_aol_icp.py`:
  - `if isinstance(gate_result, dict) and "ok" in gate_result:`
  - `    return gate_result["ok"]`
- **Why this is a gap:** vetKD response is encrypted envelope material and must be verified/unwrapped with transport secret and derived public key.
- **Impact:** no recoverable `VetKey` object/material in Python.

### Gap C: No VetKey -> symmetric material step wired for AOL IBE decrypt

- **Current behavior:** encryption uses IBE helpers (`derive_verification_key`, `ibe_encrypt_aes_key`) but decrypt-side inverse path is not wired.
- **Evidence:** `haven_cli/crypto/haven_aol_local.py` currently imports encrypt-side helpers only in `_ibe_encrypt_aes_key(...)`.
- **Why this is a gap:** without derived symmetric material aligned to AOL domain/context, `encrypted_key_b64` cannot be unwrapped to the AES content key.
- **Impact:** decryption cannot proceed beyond metadata retrieval.

### Gap D: Decrypt entry points intentionally fail-closed

- **Current behavior:** both decrypt functions stop after placeholder ICP calls and raise.
- **Evidence:** `haven_cli/crypto/haven_aol_local.py`:
  - `"decrypt_bytes is disabled for ICP-only Haven-AOL mode. Derived-key transport unwrapping is not yet wired."`
  - `"decrypt_file_streaming is disabled for ICP-only Haven-AOL mode. Derived-key transport unwrapping is not yet wired."`
- **Why this is a gap:** runtime behavior confirms missing unwrap chain is not optional.
- **Impact:** both small-payload and streaming file decryption are blocked.

### Gap E: No end-to-end tests asserting successful ICP unwrap/decrypt

- **Current behavior:** tests validate disabled behavior, not successful unwrap/decrypt.
- **Evidence:** current runtime contract is explicit `RuntimeError` for both decrypt paths.
- **Why this is a gap:** no regression guard for the intended completed ICP decryption pipeline.
- **Impact:** when unwrap is introduced, correctness and compatibility risks are high without fixture-based E2E tests.

---

## 12. Minimum acceptance criteria to close gaps

All items below should be true to mark the gap closed:

1. `request_decryption_key(...)` path uses a transport keypair whose secret half is available to the same request context.
2. Returned `GateResult.ok` bytes are deserialized and passed through `decrypt_and_verify`-equivalent logic.
3. Verified VetKey output is transformed into AOL-compatible decrypt material and successfully unwraps `encrypted_key_b64`.
4. `decrypt_bytes(...)` returns plaintext for valid inputs and fails with deterministic typed errors for invalid proofs/keys.
5. `decrypt_file_streaming(...)` successfully decrypts large files in chunks and preserves OOM-safe streaming behavior.
6. Unit + integration tests cover happy path and failure path for all above, with 100% coverage on new unwrap code paths.

---

## 13. Internet Computer skill-level gaps (implementation-agnostic)

These are gaps in guidance precision from the ICP skill material itself (not haven-cli-specific), where teams still need exact normative quotes from lower-level specs before locking design decisions.

### Skill gap 1: Version-stability uncertainty

- **Exact quote from `vetkd` skill:**  
  > "vetKeys is a newer feature of the IC. The `ic-vetkeys` Rust crate and `@dfinity/vetkeys` npm package are published, but the APIs may still change over time."
- **Why this is a gap:** this warns about churn but does not give a compatibility matrix by IC release, crate version, and breaking API surface.
- **Missing exact quote needed:** a normative changelog statement per release that explicitly lists breaking API changes affecting `decrypt_and_verify`, key serialization formats, and key-material derivation helpers.

### Skill gap 2: Transport key reuse policy is strong but underspecified

- **Exact quote from `vetkd` skill:**  
  > "Reusing transport keys across sessions. Each session must generate a fresh transport key pair."
- **Why this is a gap:** "session" is not formally defined (request, login session, process lifetime, device lifetime).
- **Missing exact quote needed:** canonical security guidance defining required rotation boundary (per request vs per auth session), and any acceptable exceptions.

### Skill gap 3: Symmetric key derivation guidance is high-level, not interoperable by default

- **Exact quote from `vetkd` skill:**  
  > "You must decrypt it with the transport secret to get the vetKey (raw key material). What you do next depends on your use case..."
- **Why this is a gap:** this is correct but does not pin an interoperable domain-separation standard for cross-language clients in the same application protocol.
- **Missing exact quote needed:** protocol-level requirement that names the exact derivation function, domain separator encoding, output length, and byte-order conventions for interoperable decrypt.

### Skill gap 4: Determinism statement may be misread without parameter-scoping nuance

- **Exact quote from `vetkd` skill:**  
  > "Derivation is deterministic: the same inputs always produce the same key..."
- **Why this is a gap:** "same inputs" can be interpreted incompletely if teams forget full tuple scope (`canister_id`, `context`, `input`, `key_id`), resulting in accidental mismatch assumptions.
- **Missing exact quote needed:** a strict statement from the interface spec enumerating the full derivation identity tuple and any serialization requirements for each field.

### Skill gap 5: Authorization requirements are advisory, not a formal checklist

- **Exact quote from `vetkd` skill:**  
  > "If you implement IBE manually ... your canister must enforce that `vetkd_derive_key` only returns the derived key to the authorized caller..."
- **Why this is a gap:** strong guidance, but no minimum mandatory access-control checklist (caller binding, replay protections, nonce semantics, audit logging expectations).
- **Missing exact quote needed:** security baseline language from IC docs or vetted library docs that can be converted into testable policy controls.

### Skill gap 6: Fee guidance is approximate and operationally insufficient

- **Exact quote from `vetkd` skill:**  
  > "`key_1` costs ~26B cycles and `test_key_1` costs ~10B cycles."
- **Why this is a gap:** approximate values are useful but not sufficient for SLO/SLA budgeting and production safety margins.
- **Missing exact quote needed:** canonical fee source and update cadence that teams can bind to alerts and automatic top-up policies.

### Skill gap 7: Verification-key retrieval options need stronger decision criteria

- **Exact quotes from `vetkd` skill:**  
  > "You can also derive this public key offline..."  
  > "vetkd_public_key does not require cycles..."
- **Why this is a gap:** both options are valid, but the skill does not provide a firm decision rubric for when offline derivation is preferred vs live retrieval.
- **Missing exact quote needed:** authoritative guidance on trust/consistency trade-offs, cache invalidation, and operational failure handling for each option.

---

## 14. How to close skill-level quote gaps

Before finalizing protocol decisions, collect and pin exact quotes from:

1. IC interface spec sections for `vetkd_public_key` and `vetkd_derive_key`.
2. `ic-vetkeys` API docs for `EncryptedVetKey`, `VetKey`, and derivation helpers.
3. DFINITY security guidance (or vetted examples) for authorization controls around key release.
4. Official fee documentation for current cycle costs and update policy.

Treat those sources as normative references and keep the skill doc as implementation guidance.
