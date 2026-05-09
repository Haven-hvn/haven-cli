# Arkiv Data Format

This document describes the data format used by haven-cli when writing to the Arkiv blockchain.

> **Related Documentation:**
> - [API Reference](API_REFERENCE.md) - Complete API reference with schema definitions
> - [Integration Guide](INTEGRATION_GUIDE.md) - Developer integration guide
> - [Migration Notes](MIGRATION_NOTES.md) - Migration guide for data format changes
> - [Python API Reference](api.md) - Python SDK documentation

## Overview

haven-cli uses the Haven Cross-Application Data Format v1.0.0, ensuring full compatibility with:
- **haven-player** (Gold Standard - reference implementation)
- **haven-dapp** (reader application)

## Entity Structure

Each video upload creates an Arkiv entity with two main components:

- **Attributes**: Public, searchable metadata indexed on-chain
- **Payload**: Private, encrypted metadata stored on-chain

```
┌─────────────────────────────────────────────────────────────┐
│                    Arkiv Entity                              │
├──────────────────────────┬──────────────────────────────────┤
│      Attributes          │           Payload                │
│  (Public, Searchable)    │    (Private, Encrypted)          │
├──────────────────────────┼──────────────────────────────────┤
│ • title                  │ • filecoin_root_cid              │
│ • creator_handle         │ • is_encrypted                   │
│ • source_uri             │ • cid_hash                       │
│ • cid_hash               │ • vlm_json_cid                   │
│ • is_encrypted           │ • encryption_metadata            │
│ • encrypted_cid          │ • segment_metadata               │
│ • phash                  │ • codec_variants                 │
│ • analysis_model         │ • duration                       │
│ • mint_id                │ • file_size                      │
│ • created_at             │                                  │
│ • updated_at             │                                  │
└──────────────────────────┴──────────────────────────────────┘
```

## Payload Schema

The payload contains sensitive data that should remain private. It is stored as encrypted bytes on the Arkiv blockchain.

### JSON Structure

```json
{
  "filecoin_root_cid": "Qm...",
  "is_encrypted": true,
  "cid_hash": "sha256...",
  "vlm_json_cid": "Qm...",
  "encryption_metadata": "{...}",
  "cid_encryption_metadata": "{...}",
  "segment_metadata": {
    "segment_index": 0,
    "start_timestamp": "2026-02-20T10:00:00Z",
    "end_timestamp": "2026-02-20T10:05:00Z",
    "mint_id": "...",
    "recording_session_id": "..."
  },
  "codec_variants": ["h264", "hevc"],
  "duration": 300.5,
  "file_size": 10485760
}
```

### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `filecoin_root_cid` | string | CID of video on Filecoin (NEVER use `root_cid`) |
| `is_encrypted` | boolean | Encryption status (`true` or `false`) |
| `cid_hash` | string | SHA256 hash of `filecoin_root_cid` |
| `vlm_json_cid` | string | CID of VLM analysis JSON on Filecoin |
| `encryption_metadata` | string | JSON string of Haven-AOL encryption metadata |
| `cid_encryption_metadata` | string | JSON string of CID encryption metadata (for encrypted videos) |
| `segment_metadata` | object | Multi-segment recording information |
| `codec_variants` | array | Available codec variants (e.g., `["h264", "hevc"]`) |
| `duration` | number | Video duration in seconds |
| `file_size` | number | File size in bytes |

### Lit Encryption Metadata Structure

```json
{
  "version": "hybrid-v1",
  "encryptedKey": "base64...",
  "keyHash": "sha256...",
  "iv": "base64...",
  "algorithm": "AES-GCM",
  "keyLength": 256,
  "accessControlConditions": [...],
  "chain": "ethereum",
  "originalMimeType": "video/mp4",
  "originalSize": 10485760,
  "originalHash": "sha256..."
}
```

### Segment Metadata Structure

For multi-segment recordings (e.g., live streams):

```json
{
  "segment_index": 0,
  "start_timestamp": "2026-02-20T10:00:00Z",
  "end_timestamp": "2026-02-20T10:05:00Z",
  "mint_id": "nft-mint-id",
  "recording_session_id": "session-uuid"
}
```

## Attributes Schema

Attributes are public, indexed fields that enable searching and duplicate detection on-chain.

### JSON Structure

```json
{
  "title": "Video Title",
  "creator_handle": "@username",
  "source_uri": "https://youtube.com/watch?v=...",
  "mint_id": "nft-mint-identifier",
  "is_encrypted": 1,
  "encrypted_cid": "encrypted-cid-string",
  "phash": "perceptual-hash",
  "analysis_model": "zai-org/glm-4.6v-flash",
  "cid_hash": "sha256-hash-of-cid",
  "created_at": "2026-02-20T10:00:00Z",
  "updated_at": "2026-02-20T10:00:00Z"
}
```

### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `title` | string | Video title |
| `creator_handle` | string | Content creator handle (e.g., `@username`) |
| `source_uri` | string | Original source URL for provenance |
| `mint_id` | string | NFT mint identifier |
| `is_encrypted` | integer | `0` or `1` (use integer for gold standard compatibility) |
| `encrypted_cid` | string | Encrypted CID (only if `is_encrypted=1`) |
| `phash` | string | Perceptual hash for content matching |
| `analysis_model` | string | VLM model used for analysis |
| `cid_hash` | string | SHA256 hash of CID for duplicate detection |
| `created_at` | string | ISO8601 timestamp of creation |
| `updated_at` | string | ISO8601 timestamp of last update |

## Privacy Rules

### ⚠️ CRITICAL: Never Store in Attributes (Public)

- **Raw CID** - Always use `cid_hash` in attributes, never the actual CID
- **Encryption Ciphertext** - Ciphertext is stored on Filecoin, never in payload or attributes
- **Encryption Keys** - Store only encrypted key metadata

### ✅ Store in Payload (Private)

- Full `filecoin_root_cid`
- Encryption metadata (encrypted key, IV, etc.)
- VLM analysis CID
- Segment metadata

### ✅ Store in Attributes (Public)

- `cid_hash` for duplicate detection
- `title` for searching
- `is_encrypted` flag
- Timestamps
- `encrypted_cid` (already encrypted, safe for public)

## Field Name Standards

### Correct Field Names (MUST USE)

| Purpose | Correct Name | Incorrect Names |
|---------|--------------|-----------------|
| Filecoin CID (payload) | `filecoin_root_cid` | `root_cid` |
| Encryption status | `is_encrypted` | `encrypted` |
| CID hash | `cid_hash` | - |
| VLM CID | `vlm_json_cid` | `vlm_cid` |
| Encryption metadata | `encryption_metadata` | `encryption_metadata` |
| CID encryption | `cid_encryption_metadata` | `cid_encryption` |

## Cross-Application Compatibility

### Gold Standard (haven-player)

The haven-player backend is the gold standard. All field names and structures must match:

```python
# Reference: backend/app/services/arkiv_sync.py
# _build_payload() - lines 406-475
# _build_attributes() - lines 346-380
```

### Reader Application (haven-dapp)

The haven-dapp reads entities created by both haven-cli and haven-player:

```typescript
// Reference: src/services/videoService.ts lines 38-163
// Reference: src/types/video.ts
```

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02 | Initial standardized format |

## Related Documentation

- [API Reference](API_REFERENCE.md) - Complete API reference with schema definitions
- [Integration Guide](INTEGRATION_GUIDE.md) - Developer integration guide
- [Migration Notes](MIGRATION_NOTES.md) - Migrating from old format
- [Python API Reference](api.md) - Python SDK documentation
- [CLI Reference](cli-reference.md) - Command-line reference
