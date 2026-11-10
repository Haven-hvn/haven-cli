"""In-memory VetKey cache for Haven-AOL Protocol v3.

This module implements the corpus-scoped VetKey cache pinned by the v3
proposal (§6.3) and ``tasking/README.md`` cross-stack contracts table:

  * Cache key:   tuple ``(chain, token_address, threshold, epoch)``.
  * Cache value: the canister-returned VetKey bundle for that
                 ``(chain, token, threshold, epoch)`` bucket — encrypted
                 derived key + per-derivation verification key. The brief's
                 phrase "VetKey bytes" is loose; the accurate object the
                 decryptor needs to avoid a repeat ``requestDecryptionKeyV3``
                 call is this bundle, which we wrap in :class:`CachedVetKey`.
                 (See proposal §6.3 and the v1 ``DecryptionKeyResponse``
                 in ``haven_cli/services/haven_aol_icp.py`` for the
                 corresponding v1 shape.)
  * Lifetime:    the process singleton persists to a 0600 JSON file in
                 ``$HAVEN_CACHE_DIR`` (default
                 ``~/.config/haven/vetkeys.json``) so the bundle survives
                 restarts — one canister call per epoch, not per run.
                 Bare instances (tests) pass ``persist_path=None`` and
                 stay in-memory only. No TTL, no eviction. Encrypted
                 derived keys are ciphertext; confidentiality of the
                 cache file is defense-in-depth, not a boundary.
  * Cache lookup MUST use ``metadata.epoch`` — never ``current_epoch()``.
    This is the v3 mitigation against scenario (D) in
    ``docs/corpus-gate-proposal-v3.md`` §1.7: an old-epoch ciphertext must
    remain decryptable after the wall clock has advanced past its
    issuing epoch, as long as the canister still considers the wallet
    a member. The dedicated unit test
    ``tests/crypto/test_gate_key_cache.py::test_lookup_independent_of_current_epoch``
    pins this.

Thread safety: protected by a single :class:`threading.Lock`. The CLI does
not currently parallelise canister calls within one process, but the daemon
mode may, and the cache must be safe against that.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

#: Cache key tuple shape. Pinned by ``tasking/README.md``. ``threshold`` is
#: an ``int`` (the int form of the decimal-string ``metadata.threshold``).
#: ``epoch`` is an ``int`` taken straight from ``metadata.epoch``.
CacheKey = Tuple[str, str, int, int]


def _default_persist_path() -> Path:
    """Persistence file for the VetKey singleton (0600 JSON)."""
    cache_dir = os.environ.get("HAVEN_CACHE_DIR") or str(
        Path.home() / ".config" / "haven"
    )
    return Path(cache_dir) / "vetkeys.json"


@dataclass(frozen=True)
class CachedVetKey:
    """In-memory VetKey bundle returned by ``requestDecryptionKeyV3``.

    The bundle pairs the canister-returned encrypted derived key with its
    per-derivation verification key. The decryptor performs the local
    transport-unwrap + IBE-decrypt chain against this bundle for every CID
    in the same ``(chain, token, threshold, epoch)`` bucket, which is the
    whole point of v3: one canister round-trip per epoch, N IBE-decrypts
    per file.

    Fields are :class:`bytes` so the values are immutable — a caller that
    receives a :class:`CachedVetKey` cannot mutate the cache by accident.
    """

    encrypted_key: bytes
    verification_key: bytes


class GateKeyCache:
    """In-memory ``(chain, token, threshold, epoch) → CachedVetKey`` cache.

    Pinned interface (``tasking/README.md`` cross-stack contracts table):

      * :meth:`get` — look up a previously-cached bundle. Returns ``None``
        if no entry exists. Must not call ``current_epoch()``.
      * :meth:`put` — install a new bundle. Overwrites any existing entry
        with the same key.
      * :meth:`clear` — drop every entry. Used by tests and by hypothetical
        operator-tooling that wants to force a re-issuance.

    No expiry, no TTL, no eviction: the v3 proposal §6.3 explicitly forbids
    those in phase 1. If memory pressure becomes a concern, that is a
    separate proposal — not a haven-cli code change.
    """

    def __init__(self, persist_path: Path | None = None) -> None:
        self._entries: dict[CacheKey, CachedVetKey] = {}
        self._lock = threading.Lock()
        self._persist_path = persist_path
        if persist_path is not None:
            self._load()

    def _load(self) -> None:
        """Load persisted entries. Corrupt/missing file → start empty."""
        if self._persist_path is None:
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception as exc:
            logger.warning(
                "GateKeyCache: cannot read %s: %s (starting empty)",
                self._persist_path, exc,
            )
            return
        try:
            for entry in data.values():
                key = GateKeyCache.make_key(
                    chain=entry["chain"],
                    token_address=entry["token"],
                    threshold=entry["threshold"],
                    epoch=entry["epoch"],
                )
                self._entries[key] = CachedVetKey(
                    encrypted_key=base64.b64decode(entry["encrypted_b64"]),
                    verification_key=base64.b64decode(entry["vk_b64"]),
                )
        except Exception as exc:
            logger.warning(
                "GateKeyCache: bad persisted data in %s: %s (starting empty)",
                self._persist_path, exc,
            )
            self._entries.clear()

    def _persist(self) -> None:
        """Atomic 0600 rewrite of the persist file (call under lock)."""
        if self._persist_path is None:
            return
        data = {
            f"{k[0]}|{k[1]}|{k[2]}|{k[3]}": {
                "chain": k[0], "token": k[1],
                "threshold": k[2], "epoch": k[3],
                "encrypted_b64": base64.b64encode(v.encrypted_key).decode("ascii"),
                "vk_b64": base64.b64encode(v.verification_key).decode("ascii"),
            }
            for k, v in self._entries.items()
        }
        path = self._persist_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(path)
        except Exception as exc:
            logger.warning("GateKeyCache: persist failed: %s", exc)

    @staticmethod
    def make_key(
        *, chain: str, token_address: str, threshold: int, epoch: int
    ) -> CacheKey:
        """Build a cache key with the canonical types.

        ``threshold`` and ``epoch`` are coerced to ``int`` so callers can
        safely pass the decimal-string ``metadata.threshold`` form: the
        coercion happens once, in this method, and downstream lookups use
        the int tuple. ``chain`` and ``token_address`` are passed through
        verbatim — the caller is responsible for using the canonical case
        produced by ``build_gate_metadata_v3``.
        """
        if not isinstance(chain, str) or not chain:
            raise ValueError("chain must be a non-empty string")
        if not isinstance(token_address, str) or not token_address:
            raise ValueError("token_address must be a non-empty string")
        thr = int(threshold)
        epo = int(epoch)
        if thr < 0:
            raise ValueError(f"threshold must be >= 0, got {thr}")
        if epo < 0:
            raise ValueError(f"epoch must be >= 0, got {epo}")
        return (chain, token_address, thr, epo)

    def get(self, key: CacheKey) -> CachedVetKey | None:
        """Return the cached bundle for *key*, or ``None`` if not present.

        This method MUST NOT call :func:`haven_aol.v3.current_epoch`. The
        caller is responsible for passing the epoch from parsed metadata.
        See ``docs/corpus-gate-proposal-v3.md`` §1.7 scenario (D).
        """
        if not isinstance(key, tuple) or len(key) != 4:
            raise ValueError(
                f"GateKeyCache key must be a 4-tuple, got {key!r}"
            )
        with self._lock:
            entry = self._entries.get(key)
        if entry is not None:
            logger.debug("GateKeyCache hit chain=%s threshold=%s epoch=%s", key[0], key[2], key[3])
        return entry

    def put(self, key: CacheKey, value: CachedVetKey) -> None:
        """Install *value* under *key*. Overwrites any existing entry."""
        if not isinstance(key, tuple) or len(key) != 4:
            raise ValueError(
                f"GateKeyCache key must be a 4-tuple, got {key!r}"
            )
        if not isinstance(value, CachedVetKey):
            raise TypeError(
                f"GateKeyCache value must be CachedVetKey, got {type(value).__name__}"
            )
        with self._lock:
            self._entries[key] = value
            self._persist()
        logger.debug("GateKeyCache put chain=%s threshold=%s epoch=%s", key[0], key[2], key[3])

    def clear(self) -> None:
        """Drop every entry."""
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            self._persist()
        if count:
            logger.debug("GateKeyCache cleared (%d entries dropped)", count)

    def __len__(self) -> int:  # pragma: no cover — diagnostic only
        with self._lock:
            return len(self._entries)


#: Process-wide singleton (persisted). Decryptors reach for this by
#: default; tests may pass their own :class:`GateKeyCache` instance
#: to keep state isolated.
gate_key_cache: GateKeyCache = GateKeyCache(persist_path=_default_persist_path())
