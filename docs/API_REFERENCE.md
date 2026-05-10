# Haven API Reference

Complete API reference for developers integrating with the Haven ecosystem.

## Table of Contents

1. [Data Format Specification](#data-format-specification)
2. [Arkiv Entity Structure](#arkiv-entity-structure)
3. [SDK Usage](#sdk-usage)
4. [Validation](#validation)
5. [Error Handling](#error-handling)
6. [Version History](#version-history)

---

## Data Format Specification

Haven uses a standardized data format across all applications (haven-player, haven-cli, haven-dapp) to ensure complete cross-application compatibility when storing and retrieving video entities from the Arkiv blockchain.

### Arkiv Entity Structure

Each video upload creates an Arkiv entity with two main components:

- **Attributes**: Public, searchable metadata indexed on-chain
- **Payload**: Private, encrypted metadata stored on-chain (base64-encoded JSON)

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
│ • encrypted_cid          │ • cid_encryption_metadata        │
│ • phash                  │ • segment_metadata               │
│ • analysis_model         │ • codec_variants                 │
│ • mint_id                │ • duration                       │
│ • created_at             │ • file_size                      │
│ • updated_at             │                                  │
└──────────────────────────┴──────────────────────────────────┘
```

#### Entity Fields

| Field | Type | Description |
|-------|------|-------------|
| key | string | Unique entity identifier (hex) |
| owner | string | Wallet address |
| attributes | object | Public searchable metadata |
| payload | string | Base64-encoded JSON (private) |
| content_type | string | MIME type |
| created_at | string | ISO8601 timestamp |

### Payload Structure (Decoded)

The payload contains sensitive data that should remain private. It is stored as a base64-encoded JSON string on the Arkiv blockchain.

```typescript
interface HavenPayload {
  // Required Fields
  is_encrypted: boolean;               // Encryption flag

  // Conditional Fields (depending on encryption status)
  filecoin_root_cid?: string;          // Filecoin CID (non-encrypted videos)
  encrypted_cid?: string;              // Encrypted CID (encrypted videos)
  
  // Optional Fields
  cid_hash?: string;                   // SHA256 of CID
  vlm_json_cid?: string;               // VLM analysis CID
  encryption_metadata?: string;        // JSON string of encryption metadata
  cid_encryption_metadata?: string;    // JSON string of CID encryption
  segment_metadata?: SegmentMetadata;  // Multi-segment recording info
  codec_variants?: CodecVariant[];     // Available codec variants
  duration?: number;                   // Duration in seconds
  file_size?: number;                  // File size in bytes
  codec?: string;                      // Video codec
}

interface SegmentMetadata {
  segment_index: number;
  start_timestamp: string;             // ISO8601 timestamp
  end_timestamp?: string;              // ISO8601 timestamp
  mint_id?: string;
  recording_session_id?: string;
}

interface CodecVariant {
  codec: 'av1' | 'h264' | 'vp9' | 'hevc';
  cid: string;
  quality_score: number;
  bitrate?: number;                    // kbps
  resolution?: { 
    width: number; 
    height: number; 
  };
  file_size?: number;
}
```

#### Payload JSON Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "HavenArkivPayload",
  "type": "object",
  "required": ["is_encrypted"],
  "properties": {
    "filecoin_root_cid": {
      "type": "string",
      "description": "Root CID for non-encrypted videos (REQUIRED if not encrypted)"
    },
    "encrypted_cid": {
      "type": "string",
      "description": "Encrypted CID for encrypted videos (stored in attributes)"
    },
    "cid_hash": {
      "type": "string",
      "pattern": "^[a-f0-9]{64}$",
      "description": "SHA256 hash of the CID"
    },
    "vlm_json_cid": {
      "type": "string",
      "description": "CID of VLM analysis JSON on Filecoin"
    },
    "is_encrypted": {
      "type": "boolean",
      "description": "Whether the content is encrypted"
    },
    "encryption_metadata": {
      "type": "string",
      "description": "JSON string of EncryptionMetadata"
    },
    "cid_encryption_metadata": {
      "type": "string",
      "description": "JSON string of CidEncryptionMetadata"
    },
    "segment_metadata": {
      "type": "object",
      "properties": {
        "segment_index": { "type": "integer" },
        "start_timestamp": { "type": "string", "format": "date-time" },
        "end_timestamp": { "type": "string", "format": "date-time" },
        "mint_id": { "type": "string" },
        "recording_session_id": { "type": "string" }
      },
      "required": ["segment_index", "start_timestamp", "mint_id"]
    },
    "codec_variants": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "codec": { 
            "type": "string", 
            "enum": ["av1", "h264", "vp9", "hevc"] 
          },
          "cid": { "type": "string" },
          "quality_score": { "type": "number" },
          "bitrate": { "type": "number" },
          "resolution": {
            "type": "object",
            "properties": {
              "width": { "type": "integer" },
              "height": { "type": "integer" }
            }
          },
          "file_size": { "type": "integer" }
        },
        "required": ["codec", "cid", "quality_score"]
      }
    },
    "duration": { "type": "number" },
    "file_size": { "type": "integer" }
  }
}
```

### Attributes Structure

Attributes are public, indexed fields that enable searching and duplicate detection on-chain.

```typescript
interface HavenAttributes {
  // Required Fields
  title: string;                       // Video title (max 256 chars)
  is_encrypted: 0 | 1;                 // Integer flag (0 = not encrypted, 1 = encrypted)
  cid_hash: string;                    // SHA256 of CID (64 hex chars)
  created_at: string;                  // ISO8601 timestamp

  // Optional Fields
  updated_at?: string;                 // ISO8601 timestamp
  creator_handle?: string;             // Content creator (max 128 chars)
  source_uri?: string;                 // Original source URL (max 2048 chars)
  mint_id?: string;                    // NFT mint identifier (max 128 chars)
  phash?: string;                      // Perceptual hash (max 64 chars)
  analysis_model?: string;             // VLM model used (max 64 chars)
  encrypted_cid?: string;              // Encrypted CID (only if is_encrypted=1)
}
```

#### Attributes JSON Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "HavenArkivAttributes",
  "type": "object",
  "required": ["title", "is_encrypted", "cid_hash", "created_at"],
  "properties": {
    "title": {
      "type": "string",
      "maxLength": 256,
      "description": "Video title"
    },
    "creator_handle": {
      "type": "string",
      "maxLength": 128,
      "description": "Content creator identifier"
    },
    "source_uri": {
      "type": "string",
      "format": "uri",
      "maxLength": 2048,
      "description": "Original source URL"
    },
    "mint_id": {
      "type": "string",
      "maxLength": 128,
      "description": "NFT mint identifier"
    },
    "is_encrypted": {
      "type": "integer",
      "enum": [0, 1],
      "description": "Encryption status: 0=not encrypted, 1=encrypted"
    },
    "encrypted_cid": {
      "type": "string",
      "maxLength": 256,
      "description": "Lit-encrypted CID (only if is_encrypted=1)"
    },
    "phash": {
      "type": "string",
      "maxLength": 64,
      "description": "Perceptual hash for deduplication"
    },
    "analysis_model": {
      "type": "string",
      "maxLength": 64,
      "description": "VLM model identifier (e.g., 'zai-org/glm-4.6v-flash')"
    },
    "cid_hash": {
      "type": "string",
      "pattern": "^[a-f0-9]{64}$",
      "description": "SHA256 hash of filecoin_root_cid"
    },
    "created_at": {
      "type": "string",
      "format": "date-time",
      "description": "ISO 8601 creation timestamp"
    },
    "updated_at": {
      "type": "string",
      "format": "date-time",
      "description": "ISO 8601 update timestamp"
    }
  },
  "additionalProperties": false
}
```

### Lit Encryption Metadata Structure

When a video is encrypted using Haven-AOL, the following metadata structure is stored in the payload as a JSON string:

```typescript
interface EncryptionMetadata {
  version: 'hybrid-v1';               // Metadata format version
  encryptedKey: string;               // Base64 BLS-encrypted AES key
  keyHash: string;                    // SHA256 of original AES key
  iv: string;                         // Base64-encoded 12-byte IV
  algorithm: 'AES-GCM';               // Encryption algorithm
  keyLength: 256;                     // AES key length in bits
  accessControlConditions: AccessControlCondition[];
  chain: string;                      // Blockchain chain (e.g., 'ethereum')
  originalMimeType?: string;          // Original file MIME type
  originalSize?: number;              // Original file size in bytes
  originalHash?: string;              // SHA256 hash of original file
}

interface AccessControlCondition {
  contractAddress: string;
  standardContractType: '' | 'ERC20' | 'ERC721' | 'ERC1155';
  chain: string;
  method: string;
  parameters: string[];
  returnValueTest: {
    comparator: '=' | '>' | '>=' | '<' | '<=' | 'contains';
    value: string;
  };
}
```

#### Example Lit Encryption Metadata

```json
{
  "version": "hybrid-v1",
  "encryptedKey": "base64encodedencryptedkey...",
  "keyHash": "sha256hashofkey...",
  "iv": "base64encodediv...",
  "algorithm": "AES-GCM",
  "keyLength": 256,
  "accessControlConditions": [
    {
      "contractAddress": "",
      "standardContractType": "",
      "chain": "ethereum",
      "method": "",
      "parameters": [":userAddress"],
      "returnValueTest": {
        "comparator": "=",
        "value": "0x1234567890abcdef..."
      }
    }
  ],
  "chain": "ethereum",
  "originalMimeType": "video/mp4",
  "originalSize": 10485760,
  "originalHash": "sha256hashoforiginalfile..."
}
```

### CID Encryption Metadata Structure

When the CID itself is encrypted (separate from video content encryption):

```typescript
interface CidEncryptionMetadata {
  version: 'hybrid-v1';
  encryptedCid: string;               // Base64 encrypted CID
  encryptedKey: string;               // BLS-encrypted key
  iv: string;                         // Base64 IV
  algorithm: 'AES-GCM';
  accessControlConditions: AccessControlCondition[];
  chain: string;
}
```

---

## SDK Usage

### Python SDK

#### Reading Entities

```python
from arkiv import Arkiv
from arkiv.provider import ProviderBuilder
from arkiv.account import NamedAccount
import json
import base64

# Create client
provider = ProviderBuilder().custom("https://kaolin.hoodi.arkiv.network/rpc").build()
account = NamedAccount.from_private_key("my-account", "0x...")
client = Arkiv(provider=provider, account=account)

# Get entity by key
entity = client.arkiv.get_entity("0x...")

# Decode payload
payload = json.loads(base64.b64decode(entity.payload))
attributes = dict(entity.attributes)

# Access fields
filecoin_cid = payload.get("filecoin_root_cid")
is_encrypted = payload.get("is_encrypted", False)
title = attributes.get("title")
cid_hash = attributes.get("cid_hash")

# Parse Lit encryption metadata (if encrypted)
if is_encrypted and payload.get("encryption_metadata"):
    encryption_meta = json.loads(payload["encryption_metadata"])
    encrypted_key = encryption_meta["encryptedKey"]
    access_conditions = encryption_meta["accessControlConditions"]
```

#### Querying Entities

```python
from arkiv.types import QueryOptions, KEY, ATTRIBUTES, PAYLOAD, CONTENT_TYPE, OWNER, CREATED_AT

# Build query options with specific fields
required_fields = KEY | ATTRIBUTES | PAYLOAD | CONTENT_TYPE | OWNER | CREATED_AT
query_options = QueryOptions(
    attributes=required_fields,
    max_results_per_page=50,
)

# Query by owner
wallet_address = "0x..."
query = f'$owner = "{wallet_address}"'
entities = list(client.arkiv.query_entities(query=query, options=query_options))

for entity in entities:
    payload = json.loads(base64.b64decode(entity.payload))
    attributes = dict(entity.attributes)
    print(f"Title: {attributes.get('title')}")
    print(f"CID: {payload.get('filecoin_root_cid')}")
```

### TypeScript/JavaScript SDK

#### Reading Entities

```typescript
import { createArkivClient, getAllEntitiesByOwner, parseEntityPayload } from './lib/arkiv';

// Create client
const client = createArkivClient();

// Fetch all entities for an owner
const entities = await getAllEntitiesByOwner(client, '0x...');

// Parse each entity
for (const entity of entities) {
  // Decode payload
  const payload = parseEntityPayload<Record<string, unknown>>(entity.payload);
  const attributes = entity.attributes;
  
  // Access fields
  const filecoinCid = payload?.filecoin_root_cid as string;
  const isEncrypted = Boolean(payload?.is_encrypted);
  const title = attributes.title as string;
  const cidHash = attributes.cid_hash as string;
  
  // Parse Lit encryption metadata
  let encryptionMeta: Record<string, unknown> | undefined;
  const rawEncryptionMeta = payload?.encryption_metadata;
  if (rawEncryptionMeta && typeof rawEncryptionMeta === 'string') {
    try {
      encryptionMeta = JSON.parse(rawEncryptionMeta);
    } catch {
      // ignore parse errors
    }
  }
}
```

#### Parsing Helper Function

```typescript
function parseHavenEntity(entity: ArkivEntity): Video {
  const payloadData = parseEntityPayload<Record<string, unknown>>(entity.payload) || {};
  
  // Merge attributes and payload data (payload takes precedence)
  const data: Record<string, unknown> = {
    ...entity.attributes,
    ...payloadData,
  };
  
  // Helper: look up a value by snake_case key first, then camelCase fallback
  const get = (snakeKey: string, camelKey: string): unknown =>
    data[snakeKey] ?? data[camelKey];
  
  // Parse Lit encryption metadata
  let encryptionMeta: Record<string, unknown> | undefined;
  const rawEncryptionMeta = get('encryption_metadata', 'encryptionMetadata');
  if (rawEncryptionMeta) {
    if (typeof rawEncryptionMeta === 'string') {
      try { encryptionMeta = JSON.parse(rawEncryptionMeta); } catch { /* ignore */ }
    } else {
      encryptionMeta = rawEncryptionMeta as Record<string, unknown>;
    }
  }
  
  return {
    id: entity.key,
    owner: (entity.owner || '').toLowerCase(),
    title: (data.title as string) || 'Untitled',
    filecoinCid: (get('filecoin_root_cid', 'filecoinCid') as string) || '',
    encryptedCid: (get('encrypted_cid', 'encryptedCid') as string) || undefined,
    isEncrypted: Boolean(get('is_encrypted', 'isEncrypted')),
    encryptionMetadata: encryptionMeta,
    cidHash: (get('cid_hash', 'cidHash') as string) || undefined,
    vlmJsonCid: (get('vlm_json_cid', 'vlmJsonCid') as string) || undefined,
    createdAt: entity.created_at ? new Date(entity.created_at) : new Date(),
  };
}
```

---

## Validation

### Field Name Validation

Required fields by location:

#### Payload (Private)
| Field | Required | Notes |
|-------|----------|-------|
| `is_encrypted` | ✅ Yes | Must be boolean |
| `filecoin_root_cid` | Conditional | Required if not encrypted |
| `cid_hash` | ⚠️ Recommended | SHA256 of CID for deduplication |

#### Attributes (Public)
| Field | Required | Notes |
|-------|----------|-------|
| `title` | ✅ Yes | Max 256 characters |
| `is_encrypted` | ✅ Yes | Must be integer 0 or 1 |
| `cid_hash` | ✅ Yes | 64-character hex string |
| `created_at` | ✅ Yes | ISO8601 timestamp |
| `encrypted_cid` | Conditional | Required if is_encrypted=1 |

### Forbidden Fields

**Never use these field names:**

#### Payload
- `root_cid` → Use `filecoin_root_cid` instead
- `encrypted` → Use `is_encrypted` instead
- `encryption_ciphertext` → Never store in payload (already on Filecoin)

#### Attributes
- `root_cid` → Privacy leak, never store raw CID in public attributes
- `filecoin_root_cid` → Privacy leak, CID should only be in payload

### Privacy Rules

1. **NEVER put raw CID in attributes (public)** - only `cid_hash`
2. **CID goes in payload (private)** as `filecoin_root_cid`
3. **NEVER put ciphertext in payload** - it's already on Filecoin

### Validation Example (Python)

```python
def validate_entity(payload: dict, attributes: dict) -> list[str]:
    """Validate entity against Haven data format."""
    errors = []
    
    # Check required payload fields
    if "is_encrypted" not in payload:
        errors.append("Payload missing required field: is_encrypted")
    elif not isinstance(payload["is_encrypted"], bool):
        errors.append("is_encrypted must be boolean in payload")
    
    # Check for forbidden fields
    if "root_cid" in payload:
        errors.append("Forbidden field in payload: root_cid (use filecoin_root_cid)")
    if "encrypted" in payload:
        errors.append("Forbidden field in payload: encrypted (use is_encrypted)")
    if "encryption_ciphertext" in payload:
        errors.append("Forbidden field in payload: encryption_ciphertext")
    
    # Check attributes
    if "root_cid" in attributes:
        errors.append("Privacy violation: root_cid found in public attributes")
    if "filecoin_root_cid" in attributes:
        errors.append("Privacy violation: filecoin_root_cid found in public attributes")
    
    # Check required attributes
    required_attrs = ["title", "is_encrypted", "cid_hash", "created_at"]
    for field in required_attrs:
        if field not in attributes:
            errors.append(f"Attributes missing required field: {field}")
    
    # Validate is_encrypted type in attributes (must be int 0 or 1)
    if "is_encrypted" in attributes:
        if attributes["is_encrypted"] not in (0, 1):
            errors.append("is_encrypted in attributes must be 0 or 1")
    
    return errors
```

---

## Error Handling

### Common Errors

#### Missing filecoin_root_cid
```json
{
  "error": "InvalidEntity",
  "message": "Payload missing required field: filecoin_root_cid"
}
```

#### Invalid is_encrypted value
```json
{
  "error": "InvalidEntity",
  "message": "is_encrypted must be boolean in payload, 0/1 in attributes"
}
```

#### Privacy violation
```json
{
  "error": "PrivacyViolation",
  "message": "Raw CID found in attributes"
}
```

#### Invalid CID hash format
```json
{
  "error": "InvalidEntity",
  "message": "cid_hash must be 64-character hex string"
}
```

### Error Handling Example (TypeScript)

```typescript
function parseEntityWithValidation(entity: ArkivEntity): { video?: Video; errors: string[] } {
  const errors: string[] = [];
  
  try {
    const payload = parseEntityPayload<Record<string, unknown>>(entity.payload);
    const attributes = entity.attributes;
    
    // Validate required fields
    if (payload?.is_encrypted === undefined) {
      errors.push("Missing is_encrypted in payload");
    }
    
    // Validate forbidden fields
    if (payload && 'root_cid' in payload) {
      errors.push("Forbidden field: root_cid (use filecoin_root_cid)");
    }
    if (attributes && 'root_cid' in attributes) {
      errors.push("Privacy violation: root_cid in attributes");
    }
    
    // Validate required attributes
    const requiredAttrs = ['title', 'is_encrypted', 'cid_hash', 'created_at'];
    for (const field of requiredAttrs) {
      if (!(field in attributes)) {
        errors.push(`Missing required attribute: ${field}`);
      }
    }
    
    if (errors.length > 0) {
      return { errors };
    }
    
    return { video: parseHavenEntity(entity), errors: [] };
  } catch (e) {
    errors.push(`Parse error: ${e}`);
    return { errors };
  }
}
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-20 | Initial standardized format |

---

## Related Documentation

- [Arkiv Data Format](ARKIV_FORMAT.md) - Detailed format specification
- [Integration Guide](INTEGRATION_GUIDE.md) - Developer integration guide
- [Migration Notes](MIGRATION_NOTES.md) - Migrating from old format
- [Python API Reference](api.md) - Python SDK documentation
