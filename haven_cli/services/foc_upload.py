"""
Filecoin Onchain Cloud (FOC) upload verification for haven-cli.

Ensures executeUpload committed the piece (complete + copies) before the
pipeline marks an upload as successful.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class FocUploadError(ValueError):
    """Raised when an upload result does not prove FOC retrievability."""


@dataclass(frozen=True)
class FocUploadVerification:
    """Verified FOC commit metadata from a Synapse / filecoin-pin upload."""

    piece_cid: str
    complete: bool
    copy_count: int
    data_set_id: str
    service_provider: str
    catalog_owner: str
    copies: tuple[Mapping[str, Any], ...]


def _copy_count(copies: Sequence[object] | None) -> int:
    if copies is None:
        return 0
    return len(copies)


def _primary_copy(copies: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return copies[0]


def verify_foc_upload_js_result(
    result: Mapping[str, Any],
    *,
    context: str = "Filecoin upload",
) -> FocUploadVerification:
    """
    Validate JS ``synapse.upload`` payload proves an on-chain FOC commit.

    Raises:
        FocUploadError: If ``complete`` is false or ``copies`` is empty.
    """
    piece_raw = result.get("pieceCid") or result.get("piece_cid") or result.get("dealId")
    piece_cid = str(piece_raw or "").strip()
    if not piece_cid.startswith("bafkzcib"):
        raise FocUploadError(
            f"{context}: missing or invalid pieceCid in upload result "
            f"(got {piece_cid[:32] + '…' if piece_cid else '(empty)'})"
        )

    complete = bool(result.get("complete"))
    if not complete:
        failed = result.get("failedAttempts") or []
        raise FocUploadError(
            f"{context}: FOC upload incomplete (complete=false, "
            f"failedAttempts={len(failed) if isinstance(failed, list) else '?'})"
        )

    copies_raw = result.get("copies")
    if not isinstance(copies_raw, list) or len(copies_raw) == 0:
        raise FocUploadError(
            f"{context}: FOC upload returned no copies — piece was not committed to warm storage"
        )

    copies: tuple[Mapping[str, Any], ...] = tuple(
        c for c in copies_raw if isinstance(c, dict)
    )
    if not copies:
        raise FocUploadError(f"{context}: FOC copies list contained no valid copy objects")

    primary = _primary_copy(copies)
    data_set_id = str(
        result.get("dataSetId")
        or primary.get("dataSetId")
        or primary.get("data_set_id")
        or ""
    ).strip()
    service_provider = str(
        result.get("serviceProvider")
        or primary.get("serviceProvider")
        or primary.get("providerId")
        or primary.get("provider")
        or ""
    ).strip()
    catalog_owner = str(result.get("catalogOwner") or result.get("catalog_owner") or "").strip()

    if not data_set_id:
        raise FocUploadError(f"{context}: upload result missing dataSetId from FOC copies")

    return FocUploadVerification(
        piece_cid=piece_cid,
        complete=True,
        copy_count=_copy_count(copies),
        data_set_id=data_set_id,
        service_provider=service_provider,
        catalog_owner=catalog_owner,
        copies=copies,
    )
