"""Dataset-per-epoch lifecycle bookkeeping (plan item 12).

Storage groups uploads into one dataset per AOL epoch (30 days)
instead of one rolling dataset forever.  This module owns:

- the **epoch map** (``dataset_epochs.json`` in ``data_dir``):
  ``{epoch: dataset_id}`` learned from completed uploads.  The
  upload step reads it to pick the right dataset for the video's
  ``gate_epoch`` and, on the first upload of a new epoch, asks the
  wrapper for a *fresh* dataset instead of reusing the old epoch's.
- **accounting** for termination gates (item 10's rule, reused for
  any epoch dataset): which pieces live in a dataset and which are
  sole-copy (present in no other dataset).  Termination is an
  explicit operator action (``haven dataset terminate``) and is
  *never* automatic — terminating ends the seed for unpopular content
  that nobody has repinned yet.
- **per-dataset views** for ``haven dataset list``: chain-side rows
  merged with local piece counts and epoch labels.

Arkiv BTL (~4 weeks) stays rolling and independent; nothing here
synchronizes all expiries to one timestamp (cliff risk).  Alignment
is by epoch label only.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Epoch map file name inside ``data_dir``.
EPOCH_MAP_FILE = "dataset_epochs.json"


# ── Epoch map ────────────────────────────────────────────────────────────────


def epoch_map_path(data_dir: Any) -> Path:
    return Path(data_dir) / EPOCH_MAP_FILE


def load_epoch_map(data_dir: Any) -> dict[int, str]:
    """Read ``{epoch: dataset_id}`` (empty dict when missing/corrupt)."""
    path = epoch_map_path(data_dir)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s (treating as empty map)", path, exc)
        return {}
    result: dict[int, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                result[int(key)] = str(value)
            except (TypeError, ValueError):
                continue
    return result


def record_epoch_dataset(data_dir: Any, epoch: int | None, dataset_id: str | None) -> None:
    """Learn ``epoch -> dataset_id`` after a successful upload (atomic)."""
    if epoch is None or dataset_id is None:
        return
    path = epoch_map_path(data_dir)
    mapping = load_epoch_map(data_dir)
    if mapping.get(int(epoch)) == str(dataset_id):
        return
    mapping[int(epoch)] = str(dataset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({str(k): v for k, v in sorted(mapping.items())}, indent=2))
    os.replace(tmp, path)


@dataclass(frozen=True)
class EpochDatasetChoice:
    """Which dataset one upload should land in."""

    epoch: int | None
    dataset_id: str | None = None  # preferred live dataset (wrapper reuses it)
    fresh: bool = False  # force a new dataset (first upload of a new epoch)


def choose_epoch_dataset(
    data_dir: Any,
    epoch: int | None,
    mapping: dict[int, str] | None = None,
) -> EpochDatasetChoice:
    """Pick the dataset for *epoch* against the recorded map.

    - Known epoch → reuse its dataset.
    - New epoch while other epochs exist → the first upload of the new
      epoch gets a fresh dataset (the wrapper must not reuse the old
      epoch's set).
    - No map yet (first-ever epoch) → normal wrapper reuse; the result
      is recorded after the upload.
    """
    if epoch is None:
        return EpochDatasetChoice(epoch=None)
    mapping = load_epoch_map(data_dir) if mapping is None else mapping
    if epoch in mapping:
        return EpochDatasetChoice(epoch=epoch, dataset_id=mapping[epoch])
    if mapping:
        return EpochDatasetChoice(epoch=epoch, fresh=True)
    return EpochDatasetChoice(epoch=epoch)


def epoch_from_gate(gate: dict[str, Any] | None) -> int | None:
    """``gate_epoch`` from verbatim gate metadata (never wall-clock)."""
    if not gate:
        return None
    raw = gate.get("epoch")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _lenient_epoch(serialized: str | None) -> int | None:
    """Gate epoch from a stored metadata JSON blob (parse or narrow read)."""
    if not serialized:
        return None
    from haven_cli.crypto.gate_metadata import parse_gate_metadata

    parsed = parse_gate_metadata(serialized)
    epoch = epoch_from_gate(parsed)
    if epoch is not None:
        return epoch
    # The strict v1/v3 validators reject partial records; the epoch
    # fact used for dataset grouping lives at the top level either way.
    try:
        raw = json.loads(serialized)
        return epoch_from_gate(raw if isinstance(raw, dict) else None)
    except (json.JSONDecodeError, TypeError):
        return None


# ── Accounting (termination gate) ────────────────────────────────────────────


@dataclass
class DatasetAccounting:
    """What ``haven dataset terminate`` must review before terminating."""

    dataset_id: str
    pieces: list[str] = field(default_factory=list)
    sole_copy_pieces: list[str] = field(default_factory=list)
    other_dataset_count: int = 0
    epochs: list[int] = field(default_factory=list)

    @property
    def safe_to_terminate(self) -> bool:
        """True only when nothing is sole-copy in this dataset."""
        return not self.sole_copy_pieces


def account_dataset(dataset_id: str) -> DatasetAccounting:
    """List pieces in *dataset_id* and flag sole-copy pieces.

    A piece is sole-copy when no *other* dataset in the local DB also
    holds it.  Sole-copy pieces must be re-uploaded elsewhere first;
    terminating with them would silently end their seed.
    """
    from haven_cli.database.connection import get_db_session
    from haven_cli.database.models import Video

    with get_db_session() as session:
        rows = (
            session.query(Video)
            .filter(Video.piece_cid.is_not(None), Video.piece_cid != "")
            .all()
        )
        # Aggregate inside the session (detached reads would fail).
        by_dataset: dict[str, set] = {}
        epochs_by_dataset: dict[str, set] = {}
        for row in rows:
            ds = row.filecoin_data_set_id
            if ds is None:
                continue
            ds = str(ds)
            by_dataset.setdefault(ds, set()).add(row.piece_cid)
            epoch = _lenient_epoch(row.encryption_metadata)
            if epoch is not None:
                epochs_by_dataset.setdefault(ds, set()).add(epoch)

    own = by_dataset.get(str(dataset_id), set())
    others: set = set()
    for ds, pieces in by_dataset.items():
        if ds != str(dataset_id):
            others |= pieces
    return DatasetAccounting(
        dataset_id=str(dataset_id),
        pieces=sorted(own),
        sole_copy_pieces=sorted(own - others),
        other_dataset_count=len([d for d in by_dataset if d != str(dataset_id)]),
        epochs=sorted(epochs_by_dataset.get(str(dataset_id), set())),
    )


# ── Per-dataset views (``haven dataset list``) ───────────────────────────────


@dataclass
class DatasetView:
    dataset_id: str
    provider_id: str | None = None
    is_live: bool | None = None
    piece_count: int = 0
    epochs: list[int] = field(default_factory=list)
    last_upload: datetime | None = None


def list_local_datasets() -> list[DatasetView]:
    """Per-dataset piece counts + epoch labels from the local DB."""
    from haven_cli.database.connection import get_db_session
    from haven_cli.database.models import Video

    with get_db_session() as session:
        rows = (
            session.query(Video)
            .filter(Video.piece_cid.is_not(None), Video.piece_cid != "")
            .order_by(Video.filecoin_uploaded_at.desc())
            .all()
        )
        # Aggregate inside the session: attribute reads after close
        # would hit DetachedInstanceError on lazy refresh.
        views: dict[str, DatasetView] = {}
        for row in rows:
            ds = row.filecoin_data_set_id
            if ds is None:
                continue
            view = views.setdefault(str(ds), DatasetView(dataset_id=str(ds)))
            view.piece_count += 1
            if view.last_upload is None and row.filecoin_uploaded_at is not None:
                view.last_upload = row.filecoin_uploaded_at
            epoch = _lenient_epoch(row.encryption_metadata)
            if epoch is not None and epoch not in view.epochs:
                view.epochs.append(epoch)
        for view in views.values():
            view.epochs.sort()
    return sorted(views.values(), key=lambda v: v.dataset_id)
