# Haven Integration Guide

A comprehensive guide for developers integrating with the Haven ecosystem.

## Table of Contents

1. [Overview](#overview)
2. [For DApp Developers](#for-dapp-developers)
3. [For CLI Developers](#for-cli-developers)
4. [For Backend Developers](#for-backend-developers)
5. [Common Patterns](#common-patterns)
6. [Troubleshooting](#troubleshooting)

---

## Overview

### Architecture

The Haven ecosystem consists of three main applications:

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   haven-cli     │     │  haven-player   │     │   haven-dapp    │
│   (Upload/Write)│     │ (Gold Standard) │     │  (Read/Playback)│
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                      ARKIV BLOCKCHAIN                           │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Entity Structure:                                        │  │
│  │  - attributes: public searchable metadata                 │  │
│  │  - payload: private base64-encoded JSON                   │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    IPFS/FILECOIN STORAGE                        │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │  Video Content  │  │  VLM Analysis   │  │  Thumbnails     │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow

**Upload Flow:**
1. Video file → Local processing (VLM analysis, encryption optional)
2. Upload to Filecoin → Receive CID(s)
3. Build Arkiv Entity (attributes + payload)
4. Write to Arkiv blockchain

**Read Flow:**
1. Query Arkiv blockchain for owner's entities
2. Parse entity (decode payload, extract attributes)
3. Retrieve video content from Filecoin
4. Decrypt if necessary (Haven-AOL)

---

## For DApp Developers

### Parsing Haven Entities

The primary task for DApp developers is parsing Arkiv entities into Video objects.

#### TypeScript Example

```typescript
import type { Video } from '../types/video';
import { parseEntityPayload, type ArkivEntity } from '../lib/arkiv';

function parseHavenEntity(entity: ArkivEntity): Video {
  // Parse payload (base64 encoded JSON)
  const payloadData = parseEntityPayload<Record<string, unknown>>(entity.payload) || {};
  
  // Merge attributes and payload (payload takes precedence)
  const data: Record<string, unknown> = {
    ...entity.attributes,
    ...payloadData,
  };
  
  // Helper to check both snake_case and camelCase keys
  const get = (snakeKey: string, camelKey: string): unknown =>
    data[snakeKey] ?? data[camelKey];
  
  // Parse encryption metadata
  let encryptionMeta: Record<string, unknown> | undefined;
  const rawEncryptionMeta = get('encryption_metadata', 'encryptionMetadata');
  if (rawEncryptionMeta && typeof rawEncryptionMeta === 'string') {
    try {
      encryptionMeta = JSON.parse(rawEncryptionMeta);
    } catch { /* ignore parse errors */ }
  }
  
  // Parse segment metadata
  const rawSegment = get('segment_metadata', 'segmentMetadata') as Record<string, unknown> | null;
  const segmentMetadata = rawSegment ? {
    startTimestamp: new Date(
      (rawSegment.start_timestamp as string) ||
      (rawSegment.startTimestamp as string) ||
      ''
    ),
    endTimestamp: rawSegment.end_timestamp || rawSegment.endTimestamp
      ? new Date(
          (rawSegment.end_timestamp as string) ||
          (rawSegment.endTimestamp as string)
        )
      : undefined,
    segmentIndex: (rawSegment.segment_index as number) ??
                  (rawSegment.segmentIndex as number) ?? 0,
    totalSegments: (rawSegment.total_segments as number) ??
                   (rawSegment.totalSegments as number) ?? 0,
    mintId: (rawSegment.mint_id as string) ??
            (rawSegment.mintId as string) ?? '',
    recordingSessionId: (rawSegment.recording_session_id as string) ??
                        (rawSegment.recordingSessionId as string),
  } : undefined;
  
  const vlmJsonCid = (get('vlm_json_cid', 'vlmJsonCid') as string) || undefined;
  
  return {
    id: entity.key,
    owner: (entity.owner || '').toLowerCase(),
    title: (data.title as string) || 'Untitled',
    description: (data.description as string) || '',
    duration: (data.duration as number) || 0,
    filecoinCid: (get('filecoin_root_cid', 'filecoinCid') as string) || '',
    encryptedCid: (get('encrypted_cid', 'encryptedCid') as string) || undefined,
    isEncrypted: Boolean(get('is_encrypted', 'isEncrypted')),
    encryptionMetadata: encryptionMeta,
    cidEncryptionMetadata: (get('cid_encryption_metadata', 'cidEncryptionMetadata') as Video['cidEncryptionMetadata']) || undefined,
    hasAiData: Boolean(get('has_ai_data', 'hasAiData') || vlmJsonCid),
    vlmJsonCid,
    mintId: (get('mint_id', 'mintId') as string) || undefined,
    sourceUri: (get('source_uri', 'sourceUri') as string) || undefined,
    creatorHandle: (get('creator_handle', 'creatorHandle') as string) || undefined,
    createdAt: entity.created_at ? new Date(entity.created_at) : new Date(),
    updatedAt: (get('updated_at', 'updatedAt') as string)
      ? new Date(get('updated_at', 'updatedAt') as string)
      : undefined,
    codecVariants: (get('codec_variants', 'codecVariants') as Video['codecVariants']) || undefined,
    segmentMetadata,
    phash: (get('phash', 'phash') as string) || undefined,
    analysisModel: (get('analysis_model', 'analysisModel') as string) || undefined,
    cidHash: (get('cid_hash', 'cidHash') as string) || undefined,
    arkivStatus: 'active',
  };
}
```

### Handling Encrypted Videos

#### Decrypting with Haven-AOL

```typescript
interface DecryptVideoParams {
  encryptedCid: string;
  encryptionMetadata: Record<string, unknown>;
}

async function decryptVideo(params: DecryptVideoParams): Promise<string> {
  const { encryptedCid, encryptionMetadata } = params;
  // Call your Haven-AOL decryption adapter here.
  // The adapter should use encryptedCid + encryptionMetadata and user wallet context.
  return await myHavenAolDecrypt(encryptedCid, encryptionMetadata);
}

// Usage example
async function getPlayableCid(video: Video, walletAddress: string, authSig: string): Promise<string> {
  if (!video.isEncrypted) {
    return video.filecoinCid || '';
  }
  
  if (!video.encryptedCid || !video.encryptionMetadata) {
    throw new Error('Encrypted video missing required metadata');
  }
  
  return await decryptVideo({
    encryptedCid: video.encryptedCid,
    encryptionMetadata: video.encryptionMetadata,
  });
}
```

### Fetching Video Content

```typescript
async function fetchVideoContent(cid: string): Promise<Blob> {
  // Use IPFS gateway to fetch content
  const gatewayUrl = 'https://ipfs.io/ipfs/';
  const response = await fetch(`${gatewayUrl}${cid}`);
  
  if (!response.ok) {
    throw new Error(`Failed to fetch video: ${response.statusText}`);
  }
  
  return await response.blob();
}

// With fallback gateways
async function fetchVideoContentWithFallback(cid: string): Promise<Blob> {
  const gateways = [
    'https://ipfs.io/ipfs/',
    'https://gateway.pinata.cloud/ipfs/',
    'https://cloudflare-ipfs.com/ipfs/',
  ];
  
  for (const gateway of gateways) {
    try {
      const response = await fetch(`${gateway}${cid}`, { 
        signal: AbortSignal.timeout(30000) 
      });
      if (response.ok) {
        return await response.blob();
      }
    } catch (e) {
      console.warn(`Gateway ${gateway} failed:`, e);
    }
  }
  
  throw new Error('All gateways failed');
}
```

### Fetching VLM Analysis

```typescript
interface VlmAnalysis {
  version: string;
  model: string;
  analyzedAt: string;
  segments: VlmSegment[];
  summary?: string;
  topics?: string[];
  phash?: string;
}

async function fetchVlmAnalysis(vlmJsonCid: string): Promise<VlmAnalysis | null> {
  if (!vlmJsonCid) return null;
  
  try {
    const gatewayUrl = 'https://ipfs.io/ipfs/';
    const response = await fetch(`${gatewayUrl}${vlmJsonCid}`);
    
    if (!response.ok) {
      throw new Error(`Failed to fetch VLM analysis: ${response.statusText}`);
    }
    
    const data = await response.json();
    
    return {
      version: data.version,
      model: data.model,
      analyzedAt: data.analyzed_at,
      segments: data.segments || [],
      summary: data.summary,
      topics: data.topics,
      phash: data.phash,
    };
  } catch (e) {
    console.error('Failed to fetch VLM analysis:', e);
    return null;
  }
}
```

---

## For CLI Developers

### Building Compliant Entities

When building entities for Arkiv, ensure you follow the gold standard format.

#### Python Example

```python
import json
import hashlib
import base64
from datetime import datetime
from typing import Optional

def build_payload(
    filecoin_root_cid: Optional[str],
    is_encrypted: bool,
    vlm_json_cid: Optional[str] = None,
    encryption_metadata: Optional[dict] = None,
    cid_encryption_metadata: Optional[dict] = None,
    segment_metadata: Optional[dict] = None,
    duration: Optional[float] = None,
    file_size: Optional[int] = None,
) -> dict:
    """
    Build compliant Haven payload.
    
    Args:
        filecoin_root_cid: CID for non-encrypted videos
        is_encrypted: Whether the video is encrypted
        vlm_json_cid: CID of VLM analysis JSON
        encryption_metadata: Haven-AOL encryption metadata
        cid_encryption_metadata: CID encryption metadata
        segment_metadata: Multi-segment recording info
        duration: Video duration in seconds
        file_size: File size in bytes
    
    Returns:
        dict: Payload ready for Arkiv storage
    """
    payload = {
        "is_encrypted": is_encrypted,
    }
    
    # For non-encrypted videos, store CID in payload
    if not is_encrypted and filecoin_root_cid:
        payload["filecoin_root_cid"] = filecoin_root_cid
        # Add CID hash for deduplication
        cid_hash = hashlib.sha256(filecoin_root_cid.encode()).hexdigest()
        payload["cid_hash"] = cid_hash
    
    # Add optional fields if present
    if vlm_json_cid:
        payload["vlm_json_cid"] = vlm_json_cid
    
    if encryption_metadata:
        # Ensure no ciphertext in metadata
        metadata = {k: v for k, v in encryption_metadata.items() 
                   if k != "ciphertext"}
        payload["encryption_metadata"] = json.dumps(metadata)
    
    if cid_encryption_metadata:
        payload["cid_encryption_metadata"] = json.dumps(cid_encryption_metadata)
    
    if segment_metadata:
        payload["segment_metadata"] = segment_metadata
    
    if duration is not None:
        payload["duration"] = duration
    
    if file_size is not None:
        payload["file_size"] = file_size
    
    return payload


def build_attributes(
    title: str,
    is_encrypted: bool,
    cid_hash: str,
    created_at: Optional[datetime] = None,
    updated_at: Optional[datetime] = None,
    creator_handle: Optional[str] = None,
    source_uri: Optional[str] = None,
    mint_id: Optional[str] = None,
    phash: Optional[str] = None,
    analysis_model: Optional[str] = None,
    encrypted_cid: Optional[str] = None,
) -> dict:
    """
    Build compliant Haven attributes.
    
    Args:
        title: Video title (required)
        is_encrypted: Encryption flag (required)
        cid_hash: SHA256 hash of CID (required)
        created_at: Creation timestamp
        updated_at: Update timestamp
        creator_handle: Content creator handle
        source_uri: Original source URL
        mint_id: NFT mint identifier
        phash: Perceptual hash
        analysis_model: VLM model used
        encrypted_cid: Encrypted CID (if is_encrypted=True)
    
    Returns:
        dict: Attributes ready for Arkiv storage
    """
    attributes = {
        "title": title,
        "is_encrypted": 1 if is_encrypted else 0,
        "cid_hash": cid_hash,
        "created_at": (created_at or datetime.utcnow()).isoformat(),
    }
    
    if updated_at:
        attributes["updated_at"] = updated_at.isoformat()
    
    if creator_handle:
        attributes["creator_handle"] = creator_handle
    
    if source_uri:
        attributes["source_uri"] = source_uri
    
    if mint_id:
        attributes["mint_id"] = mint_id
    
    if phash:
        attributes["phash"] = phash
    
    if analysis_model:
        attributes["analysis_model"] = analysis_model
    
    # For encrypted videos, store encrypted CID in attributes
    if is_encrypted and encrypted_cid:
        attributes["encrypted_cid"] = encrypted_cid
    
    return attributes


def create_arkiv_entity(
    payload: dict,
    attributes: dict,
    content_type: str = "application/json",
) -> bytes:
    """
    Create Arkiv entity payload (base64 encoded).
    
    Args:
        payload: Private payload data
        attributes: Public attributes
        content_type: MIME type
    
    Returns:
        bytes: Base64-encoded payload
    """
    payload_json = json.dumps(payload, separators=(',', ':'))
    payload_bytes = payload_json.encode('utf-8')
    
    # Arkiv expects base64-encoded payload
    return base64.b64encode(payload_bytes)
```

### Complete Upload Example

```python
import os
from arkiv import Arkiv
from arkiv.provider import ProviderBuilder
from arkiv.account import NamedAccount

async def upload_video_to_haven(
    video_path: str,
    title: str,
    filecoin_cid: str,
    private_key: str,
    is_encrypted: bool = False,
    vlm_json_cid: Optional[str] = None,
    lit_metadata: Optional[dict] = None,
) -> str:
    """
    Upload a video to Haven via Arkiv.
    
    Returns:
        str: Entity key
    """
    # Build payload and attributes
    cid_hash = hashlib.sha256(filecoin_cid.encode()).hexdigest()
    
    payload = build_payload(
        filecoin_root_cid=filecoin_cid if not is_encrypted else None,
        is_encrypted=is_encrypted,
        vlm_json_cid=vlm_json_cid,
        encryption_metadata=encryption_metadata,
    )
    
    attributes = build_attributes(
        title=title,
        is_encrypted=is_encrypted,
        cid_hash=cid_hash,
    )
    
    # Create Arkiv client
    rpc_url = os.getenv("ARKIV_RPC_URL", "https://mendoza.hoodi.arkiv.network/rpc")
    provider = ProviderBuilder().custom(rpc_url).build()
    account = NamedAccount.from_private_key("haven-upload", private_key)
    client = Arkiv(provider=provider, account=account)
    
    # Encode payload
    payload_bytes = json.dumps(payload).encode('utf-8')
    
    # Create entity
    entity_key, receipt = client.arkiv.create_entity(
        payload=payload_bytes,
        content_type="application/json",
        attributes=attributes,
        expires_in=4 * 7 * 24 * 60 * 60,  # 4 weeks
    )
    
    return entity_key
```

---

## For Backend Developers

### Implementing Arkiv Sync

Reference implementation from haven-player (gold standard):

```python
# backend/app/services/arkiv_sync.py

def _build_attributes(video: Video) -> dict[str, str | int]:
    """
    Public attributes sent to Arkiv.
    For encrypted videos, encrypted_cid is stored in attributes (public).
    The actual filecoin_root_cid is stored in the encrypted payload.
    """
    attributes: dict[str, str | int] = {}

    if video.title:
        attributes["title"] = video.title
    if video.creator_handle:
        attributes["creator_handle"] = video.creator_handle
    if video.mint_id:
        attributes["mint_id"] = video.mint_id
    if video.is_encrypted:
        attributes["is_encrypted"] = 1
        # Store encrypted CID in public attributes (privacy - actual CID is hidden)
        if video.encrypted_filecoin_cid:
            attributes["encrypted_cid"] = video.encrypted_filecoin_cid
    if video.phash:
        attributes["phash"] = video.phash
    if video.analysis_model:
        attributes["analysis_model"] = video.analysis_model
    if video.source_uri:
        attributes["source_uri"] = video.source_uri
    if video.created_at:
        attributes["created_at"] = video.created_at.isoformat()
    if video.updated_at:
        attributes["updated_at"] = video.updated_at.isoformat()

    return attributes


def _build_payload(video: Video, segment_payload: dict | None) -> dict:
    """
    Build optimized payload for Arkiv entity.
    
    The payload should only contain:
    1. Encrypted/sensitive data (CIDs, encryption metadata)
    2. Data not available in attributes (VLM JSON CID for archival)
    3. Essential fields needed for restore
    """
    payload: dict[str, object] = {}
    
    # For encrypted videos: encrypted_cid is in attributes (public), 
    # actual CID is decrypted during restore
    if video.is_encrypted:
        if video.cid_encryption_metadata:
            payload["cid_encryption_metadata"] = video.cid_encryption_metadata
        if video.encryption_metadata:
            # Parse metadata and remove ciphertext if present
            metadata_dict = json.loads(video.encryption_metadata)
            if "ciphertext" in metadata_dict:
                metadata_dict.pop("ciphertext")
            payload["encryption_metadata"] = json.dumps(metadata_dict)
    else:
        # For non-encrypted videos, store filecoin_root_cid in payload
        if video.filecoin_root_cid:
            payload["filecoin_root_cid"] = video.filecoin_root_cid
    
    # cid_hash is needed for deduplication during restore
    if video.cid_hash:
        payload["cid_hash"] = video.cid_hash
    
    # Include VLM JSON CID if available
    if video.vlm_json_cid:
        payload["vlm_json_cid"] = video.vlm_json_cid
    
    # Essential flag for restore
    payload["is_encrypted"] = video.is_encrypted

    if segment_payload:
        payload["segment_metadata"] = segment_payload
    
    return payload
```

---

## Common Patterns

### Duplicate Detection

```python
def check_duplicate(cid_hash: str, phash: Optional[str], db_session) -> Optional[Video]:
    """Check if video already exists by CID hash or perceptual hash."""
    # Check by pHash first (content-based)
    if phash:
        existing = db_session.query(Video).filter(Video.phash == phash).first()
        if existing:
            return existing
    
    # Check by CID hash
    if cid_hash:
        existing = db_session.query(Video).filter(Video.cid_hash == cid_hash).first()
        if existing:
            return existing
    
    return None
```

### Caching Strategy

```typescript
// Implement write-through caching
class VideoCacheService {
  async syncWithArkiv(arkivVideos: Video[]): Promise<void> {
    const cache = await this.getCache();
    
    for (const video of arkivVideos) {
      await cache.put(video.id, video);
    }
  }
  
  async getMergedVideos(arkivVideos: Video[]): Promise<Video[]> {
    const cache = await this.getCache();
    const cachedVideos = await cache.getAll();
    
    // Create map of Arkiv videos
    const arkivMap = new Map(arkivVideos.map(v => [v.id, v]));
    
    // Add expired cache entries (not in Arkiv but previously cached)
    for (const cached of cachedVideos) {
      if (!arkivMap.has(cached.id)) {
        cached.arkivStatus = 'expired';
        arkivVideos.push(cached);
      }
    }
    
    return arkivVideos;
  }
}
```

### Error Recovery

```python
def safe_entity_parse(entity, default_title="Untitled"):
    """Safely parse an Arkiv entity with error recovery."""
    try:
        payload = json.loads(base64.b64decode(entity.payload))
    except Exception:
        payload = {}
    
    try:
        attributes = dict(entity.attributes) if entity.attributes else {}
    except Exception:
        attributes = {}
    
    # Extract what we can, use defaults for missing fields
    return {
        "id": getattr(entity, 'key', 'unknown'),
        "title": attributes.get('title', default_title),
        "filecoin_cid": payload.get('filecoin_root_cid'),
        "is_encrypted": payload.get('is_encrypted', False),
    }
```

---

## Troubleshooting

### Common Issues

#### Issue: "root_cid not found" errors

**Cause**: Old CLI uploads use `root_cid` instead of `filecoin_root_cid`.

**Fix**: Add fallback in your parser:

```typescript
const filecoinCid = (get('filecoin_root_cid', 'filecoinCid') as string) 
  || (get('root_cid', 'rootCid') as string)  // Fallback for legacy
  || '';
```

#### Issue: Encryption detection fails

**Cause**: Some uploads use `encrypted` instead of `is_encrypted`.

**Fix**: Check both field names:

```typescript
const isEncrypted = Boolean(
  get('is_encrypted', 'isEncrypted') 
  || get('encrypted', 'encrypted')  // Fallback for legacy
);
```

#### Issue: Cannot decrypt video

**Checklist:**
1. Verify `encryption_metadata` exists in payload
2. Check that `accessControlConditions` are valid
3. Ensure user meets access conditions (e.g., owns required NFT)
4. Verify Lit network connection (datil-dev vs datil)

#### Issue: VLM analysis not loading

**Checklist:**
1. Verify `vlm_json_cid` exists in payload
2. Check IPFS gateway connectivity
3. Verify CID is accessible on Filecoin
4. Check JSON parsing for VLM data format

### Debugging Tips

```typescript
// Log entity data for debugging
function debugEntity(entity: ArkivEntity): void {
  console.log('Entity Key:', entity.key);
  console.log('Attributes:', JSON.stringify(entity.attributes, null, 2));
  
  try {
    const payload = parseEntityPayload(entity.payload);
    console.log('Payload:', JSON.stringify(payload, null, 2));
  } catch (e) {
    console.error('Failed to parse payload:', e);
  }
}
```

---

## Related Documentation

- [API Reference](API_REFERENCE.md) - Complete API reference
- [Arkiv Data Format](ARKIV_FORMAT.md) - Detailed format specification
- [Migration Notes](MIGRATION_NOTES.md) - Migrating from old format
