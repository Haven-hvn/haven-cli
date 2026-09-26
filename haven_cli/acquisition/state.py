"""Persistent state for acquisitions that outlive one archive() call.

Torrent and Usenet downloads can take far longer than a scheduler tick.
The archiver submits once, records the client handle here, and on later
runs polls instead of submitting again. Failures are recorded with a
backoff so a broken release is not retried every tick forever.

The store is a single JSON file written atomically; an in-process lock
serializes writers (one daemon process per data directory is assumed).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

STATE_VERSION = 1


@dataclass
class AcquisitionRecord:
    key: str
    backend: str = ""
    handle: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | failed | done
    attempts: int = 0
    last_error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    next_attempt_at: float = 0.0
    #: Serialized source so pending items can be re-surfaced by discovery.
    source: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AcquisitionRecord:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


_LOCKS: dict[str, asyncio.Lock] = {}


class AcquisitionStore:
    """JSON-backed map of ``key → AcquisitionRecord``."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = _LOCKS.setdefault(str(self._path.resolve()), asyncio.Lock())

    @property
    def path(self) -> Path:
        return self._path

    def _read(self) -> dict[str, AcquisitionRecord]:
        try:
            raw = json.loads(self._path.read_text("utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            # Corrupt state must not wedge the archiver; keep a copy for debugging.
            with contextlib.suppress(OSError):
                self._path.replace(self._path.with_suffix(f".corrupt-{int(time.time())}"))
            return {}
        records = raw.get("records", {}) if isinstance(raw, dict) else {}
        return {
            key: AcquisitionRecord.from_dict(value)
            for key, value in records.items()
            if isinstance(value, dict)
        }

    def _write(self, records: dict[str, AcquisitionRecord]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(f".tmp-{os.getpid()}")
        payload = {"version": STATE_VERSION, "records": {k: asdict(v) for k, v in records.items()}}
        # Handles may include client-side secrets (e.g. private magnet URIs):
        # keep the file owner-only.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=1, sort_keys=True))
        os.replace(tmp, self._path)

    async def get(self, key: str) -> AcquisitionRecord | None:
        async with self._lock:
            return self._read().get(key)

    async def all(self) -> list[AcquisitionRecord]:
        async with self._lock:
            return list(self._read().values())

    async def put(self, record: AcquisitionRecord) -> None:
        async with self._lock:
            records = self._read()
            record.updated_at = time.time()
            records[record.key] = record
            self._write(records)

    async def remove(self, key: str) -> None:
        async with self._lock:
            records = self._read()
            if records.pop(key, None) is not None:
                self._write(records)

    async def record_failure(
        self,
        key: str,
        error: str,
        *,
        permanent: bool,
        max_attempts: int,
        base_backoff: float,
        source: dict[str, Any] | None = None,
    ) -> AcquisitionRecord:
        """Count a failed attempt; mark ``failed`` when permanent or exhausted.

        Backoff doubles per attempt (``base_backoff * 2**(attempts-1)``),
        capped at one day.
        """
        async with self._lock:
            records = self._read()
            record = records.get(key) or AcquisitionRecord(key=key)
            record.attempts += 1
            record.last_error = error[:500]
            record.backend = ""
            record.handle = {}
            if source is not None:
                record.source = source
            if permanent or record.attempts >= max_attempts:
                record.status = "failed"
                record.next_attempt_at = 0.0
            else:
                record.status = "retry"
                record.next_attempt_at = time.time() + min(
                    86400.0, base_backoff * 2 ** (record.attempts - 1)
                )
            record.updated_at = time.time()
            records[key] = record
            self._write(records)
            return record

    async def prune(self, *, done_older_than: float, failed_older_than: float) -> int:
        """Drop old ``done``/``failed`` records; returns how many were removed."""
        now = time.time()
        async with self._lock:
            records = self._read()
            keep = {
                k: r
                for k, r in records.items()
                if not (
                    (r.status == "done" and now - r.updated_at > done_older_than)
                    or (
                        r.status == "failed"
                        and failed_older_than > 0
                        and now - r.updated_at > failed_older_than
                    )
                )
            }
            removed = len(records) - len(keep)
            if removed:
                self._write(keep)
            return removed
