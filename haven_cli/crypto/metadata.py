"""Encryption metadata handling for Haven-AOL gate v1."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from haven_cli.crypto.gate_metadata import is_gate_metadata, parse_gate_metadata
from haven_cli.pipeline.context import EncryptionMetadata

try:
    from haven_cli.database.connection import get_db_session
    HAS_DATABASE = True
except ImportError:
    HAS_DATABASE = False
    get_db_session = None  # type: ignore

logger = logging.getLogger(__name__)


async def load_encryption_metadata(file_path: Path) -> Optional[EncryptionMetadata]:
    """Load gate v1 metadata from a ``.encmeta`` sidecar next to the encrypted file."""
    metadata_path = file_path.with_suffix(file_path.suffix + ".encmeta")

    if metadata_path.exists():
        try:
            data = json.loads(metadata_path.read_text())
            gate = parse_gate_metadata(data)
            if gate:
                return EncryptionMetadata(gate=gate)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to parse sidecar metadata from {metadata_path}: {e}")

    return None


async def load_encryption_metadata_by_cid(cid: str) -> Optional[EncryptionMetadata]:
    """Load gate v1 metadata from the database by Filecoin CID."""
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
                    gate = parse_gate_metadata(data)
                    if gate:
                        return EncryptionMetadata(gate=gate)
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    logger.warning(f"Failed to parse database metadata for CID {cid}: {e}")
    except Exception as e:
        logger.warning(f"Failed to query database for CID {cid}: {e}")

    return None


async def save_encryption_metadata(
    file_path: Path,
    metadata: EncryptionMetadata,
) -> None:
    """Save gate v1 metadata as a sidecar ``.encmeta`` file."""
    from haven_cli.crypto.gate_metadata import gate_metadata_to_json

    metadata_path = file_path.with_suffix(file_path.suffix + ".encmeta")
    metadata_path.write_text(gate_metadata_to_json(metadata.gate))


def verify_cid_format(cid: str) -> bool:
    """Verify that a string is a valid CID format."""
    if not cid or not isinstance(cid, str):
        return False

    if cid.startswith("Qm"):
        return len(cid) == 46 and all(
            c in "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
            for c in cid
        )

    if cid.startswith("baf"):
        return len(cid) >= 55 and all(
            c in "abcdefghijklmnopqrstuvwxyz234567" for c in cid.lower()
        )

    if cid.startswith("ba"):
        return len(cid) >= 50

    return False


def get_encryption_metadata_path(file_path: Path) -> Path:
    return file_path.with_suffix(file_path.suffix + ".encmeta")


async def delete_encryption_metadata(file_path: Path) -> bool:
    metadata_path = get_encryption_metadata_path(file_path)

    if metadata_path.exists():
        try:
            metadata_path.unlink()
            return True
        except IOError as e:
            logger.warning(f"Failed to delete metadata {metadata_path}: {e}")

    return False


async def find_encryption_metadata(
    cid: Optional[str] = None,
    file_path: Optional[Path] = None,
) -> Optional[EncryptionMetadata]:
    if cid:
        metadata = await load_encryption_metadata_by_cid(cid)
        if metadata:
            return metadata

    if file_path:
        metadata = await load_encryption_metadata(file_path)
        if metadata:
            return metadata

    return None


def _gate_from_arkiv_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    enc_meta_raw = payload.get("encryption_metadata")
    if not enc_meta_raw:
        return None
    if isinstance(enc_meta_raw, str):
        return parse_gate_metadata(enc_meta_raw)
    return parse_gate_metadata(enc_meta_raw)


async def load_encryption_metadata_from_arkiv_entity(
    entity_key: str,
    rpc_url: str,
    private_key: str,
) -> Optional[EncryptionMetadata]:
    try:
        from arkiv import Arkiv
        from arkiv.account import NamedAccount
        from arkiv.provider import ProviderBuilder
        from arkiv.types import EntityKey

        provider = ProviderBuilder().custom(rpc_url).build()
        account = NamedAccount.from_private_key("haven-cli", private_key)
        client = Arkiv(provider=provider, account=account)

        entity = client.arkiv.get_entity(EntityKey(entity_key))

        if not entity or not entity.payload:
            logger.warning(f"Entity {entity_key} not found or has no payload")
            return None

        payload = json.loads(entity.payload.decode("utf-8"))
        gate = _gate_from_arkiv_payload(payload)
        if not gate:
            logger.warning(f"Entity {entity_key} has no gate v1 encryption_metadata")
            return None
        return EncryptionMetadata(gate=gate)

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
    try:
        from arkiv import Arkiv
        from arkiv.account import NamedAccount
        from arkiv.provider import ProviderBuilder
        from arkiv.types import EntityKey, QueryOptions

        provider = ProviderBuilder().custom(rpc_url).build()
        account = NamedAccount.from_private_key("haven-cli", private_key)
        client = Arkiv(provider=provider, account=account)

        entities = list(
            client.arkiv.query_entities(
                query=f'cid_hash = "{cid_hash}" AND is_encrypted = 1',
                options=QueryOptions(max_results_per_page=5),
            )
        )

        if not entities:
            logger.warning(f"No encrypted entities found for CID hash {cid_hash}")
            return None

        entity = client.arkiv.get_entity(EntityKey(str(entities[0].key)))

        if not entity or not entity.payload:
            return None

        payload = json.loads(entity.payload.decode("utf-8"))
        gate = _gate_from_arkiv_payload(payload)
        if not gate:
            return None
        return EncryptionMetadata(gate=gate)

    except ImportError:
        logger.warning("arkiv package not installed, cannot query Arkiv")
        return None
    except Exception as e:
        logger.warning(f"Failed to query Arkiv for CID hash {cid_hash}: {e}")
        return None
