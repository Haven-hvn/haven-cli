"""Encryption metadata handling for Haven-AOL.

Provides functions to load and save encryption metadata for files,
supporting database storage, sidecar files, and Arkiv blockchain entities.
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
    file (.encmeta extension). For database lookup by CID, use
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
    metadata_path = file_path.with_suffix(file_path.suffix + ".encmeta")
    
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
            
            if video and video.encryption_metadata:
                try:
                    data = json.loads(video.encryption_metadata)
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
    encrypted_key = data.get("encrypted_key") or data.get("encryptedKey", "")
    key_hash = data.get("key_hash") or data.get("keyHash", "")
    iv = data.get("iv", "")
    
    return EncryptionMetadata(
        ciphertext=ciphertext,
        data_to_encrypt_hash=data_to_encrypt_hash,
        encrypted_key=encrypted_key,
        key_hash=key_hash,
        iv=iv,
        access_control_conditions=access_control_conditions,
        chain=chain,
    )


async def save_encryption_metadata(
    file_path: Path,
    metadata: EncryptionMetadata,
) -> None:
    """Save encryption metadata as sidecar file.
    
    Saves encryption metadata to a sidecar file with .encmeta extension.
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
    metadata_path = file_path.with_suffix(file_path.suffix + ".encmeta")
    
    data = {
        "ciphertext": metadata.ciphertext,
        "data_to_encrypt_hash": metadata.data_to_encrypt_hash,
        "dataToEncryptHash": metadata.data_to_encrypt_hash,  # For compatibility
        "encrypted_key": metadata.encrypted_key,
        "encryptedKey": metadata.encrypted_key,  # For compatibility
        "key_hash": metadata.key_hash,
        "keyHash": metadata.key_hash,  # For compatibility
        "iv": metadata.iv,
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
    
    # CIDv1: Starts with baf (base32), typically 59+ characters
    if cid.startswith("baf"):
        # CIDv1 base32 encoded - variable length, typically 59+ chars
        return len(cid) >= 55 and all(c in "abcdefghijklmnopqrstuvwxyz234567" for c in cid.lower())
    
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
    return file_path.with_suffix(file_path.suffix + ".encmeta")


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


async def load_encryption_metadata_from_arkiv_entity(
    entity_key: str,
    rpc_url: str,
    private_key: str,
) -> Optional[EncryptionMetadata]:
    """Load encryption metadata from an Arkiv entity.
    
    Fetches the entity by its key and extracts encryption metadata
    from the entity payload. This is used as a fallback when metadata
    is not available in the local database or sidecar files.
    
    Args:
        entity_key: Arkiv entity key (hex string)
        rpc_url: Arkiv RPC URL
        private_key: EVM private key for Arkiv connection
        
    Returns:
        EncryptionMetadata if found, None otherwise
        
    Example:
        metadata = await load_encryption_metadata_from_arkiv_entity(
            "0x1234...abcd",
            "https://braga.hoodi.arkiv.network/rpc",
            os.environ["HAVEN_PRIVATE_KEY"],
        )
    """
    try:
        from arkiv import Arkiv
        from arkiv.account import NamedAccount
        from arkiv.provider import ProviderBuilder
        from arkiv.types import EntityKey

        provider = ProviderBuilder().custom(rpc_url).build()
        account = NamedAccount.from_private_key("haven-cli", private_key)
        client = Arkiv(provider=provider, account=account)

        key = EntityKey(entity_key)
        entity = client.arkiv.get_entity(key)

        if not entity or not entity.payload:
            logger.warning(f"Entity {entity_key} not found or has no payload")
            return None

        payload = json.loads(entity.payload.decode("utf-8"))
        enc_meta_raw = payload.get("encryption_metadata", "")
        if not enc_meta_raw:
            logger.warning(f"Entity {entity_key} has no encryption_metadata in payload")
            return None

        enc = json.loads(enc_meta_raw) if isinstance(enc_meta_raw, str) else enc_meta_raw
        return _parse_encryption_metadata(enc)

    except ImportError:
        logger.warning("arkiv package not installed, cannot fetch entity metadata")
        return None
    except Exception as e:
        logger.warning(f"Failed to load encryption metadata from Arkiv entity {entity_key}: {e}")
        return None


async def load_encryption_metadata_from_arkiv_query(
    cid_hash: str,
    rpc_url: str,
    private_key: str,
) -> Optional[EncryptionMetadata]:
    """Load encryption metadata from Arkiv by querying entities with a CID hash.
    
    Searches for encrypted entities matching the given CID hash and returns
    the encryption metadata from the first match.
    
    Args:
        cid_hash: SHA-256 hash of the root CID
        rpc_url: Arkiv RPC URL
        private_key: EVM private key for Arkiv connection
        
    Returns:
        EncryptionMetadata if found, None otherwise
        
    Example:
        metadata = await load_encryption_metadata_from_arkiv_query(
            "c8732a56d8f08d52c05f89607b3d3b4125fc2a24e3b9bbe4b2f0dfed904568ad",
            "https://braga.hoodi.arkiv.network/rpc",
            os.environ["HAVEN_PRIVATE_KEY"],
        )
    """
    try:
        from arkiv import Arkiv
        from arkiv.account import NamedAccount
        from arkiv.provider import ProviderBuilder
        from arkiv.types import QueryOptions

        provider = ProviderBuilder().custom(rpc_url).build()
        account = NamedAccount.from_private_key("haven-cli", private_key)
        client = Arkiv(provider=provider, account=account)

        # Query for encrypted entities with matching CID hash
        entities = list(client.arkiv.query_entities(
            query=f'cid_hash = "{cid_hash}" AND is_encrypted = 1',
            options=QueryOptions(
                max_results_per_page=5,
            ),
        ))

        if not entities:
            logger.warning(f"No encrypted entities found for CID hash {cid_hash}")
            return None

        # Fetch the first matching entity to get the payload
        from arkiv.types import EntityKey
        entity = client.arkiv.get_entity(EntityKey(str(entities[0].key)))

        if not entity or not entity.payload:
            logger.warning(f"Entity {entities[0].key} has no payload")
            return None

        payload = json.loads(entity.payload.decode("utf-8"))
        enc_meta_raw = payload.get("encryption_metadata", "")
        if not enc_meta_raw:
            logger.warning(f"Entity {entities[0].key} has no encryption_metadata")
            return None

        enc = json.loads(enc_meta_raw) if isinstance(enc_meta_raw, str) else enc_meta_raw
        return _parse_encryption_metadata(enc)

    except ImportError:
        logger.warning("arkiv package not installed, cannot query Arkiv")
        return None
    except Exception as e:
        logger.warning(f"Failed to query Arkiv for CID hash {cid_hash}: {e}")
        return None
