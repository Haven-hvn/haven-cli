# Migration Notes

This document describes the changes to the data format used by haven-cli when writing to the Arkiv blockchain.

## Changes in v1.0.0

### Overview

Version 1.0.0 introduces the Haven Cross-Application Data Format, ensuring full compatibility between:
- haven-player (Gold Standard)
- haven-cli
- haven-dapp

### Breaking Changes

#### 1. Payload Field Renamed: `root_cid` → `filecoin_root_cid`

**Old Format:**
```json
{
  "root_cid": "Qm..."
}
```

**New Format:**
```json
{
  "filecoin_root_cid": "Qm..."
}
```

**Impact:** All code referencing `root_cid` in payload must be updated to use `filecoin_root_cid`.

#### 2. Payload Field Renamed: `encrypted` → `is_encrypted`

**Old Format:**
```json
{
  "encrypted": true
}
```

**New Format:**
```json
{
  "is_encrypted": true
}
```

**Impact:** Both payload and attributes now use `is_encrypted` consistently.

#### 3. Attributes Field Removed: `root_cid` No Longer in Attributes

**Old Format:**
```json
{
  "root_cid": "Qm..."
}
```

**New Format:**
```json
{
  "cid_hash": "sha256..."
}
```

**Impact:** Raw CID is no longer stored in public attributes. Use `cid_hash` for duplicate detection.

#### 4. Payload Field Removed: `encryption_ciphertext`

**Old Format:**
```json
{
  "encryption_ciphertext": "base64..."
}
```

**New Format:**
```json
{
  "encryption_metadata": "{...}"
}
```

**Impact:** Ciphertext is no longer stored in the payload (it's already on Filecoin). Use `encryption_metadata` for decryption instructions.

### New Fields

#### 1. Payload: `encryption_metadata`

Unified encryption metadata for Haven-AOL decryption.

```json
{
  "encryption_metadata": {
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
}
```

#### 2. Payload: `cid_hash`

CID verification hash for both encrypted and non-encrypted videos.

```json
{
  "cid_hash": "sha256-of-filecoin-root-cid"
}
```

#### 3. Payload: `segment_metadata`

Multi-segment recording support for live streams.

```json
{
  "segment_metadata": {
    "segment_index": 0,
    "start_timestamp": "2026-02-20T10:00:00Z",
    "end_timestamp": "2026-02-20T10:05:00Z",
    "mint_id": "...",
    "recording_session_id": "..."
  }
}
```

#### 4. Attributes: `mint_id`

NFT mint identifier for tracking associated NFTs.

```json
{
  "mint_id": "nft-mint-identifier"
}
```

#### 5. Attributes: `analysis_model`

VLM model tracking for reproducibility.

```json
{
  "analysis_model": "zai-org/glm-4.6v-flash"
}
```

#### 6. Attributes: `updated_at`

Update timestamp for entity modification tracking.

```json
{
  "updated_at": "2026-02-20T10:00:00Z"
}
```

### Type Changes

#### `is_encrypted` in Attributes

- **Old:** Boolean (`true`/`false`)
- **New:** Integer (`0` or `1`)

**Reason:** Aligns with gold standard (haven-player) for cross-application compatibility.

### Backward Compatibility

#### New Uploads

All new uploads use the standardized format automatically.

#### Legacy Entities

Legacy entities created before v1.0.0 may need migration. The following fields may differ:

| Legacy Field | Standardized Field | Migration Action |
|--------------|-------------------|------------------|
| `root_cid` (payload) | `filecoin_root_cid` | Read legacy, write new |
| `encrypted` (payload) | `is_encrypted` | Read legacy, write new |
| `root_cid` (attributes) | `cid_hash` | Calculate hash from CID |
| `encryption_ciphertext` | Removed | Use Filecoin data |

### Migration Script

For applications reading Arkiv entities:

```python
def migrate_entity(entity):
    """Migrate legacy entity to standardized format."""
    payload = entity.get('payload', {})
    attributes = entity.get('attributes', {})
    
    # Handle payload field renames
    if 'root_cid' in payload and 'filecoin_root_cid' not in payload:
        payload['filecoin_root_cid'] = payload.pop('root_cid')
    
    if 'encrypted' in payload and 'is_encrypted' not in payload:
        payload['is_encrypted'] = payload.pop('encrypted')
    
    # Handle attributes field renames
    if 'root_cid' in attributes and 'cid_hash' not in attributes:
        # Calculate cid_hash from root_cid
        import hashlib
        cid = attributes.pop('root_cid')
        attributes['cid_hash'] = hashlib.sha256(cid.encode()).hexdigest()
    
    if 'encrypted' in attributes and 'is_encrypted' not in attributes:
        attributes['is_encrypted'] = 1 if attributes.pop('encrypted') else 0
    
    return entity
```

### Reading Entities

For backward compatibility when reading entities:

```python
def get_cid(entity):
    """Get CID from entity (handles both legacy and new format)."""
    payload = entity.get('payload', {})
    
    # Try new format first
    if 'filecoin_root_cid' in payload:
        return payload['filecoin_root_cid']
    
    # Fall back to legacy format
    if 'root_cid' in payload:
        return payload['root_cid']
    
    return None


def is_encrypted(entity):
    """Check if entity is encrypted (handles both formats)."""
    payload = entity.get('payload', {})
    attributes = entity.get('attributes', {})
    
    # Check payload
    if 'is_encrypted' in payload:
        return payload['is_encrypted']
    if 'encrypted' in payload:
        return payload['encrypted']
    
    # Check attributes (int or bool)
    if 'is_encrypted' in attributes:
        return bool(attributes['is_encrypted'])
    if 'encrypted' in attributes:
        return attributes['encrypted']
    
    return False
```

## Code Changes

### Updating haven-cli Code

If you have custom code using these fields:

1. **Update payload access:**
   ```python
   # Old
   cid = payload['root_cid']
   
   # New
   cid = payload['filecoin_root_cid']
   ```

2. **Update encryption checks:**
   ```python
   # Old
   if payload.get('encrypted'):
   
   # New
   if payload.get('is_encrypted'):
   ```

3. **Update attributes:**
   ```python
   # Old
   attributes['root_cid'] = cid
   
   # New
   import hashlib
   attributes['cid_hash'] = hashlib.sha256(cid.encode()).hexdigest()
   ```

## Timeline

| Date | Milestone |
|------|-----------|
| 2026-02 | v1.0.0 released with standardized format |
| 2026-02 | Legacy format deprecated |
| Future | Legacy format support may be removed |

## References

- [API Reference](API_REFERENCE.md) - Complete API reference with schema definitions
- [Integration Guide](INTEGRATION_GUIDE.md) - Developer integration guide
- [Arkiv Data Format](ARKIV_FORMAT.md) - Complete format specification
- [HAVEN_CROSS_APPLICATION_DATA_FORMAT_SPECIFICATION.md](../HAVEN_CROSS_APPLICATION_DATA_FORMAT_SPECIFICATION.md) - Project-wide specification
- [Gold Standard Reference](../haven-player/backend/app/services/arkiv_sync.py) - haven-player implementation
