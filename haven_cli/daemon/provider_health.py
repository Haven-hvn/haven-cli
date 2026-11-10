"""Periodic provider/dataset health for the daemon (partial plan item 9).

Item 9's core fix already fails an upload whose piece never becomes
retrievable.  What was still missing is a *background* check: is the
dataset this wallet seeds (dataset 52242, provider 9, pinned via
``synapse_provider_ids=[9]``) still serving its pieces — independent of
whether an upload happens to be running right now?

This module answers that once per ``pipeline.provider_health_interval``
seconds (default 1 hour, 0 disables).  Each check:

1. reads the most recent seeded piece from the local database
   (``videos.filecoin_data_set_id`` + ``piece_cid``), and
2. probes it through the same ``synapse.getStatus`` path the upload
   step uses (:mod:`haven_cli.pipeline.steps.upload_step`), and
3. logs one line — ``Provider health: dataset=… providers=[…] status=live …``
   at INFO, or at ERROR when the dataset is dead/degraded — so an
   operator watching ``daemon.log`` sees a dead provider without
   waiting for the next upload to fail.

Failures (bridge down, RPC error, empty DB) never kill the daemon:
they are reported as ``status=error``/``unknown`` and the loop keeps
running.  The check is dependency-injected (:class:`HealthDeps`) so
tests exercise every status transition without a bridge or database.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

#: Statuses produced by :func:`run_provider_health_check`.
STATUS_LIVE = "live"
STATUS_DEGRADED = "degraded"
STATUS_UNKNOWN = "unknown"
STATUS_ERROR = "error"


@dataclass(frozen=True)
class LatestPiece:
    """The most recently seeded piece, straight from the local DB."""

    piece_cid: str
    root_cid: str | None = None
    dataset_id: str | None = None
    provider: str | None = None
    uploaded_at: datetime | None = None


@dataclass(frozen=True)
class ProviderHealthStatus:
    """One background provider/dataset health verdict."""

    status: str  # live | degraded | unknown | error
    dataset_id: str | None
    provider_ids: tuple[int, ...] = ()
    piece_cid: str | None = None
    retrievable: bool | None = None
    detail: str = ""
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def format_health_line(status: ProviderHealthStatus) -> str:
    """Render the periodic log line for a health verdict."""
    providers = list(status.provider_ids)
    piece = status.piece_cid or "none"
    retrievable = status.retrievable
    return (
        f"Provider health: dataset={status.dataset_id or 'unknown'} "
        f"providers={providers} status={status.status} "
        f"piece={piece} retrievable={retrievable} "
        f"checked={status.checked_at.isoformat()} "
        f"detail={status.detail}"
    )


def log_health(status: ProviderHealthStatus) -> None:
    """Log one health line (ERROR for dead/error so it survives filtering)."""
    line = format_health_line(status)
    if status.status in (STATUS_DEGRADED, STATUS_ERROR):
        logger.error("%s", line)
    else:
        logger.info("%s", line)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


# ── Real dependencies (defaults; tests inject fakes) ────────────────────────


def db_latest_piece() -> LatestPiece | None:
    """Read the newest seeded piece from the local ``videos`` table."""
    from haven_cli.database.connection import get_db_session
    from haven_cli.database.models import Video

    with get_db_session() as session:
        row = (
            session.query(Video)
            .filter(Video.piece_cid.is_not(None), Video.piece_cid != "")
            .order_by(Video.filecoin_uploaded_at.desc(), Video.updated_at.desc())
            .first()
        )
        if row is None:
            return None
        return LatestPiece(
            piece_cid=row.piece_cid or "",
            root_cid=row.cid,
            dataset_id=row.filecoin_data_set_id,
            uploaded_at=row.filecoin_uploaded_at,
        )


async def js_get_status(params: dict[str, Any]) -> dict[str, Any]:
    """Probe ``synapse.getStatus`` through the JS bridge (same as upload)."""
    from haven_cli.js_runtime.manager import JSBridgeManager

    result: dict[str, Any] = await JSBridgeManager.get_instance().call_with_retry(
        "synapse.getStatus", params, max_retries=1
    )
    return result


@dataclass
class HealthDeps:
    """Injectable dependencies for :func:`run_provider_health_check`."""

    lookup_latest: Callable[[], Any] = db_latest_piece
    probe: Callable[[dict[str, Any]], Any] = js_get_status
    provider_ids: tuple[int, ...] = ()


async def run_provider_health_check(deps: HealthDeps) -> ProviderHealthStatus:
    """Run one health check.  Never raises — every failure becomes a status."""
    try:
        piece = await _maybe_await(deps.lookup_latest())
    except Exception as exc:  # DB down etc. — report, don't crash the daemon
        return ProviderHealthStatus(
            status=STATUS_ERROR,
            dataset_id=None,
            provider_ids=tuple(deps.provider_ids),
            detail=f"db lookup failed: {exc}",
        )

    if piece is None or not getattr(piece, "piece_cid", None):
        return ProviderHealthStatus(
            status=STATUS_UNKNOWN,
            dataset_id=None,
            provider_ids=tuple(deps.provider_ids),
            detail="no seeded piece in local DB yet",
        )

    params: dict[str, Any] = {"pieceCid": piece.piece_cid}
    if piece.root_cid:
        params["cid"] = piece.root_cid
    try:
        payload = await _maybe_await(deps.probe(params))
    except Exception as exc:
        return ProviderHealthStatus(
            status=STATUS_ERROR,
            dataset_id=piece.dataset_id,
            provider_ids=tuple(deps.provider_ids),
            piece_cid=piece.piece_cid,
            detail=f"getStatus probe failed: {exc}",
        )

    payload = payload or {}
    retrievable = bool(payload.get("retrievable"))
    state = str(payload.get("status") or "unknown")
    if retrievable or state in ("active", "confirmed"):
        return ProviderHealthStatus(
            status=STATUS_LIVE,
            dataset_id=piece.dataset_id,
            provider_ids=tuple(deps.provider_ids),
            piece_cid=piece.piece_cid,
            retrievable=True,
            detail=f"piece retrievable (state={state})",
        )
    return ProviderHealthStatus(
        status=STATUS_DEGRADED,
        dataset_id=piece.dataset_id,
        provider_ids=tuple(deps.provider_ids),
        piece_cid=piece.piece_cid,
        retrievable=False,
        detail=f"piece NOT retrievable (state={state}); dataset/provider may be dead",
    )


async def provider_health_loop(
    deps: HealthDeps,
    *,
    interval_seconds: float,
    stop_event: asyncio.Event | None = None,
    initial_delay: float = 60.0,
) -> None:
    """Background loop: check, log one line, sleep, repeat until stopped."""
    if interval_seconds <= 0:
        logger.info("Provider health loop disabled (interval=0)")
        return
    if stop_event is not None:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=initial_delay)
            return
        except TimeoutError:
            pass
    else:
        await asyncio.sleep(initial_delay)

    while stop_event is None or not stop_event.is_set():
        status = await run_provider_health_check(deps)
        log_health(status)
        if stop_event is not None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                return
            except TimeoutError:
                continue
        await asyncio.sleep(interval_seconds)


def provider_ids_from_config(config: Any) -> tuple[int, ...]:
    """Best-effort read of ``pipeline.synapse_provider_ids`` (config or dict)."""
    try:
        pipeline = getattr(config, "pipeline", None)
        if pipeline is not None:
            ids = getattr(pipeline, "synapse_provider_ids", []) or []
        elif isinstance(config, dict):
            ids = (config.get("pipeline") or {}).get("synapse_provider_ids", []) or []
        else:
            ids = []
        return tuple(int(i) for i in ids)
    except (TypeError, ValueError):
        return ()


def health_interval_from_config(config: Any) -> float:
    """``pipeline.provider_health_interval`` seconds (default 3600, 0 = off)."""
    try:
        pipeline = getattr(config, "pipeline", None)
        if pipeline is not None:
            return float(getattr(pipeline, "provider_health_interval", 3600))
        if isinstance(config, dict):
            return float((config.get("pipeline") or {}).get("provider_health_interval", 3600))
    except (TypeError, ValueError):
        pass
    return 3600.0
