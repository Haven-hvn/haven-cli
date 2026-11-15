"""Encryption metadata handling for Haven-AOL gates (v1/v3 content gates)."""

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
    """Extract the content gate (v1 or v3) from a 2.0 entity payload.

    2.0 payloads carry the gate under ``gate`` (``cid_gate`` is the
    CID-layer gate, only needed to decrypt the locator itself).
    """
    gate_raw = payload.get("gate")
    if not gate_raw:
        return None
    return parse_gate_metadata(gate_raw)


#: Attribute that carries the serialized JSON entity payload on Rabdi.
ARKIV_PAYLOAD_ATTR = "payload"

#: Entity fields selected by :func:`arkiv_get_entity`.
_ARKIV_ENTITY_SELECT = {
    "key": True,
    "owner": True,
    "creator": True,
    "createdAt": True,
    "updatedAt": True,
    "expiresAt": True,
    "creationFlags": True,
    "attributes": True,
}


def arkiv_query(
    rpc_url: str,
    query: str,
    limit: int = 10,
    select: Optional[Dict[str, bool]] = None,
) -> list[Dict[str, Any]]:
    """Run an ``arkiv_query`` JSON-RPC call and return the result rows.

    Talks to the node directly because the bundled ``arkiv`` SDK still
    speaks the pre-Rabdi query dialect (``includeData``), which Rabdi
    rejects.
    """
    import requests

    if select is None:
        select = {"key": True, "attributes": True}
    resp = requests.post(
        rpc_url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "arkiv_query",
            "params": [query, {"select": select, "limit": limit}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("result", {}).get("data", []) or []


def arkiv_get_entity(rpc_url: str, entity_key: str) -> Optional[Dict[str, Any]]:
    """Fetch one entity by key via ``arkiv_query`` (Rabdi dialect)."""
    rows = arkiv_query(
        rpc_url,
        f"$key = key({entity_key})",
        limit=1,
        select=_ARKIV_ENTITY_SELECT,
    )
    return rows[0] if rows else None


def _arkiv_attr_value(attr: Dict[str, Any]) -> Any:
    """Decode one ``arkiv_query`` attribute ``{type, value}`` to Python."""
    typ = str(attr.get("type", "")).lower()
    value = attr.get("value")
    if value is None:
        return None
    if typ == "str":
        return value
    if typ == "bytes":
        if isinstance(value, str):
            return bytes.fromhex(value[2:] if value.startswith("0x") else value)
        return bytes(value)
    if isinstance(value, str):
        try:
            return int(value, 16) if value.startswith("0x") else int(value)
        except ValueError:
            return value
    return value


def arkiv_row_attributes(row: Dict[str, Any]) -> Dict[str, Any]:
    """Decode an entity row's attributes into a ``{name: value}`` map."""
    return {
        a.get("name"): _arkiv_attr_value(a)
        for a in (row.get("attributes") or [])
        if a.get("name") is not None
    }


def arkiv_row_payload(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Decode the ``payload`` attribute of an entity row to a dict."""
    raw = arkiv_row_attributes(row).get(ARKIV_PAYLOAD_ATTR)
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return json.loads(bytes(raw).decode("utf-8"))


async def load_encryption_metadata_from_arkiv_entity(
    entity_key: str,
    rpc_url: str,
    private_key: str,
) -> Optional[EncryptionMetadata]:
    try:
        row = arkiv_get_entity(rpc_url, entity_key)
        if row is None:
            logger.warning(f"Entity {entity_key} not found")
            return None

        payload = arkiv_row_payload(row)
        if payload is None:
            logger.warning(f"Entity {entity_key} has no payload")
            return None

        gate = _gate_from_arkiv_payload(payload)
        if not gate:
            logger.warning(f"Entity {entity_key} has no content gate")
            return None
        return EncryptionMetadata(gate=gate)

    except Exception as e:
        logger.warning(f"Failed to load encryption metadata from Arkiv entity {entity_key}: {e}")
        return None


async def load_encryption_metadata_from_arkiv_query(
    sha256_ct: str,
    rpc_url: str,
    private_key: str,
) -> Optional[EncryptionMetadata]:
    """Find a gated entity by locator hash and return its content gate.

    Queries ``sha256_ct`` (near-unique) and picks the first hit whose
    payload parses to a valid v1/v3 gate — gate presence is decided
    client-side since 2.0 carries no ``is_encrypted`` flag.
    """
    try:
        rows = arkiv_query(rpc_url, f"sha256_ct = str('{sha256_ct}')", limit=5)
        if not rows:
            logger.warning(f"No entities found for locator hash {sha256_ct}")
            return None

        for row in rows:
            payload = arkiv_row_payload(row)
            if payload is None:
                continue

            gate = _gate_from_arkiv_payload(payload)
            if gate:
                return EncryptionMetadata(gate=gate)

        logger.warning(f"No gated entity found for locator hash {sha256_ct}")
        return None

    except Exception as e:
        logger.warning(f"Failed to query Arkiv for locator hash {sha256_ct}: {e}")
        return None
