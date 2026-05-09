# Haven-AOL Alignment Status

Documentation and implementation are now aligned to Haven-AOL terminology.

## Current Implementation Status

- Runtime code paths are Haven-AOL only (`haven_cli`, `js-services`, `haven_tui`).
- Encryption metadata naming is standardized:
  - `encryption_metadata`
  - `encrypted_ref`
  - `.encmeta` sidecar extension
- Pipeline derives deterministic pre-upload gate CID (`sha256:<file-hash>`) when explicit CID is unavailable.

## Validation Performed

- `tests/database/test_pipeline_models.py` passed
- `tests/database/test_repositories.py` passed
- `tests/integration/test_cross_application.py` passed

