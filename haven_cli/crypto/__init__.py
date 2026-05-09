"""Cryptographic utilities for Haven CLI.

Provides encryption/decryption functionality and metadata management
for Haven-AOL integration.
"""

from haven_cli.pipeline.context import EncryptionMetadata

from .metadata import (
    load_encryption_metadata,
    save_encryption_metadata,
    load_encryption_metadata_by_cid,
    verify_cid_format,
)
from .haven_aol_local import GateParams, compute_derivation_input, encrypt_bytes, decrypt_bytes

__all__ = [
    "EncryptionMetadata",
    "load_encryption_metadata",
    "save_encryption_metadata",
    "load_encryption_metadata_by_cid",
    "verify_cid_format",
    "GateParams",
    "compute_derivation_input",
    "encrypt_bytes",
    "decrypt_bytes",
]
