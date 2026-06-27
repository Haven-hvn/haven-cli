"""Unit tests for the in-memory :class:`GateKeyCache` (Sprint 4 v3).

Pinned invariants:
  * Cache key tuple = ``(chain, token_address, threshold, epoch)`` with
    int-coerced threshold/epoch.
  * ``get`` returns ``None`` on miss, the same :class:`CachedVetKey` on hit.
  * ``put`` overwrites.
  * ``clear`` drops everything.
  * **Lookup keyed by metadata.epoch, never current_epoch()** — a future
    wall clock must not invalidate an old-epoch cache hit. This is the
    Sprint 4 brief's scenario-(D) regression test.
  * No filesystem / pickle / sqlite imports in the cache module.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from haven_cli.crypto.gate_key_cache import (
    CachedVetKey,
    GateKeyCache,
    gate_key_cache as singleton_cache,
)


def _bundle(byte: int = 0xAA) -> CachedVetKey:
    return CachedVetKey(
        encrypted_key=bytes([byte]) * 48,
        verification_key=bytes([byte]) * 96,
    )


class TestMakeKey:
    def test_canonical_tuple_shape(self) -> None:
        key = GateKeyCache.make_key(
            chain="EthMainnet",
            token_address="0x" + "ab" * 20,
            threshold=1,
            epoch=687,
        )
        assert key == ("EthMainnet", "0x" + "ab" * 20, 1, 687)
        # Element types are exactly what the docstring promises.
        assert isinstance(key[0], str)
        assert isinstance(key[1], str)
        assert isinstance(key[2], int)
        assert isinstance(key[3], int)

    def test_threshold_and_epoch_coerced_to_int(self) -> None:
        # Callers commonly pass the decimal-string threshold from metadata.
        key = GateKeyCache.make_key(
            chain="EthMainnet",
            token_address="0x" + "ab" * 20,
            threshold="42",  # type: ignore[arg-type]
            epoch="1000",  # type: ignore[arg-type]
        )
        assert key == ("EthMainnet", "0x" + "ab" * 20, 42, 1000)

    @pytest.mark.parametrize("bad", [-1, -10, -2**31])
    def test_negative_threshold_rejected(self, bad: int) -> None:
        with pytest.raises(ValueError):
            GateKeyCache.make_key(
                chain="EthMainnet",
                token_address="0x" + "ab" * 20,
                threshold=bad,
                epoch=0,
            )

    def test_empty_chain_rejected(self) -> None:
        with pytest.raises(ValueError):
            GateKeyCache.make_key(
                chain="",
                token_address="0x" + "ab" * 20,
                threshold=1,
                epoch=0,
            )

    def test_empty_token_rejected(self) -> None:
        with pytest.raises(ValueError):
            GateKeyCache.make_key(
                chain="EthMainnet",
                token_address="",
                threshold=1,
                epoch=0,
            )


class TestGetPutClear:
    def test_miss_returns_none(self) -> None:
        cache = GateKeyCache()
        key = GateKeyCache.make_key(
            chain="EthMainnet", token_address="0x" + "ab" * 20, threshold=1, epoch=0
        )
        assert cache.get(key) is None

    def test_put_then_get_returns_same_bundle(self) -> None:
        cache = GateKeyCache()
        key = GateKeyCache.make_key(
            chain="EthMainnet", token_address="0x" + "ab" * 20, threshold=1, epoch=0
        )
        bundle = _bundle()
        cache.put(key, bundle)
        hit = cache.get(key)
        assert hit is bundle  # identity, not just equality

    def test_put_overwrites(self) -> None:
        cache = GateKeyCache()
        key = GateKeyCache.make_key(
            chain="EthMainnet", token_address="0x" + "ab" * 20, threshold=1, epoch=0
        )
        cache.put(key, _bundle(0xAA))
        cache.put(key, _bundle(0xBB))
        assert cache.get(key).encrypted_key[0] == 0xBB

    def test_clear(self) -> None:
        cache = GateKeyCache()
        for i in range(3):
            cache.put(
                GateKeyCache.make_key(
                    chain="EthMainnet",
                    token_address="0x" + "ab" * 20,
                    threshold=1,
                    epoch=i,
                ),
                _bundle(i),
            )
        assert len(cache) == 3
        cache.clear()
        assert len(cache) == 0

    def test_distinct_keys_do_not_collide(self) -> None:
        cache = GateKeyCache()
        k1 = GateKeyCache.make_key(
            chain="EthMainnet", token_address="0x" + "ab" * 20, threshold=1, epoch=687
        )
        k2 = GateKeyCache.make_key(
            chain="EthSepolia", token_address="0x" + "ab" * 20, threshold=1, epoch=687
        )
        k3 = GateKeyCache.make_key(
            chain="EthMainnet", token_address="0x" + "cd" * 20, threshold=1, epoch=687
        )
        k4 = GateKeyCache.make_key(
            chain="EthMainnet", token_address="0x" + "ab" * 20, threshold=2, epoch=687
        )
        k5 = GateKeyCache.make_key(
            chain="EthMainnet", token_address="0x" + "ab" * 20, threshold=1, epoch=688
        )
        cache.put(k1, _bundle(1))
        cache.put(k2, _bundle(2))
        cache.put(k3, _bundle(3))
        cache.put(k4, _bundle(4))
        cache.put(k5, _bundle(5))
        assert cache.get(k1).encrypted_key[0] == 1
        assert cache.get(k2).encrypted_key[0] == 2
        assert cache.get(k3).encrypted_key[0] == 3
        assert cache.get(k4).encrypted_key[0] == 4
        assert cache.get(k5).encrypted_key[0] == 5

    def test_get_rejects_non_tuple_key(self) -> None:
        cache = GateKeyCache()
        with pytest.raises(ValueError):
            cache.get("not-a-tuple")  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            cache.get(("a", "b", 1))  # type: ignore[arg-type]

    def test_put_rejects_wrong_value_type(self) -> None:
        cache = GateKeyCache()
        key = GateKeyCache.make_key(
            chain="EthMainnet", token_address="0x" + "ab" * 20, threshold=1, epoch=0
        )
        with pytest.raises(TypeError):
            cache.put(key, b"not-a-cached-vetkey")  # type: ignore[arg-type]


class TestSingleton:
    def test_module_singleton_exists(self) -> None:
        assert isinstance(singleton_cache, GateKeyCache)
        # We don't assert it's empty — other tests in the suite may have
        # touched it. We just confirm the singleton is reachable.


class TestThreadSafety:
    def test_concurrent_puts_do_not_corrupt_state(self) -> None:
        cache = GateKeyCache()
        threads = []
        N = 64

        def writer(i: int) -> None:
            key = GateKeyCache.make_key(
                chain="EthMainnet",
                token_address="0x" + "ab" * 20,
                threshold=1,
                epoch=i,
            )
            cache.put(key, _bundle(i & 0xFF))

        for i in range(N):
            t = threading.Thread(target=writer, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        assert len(cache) == N
        for i in range(N):
            key = GateKeyCache.make_key(
                chain="EthMainnet",
                token_address="0x" + "ab" * 20,
                threshold=1,
                epoch=i,
            )
            hit = cache.get(key)
            assert hit is not None
            assert hit.encrypted_key[0] == (i & 0xFF)


class TestNoFilesystem:
    """The proposal §6.3 forbids on-disk persistence. We enforce that by
    grepping the module source for forbidden tokens; if any of these
    appear, the build must fail loudly. The Sprint 4 validator does the
    same check at script level; this test is the in-test mirror."""

    def test_module_source_has_no_filesystem_imports(self) -> None:
        """Parse the module's AST and walk every Import / ImportFrom /
        Attribute node, asserting no forbidden filesystem-touching symbol
        appears in real code. Docstring prose is naturally excluded by
        the AST — we never visit string literals here."""
        import ast

        module_path = Path(__file__).resolve().parents[2] / (
            "haven_cli/crypto/gate_key_cache.py"
        )
        src = module_path.read_text(encoding="utf-8")
        tree = ast.parse(src)

        forbidden_modules = {"pickle", "shelve", "sqlite3"}
        forbidden_attrs = {
            # On a Path instance, write_bytes writes to disk. We forbid
            # ANY call to .write_bytes in this module.
            "write_bytes",
            "write_text",
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden_modules, (
                        f"GateKeyCache imports forbidden module {alias.name!r}; "
                        "proposal §6.3 bans on-disk persistence."
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root = node.module.split(".")[0]
                    assert root not in forbidden_modules, (
                        f"GateKeyCache imports forbidden module {node.module!r}; "
                        "proposal §6.3 bans on-disk persistence."
                    )
            elif isinstance(node, ast.Attribute):
                assert node.attr not in forbidden_attrs, (
                    f"GateKeyCache uses forbidden filesystem call ``.{node.attr}``; "
                    "proposal §6.3 bans on-disk persistence."
                )

        # Also assert the obvious built-ins aren't called in real code.
        # ``open(...)`` is allowed nowhere in this module.
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "open", (
                    "GateKeyCache uses open(); proposal §6.3 bans on-disk persistence."
                )

    def test_lookup_independent_of_current_epoch(self, monkeypatch) -> None:
        """The Sprint 4 brief's scenario-(D) regression.

        We install a cache entry under epoch=K, then move
        ``haven_aol.v3.current_epoch`` far into the future, and confirm
        the cache still hits when looking up by the original epoch. The
        cache module must NOT consult ``current_epoch()``; the lookup
        key is sourced from ``metadata.epoch`` exclusively.
        """
        import haven_aol.v3 as sdk_v3

        cache = GateKeyCache()
        key = GateKeyCache.make_key(
            chain="EthMainnet",
            token_address="0x" + "ab" * 20,
            threshold=1,
            epoch=100,
        )
        cache.put(key, _bundle(0x42))

        # Pretend the wall clock has advanced past the entry's epoch.
        monkeypatch.setattr(sdk_v3, "current_epoch", lambda: 10_000)

        hit = cache.get(key)
        assert hit is not None
        assert hit.encrypted_key[0] == 0x42
