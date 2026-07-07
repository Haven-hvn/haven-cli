"""Cryptographic utilities for Haven CLI.

Provides encryption/decryption functionality and metadata management
for Haven-AOL integration. Sprint 4 adds the v3 surface alongside the
existing v1 surface — both versions are exported here for one-stop
consumption by upload/download/pipeline code.
"""

from haven_cli.pipeline.context import EncryptionMetadata

from .metadata import (
    load_encryption_metadata,
    save_encryption_metadata,
    load_encryption_metadata_by_cid,
    load_encryption_metadata_from_arkiv_entity,
    load_encryption_metadata_from_arkiv_query,
    verify_cid_format,
)
from .haven_aol_local import GateParams, compute_derivation_input, encrypt_bytes, decrypt_bytes

# v3 surface — additive, no v1 behavior change. The cache singleton and
# the dispatch helpers are exported so callers (CLI download command,
# pipeline decrypt step) can hit a single entry point regardless of
# metadata version.
from .haven_aol_v3 import (
    encrypt_bytes_v3,
    encrypt_file_streaming_v3,
    decrypt_bytes_v3,
    decrypt_file_streaming_v3,
    decrypt_bytes_dispatch,
    decrypt_file_dispatch,
)
from .gate_key_cache import CachedVetKey, GateKeyCache, gate_key_cache
from .epoch_key_cache import EpochAesKey, EpochAesKeyCache, epoch_aes_key_cache

__all__ = [

    # v1 surface (unchanged)
    "EncryptionMetadata",
    "load_encryption_metadata",
    "save_encryption_metadata",
    "load_encryption_metadata_by_cid",
    "load_encryption_metadata_from_arkiv_entity",
    "load_encryption_metadata_from_arkiv_query",
    "verify_cid_format",
    "GateParams",
    "compute_derivation_input",
    "encrypt_bytes",
    "decrypt_bytes",
    # v3 surface (Sprint 4)
    "encrypt_bytes_v3",
    "encrypt_file_streaming_v3",
    "decrypt_bytes_v3",
    "decrypt_file_streaming_v3",
    "decrypt_bytes_dispatch",
    "decrypt_file_dispatch",
    "CachedVetKey",
    "GateKeyCache",
    "gate_key_cache",
    "EpochAesKey",
    "EpochAesKeyCache",
    "epoch_aes_key_cache",
]

