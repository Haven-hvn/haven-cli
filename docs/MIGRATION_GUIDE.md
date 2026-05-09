# Migration Guide: Legacy Entities

## Overview

This guide helps migrate existing Arkiv entities created with older versions of haven-cli to the new standardized format.

## Who Needs to Migrate?

You need to migrate if:
- You uploaded videos with haven-cli before v1.0.0
- Videos uploaded via CLI don't appear/play in haven-dapp
- You see `root_cid` instead of `filecoin_root_cid` in your entities
- You see `encrypted` instead of `is_encrypted` in your entities

## Migration Script

A migration script is provided at `scripts/migrate_entities.py`.

### Prerequisites

- Python 3.9+
- `arkiv` SDK installed
- Private key with access to entities
- Environment variables set:
  ```bash
  export HAVEN_PRIVATE_KEY="0x..."
  export ARKIV_RPC_URL="https://..."  # optional
  ```

### Usage

1. **Dry run (recommended first)**:
   ```bash
   python scripts/migrate_entities.py --dry-run
   ```

2. **Migrate specific entity**:
   ```bash
   python scripts/migrate_entities.py --entity-key 0x...
   ```

3. **Migrate all entities**:
   ```bash
   python scripts/migrate_entities.py --all
   ```

4. **Backup first**:
   ```bash
   python scripts/migrate_entities.py --all --backup ./backup.json
   ```

5. **Filter by owner**:
   ```bash
   python scripts/migrate_entities.py --all --owner 0x...
   ```

## What Gets Migrated?

### Automatic Fixes

1. **Payload field rename**: `root_cid` → `filecoin_root_cid`
2. **Payload field rename**: `encrypted` → `is_encrypted`
3. **Attributes field removal**: Remove `root_cid` from public attributes
4. **Payload field removal**: Remove `encryption_ciphertext`
5. **Encryption metadata consolidation**: Combine scattered fields into `encryption_metadata`
6. **Type normalization**: Ensure `is_encrypted` is int 0 or 1 in attributes

### Manual Review Required

1. **Segment metadata**: May need manual addition if not present
2. **Mint ID**: May need to be added if known
3. **Analysis model**: May need to be added
4. **VLM JSON CID**: May need to be uploaded and linked separately

## Verification

After migration, verify:

```bash
haven entity get <entity_key>
```

Or use the Arkiv SDK directly:

```python
from arkiv import Arkiv

client = Arkiv(private_key="0x...")
entity = client.arkiv.get_entity(entity_key)

import json
payload = json.loads(entity.payload)
attributes = dict(entity.attributes)

# Check payload
assert "filecoin_root_cid" in payload, "Missing filecoin_root_cid in payload"
assert "root_cid" not in payload, "Old root_cid still in payload"
assert payload.get("is_encrypted") in [0, 1, True, False], "Invalid is_encrypted value"

# Check attributes
assert "root_cid" not in attributes, "Privacy leak: root_cid in attributes"
assert "cid_hash" in attributes, "Missing cid_hash in attributes"

print("✅ Entity migrated correctly")
```

## Rollback

If you created a backup:

```bash
python scripts/restore_entities.py --backup ./backup.json
```

Or restore a specific entity:

```bash
python scripts/restore_entities.py --backup ./backup.json --entity-key 0x...
```

## Manual Migration

If the script doesn't work for your case, manual migration:

1. Fetch the entity:
   ```python
   from arkiv import Arkiv
   import json
   
   client = Arkiv(private_key="0x...")
   entity = client.arkiv.get_entity(entity_key)
   ```

2. Decode payload:
   ```python
   payload = json.loads(entity.payload.decode('utf-8'))
   ```

3. Fix fields:
   ```python
   import hashlib
   
   # Fix 1: Rename root_cid to filecoin_root_cid
   if "root_cid" in payload:
       payload["filecoin_root_cid"] = payload.pop("root_cid")
   
   # Fix 2: Rename encrypted to is_encrypted
   if "encrypted" in payload:
       payload["is_encrypted"] = payload.pop("encrypted")
   
   # Fix 3: Remove ciphertext (already on Filecoin)
   payload.pop("encryption_ciphertext", None)
   ```

4. Update attributes:
   ```python
   attributes = dict(entity.attributes)
   
   # Remove root_cid from attributes (privacy leak)
   cid = attributes.pop("root_cid", None)
   
   # Add cid_hash if root_cid was present
   if cid and "cid_hash" not in attributes:
       attributes["cid_hash"] = hashlib.sha256(cid.encode()).hexdigest()
   
   # Normalize is_encrypted to int
   if "is_encrypted" in attributes:
       attributes["is_encrypted"] = int(attributes["is_encrypted"])
   elif "encrypted" in attributes:
       attributes["is_encrypted"] = int(attributes.pop("encrypted"))
   ```

5. Write back:
   ```python
   from arkiv.types import Attributes, EntityKey
   
   client.arkiv.update_entity(
       key=EntityKey(entity_key),
       payload=json.dumps(payload).encode('utf-8'),
       content_type="application/json",
       attributes=Attributes(attributes),
       expires_in=entity.expires_in if hasattr(entity, 'expires_in') else 4 * 7 * 24 * 60 * 60,
   )
   ```

## Troubleshooting

### Issue: Entity not found

**Symptoms**: `Failed to fetch entity: Entity not found`

**Solution**: 
- Check the entity key is correct
- Verify you're using the owner wallet
- Try querying: `client.arkiv.query_entities('$owner = "0x..."')`

### Issue: Permission denied

**Symptoms**: `Permission denied` or `Not authorized`

**Solution**:
- Ensure you're using the owner wallet (the wallet that created the entity)
- Check private key is correct: `export HAVEN_PRIVATE_KEY="0x..."`

### Issue: Gas estimation failed

**Symptoms**: `Gas estimation failed` or `insufficient funds`

**Solution**:
- Check wallet balance for transaction fees
- Try on testnet first with `--dry-run`
- See [Gas Fees](#gas-fees) section below

### Issue: Payload too large

**Symptoms**: `Payload too large` or `413 Request Entity Too Large`

**Solution**:
- Remove any remaining ciphertext before migration
- The script automatically removes `encryption_ciphertext`
- If still too large, may need to recreate entity without large fields

### Issue: Import error

**Symptoms**: `ImportError: No module named 'arkiv'`

**Solution**:
```bash
pip install arkiv
```

Or if using the CLI virtual environment:
```bash
source .venv/bin/activate
pip install arkiv
```

## Gas Fees

Migrating entities requires gas fees for the update transactions.

**Estimated costs** (varies by network):
- Per entity update: ~0.0001-0.001 ETH
- 100 entities: ~0.01-0.1 ETH

**Test first**: Use `--dry-run` to estimate without spending gas.

**Testnet**: Test migration on testnet before mainnet.

## Format Reference

### Old Format (Pre-v1.0.0)

```json
{
  "payload": {
    "root_cid": "Qm...",
    "encrypted": true,
    "encryption_ciphertext": "base64..."
  },
  "attributes": {
    "root_cid": "Qm...",
    "encrypted": true
  }
}
```

### New Format (v1.0.0+)

```json
{
  "payload": {
    "filecoin_root_cid": "Qm...",
    "is_encrypted": 1,
    "encryption_metadata": "{...}",
    "cid_hash": "sha256..."
  },
  "attributes": {
    "cid_hash": "sha256...",
    "is_encrypted": 1,
    "encrypted_cid": "..."
  }
}
```

## Related Documentation

- [Migration Notes](MIGRATION_NOTES.md) - Detailed format changes
- [API Reference](API_REFERENCE.md) - Complete API reference
- [Arkiv Format](ARKIV_FORMAT.md) - Complete format specification
- [Integration Guide](INTEGRATION_GUIDE.md) - Developer integration guide

## Support

For migration support:
- Open an issue: https://github.com/haven-project/haven-cli/issues
- Discord: #support channel
