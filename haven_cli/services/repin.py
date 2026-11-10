"""Repin service — the popularity mechanism (plan item 11).

Haven CLI is the initial seed only; retention is *repinning* by others
(re)pinners.  ``repin`` takes an Arkiv entity ID or a Filecoin piece
CID, fetches the (ciphertext) content, uploads it to the caller's own
dataset (``copies=1``, dataset reuse as in the B1 fix), and publishes
an Arkiv entity pointing at the new copy with the original
``gate_epoch`` preserved verbatim.

Invariants:

- **No re-encryption.**  The fetched bytes are uploaded untouched, so
  the original AOL gate metadata (including ``epoch``) stays valid.
  This depends on plan item 5: old-epoch VetKD keys must remain
  releasable.  If they ever lapse, repinning would need re-encryption
  into the current epoch instead — the operator-visible failure is a
  decrypt failure at read time, not a silent repin bug.
- **No original torrent required.**  The only inputs are the piece
  CID (or the entity that carries it) plus gate metadata fetched from
  the local DB or the Arkiv entity itself.
- **Fail closed on the new copy.**  As with first-seed uploads
  (item 9), the new piece must prove retrievability via
  ``synapse.getStatus`` before the entity is published; otherwise the
  repin fails and no broken entity is left behind.
- **Fresh entity.**  Publishing always *creates* a new Arkiv entity
  (``create_only=True``) owned by the repinner — it never patches the
  original owner's entity, so repins from a second wallet work.

Live acceptance (repin from a second wallet + gated decrypt of the new
copy) requires mainnet and a second wallet; the runnable paths here
are ``--dry-run`` (resolve + plan, no network writes) and unit tests
with fake JS/Arkiv clients.
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Name of the temp ciphertext file inside the repin output directory.
_REPIN_FILE_NAME = "repin-ciphertext.car"


@dataclass(frozen=True)
class RepinTarget:
    """Everything needed to repin one piece: locator + gate + display data."""

    piece_cid: str
    root_cid: str | None = None
    gate: dict[str, Any] | None = None  # verbatim gate metadata (v3)
    title: str = ""
    mime_type: str = "video/mp4"
    file_size: int = 0
    dataset_id: str | None = None
    source: str = ""  # where the target was resolved from


@dataclass
class RepinResult:
    """Outcome of one repin (or a dry-run plan)."""

    target: RepinTarget
    downloaded_path: str | None = None
    piece_cid: str | None = None
    root_cid: str | None = None
    dataset_id: str | None = None
    entity_key: str | None = None
    transaction_hash: str | None = None
    retrievable: bool = False
    dry_run: bool = False
    error: str = ""


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


# ── Resolution ───────────────────────────────────────────────────────────────


def resolve_from_db(target: str) -> RepinTarget | None:
    """Resolve a piece CID / root CID / Arkiv key against the local DB."""
    from haven_cli.crypto.gate_metadata import parse_gate_metadata
    from haven_cli.database.connection import get_db_session
    from haven_cli.database.models import Video

    with get_db_session() as session:
        row = (
            session.query(Video)
            .filter(
                (Video.piece_cid == target)
                | (Video.cid == target)
                | (Video.arkiv_entity_key == target)
            )
            .first()
        )
        if row is None:
            return None
        gate = parse_gate_metadata(row.encryption_metadata) if row.encryption_metadata else None
        return RepinTarget(
            piece_cid=row.piece_cid or target,
            root_cid=row.cid,
            gate=gate,
            title=row.title or "",
            mime_type=row.mime_type or "video/mp4",
            file_size=row.file_size or 0,
            dataset_id=row.filecoin_data_set_id,
            source="local DB",
        )


def resolve_from_entity(entity_key: str, arkiv_config: Any = None) -> RepinTarget:
    """Resolve an Arkiv entity into a repin target (raises on failure)."""
    from haven_cli.crypto.gate_metadata import parse_gate_metadata
    from haven_cli.services.arkiv_sync import build_arkiv_config

    arkiv_config = arkiv_config or build_arkiv_config(enabled=True)
    try:
        from arkiv import Arkiv
        from arkiv.account import NamedAccount
        from arkiv.provider import ProviderBuilder
        from arkiv.types import (
            ATTRIBUTES,
            CONTENT_TYPE,
            CREATED_AT,
            KEY,
            OWNER,
            PAYLOAD,
            EntityKey,
        )
    except ImportError as exc:
        raise RuntimeError(
            "arkiv package is required for entity resolution. Install: pip install arkiv"
        ) from exc
    try:
        from arkiv.types import UPDATED_AT  # type: ignore[attr-defined]
    except ImportError:
        UPDATED_AT = None  # older SDKs lack this flag; harmless to omit

    provider = ProviderBuilder().custom(arkiv_config.rpc_url).build()
    account = NamedAccount.from_private_key("haven-cli", arkiv_config.private_key)
    attributes = KEY | ATTRIBUTES | PAYLOAD | CONTENT_TYPE | OWNER | CREATED_AT
    if UPDATED_AT is not None:
        attributes = attributes | UPDATED_AT
    # arkiv stubs disagree with the runtime (same as cli/entity.py).
    client = Arkiv(provider=provider, account=account)  # type: ignore[arg-type]
    entity = client.arkiv.get_entity(  # type: ignore[call-arg]
        EntityKey(entity_key),  # type: ignore[arg-type]
        attributes=attributes,
    )
    if not entity:
        raise ValueError(f"Arkiv entity not found: {entity_key}")

    payload: dict[str, Any] = {}
    raw_payload = getattr(entity, "payload", None)
    if raw_payload:
        try:
            payload = json.loads(raw_payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Arkiv entity payload is not JSON: {exc}") from exc

    piece = payload.get("piece") or payload.get("fcid")
    if not piece:
        raise ValueError(
            f"Arkiv entity {entity_key} carries no Filecoin locator "
            "(expected 'piece' or 'fcid' in payload)"
        )
    gate_raw = payload.get("gate")
    gate = parse_gate_metadata(gate_raw) if gate_raw else None
    if gate is None and payload.get("gate") is not None:
        raise ValueError(
            f"Arkiv entity {entity_key} gate metadata did not parse "
            "(refusing to repin without a preserved gate)"
        )
    entity_attributes = dict(getattr(entity, "attributes", {}) or {})
    title = entity_attributes.get("title") or payload.get("name") or ""
    return RepinTarget(
        piece_cid=str(piece),
        root_cid=None,
        gate=gate,
        title=str(title),
        file_size=int(payload.get("size") or 0),
        source=f"Arkiv entity {entity_key}",
    )


def resolve_target(
    target: str,
    *,
    db_lookup: Callable[[str], RepinTarget | None] = resolve_from_db,
    entity_lookup: Callable[[str], RepinTarget] | None = None,
) -> RepinTarget:
    """Resolve either a DB-known CID/key or an Arkiv entity key."""
    found = db_lookup(target)
    if found is not None:
        return found
    # Not in the local DB — treat the input as an Arkiv entity key.
    lookup = entity_lookup or (lambda key: resolve_from_entity(key))
    try:
        resolved = lookup(target)
    except Exception as exc:
        raise ValueError(
            f"Cannot repin {target!r}: not in the local DB and Arkiv "
            f"entity lookup failed ({exc})"
        ) from exc
    if resolved is None:
        raise ValueError(
            f"Cannot repin {target!r}: not found in the local DB and not a "
            "resolvable Arkiv entity (is it a 0x… entity key, or a CID that "
            "has never been seeded by this wallet?)"
        )
    return resolved


# ── Remote operations (defaults hit the live bridge/chain) ──────────────────


async def js_download(piece_cid: str, output_path: str) -> dict[str, Any]:
    """Fetch ciphertext bytes for ``piece_cid`` via ``synapse.download``."""
    from haven_cli.js_runtime.manager import JSBridgeManager

    downloaded: dict[str, Any] = await JSBridgeManager.get_instance().call_with_retry(
        "synapse.download",
        {"pieceCid": piece_cid, "cid": piece_cid, "outputPath": output_path},
        timeout=600.0,
        max_retries=1,
    )
    return downloaded


async def js_upload(file_path: str) -> dict[str, Any]:
    """Upload ``file_path`` as one new copy (wrapper reuses the dataset)."""
    from haven_cli.js_runtime.manager import JSBridgeManager

    result: dict[str, Any] = await JSBridgeManager.get_instance().call_with_retry(
        "synapse.upload",
        {"filePath": file_path, "metadata": {"encrypted": True, "dataSetId": None}},
        timeout=6000.0,
        max_retries=1,
    )
    return result


async def js_get_status(piece_cid: str, root_cid: str | None = None) -> dict[str, Any]:
    """Probe ``synapse.getStatus`` for a fresh piece (fail-closed check)."""
    from haven_cli.js_runtime.manager import JSBridgeManager

    params: dict[str, Any] = {"pieceCid": piece_cid}
    if root_cid:
        params["cid"] = root_cid
    status: dict[str, Any] = await JSBridgeManager.get_instance().call_with_retry(
        "synapse.getStatus", params, max_retries=1
    )
    return status


def publish_repin_entity(target: RepinTarget, piece_cid: str, root_cid: str) -> dict[str, Any]:
    """Create a fresh Arkiv entity for the repinned copy (verbatim gate)."""
    from haven_cli.pipeline.context import (
        EncryptionMetadata,
        PipelineContext,
        UploadResult,
        VideoMetadata,
    )
    from haven_cli.services.arkiv_sync import ArkivSyncClient, build_arkiv_config

    arkiv_config = build_arkiv_config(enabled=True)
    if not arkiv_config.enabled:
        raise ValueError(
            "Arkiv sync is disabled (needs HAVEN_PRIVATE_KEY and "
            "ARKIV_SYNC_ENABLED); cannot publish the repin entity"
        )
    context = PipelineContext(
        source_path=Path(f"/repin/{piece_cid[:16]}"),
        options={},
    )
    context.video_metadata = VideoMetadata(
        path=f"/repin/{piece_cid[:16]}",
        title=target.title,
        file_size=target.file_size,
        mime_type=target.mime_type,
    )
    context.encryption_metadata = EncryptionMetadata(gate=dict(target.gate or {}))
    context.upload_result = UploadResult(
        video_path=f"/repin/{piece_cid[:16]}",
        root_cid=root_cid,
        piece_cid=piece_cid,
    )
    result = ArkivSyncClient(arkiv_config).sync_context(context, create_only=True)
    if result is None:
        raise ValueError("Arkiv publish returned nothing (sync disabled?)")
    return result


# ── Orchestration ────────────────────────────────────────────────────────────


async def repin(
    target: str,
    *,
    output_dir: Path | None = None,
    dry_run: bool = False,
    db_lookup: Callable[[str], RepinTarget | None] = resolve_from_db,
    entity_lookup: Callable[[str], RepinTarget] | None = None,
    downloader: Callable[[str, str], Any] = js_download,
    uploader: Callable[[str], Any] = js_upload,
    status_probe: Callable[[str, str | None], Any] = js_get_status,
    publisher: Callable[[RepinTarget, str, str], Any] = publish_repin_entity,
) -> RepinResult:
    """Repin *target* into the caller's own dataset + fresh Arkiv entity.

    ``dry_run=True`` resolves the target and returns the plan without
    touching the network or the chain.
    """
    resolved = resolve_target(target, db_lookup=db_lookup, entity_lookup=entity_lookup)
    output_dir = Path(output_dir) if output_dir is not None else Path("downloads/repin")
    output_dir.mkdir(parents=True, exist_ok=True)

    result = RepinResult(target=resolved, dry_run=dry_run)
    if dry_run:
        logger.info(
            "Repin plan (dry-run): piece=%s dataset=%s gate_epoch=%s title=%r",
            resolved.piece_cid,
            resolved.dataset_id,
            (resolved.gate or {}).get("epoch"),
            resolved.title,
        )
        return result

    if not resolved.gate:
        return RepinResult(
            target=resolved,
            error=(
                "resolved target carries no gate metadata; refusing to "
                "repin clear content through the gated path"
            ),
        )

    downloaded = output_dir / _REPIN_FILE_NAME
    await _maybe_await(downloader(resolved.piece_cid, str(downloaded)))
    if not downloaded.exists() or downloaded.stat().st_size == 0:
        return RepinResult(target=resolved, error=f"download produced no bytes: {downloaded}")
    result.downloaded_path = str(downloaded)

    uploaded = await _maybe_await(uploader(str(downloaded))) or {}
    new_piece = uploaded.get("pieceCid") or uploaded.get("piece_cid") or ""
    new_root = uploaded.get("cid") or uploaded.get("rootCid") or ""
    if not new_piece:
        return RepinResult(
            target=resolved,
            downloaded_path=str(downloaded),
            error="upload returned no piece CID",
        )
    result.piece_cid = new_piece
    result.root_cid = new_root
    result.dataset_id = (
        str(uploaded.get("dataSetId"))
        if uploaded.get("dataSetId") is not None
        else resolved.dataset_id
    )

    # Fail closed, exactly like first-seed (item 9): never publish an
    # entity for a copy the provider cannot serve.
    status = await _maybe_await(status_probe(new_piece, new_root or None)) or {}
    retrievable = bool(status.get("retrievable")) or status.get("status") in (
        "active",
        "confirmed",
    )
    result.retrievable = retrievable
    if not retrievable:
        return RepinResult(
            target=resolved,
            downloaded_path=str(downloaded),
            piece_cid=new_piece,
            root_cid=new_root,
            error=(
                "new piece is not retrievable (status="
                f"{status.get('status')}); refusing to publish a repin "
                "entity for a dead copy"
            ),
        )

    published = await _maybe_await(publisher(resolved, new_piece, new_root)) or {}
    result.entity_key = published.get("entity_key")
    result.transaction_hash = published.get("transaction_hash")
    logger.info(
        "Repin complete: piece=%s entity=%s tx=%s (gate_epoch=%s preserved)",
        new_piece,
        result.entity_key,
        result.transaction_hash,
        (resolved.gate or {}).get("epoch"),
    )
    return result
