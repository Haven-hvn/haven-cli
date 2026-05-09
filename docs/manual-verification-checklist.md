# Manual Verification Checklist for haven-cli

**Sprint 3 - Task 4**  
**Priority:** MUST HAVE - Production readiness

---

## Overview

This document provides a comprehensive manual verification checklist to ensure haven-cli uploads work correctly in real-world scenarios. Automated tests verify code logic, but manual testing verifies the actual user experience and integration with real services (Arkiv, Filecoin).

## ICP Gate Signature Requirement

For Haven-AOL mainnet canister decryption-gate checks, callers must provide a valid EIP-712 proof of EVM wallet ownership. The gate no longer accepts requests that only provide an `evmAddress`.

Required signed payload (`primaryType = GateRequest`):

- `evmAddress` (`address`)
- `transportPublicKey` (`bytes`)
- `nonce` (`uint256`)

Required domain:

- `name = "HavenAOL"`
- `chainId = 1` (Ethereum mainnet)
- `verifyingContract` = selected domain contract address for the request

Required request fields to the canister:

- `nonce`
- `signature` (`r||s||v`, 65 bytes, `v` in {27, 28})
- `eip712ChainId`
- `eip712VerifyingContract`

## Prerequisites

Before running these tests, ensure you have:

- [ ] haven-cli installed and configured (`pip install -e .` in haven-cli directory)
- [ ] Test wallet with funds for Arkiv transactions
- [ ] Access to Filecoin/IPFS (FILECOIN_RPC_URL configured)
- [ ] Test video files available (create with: `ffmpeg -f lavfi -i testsrc=duration=10:size=640x480:rate=1 -pix_fmt yuv420p test_video.mp4`)
- [ ] Arkiv sync enabled (`ARKIV_SYNC_ENABLED=true` and `HAVEN_PRIVATE_KEY` set)
- [ ] Environment variables configured:
  ```bash
  export HAVEN_PRIVATE_KEY="your_private_key"
  export ARKIV_SYNC_ENABLED="true"
  export FILECOIN_RPC_URL="https://api.node.glif.io/rpc/v1"  # or testnet
  export ARKIV_RPC_URL="https://rpc.arkiv.study"  # or testnet
  ```

---

## Test 1: Non-encrypted Video Upload

### Command
```bash
haven upload file test_video.mp4 --title "Test Non-encrypted Video"
```

### Verification Steps
- [ ] Upload completes without errors
- [ ] Entity key is returned
- [ ] Filecoin CID is returned (starts with `Qm` or `bafy`)

### Post-Upload Verification
Query the Arkiv entity:
```bash
haven entity get <entity_key>
```

Verify:
- [ ] `payload.filecoin_root_cid` exists and is valid CID
- [ ] `payload.root_cid` does NOT exist
- [ ] `attributes.root_cid` does NOT exist
- [ ] `attributes.cid_hash` exists (64-char hex)
- [ ] `payload.is_encrypted` is `false`
- [ ] `attributes.is_encrypted` is `0` or not present
- [ ] `attributes.title` is "Test Non-encrypted Video"
- [ ] `attributes.created_at` is ISO8601 format (e.g., `2026-02-20T10:30:00+00:00`)
- [ ] `attributes.updated_at` matches `created_at` format

---

## Test 2: Encrypted Video Upload

### Command
```bash
haven upload file test_video.mp4 --encrypt --title "Test Encrypted Video"
```

### Verification Steps
- [ ] Upload completes without errors
- [ ] Encryption step completes (may take 10-30 seconds)
- [ ] Entity key is returned

### Post-Upload Verification
```bash
haven entity get <entity_key>
```

Verify:
- [ ] `payload.is_encrypted` is `true`
- [ ] `attributes.is_encrypted` is `1`
- [ ] `payload.encryption_metadata` exists
- [ ] `encryption_metadata` is valid JSON (not a string in JSON)
- [ ] JSON has required fields:
  - [ ] `version` = "hybrid-v1"
  - [ ] `encryptedKey` (base64 string)
  - [ ] `keyHash` (hex string)
  - [ ] `iv` (base64 string)
  - [ ] `algorithm` = "AES-GCM"
  - [ ] `keyLength` = 256
  - [ ] `accessControlConditions` (array)
  - [ ] `chain` = "ethereum" (or configured chain)
- [ ] `attributes.encrypted_cid` exists (encrypted CID for public access)
- [ ] `payload.encrypted` does NOT exist
- [ ] `payload.encryption_ciphertext` does NOT exist
- [ ] `payload.encryption_chain` does NOT exist

---

## Test 3: Video with VLM Analysis

### Command
```bash
haven upload file test_video.mp4 --title "Test with VLM"
```

Note: VLM analysis is enabled by default unless `--no-vlm` is specified.

### Verification Steps
- [ ] Upload completes
- [ ] VLM analysis step completes (may take 1-5 minutes)
- [ ] Entity key is returned

### Post-Upload Verification
```bash
haven entity get <entity_key>
```

Verify:
- [ ] `payload.vlm_json_cid` exists and is valid CID
- [ ] `attributes.analysis_model` is set (e.g., "llava-1.5-7b")
- [ ] `payload.has_ai_data` is `true`
- [ ] `payload.tag_count` is > 0

Fetch and verify VLM JSON:
```bash
# Fetch from Filecoin using vlm_json_cid (requires ipfs-cli or similar)
ipfs cat <vlm_json_cid> | jq .
```

- [ ] VLM JSON is valid JSON
- [ ] VLM JSON has required fields:
  - [ ] `version`
  - [ ] `model`
  - [ ] `analyzed_at` (ISO8601 timestamp)
  - [ ] `segments` (array of analyzed segments)

---

## Test 4: Video with All Features

### Command
```bash
haven upload file test_video.mp4 \
  --encrypt \
  --title "Complete Test Video" \
  --creator "@testuser" \
  --source "https://example.com/original.mp4"
```

### Verification Steps
- [ ] All steps complete without errors
- [ ] Upload, encryption, VLM analysis, and Arkiv sync all succeed

### Post-Upload Verification
```bash
haven entity get <entity_key>
```

Verify:
- [ ] All fields from Tests 1-3 are correct
- [ ] `attributes.creator_handle` is "@testuser"
- [ ] `attributes.source_uri` is "https://example.com/original.mp4"
- [ ] `attributes.updated_at` matches `created_at` format
- [ ] `payload.encryption_metadata.originalMimeType` exists
- [ ] `payload.encryption_metadata.originalSize` matches file size

---

## Test 5: Cross-Application Verification

### 5.1 Verify in haven-dapp

1. Open haven-dapp in browser
2. Connect wallet (same as upload wallet)
3. Navigate to library
4. Find uploaded videos

#### Verify Non-Encrypted Video:
- [ ] Video appears in library
- [ ] Title displays correctly ("Test Non-encrypted Video")
- [ ] Thumbnail loads (if available)
- [ ] Duration shows correctly
- [ ] Video plays without decryption prompt
- [ ] No console errors in browser

#### Verify Encrypted Video:
- [ ] Video appears in library
- [ ] Title displays correctly ("Test Encrypted Video")
- [ ] Thumbnail loads (if available)
- [ ] On play, decryption prompt appears (or auto-decrypts if authorized)
- [ ] After decryption, video plays correctly
- [ ] No console errors in browser

### 5.2 Verify in haven-player (if available)

1. Run catalog restore
2. Check library

Verify:
- [ ] Videos appear in library
- [ ] Metadata correct (title, creator, duration)
- [ ] Non-encrypted videos play directly
- [ ] Encrypted videos prompt for decryption then play

---

## Test Results Documentation

Create a test results file at `docs/test-results/YYYY-MM-DD-manual-verification.md`:

```markdown
# Manual Verification Results - YYYY-MM-DD

## Test Environment
- CLI Version: x.x.x
- OS: [e.g., Ubuntu 22.04, macOS 14]
- Wallet: [address]
- Network: [mainnet/testnet]
- Arkiv RPC: [URL]
- Filecoin RPC: [URL]

## Test 1: Non-encrypted Upload
- Status: [PASS/FAIL]
- Entity Key: [key]
- Filecoin CID: [cid]
- Notes: [any issues]

## Test 2: Encrypted Upload
- Status: [PASS/FAIL]
- Entity Key: [key]
- Filecoin CID: [cid]
- Notes: [any issues]

## Test 3: VLM Analysis
- Status: [PASS/FAIL]
- Entity Key: [key]
- VLM JSON CID: [cid]
- Notes: [any issues]

## Test 4: All Features
- Status: [PASS/FAIL]
- Entity Key: [key]
- Notes: [any issues]

## Cross-Application Verification
- haven-dapp: [PASS/FAIL]
- haven-player: [PASS/FAIL] (if tested)

## Issues Found
1. [Issue description and reproduction steps]
2. [Issue description and reproduction steps]

## Sign-off
Tested by: [Name]
Date: [Date]
Approved for release: [YES/NO]
```

---

## Success Criteria

- [ ] All 4 test scenarios executed
- [ ] All verification checks pass
- [ ] Cross-application verification completed
- [ ] Test results documented
- [ ] No critical issues found

---

## Troubleshooting

### If tests fail:

1. **Check Arkiv connection:**
   ```bash
   haven config get arkiv.rpc_url
   # or check environment:
   echo $ARKIV_RPC_URL
   ```

2. **Check wallet balance:**
   ```bash
   # Use foundry cast or similar
   cast balance $(cast wallet address --private-key $HAVEN_PRIVATE_KEY) --rpc-url $ARKIV_RPC_URL
   ```

3. **Check Filecoin gateway accessibility:**
   ```bash
   curl -I $FILECOIN_RPC_URL
   ```

4. **Review CLI logs:**
   ```bash
   haven upload file test_video.mp4 --verbose
   # or
   haven upload file test_video.mp4 --debug --log-file upload.log
   ```

5. **Compare with gold standard implementation:**
   - Check `haven-player/backend/app/services/arkiv_sync.py`
   - Run integration tests: `cd haven-cli && python -m pytest tests/integration/test_cross_application.py -v`

### Common Issues:

| Issue | Solution |
|-------|----------|
| "Arkiv sync is disabled" | Set `HAVEN_PRIVATE_KEY` and `ARKIV_SYNC_ENABLED=true` |
| "Insufficient gas" | Fund your wallet with testnet/mainnet tokens |
| "Payload too large" | Video metadata is too large; check for excessive tags/timestamps |
| VLM analysis times out | Normal for long videos; try shorter test video |
| Entity not found | Wait 30-60 seconds for blockchain confirmation |

---

## Related Documentation

- [HAVEN_CROSS_APPLICATION_DATA_FORMAT_SPECIFICATION.md](../../HAVEN_CROSS_APPLICATION_DATA_FORMAT_SPECIFICATION.md)
- [haven-field-migration-guide.md](../../haven-field-migration-guide.md)
- [haven-data-format-analysis.md](../../haven-data-format-analysis.md)
- Sprint 3 Task 3: Integration tests
- Sprint 1 & 2: All implementation tasks
