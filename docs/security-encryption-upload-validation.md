# Security Fix: Encryption Failure Prevents Upload (Fail-Closed)

## Summary

**Issue:** A "fail open" security vulnerability was discovered where files could be uploaded unencrypted even when encryption was requested but failed.

**Severity:** HIGH - Could result in sensitive data being exposed on the public Filecoin network without encryption.

**Status:** ✅ FIXED

## Additional Gate Security Requirement (EIP-712)

Haven-AOL mainnet gate requests now require cryptographic proof that the requester controls the submitted EVM address. The proof must be an EIP-712 `GateRequest` signature bound to:

- `evmAddress`
- `transportPublicKey`
- `nonce`

Domain fields:

- `name = "HavenAOL"`
- `chainId`
- `verifyingContract`

This prevents address-impersonation attacks where a caller submits someone else's funded wallet address without controlling its private key.

## The Problem

When a user requested encryption (`encrypt=True`) but the encryption step failed (e.g., due to missing Haven-AOL inputs, metadata issues, or runtime errors), the system would still proceed to upload the **unencrypted** file to Filecoin.

### Vulnerability Scenario

1. User runs: `haven upload video.mp4 --encrypt`
2. Encryption step fails (e.g., key/metadata derivation failure)
3. Upload step executes anyway
4. **Unencrypted file is uploaded to Filecoin** ❌

This is a classic "fail open" security vulnerability where a security control (encryption) failing results in a less secure state (unencrypted upload).

## The Fix

Added a `should_skip()` method to the `UploadStep` class that checks:

1. If encryption was requested (`encrypt=True`)
2. If encryption succeeded (by checking `context.encryption_metadata`)
3. If the encrypted file exists

If encryption was requested but failed, the upload step is **skipped** to prevent uploading unencrypted data.

### Code Changes

**File:** `haven_cli/pipeline/steps/upload_step.py`

```python
async def should_skip(self, context: PipelineContext) -> bool:
    """Skip if the step is not enabled in context options.
    
    Also skips if encryption was requested but failed (security measure).
    This prevents uploading unencrypted files when encryption fails.
    """
    # Check if upload is disabled
    enabled = context.options.get(self.enabled_option, self.default_enabled)
    if not enabled:
        return True
    
    # SECURITY CHECK: If encryption was requested but failed, skip upload
    # This prevents "fail open" behavior where unencrypted files could be uploaded
    if context.options.get("encrypt", False):
        # Encryption was requested - check if it succeeded
        if context.encryption_metadata is None:
            # No encryption metadata means encryption failed or didn't complete
            logger.warning(
                "Skipping upload: encryption was requested but failed or did not complete. "
                "This is a security measure to prevent uploading unencrypted content."
            )
            return True
        
        # Verify encrypted file exists
        if context.encrypted_video_path:
            if not os.path.exists(context.encrypted_video_path):
                logger.error(
                    f"Skipping upload: encrypted file not found at {context.encrypted_video_path}"
                )
                return True
    
    return False
```

## Security Tests

Created comprehensive security tests in `tests/pipeline/test_encryption_upload_security.py`:

### Test Coverage

1. **test_encryption_failure_prevents_upload_fail_open**
   - ✅ Verifies that when encryption fails, upload is skipped
   - ✅ Ensures no CID is generated (nothing uploaded)
   - ✅ Confirms upload bridge is not called

2. **test_encryption_skipped_allows_upload**
   - ✅ Verifies normal operation when encryption is not requested
   - ✅ Ensures upload proceeds normally when `encrypt=False`

3. **test_encryption_success_upload_encrypted_file**
   - ✅ Verifies that when encryption succeeds, the encrypted file is uploaded
   - ✅ Confirms the `.enc` file is uploaded, not the original
   - ✅ Validates metadata indicates encryption was used

4. **test_partial_encryption_metadata_prevents_upload**
   - ✅ Edge case: incomplete encryption metadata prevents upload
   - ✅ Ensures no upload if encryption partially fails

### Test Results

```
tests/pipeline/test_encryption_upload_security.py::TestEncryptionUploadSecurity::test_encryption_failure_prevents_upload_fail_open PASSED
tests/pipeline/test_encryption_upload_security.py::TestEncryptionUploadSecurity::test_encryption_skipped_allows_upload PASSED
tests/pipeline/test_encryption_upload_security.py::TestEncryptionUploadSecurity::test_encryption_success_upload_encrypted_file PASSED
tests/pipeline/test_encryption_upload_security.py::TestEncryptionUploadSecurity::test_partial_encryption_metadata_prevents_upload PASSED

======================= 4 passed =======================
```

All existing upload step tests continue to pass:
```
tests/pipeline/test_upload_step.py - 23 passed
```

## Behavior Changes

### Before Fix (Vulnerable)

| Scenario | Encryption Requested | Encryption Result | Upload Behavior |
|----------|---------------------|-------------------|-----------------|
| Normal | No | N/A | Uploads original file ✅ |
| Normal | Yes | Success | Uploads encrypted file ✅ |
| **VULNERABLE** | **Yes** | **Failure** | **Uploads unencrypted file** ❌ |

### After Fix (Secure)

| Scenario | Encryption Requested | Encryption Result | Upload Behavior |
|----------|---------------------|-------------------|-----------------|
| Normal | No | N/A | Uploads original file ✅ |
| Normal | Yes | Success | Uploads encrypted file ✅ |
| **SECURE** | **Yes** | **Failure** | **Skips upload** ✅ |

## Security Principles Applied

1. **Fail-Closed**: When a security control fails, the system defaults to a more secure state
2. **Defense in Depth**: Multiple checks (encryption metadata + file existence)
3. **Explicit Security**: Clear logging when security measures are triggered
4. **Test Coverage**: Comprehensive tests for security-critical paths

## Recommendations

1. **User Communication**: When upload is skipped due to encryption failure, provide clear error messages to users explaining why the upload didn't proceed.

2. **Retry Logic**: Consider adding retry logic specifically for encryption failures to improve reliability without compromising security.

3. **Audit Logging**: Log all instances where encryption was requested but failed for security auditing.

4. **Configuration Validation**: Validate encryption configuration (e.g., `owner_wallet`, `token_contract`, `HAVEN_PRIVATE_KEY`) before starting the pipeline to fail fast.

## Related Files

- `haven_cli/pipeline/steps/upload_step.py` - Fixed file
- `haven_cli/pipeline/steps/encrypt_step.py` - Encryption step
- `tests/pipeline/test_encryption_upload_security.py` - Security tests
- `tests/pipeline/test_upload_step.py` - Existing upload tests

## Verification

To verify the fix:

```bash
# Run security tests
.venv/bin/python -m pytest tests/pipeline/test_encryption_upload_security.py -v

# Run all upload tests
.venv/bin/python -m pytest tests/pipeline/test_upload_step.py -v
```

All tests should pass.
