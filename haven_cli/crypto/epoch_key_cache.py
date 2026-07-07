"""Encrypt-side per-epoch AES key cache for Haven-AOL Protocol v3.

Sibling of :mod:`haven_cli.crypto.gate_key_cache` (which caches
canister-returned VetKey bundles for the **decrypt** path). This module
caches the **encrypt-side** symmetric AES key used to seal file
ciphertexts, so a single ``(chain, token, threshold, epoch)`` bucket can
seal N files with one shared AES key and one shared IBE-wrapped key.

Why the two caches are separate:

  * :class:`GateKeyCache` value = ``CachedVetKey`` bundle (encrypted key +
    verification key from the canister). Used to *unwrap* an on-chain
    ``encryptedAesKey`` into a raw AES key.
  * :class:`EpochAesKeyCache` value = :class:`EpochAesKey` pairing (raw
    AES key bytes + already-IBE-wrapped base64 form). Used at *encrypt*
    time to seal fresh files under a stable per-epoch key so their
    on-chain metadata all references the same ``encryptedAesKey`` blob.

Fixes ``HAVEN_AOL_V3_BUGS.md`` Bug 6 (no encrypt-side cache) and is the
storage substrate for Bugs 4 (per-file random key) and 5 (no pipeline
injection point).

Invariants:

  * Same lookup key shape as :class:`GateKeyCache`:
    ``(chain, token_address, threshold, epoch)`` 4-tuple with canonical
    types. This is intentional — a future refactor that unifies both
    caches into one keyed structure is trivial with this shape.
  * Process-lifetime only. No disk persistence. Process restart clears.
  * ``threshold == 0`` collapses ``epoch`` to ``0`` at the caller side
    (see :func:`compute_epoch_cache_key`) so the whole threshold-0
    corpus lands in a single cache slot regardless of wall-clock time.
  * Thread-safe via :class:`threading.Lock`.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Tuple

logger = logging.getLogger(__name__)

#: Same key-tuple shape as :data:`haven_cli.crypto.gate_key_cache.CacheKey`.
EpochCacheKey = Tuple[str, str, int, int]


@dataclass(frozen=True)
class EpochAesKey:
    """A single per-epoch AES key bundle for the encrypt path.

    Both fields are immutable so a caller cannot mutate the cache by
    accident after retrieval.

    Fields:
      * ``raw_key`` — 32 raw bytes fed to :class:`AESGCM`.
      * ``encrypted_aes_key_b64`` — the IBE-wrapped form (base64) that
        gets stored in every on-chain ``encryptedAesKey`` field for this
        epoch. Because the raw key is identical across files in this
        epoch, this b64 blob is byte-identical too.
    """

    raw_key: bytes
    encrypted_aes_key_b64: str


class EpochAesKeyCache:
    """In-memory ``(chain, token, threshold, epoch) → EpochAesKey`` cache.

    Public interface mirrors :class:`GateKeyCache`:

      * :meth:`get`     — return cached bundle or ``None``.
      * :meth:`put`     — install (overwriting any prior entry).
      * :meth:`clear`   — drop everything (used by tests + operator tools).
      * :meth:`get_or_create` — atomic miss-fills; the caller supplies a
        factory that computes ``(raw_key, encrypted_aes_key_b64)`` if the
        slot is empty. Held under the lock so two concurrent encrypts for
        the same bucket share one factory call.
    """

    def __init__(self) -> None:
        self._entries: dict[EpochCacheKey, EpochAesKey] = {}
        self._lock = threading.Lock()

    @staticmethod
    def make_key(
        *, chain: str, token_address: str, threshold: int, epoch: int
    ) -> EpochCacheKey:
        """Build a canonical cache-key tuple.

        Mirrors :meth:`GateKeyCache.make_key`. ``threshold == 0`` MUST be
        paired with ``epoch == 0`` by the caller (v3 threshold-zero
        collapse rule). We enforce that here rather than silently
        collapsing so callers stay honest about the corpus they're
        addressing.
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
        if thr == 0 and epo != 0:
            raise ValueError(
                "threshold==0 requires epoch==0 (v3 canister collapse rule)"
            )
        return (chain, token_address, thr, epo)

    def get(self, key: EpochCacheKey) -> EpochAesKey | None:
        if not isinstance(key, tuple) or len(key) != 4:
            raise ValueError(f"EpochAesKeyCache key must be a 4-tuple, got {key!r}")
        with self._lock:
            entry = self._entries.get(key)
        if entry is not None:
            logger.debug(
                "EpochAesKeyCache hit chain=%s threshold=%s epoch=%s",
                key[0], key[2], key[3],
            )
        return entry

    def put(self, key: EpochCacheKey, value: EpochAesKey) -> None:
        if not isinstance(key, tuple) or len(key) != 4:
            raise ValueError(f"EpochAesKeyCache key must be a 4-tuple, got {key!r}")
        if not isinstance(value, EpochAesKey):
            raise TypeError(
                f"EpochAesKeyCache value must be EpochAesKey, got {type(value).__name__}"
            )
        with self._lock:
            self._entries[key] = value
        logger.debug(
            "EpochAesKeyCache put chain=%s threshold=%s epoch=%s",
            key[0], key[2], key[3],
        )

    def get_or_create(
        self,
        key: EpochCacheKey,
        factory,
    ) -> EpochAesKey:
        """Atomic miss-fill.

        If *key* is not cached, invokes ``factory()`` (must return an
        :class:`EpochAesKey`) inside the lock and installs the result.
        Concurrent callers targeting the same key will see one factory
        invocation and share its result.

        Note: the factory is invoked under the lock. It should therefore
        be quick — IBE wrap is a few ms of CPU, well within acceptable
        lock-hold time. Doing this outside the lock and then racing on
        ``put`` would let two files in the same batch produce two
        different ``encryptedAesKey`` blobs, defeating the whole point
        of the epoch cache.
        """
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                logger.debug(
                    "EpochAesKeyCache hit (get_or_create) chain=%s threshold=%s epoch=%s",
                    key[0], key[2], key[3],
                )
                return existing
            new_value = factory()
            if not isinstance(new_value, EpochAesKey):
                raise TypeError(
                    f"EpochAesKeyCache factory must return EpochAesKey, "
                    f"got {type(new_value).__name__}"
                )
            self._entries[key] = new_value
            logger.debug(
                "EpochAesKeyCache miss-fill chain=%s threshold=%s epoch=%s",
                key[0], key[2], key[3],
            )
            return new_value

    def clear(self) -> None:
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
        if count:
            logger.debug("EpochAesKeyCache cleared (%d entries dropped)", count)

    def __len__(self) -> int:  # pragma: no cover — diagnostic only
        with self._lock:
            return len(self._entries)


#: Process-wide singleton. Encrypt callers reach for this by default;
#: tests may pass their own :class:`EpochAesKeyCache` instance to keep
#: state isolated.
epoch_aes_key_cache: EpochAesKeyCache = EpochAesKeyCache()


__all__ = [
    "EpochAesKey",
    "EpochAesKeyCache",
    "EpochCacheKey",
    "epoch_aes_key_cache",
]
