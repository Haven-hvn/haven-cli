# Arkiv Data Format — v2.0.0

This document describes the data format used by haven-cli when writing to the Arkiv blockchain.

> **Related Documentation:**
> - [API Reference](API_REFERENCE.md) - Complete API reference with schema definitions
> - [Integration Guide](INTEGRATION_GUIDE.md) - Developer integration guide
> - [Migration Notes](MIGRATION_NOTES.md) - Migration guide for data format changes
> - [Python API Reference](api.md) - Python SDK documentation
> - Shared media spec: `docs/entities/MEDIA_CONTENT_SPEC.md` (haven-docs repo) — normative for key names

## Overview

haven-cli uses the Haven Cross-Application Data Format **v2.0.0**. Straight migration from v1.x:
no backwards compatibility, no alias readers, no dual keys. Old entities are left to expire
(CLI records expire by default — see Expiry) except long-pinned drip records, which must be
explicitly deleted (see Straight-migration checklist).

Design goals: **few attributes, numeric types, one hierarchical group key, zero mirrors.**
Arkiv is a blockchain — every stored byte is paid for (see Cost model).

## Cost model

An Arkiv `Attribute` is `{ name; valueType; value }` — roughly **~6 words ≈ 192 bytes on-chain
per attribute, independent of value length**. A `str` always occupies its full 128-byte slot,
so `language:"en"` (2 B) costs the same on-chain as a 128 B string.

Rules that follow:

1. **Attribute *count* dominates cost.** 17–20 attrs ≈ 3.3–3.8 KB/entity before payload.
   Prefer fewer attributes over shorter values.
2. **`str` → numeric saves ~96 B/attr** (4 words → 1 word). Shortening a string value saves
   only calldata, not storage.
3. **An attribute must justify itself with a query pattern.** If no reader filters, sorts, or
   displays from it on-chain, it belongs in the payload (or Filecoin), not in attributes.

## Query language (SDK semantics — normative for what may be an attribute)

From `@arkiv-network/sdk` `src/query/expression.ts` + `src/attr/types.ts` (develop):

- **Operators:** `=` / `!=` on all types; `<` `<=` `>` `>=` only on ordered types
  (`i32`, `u64`, `u256`, `dec`); `STARTSWITH` on `str` only (raw UTF-8 byte-prefix index);
  `EXISTS(name)`; `TYPEOF(name) = tag`; `NOT` / `AND` / `OR`. **No joins.**
- **Comparisons are type-exact.** Literals: bare number → `i32`, bare string → `str`,
  bare bool → `bool`; tagged: `i32(4)`, `u64(…)`, `str('…')`, `addr(0x…)`, `key(0x…)`,
  `bytes32(0x…)`, `dec(…)`. A query for `gate_type = i32(4)` never matches a `str` of the
  same digits — writers must use the exact tagged type in the tables below.
- **`!=` is typed negation:** entities that never set the attribute (or set it with another
  type) do **not** match. Use `NOT attr = value` for the complement.
- **Attribute types:** `bool`, `i32`, `u64`, `u256`, `dec`, `bytes32`, `str` (≤128 B), `addr`, `key`.
  `bytes` is system-only (backs `$payload`), never settable.
- **`key` is a weak entity reference** (dangling refs permitted), equality-indexed.
  Series→parts fan-out is ONE indexed query (`series_ref = key(0x…)`), not N+1.
- **Queryable system attributes:** `$key`, `$owner`, `$creator`, `$expiresAt` (u64).
  **Result-only (selectable, not filterable):** `$createdAt`, `$updatedAt`, `$contentType`,
  `$payload`. Recency/ordering uses `$createdAt` — never a custom timestamp attribute.

> **Wire-vocabulary note.** Older Haven docs describe `valueType: 1=ATTR_UINT, 2=ATTR_STRING,
> 3=ATTR_ENTITY_KEY`. The current JS SDK wire uses `bool=1, i32=2, u64=3, u256=4, dec=5,
> bytes32=6, str=8, addr=9, key=10`. v2.0.0 specifies JS SDK tags. The CLI's Python
> SDK (`arkiv-sdk 1.0.0b2`) expresses only `str`/`int` annotations, so the CLI writer
> realizes `gate_token`/`sha256_ct` as lowercase-hex `str` and all gate/enum facts as
> `int` — see `CHAIN_VARIANT_TO_EIP155` / `MIME_TO_ENUM` in `arkiv_sync.py`.

## Taxonomy (`grp` — the single group key)

Usenet/Big-8 style dot hierarchy. One `str` attribute replaces `project` + `type` + `category`
(+ `tags`, which were never indexed — free text moves to payload or dies).

| `grp` value | Producer | Content |
|---|---|---|
| `haven.video.full` | haven-cli | Full media record (v1 per-file / v3 per-epoch gates) |
| `haven.video.drip.series` | haven-dapp | v4 drip series header (shared facts, stored once) |
| `haven.video.drip.part` | haven-dapp | v4 drip chunk (per-stage facts + crypto material) |
| `haven.audio.full` | reserved | Future audio uploads |
| `haven.image.full` | reserved | Future image uploads |
| `haven.text.full` | reserved | Future text uploads |
| `haven.meta.gate` | reserved | Future shared gate-corpus records |

Query patterns: exact `grp = str('haven.video.full')`; subtree `grp STARTSWITH str('haven.video.')`.
`STARTSWITH` matches raw bytes — always lowercase ASCII, never trailing-dot the prefix.

## Attributes schema

All keys lowercase snake_case. Types are SDK tags (`str`, `i32`, `addr`, `bytes32`, `key`).

### `haven.video.full` (haven-cli)

| Key | Type | When | Query justification |
|---|---|---|---|
| `grp` | `str` | always `haven.video.full` | subtree/exact scoping |
| `title` | `str` | always (≤128 B) | list display + prefix search |
| `gate_type` | `i32` | always, `1`\|`3` | gate-class filter; `== gate.version` numerically |
| `gate_token` | `addr` | always | co-membership / community discovery |
| `gate_chain` | `i32` | always | EIP chain id (`1, 10, 56, 137, 42161, 8453, …`) — replaces `EthMainnet`-style strings |
| `gate_threshold` | `i32` | always | threshold filter (must fit i32; revisit as `u256` if raw-unit thresholds outgrow it) |
| `gate_epoch` | `i32` | v3 only | epoch corpus grouping |
| `sha256_ct` | `bytes32` | always | sha256 hex digest of the record's root locator string (dedup lookup + restore key; the locator itself lives in payload `piece`/`fcid`). Renamed from `cid_hash`. Attrs-side only — never mirrored in payload |
| `mime` | `i32` | always | MIME enum (see table); viewer dispatch without fetching payload |
| `dur_s` | `i32` | when known, whole seconds (`0`/omit = unknown) | duration display/sort without payload |

Max **10 attributes** (typical encrypted v3 record: 10; v1: 9).

### `haven.video.drip.series` (haven-dapp publisher)

Shared facts stored **once** per drip run — never repeated per chunk.

| Key | Type | Notes |
|---|---|---|
| `grp` | `str` | `haven.video.drip.series` |
| `title` | `str` | series title |
| `gate_type` | `i32` | always `4` |
| `gate_token` | `addr` | drip token contract (lowercased) |
| `gate_chain` | `i32` | EIP chain id |
| `gate_threshold` | `i32` | threshold (full corpus triple lives on the series; parts carry none) |
| `drip_id` | `str` | stable run id (uuid, 36 B) — the thread key |
| `drip_total` | `i32` | stage count |

Series payload: `{ targets: <uint[] per-stage whole-USD targets>, creator?: <handle>, mime?: <enum int> }`.
The feed lists **parts**, not series; the series is fetched once per `drip_id` for title/total.

### `haven.video.drip.part` (haven-dapp publisher)

| Key | Type | Notes |
|---|---|---|
| `grp` | `str` | `haven.video.drip.part` |
| `gate_type` | `i32` | always `4` |
| `drip_id` | `str` | thread key (joins to series) |
| `drip_idx` | `i32` | 0-based stage index |
| `series_ref` | `key` | entity key of the series header — one indexed query fans out |
| `mcap_usd` | `i32` | whole-USD unlock target for **this** stage; range-indexed (`mcap_usd >= i32(50000)`); i32 caps a stage at ~$2.1 B |
| `sha256_ct` | `bytes32` | sha256 of ciphertext bytes |

Max **7 attributes** (was 17). No `title` (join series once), no `drip_total` (on series),
no `oracle_address` (unused — re-add only when enforced on-chain), no `published_by`
(never queried — provenance is `$creator`), no `epoch` mirror (lives inside the v4 gate JSON).

Part payload: `{ piece, gate }` — see Payload schema.

### Deleted in 2.0.0 (do not write, do not read)

`project`, `type`, `category`, `tags`, `language`, `is_encrypted` (infer from `gate_type`
presence), `encrypted_cid` (locator is now `sha256_ct` + payload `piece`), `cid_hash`
(renamed `sha256_ct`), `created_at` / `updated_at` / `created_at_ts` (use system `$createdAt`),
`creator_handle` / `source_uri` / `phash` / `analysis_model` / `mint_id` as attributes
(payload-only now, see below), `published_by`, `oracle_address`, `description` (unbounded —
dropped; long-form text lives off-chain behind a CID), `thumbnail_cid` (specified but never
written/read — stays out until a writer exists), `gate_version` (already removed in 1.1.0).

## Payload schema

JSON, `snake_case`, **short keys**. Payload mirrors of attributes are forbidden (Q: no
Filecoin-without-Arkiv restore requirement — attrs are always readable alongside payload,
so mirrors only ever duplicated bytes).

### `haven.video.full` payload

```json
{
  // Encrypted records carry "piece"; clear records carry "fcid" instead — never both.
  "piece": "bafkzcib…",
  // "fcid": "Qm…/bafy…",
  "gate": "<v1/v3 gate JSON string: version,cid,chain,tokenAddress,threshold[,epoch],encryptedAesKey>",
  "cid_gate": "<CID-gate JSON string, only if distinct from content gate>",
  "size": 10485760,
  "pt_hash": "0x… (sha256 of plaintext before encryption)",
  "seg": { "segment_index": 0, "start_timestamp": "…", "end_timestamp": "…", "mint_id": "…", "recording_session_id": "…" },
  "codecs": ["h264", "hevc"],
  "vlm": "Qm… (VLM analysis JSON CID)",
  "vlm_model": "zai-org/glm-4.6v-flash",
  "src": "https://… (provenance URI)",
  "creator": "@handle",
  "phash": "<perceptual hash>",
  "attn": "{…single attestation…} | {…merkle-v2…}"
}
```

Key renames (old → new): `filecoin_root_cid` → `fcid` (clear only) / `piece_cid` → `piece`
(encrypted only — one locator per record class, never both, never thrice);
`encryption_metadata` → `gate`; `cid_encryption_metadata` → `cid_gate`;
`content_file_size`/`file_size` → `size`; `original_hash` → `pt_hash`;
`vlm_json_cid` → `vlm`; `analysis_model` → `vlm_model`; `source_uri` → `src`;
`creator_handle` → `creator`; `codec_variants` → `codecs`; `segment_metadata` → `seg`;
`attestation` → `attn`.

Dropped from payload: `is_encrypted` (infer from `gate` presence), `cid_hash` (attrs-side
`sha256_ct`), `gate_type`/`epoch` top-level mirrors (inside `gate` JSON already),
`duration` float mirror (attr `dur_s`), `content_mime_type` (attr `mime`),
`expires_at_block` (use system `$expiresAt`), `created_at_block` (use `$createdAt`),
`has_ai_data` (infer from `vlm` presence), `description` (off-chain).

### MIME enum (`mime: i32`, shared across all future `haven.*` groups)

| Value | MIME | Value | MIME |
|---|---|---|---|
| 1 | `video/mp4` | 7 | `image/png` |
| 2 | `video/webm` | 8 | `image/jpeg` |
| 3 | `video/quicktime` | 9 | `image/webp` |
| 4 | `audio/mpeg` | 10 | `image/gif` |
| 5 | `audio/wav` | 11 | `image/svg+xml` |
| 6 | `audio/ogg` | 12 | `text/plain` |
| 13 | `text/markdown` | 14 | `application/pdf` |

`0` / omitted = unknown. Extend by appending — never renumber.

### Gate JSON (frozen — Haven-AOL layer, NOT changed by 2.0.0)

`version: 1` / `3` (+`epoch`, 2592000 s epochs) / `4` (+`marketCapTarget`, `oracleAddress`).
`gate_type == gate.version` numerically, always. The verbose in-JSON spellings
(`chain: "EthMainnet"`, `threshold: "1"` string) stay frozen inside the blob; the compact
forms (`gate_chain: i32`, `gate_threshold: i32`) live in attributes.

## Expiry (BTL) policy

Storage cost scales with block-to-live. Defaults:

| Record class | Default BTL | Mechanism |
|---|---|---|
| `haven.video.full` (CLI) | **4 weeks** | unchanged; `ARKIV_EXPIRATION_WEEKS` env (min 1) |
| `haven.video.drip.series` | **52 weeks** | series header outlives parts |
| `haven.video.drip.part` | **12 weeks** | `EXTEND` (op 3) while the series is active; expire after |

The dapp's former 10-year pin (`expiresIn: 315360000`) is abolished — pinning ~5 KB/chunk
for a decade is the single most expensive line in the old format.

## Query cookbook (exact SDK spellings)

```
// All Haven video (feed scope)
grp STARTSWITH str('haven.video.')

// Drip feed: attributes only — never select payload for list rows
// (old feed over-fetched encryptedAesKey per row and ignored it)
AND(grp = str('haven.video.drip.part'), gate_type = i32(4))

// Stages of one drip (one indexed query, no N+1)
series_ref = key(0x<series entity key>)

// Dedup on upload (CLI find_existing_entity)
sha256_ct = bytes32(0x…)

// Price-gated discovery (ordered index)
mcap_usd >= i32(50000)

// Complement (NOT != — != misses entities lacking the attribute)
NOT gate_type = i32(4)

// Recency: select $createdAt (+ $expiresAt for staleness), sort client-side
```

`select()` only what the view renders: list rows need key + attributes; detail/decrypt
views add payload. Every selected field is fetched over the wire.

## Privacy rules

### ⚠️ CRITICAL: Never Store in Attributes (Public)

- **Raw CIDs** — `sha256_ct` in attributes, CIDs only in payload
- **Ciphertext** — on Filecoin, never in payload or attributes
- **Encryption keys** — only IBE-wrapped key metadata inside `gate` JSON

### ✅ Store in Payload

- Locator CID (`piece` / `fcid`), `gate` / `cid_gate` JSON, `vlm`, `seg`, `attn`,
  provenance (`src`, `creator`, `phash`)

### ✅ Store in Attributes

- `grp`, `title`, gate corpus (`gate_type/token/chain/threshold[/epoch]`),
  `sha256_ct`, `mime`, `dur_s`, drip coordinates (`drip_id/idx/total`, `mcap_usd`, `series_ref`)

### Design notes (preserved from v1.x)

- `title` is required and public: anyone can read the table of contents while content stays
  sealed. There is no member-visible-but-not-public tier (see gaps note in the shared spec).
- The gate attributes are public **by design**: `gate_token` / `gate_chain` / `gate_threshold`
  in the clear make the co-membership graph computable from public chain state — the
  protocol's discovery and recommendation mechanism needs no server and no tracking.
  Do not blind these. Addresses are pseudonyms; Haven records **no view events**, so the
  graph states who *can* read what, never who read what.

## Field name standards (v2.0.0 canonical)

| Purpose | v2.0.0 name | v1.x name(s) — do not use |
|---|---|---|
| Group / namespace | `grp` | `project` + `type` (+ `category`, `tags`) |
| Ciphertext hash | `sha256_ct` (attrs, `bytes32`) | `cid_hash` |
| Chain | `gate_chain` (`i32` EIP id) | `gate_chain` (`str` `EthMainnet`…) |
| MIME | `mime` (`i32` enum) | `content_mime_type` (`str`) |
| Duration | `dur_s` (`i32` seconds) | `duration` (`UINT` + float mirror) |
| Filecoin locator | `piece` / `fcid` (payload) | `piece_cid` + `filecoin_root_cid` + `cid_hash` (all three) |
| Content gate | `gate` (payload) | `encryption_metadata` |
| CID gate | `cid_gate` (payload) | `cid_encryption_metadata` |
| Plaintext hash | `pt_hash` (payload) | `original_hash` |
| Size | `size` (payload) | `content_file_size` / `file_size` |
| VLM CID / model | `vlm` / `vlm_model` (payload) | `vlm_json_cid` / `analysis_model` |
| Provenance | `src` / `creator` (payload) | `source_uri` / `creator_handle` |
| Segments / codecs / attestation | `seg` / `codecs` / `attn` (payload) | `segment_metadata` / `codec_variants` / `attestation` |
| Drip stage target | `mcap_usd` (attrs, `i32`) | `market_cap_target_usd` |
| Drip index / total / id | `drip_idx` / `drip_total` / `drip_id` | `drip_index` / `drip_total` / `drip_id` |
| Series link | `series_ref` (attrs, `key`) | (new — repeated series facts per chunk) |
| Created / expiry | system `$createdAt` / `$expiresAt` | `created_at` / `updated_at` / `created_at_ts` / `expires_at_block` / `created_at_block` |

## Cross-application compatibility

- **haven-cli** (writer, `haven.video.full`): `_build_attributes()` / `_build_payload()` in
  `haven_cli/services/arkiv_sync.py`; dedup via `sha256_ct = bytes32(…)` (`find_existing_entity`).
- **haven-dapp** (writer `haven.video.drip.*`, reader): drip publisher + feed query
  (`grp = str('haven.video.drip.part')`, attributes-only select for rows).
- **haven-mobile** (reader): tolerant single-casing parse (snake_case canonical — the
  `firstString`×4 alias chains collapse), gateway `GET /api/arkiv/media` family unchanged.
- **haven-player**: gold-standard reference; must adopt `grp` scoping on read.

## Straight-migration checklist (no backcompat)

1. CLI: rewrite `_build_attributes` / `_build_payload` to the `haven.video.full` tables;
   verify Python lib tag mapping (`addr`/`bytes32`/`i32`) against the chain.
2. dapp: publisher emits series + parts; feed queries parts (attributes-only) + one series
   fetch per `drip_id`; delete camelCase/snake duals and payload mirrors.
3. Mobile: parse canonical keys; drop alias chains; no `gate_version`/`gateVersion` anywhere.
4. Data: CLI v1.x records self-clean via 4-week BTL. All 10-year-pinned v4 drip records
   must be explicitly `DELETE`d (op 5) — list them via the old markers (`gate_type = i32(4)`
   and the pre-1.1.0 `gate_version = str('v4')`), then delete by entity key.
5. Docs: shared spec `MEDIA_CONTENT_SPEC.md` is normative on key names; this file is
   normative on CLI wire shape. On conflict, file an issue — do not fork keys locally.

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02 | Initial standardized format |
| 1.1.0 | 2026-09 | `gate_version` → `gate_type` (numeric `1`/`3`/`4`; no backcompat) |
| 2.0.0 | 2026-09 | Usenet-style `grp` taxonomy replaces `project`/`type`/`category`/`tags`; numeric SDK types (`addr`/`bytes32`/`i32` chain ids, MIME enum); drip threading (series + `series_ref: key` parts); payload short keys; all mirrors deleted; `$createdAt`/`$expiresAt` replace timestamp attrs; BTL policy (4 w full / 52 w series / 12 w parts); 10-year pins abolished. Straight migration, no backcompat. |

## Related Documentation

- [API Reference](API_REFERENCE.md) - Complete API reference with schema definitions
- [Integration Guide](INTEGRATION_GUIDE.md) - Developer integration guide
- [Migration Notes](MIGRATION_NOTES.md) - Migrating from old format
- [Python API Reference](api.md) - Python SDK documentation
- [CLI Reference](cli-reference.md) - Command-line reference
