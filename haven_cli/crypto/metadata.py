"""Encryption metadata handling for Lit Protocol.

Provides functions to load and save encryption metadata for files,
supporting both database storage and sidecar files.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List

from haven_cli.pipeline.context import EncryptionMetadata

# Database import is optional - allows module to work without DB
try:
    from haven_cli.database.connection import get_db_session
    HAS_DATABASE = True
except ImportError:
    HAS_DATABASE = False
    get_db_session = None  # type: ignore

logger = logging.getLogger(__name__)


async def load_encryption_metadata(file_path: Path) -> Optional[EncryptionMetadata]:
    """Load encryption metadata for a file from sidecar.
    
    This function attempts to load encryption metadata from a sidecar
    file (.lit extension). For database lookup by CID, use
    load_encryption_metadata_by_cid instead.
    
    Args:
        file_path: Path to the encrypted file
        
    Returns:
        EncryptionMetadata if found, None otherwise
        
    Example:
        metadata = await load_encryption_metadata(Path("video.mp4"))
        if metadata:
            print(f"Ciphertext hash: {metadata.data_to_encrypt_hash}")
    """
    # Try sidecar file first
    metadata_path = file_path.with_suffix(file_path.suffix + ".lit")
    
    if metadata_path.exists():
        try:
            data = json.loads(metadata_path.read_text())
            return _parse_encryption_metadata(data)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to parse sidecar metadata from {metadata_path}: {e}")
    
    return None


async def load_encryption_metadata_by_cid(cid: str) -> Optional[EncryptionMetadata]:
    """Load encryption metadata for a file from database by CID.
    
    This function queries the database for a video record with the
    given CID and returns its encryption metadata if available.
    
    Args:
        cid: Content identifier (CID) of the file
        
    Returns:
        EncryptionMetadata if found, None otherwise
        
    Example:
        metadata = await load_encryption_metadata_by_cid("bafybeig...")
        if metadata:
            print(f"Chain: {metadata.chain}")
    """
    if not HAS_DATABASE or get_db_session is None:
        logger.debug("Database not available, skipping CID lookup")
        return None
    
    try:
        with get_db_session() as session:
            from haven_cli.database.repositories import VideoRepository
            
            video_repo = VideoRepository(session)
            video = video_repo.get_by_cid(cid)
            
            if video and video.lit_encryption_metadata:
                try:
                    data = json.loads(video.lit_encryption_metadata)
                    return _parse_encryption_metadata(data)
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    logger.warning(f"Failed to parse database metadata for CID {cid}: {e}")
    except Exception as e:
        logger.warning(f"Failed to query database for CID {cid}: {e}")
    
    return None


def _parse_encryption_metadata(data: Dict[str, Any]) -> EncryptionMetadata:
    """Parse encryption metadata from dictionary.
    
    Args:
        data: Dictionary containing encryption metadata
        
    Returns:
        EncryptionMetadata instance
        
    Raises:
        KeyError: If required fields are missing
        TypeError: If data types are invalid
    """
    # Handle different field naming conventions
    ciphertext = data.get("ciphertext", "")
    data_to_encrypt_hash = data.get("data_to_encrypt_hash") or data.get("dataToEncryptHash", "")
    access_control_conditions = data.get("access_control_conditions") or data.get("accessControlConditions", [])
    chain = data.get("chain", "ethereum")
    
    return EncryptionMetadata(
        ciphertext=ciphertext,
        data_to_encrypt_hash=data_to_encrypt_hash,
        access_control_conditions=access_control_conditions,
        chain=chain,
    )


async def save_encryption_metadata(
    file_path: Path,
    metadata: EncryptionMetadata,
) -> None:
    """Save encryption metadata as sidecar file.
    
    Saves encryption metadata to a sidecar file with .lit extension.
    This allows the metadata to travel with the file when moved.
    
    Args:
        file_path: Path to the encrypted file
        metadata: Encryption metadata to save
        
    Example:
        await save_encryption_metadata(
            Path("video.mp4"),
            EncryptionMetadata(
                ciphertext="...",
                data_to_encrypt_hash="...",
                access_control_conditions=[...],
                chain="ethereum",
            )
        )
    """
    metadata_path = file_path.with_suffix(file_path.suffix + ".lit")
    
    data = {
        "ciphertext": metadata.ciphertext,
        "data_to_encrypt_hash": metadata.data_to_encrypt_hash,
        "dataToEncryptHash": metadata.data_to_encrypt_hash,  # For compatibility
        "access_control_conditions": metadata.access_control_conditions,
        "accessControlConditions": metadata.access_control_conditions,  # For compatibility
        "chain": metadata.chain,
    }
    
    try:
        metadata_path.write_text(json.dumps(data, indent=2))
        logger.debug(f"Saved encryption metadata to {metadata_path}")
    except IOError as e:
        logger.error(f"Failed to save metadata to {metadata_path}: {e}")
        raise


def verify_cid_format(cid: str) -> bool:
    """Verify that a string is a valid CID format.
    
    Performs basic validation on CID format. Supports:
    - CIDv0 (Qm... base58-encoded sha2-256)
    - CIDv1 (bafy... base32-encoded)
    
    Args:
        cid: Content identifier to verify
        
    Returns:
        True if CID format appears valid, False otherwise
        
    Example:
        >>> verify_cid_format("bafybeig...")
        True
        >>> verify_cid_format("Qm...")
        True
        >>> verify_cid_format("invalid")
        False
    """
    if not cid or not isinstance(cid, str):
        return False
    
    # CIDv0: Starts with Qm, base58-encoded, 46 characters
    if cid.startswith("Qm"):
        return len(cid) == 46 and all(c in "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz" for c in cid)
    
    # CIDv1: Starts with bafy (base32), typically 59+ characters
    if cid.startswith("baf"):
        return len(cid) >= 59 and all(c in "abcdefghijklmnopqrstuvwxyz234567" for c in cid.lower())
    
    # Other CIDv1 variants
    if cid.startswith("ba"):
        return len(cid) >= 50
    
    return False


def get_encryption_metadata_path(file_path: Path) -> Path:
    """Get the path to the encryption metadata sidecar file.
    
    Args:
        file_path: Path to the encrypted file
        
    Returns:
        Path to the metadata sidecar file
    """
    return file_path.with_suffix(file_path.suffix + ".lit")


async def delete_encryption_metadata(file_path: Path) -> bool:
    """Delete encryption metadata sidecar file.
    
    Args:
        file_path: Path to the encrypted file
        
    Returns:
        True if metadata was deleted, False if it didn't exist
    """
    metadata_path = get_encryption_metadata_path(file_path)
    
    if metadata_path.exists():
        try:
            metadata_path.unlink()
            logger.debug(f"Deleted encryption metadata: {metadata_path}")
            return True
        except IOError as e:
            logger.warning(f"Failed to delete metadata {metadata_path}: {e}")
    
    return False


async def find_encryption_metadata(
    cid: Optional[str] = None,
    file_path: Optional[Path] = None,
) -> Optional[EncryptionMetadata]:
    """Find encryption metadata using multiple lookup methods.
    
    This is a convenience function that attempts to find encryption
    metadata using any available method:
    1. If CID is provided, query database
    2. If file_path is provided, check for sidecar file
    
    Args:
        cid: Optional CID to lookup in database
        file_path: Optional file path to check for sidecar
        
    Returns:
        EncryptionMetadata if found, None otherwise
    """
    if not cid and not file_path:
        logger.debug("No CID or file_path provided for metadata lookup")
        return None
    
    # Try CID lookup first (database)
    if cid:
        metadata = await load_encryption_metadata_by_cid(cid)
        if metadata:
            return metadata
    
    # Try file path lookup (sidecar)
    if file_path:
        metadata = await load_encryption_metadata(file_path)
        if metadata:
            return metadata
    
    return None
