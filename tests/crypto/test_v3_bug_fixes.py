"""Regression tests for the eight bugs in ``docs/HAVEN_AOL_V3_BUGS.md``.

Every test in this module maps 1:1 to a numbered bug. The tests exercise
only the pure-Python surface (metadata helpers, cache, injection API);
IBE / VetKD side effects are stubbed out so the suite runs offline in
CI without ``vetkd_py`` or the canister.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from haven_cli.crypto.epoch_key_cache import (
    EpochAesKey,
    EpochAesKeyCache,
    epoch_aes_key_cache,
)
from haven_cli.crypto.gate_metadata import (
    GATE_METADATA_VERSION,
    GATE_METADATA_VERSION_V3,
    build_gate_metadata,
    build_gate_metadata_v3,
    gate_metadata_any_to_json,
    gate_metadata_to_json,
    is_gate_metadata,
    is_gate_metadata_any,
    is_gate_metadata_v3,
    merge_encrypt_result_gate,
)


# ── Bug 1: v3→v1 metadata downgrade ─────────────────────────────────


class TestBug1MergePreservesV3:
    """``merge_encrypt_result_gate`` must not downgrade v3 gates to v1.

    Before the fix the function always routed through ``build_gate_metadata``
    (the v1 builder), so the returned dict said ``version:1`` and dropped
    ``epoch`` — even when the input was a full v3 gate. Since the v3 IBE
    ciphertext was already sealed under the v3 corpus-scoped derivation,
    the stored metadata + ciphertext disagreed and the content became
    undecryptable.
    """

    def test_v1_input_produces_v1_output(self):
        partial = {
            "version": 1,
            "cid": "bafycid",
            "chain": "EthMainnet",
            "tokenAddress": "0x" + "ab" * 20,
            "threshold": "1",
        }
        gate = merge_encrypt_result_gate(partial, "wrapped-key-b64")
        assert gate["version"] == GATE_METADATA_VERSION
        assert "epoch" not in gate
        assert gate["encryptedAesKey"] == "wrapped-key-b64"

    def test_v3_input_produces_v3_output(self):
        partial = build_gate_metadata_v3(
            cid="bafycid",
            chain="EthMainnet",
            token_address="0x" + "cd" * 20,
            threshold=1,
            epoch=42,
            encrypted_aes_key_b64="AA==",
        )
        merged = merge_encrypt_result_gate(partial, "ZZZZ")
        assert merged["version"] == GATE_METADATA_VERSION_V3
        assert merged["epoch"] == 42
        assert merged["encryptedAesKey"] == "ZZZZ"
        # threshold is stringified by the v3 builder too (parity with v1).
        assert merged["threshold"] == "1"

    def test_v3_input_missing_epoch_raises(self):
        # Guard against a malformed v3 partial (no epoch) reaching this
        # function. Better to fail loudly than silently reconstruct a
        # zero-epoch record.
        with pytest.raises(KeyError):
            merge_encrypt_result_gate(
                {
                    "version": 3,
                    "cid": "bafycid",
                    "chain": "EthMainnet",
                    "tokenAddress": "0x" + "ab" * 20,
                    "threshold": 1,
                    # no epoch
                },
                "wrapped",
            )


# ── Bug 2: is_gate_metadata was v1-only ─────────────────────────────


class TestBug2IsGateMetadataAcceptsV3:
    """``is_gate_metadata_any`` must return True for both v1 and v3 records.

    Callers that used the strict v1 ``is_gate_metadata`` unwittingly
    dropped every v3 record — that's Bug 2. The new helper is what those
    callers now use.
    """

    def test_v1_record_is_accepted(self):
        gate = build_gate_metadata(
            cid="bafycid",
            chain="EthMainnet",
            token_address="0x" + "ab" * 20,
            threshold=1,
            encrypted_aes_key_b64="k",
        )
        assert is_gate_metadata_any(gate) is True
        assert is_gate_metadata(gate) is True
        assert is_gate_metadata_v3(gate) is False

    def test_v3_record_is_accepted(self):
        gate = build_gate_metadata_v3(
            cid="bafycid",
            chain="EthMainnet",
            token_address="0x" + "cd" * 20,
            threshold=1,
            epoch=5,
            encrypted_aes_key_b64="AA==",
        )
        assert is_gate_metadata_any(gate) is True
        assert is_gate_metadata(gate) is False  # strict v1 rejects v3
        assert is_gate_metadata_v3(gate) is True

    def test_non_dict_rejected(self):
        assert is_gate_metadata_any("not a dict") is False
        assert is_gate_metadata_any(None) is False
        assert is_gate_metadata_v3([]) is False

    def test_bool_version_is_rejected(self):
        # ``True == 1`` in Python — a JSON-decoded ``true`` must not sneak
        # into the v1 or v3 dispatcher.
        assert is_gate_metadata_v3({"version": True}) is False


# ── Bug 3: gate_metadata_to_json v1-only ────────────────────────────


class TestBug3AnyToJsonHandlesV3:
    """``gate_metadata_any_to_json`` serializes v1 and v3 gates alike."""

    def test_v1_serializes(self):
        gate = build_gate_metadata(
            cid="bafycid",
            chain="EthMainnet",
            token_address="0x" + "ab" * 20,
            threshold=1,
            encrypted_aes_key_b64="k",
        )
        s = gate_metadata_any_to_json(gate)
        # Byte-identical to the v1-only serializer for v1 inputs.
        assert s == gate_metadata_to_json(gate)
        parsed = json.loads(s)
        assert parsed["version"] == 1

    def test_v3_serializes(self):
        gate = build_gate_metadata_v3(
            cid="bafycid",
            chain="EthMainnet",
            token_address="0x" + "cd" * 20,
            threshold=1,
            epoch=7,
            encrypted_aes_key_b64="AA==",
        )
        s = gate_metadata_any_to_json(gate)
        parsed = json.loads(s)
        assert parsed["version"] == 3
        assert parsed["epoch"] == 7

    def test_v3_gate_no_longer_raises_when_reaching_serializer(self):
        # Pre-fix: even if a v3 gate somehow reached the serializer, the
        # v1 guard raised ValueError. Now the dispatched serializer
        # accepts it.
        gate = build_gate_metadata_v3(
            cid="bafycid",
            chain="EthMainnet",
            token_address="0x" + "ab" * 20,
            threshold=1,
            epoch=0,
            encrypted_aes_key_b64="AA==",
        )
        # No exception.
        gate_metadata_any_to_json(gate)


# ── Bugs 4 / 5 / 6 / 8: per-epoch AES key reuse ─────────────────────


class TestBug6EpochAesKeyCache:
    """The encrypt-side cache stores raw AES keys keyed by corpus bucket."""

    def test_make_key_canonical_types(self):
        key = EpochAesKeyCache.make_key(
            chain="EthMainnet",
            token_address="0x" + "ab" * 20,
            threshold="5",  # string threshold from metadata coerces to int
            epoch=10,
        )
        assert key == ("EthMainnet", "0x" + "ab" * 20, 5, 10)

    def test_threshold_zero_requires_epoch_zero(self):
        with pytest.raises(ValueError):
            EpochAesKeyCache.make_key(
                chain="EthMainnet",
                token_address="0x" + "ab" * 20,
                threshold=0,
                epoch=42,
            )

    def test_get_or_create_miss_then_hit(self):
        cache = EpochAesKeyCache()
        key = EpochAesKeyCache.make_key(
            chain="EthMainnet",
            token_address="0x" + "ab" * 20,
            threshold=1,
            epoch=1,
        )

        factory_calls = []

        def factory() -> EpochAesKey:
            factory_calls.append(1)
            return EpochAesKey(raw_key=b"\x00" * 32, encrypted_aes_key_b64="AA==")

        first = cache.get_or_create(key, factory)
        second = cache.get_or_create(key, factory)
        assert first is second
        assert len(factory_calls) == 1  # factory only ran once

    def test_get_or_create_rejects_bad_factory(self):
        cache = EpochAesKeyCache()
        key = EpochAesKeyCache.make_key(
            chain="EthMainnet",
            token_address="0x" + "ab" * 20,
            threshold=1,
            epoch=1,
        )
        with pytest.raises(TypeError):
            cache.get_or_create(key, lambda: "not an EpochAesKey")

    def test_singleton_exposed_and_isolable(self):
        # The process-wide singleton is importable, but tests can pass
        # their own instance to keep state isolated.
        from haven_cli.crypto import epoch_aes_key_cache as global_cache
        assert global_cache is epoch_aes_key_cache
        local = EpochAesKeyCache()
        assert local is not global_cache


class TestBug8EncryptInjection:
    """``encrypt_bytes_v3`` and ``encrypt_file_streaming_v3`` accept a raw
    AES key + wrapped b64 pair for epoch-key reuse.

    We patch out the IBE wrap so this test runs without ``vetkd_py``.
    """

    def _patch_ibe(self):
        # Return a deterministic 48-byte "wrapped" blob so callers get a
        # stable base64 string. The real primitive lives in ``vetkd_py``
        # and requires a compiled extension; we don't need it here.
        return patch(
            "haven_cli.crypto.haven_aol_v3._ibe_encrypt_aes_key_v3",
            return_value=b"\xab" * 48,
        )

    def test_bytes_reuses_supplied_key_pair(self):
        from haven_cli.crypto.haven_aol_v3 import encrypt_bytes_v3

        raw = b"\x11" * 32
        # ``build_gate_metadata_v3`` validates that the wrapped-key looks
        # like base64. Use something that satisfies its regex.
        wrapped_b64 = "c3VwcGxpZWQtd3JhcHBlZC1iNjQ="
        # Both supplied → NO ibe wrap should be called.
        with patch(
            "haven_cli.crypto.haven_aol_v3._ibe_encrypt_aes_key_v3"
        ) as ibe_mock:
            result = encrypt_bytes_v3(
                b"hello",
                chain="EthMainnet",
                token_address="0x" + "ab" * 20,
                threshold=1,
                cid="bafycid",
                aes_key=raw,
                encrypted_aes_key_b64=wrapped_b64,
                epoch=3,
            )
            ibe_mock.assert_not_called()

        assert result["encrypted_key_b64"] == wrapped_b64
        assert result["gate"]["encryptedAesKey"] == wrapped_b64
        assert result["gate"]["epoch"] == 3


    def test_bytes_wraps_when_only_raw_supplied(self):
        from haven_cli.crypto.haven_aol_v3 import encrypt_bytes_v3

        with self._patch_ibe() as ibe_mock:
            result = encrypt_bytes_v3(
                b"hello",
                chain="EthMainnet",
                token_address="0x" + "ab" * 20,
                threshold=1,
                cid="bafycid",
                aes_key=b"\x22" * 32,
                epoch=1,
            )
            ibe_mock.assert_called_once()
        # Wrapped b64 should be the base64 of the 48-byte 0xab blob.
        import base64
        assert result["encrypted_key_b64"] == base64.b64encode(b"\xab" * 48).decode("ascii")

    def test_bytes_rejects_wrapped_without_raw(self):
        from haven_cli.crypto.haven_aol_v3 import encrypt_bytes_v3

        # This combination was accepted pre-fix but produced ciphertext
        # sealed under a *different* random key than the metadata claimed.
        with pytest.raises(ValueError):
            encrypt_bytes_v3(
                b"hello",
                chain="EthMainnet",
                token_address="0x" + "ab" * 20,
                threshold=1,
                cid="bafycid",
                encrypted_aes_key_b64="anything",
                epoch=1,
            )

    def test_bytes_rejects_wrong_size_key(self):
        from haven_cli.crypto.haven_aol_v3 import encrypt_bytes_v3

        with pytest.raises(ValueError):
            encrypt_bytes_v3(
                b"hello",
                chain="EthMainnet",
                token_address="0x" + "ab" * 20,
                threshold=1,
                cid="bafycid",
                aes_key=b"\x00" * 16,  # too short
                epoch=1,
            )

    def test_file_streaming_reuses_supplied_key_pair(self, tmp_path):
        from haven_cli.crypto.haven_aol_v3 import encrypt_file_streaming_v3

        src = tmp_path / "plain.bin"
        dst = tmp_path / "enc.bin"
        src.write_bytes(b"hello world" * 100)

        raw = b"\x33" * 32
        reused_wrapped_b64 = "cmV1c2Vk"  # base64("reused")
        with patch(
            "haven_cli.crypto.haven_aol_v3._ibe_encrypt_aes_key_v3"
        ) as ibe_mock:
            result = encrypt_file_streaming_v3(
                str(src),
                str(dst),
                chain="EthMainnet",
                token_address="0x" + "ab" * 20,
                threshold=1,
                cid="bafycid",
                epoch=99,
                aes_key=raw,
                encrypted_aes_key_b64=reused_wrapped_b64,
            )
            ibe_mock.assert_not_called()

        assert result["encrypted_key_b64"] == reused_wrapped_b64
        assert result["gate"]["epoch"] == 99
        assert dst.exists() and dst.stat().st_size > 0



class TestBug4And5EpochKeyReuseAcrossFiles:
    """Two files in the same epoch bucket end up with the same on-chain
    ``encryptedAesKey`` when routed through the cache.

    This is the observable payoff of Bugs 4 + 5 + 6 + 8 combined: one AES
    key + one IBE wrap for the whole corpus/epoch, so the on-chain
    metadata deduplicates.
    """

    def test_two_files_share_epoch_key(self, tmp_path):
        from haven_cli.crypto.haven_aol_v3 import encrypt_file_streaming_v3

        cache = EpochAesKeyCache()
        cache_key = EpochAesKeyCache.make_key(
            chain="EthMainnet",
            token_address="0x" + "ab" * 20,
            threshold=1,
            epoch=1,
        )
        # Seed the cache with a fixed key pair. The b64 blob must satisfy
        # ``build_gate_metadata_v3``'s base64 validation.
        epoch_wrapped_b64 = "RVBPQ0hfV1JBUA=="  # base64("EPOCH_WRAP")
        epoch_key = EpochAesKey(
            raw_key=b"\x44" * 32, encrypted_aes_key_b64=epoch_wrapped_b64
        )
        cache.put(cache_key, epoch_key)


        src1 = tmp_path / "a.bin"
        src2 = tmp_path / "b.bin"
        src1.write_bytes(b"file-a-content")
        src2.write_bytes(b"file-b-content-different-length")

        with patch("haven_cli.crypto.haven_aol_v3._ibe_encrypt_aes_key_v3") as ibe_mock:
            r1 = encrypt_file_streaming_v3(
                str(src1), str(tmp_path / "a.enc"),
                chain="EthMainnet", token_address="0x" + "ab" * 20,
                threshold=1, cid="cid-a", epoch=1,
                aes_key=epoch_key.raw_key,
                encrypted_aes_key_b64=epoch_key.encrypted_aes_key_b64,
            )
            r2 = encrypt_file_streaming_v3(
                str(src2), str(tmp_path / "b.enc"),
                chain="EthMainnet", token_address="0x" + "ab" * 20,
                threshold=1, cid="cid-b", epoch=1,
                aes_key=epoch_key.raw_key,
                encrypted_aes_key_b64=epoch_key.encrypted_aes_key_b64,
            )
            # No IBE calls at all: both files reused the pre-wrapped blob.
            ibe_mock.assert_not_called()

        # Both files publish the SAME on-chain wrapped key.
        assert r1["encrypted_key_b64"] == r2["encrypted_key_b64"] == epoch_wrapped_b64
        assert r1["gate"]["encryptedAesKey"] == r2["gate"]["encryptedAesKey"]

        # But different CIDs → different derivation_input hex → different
        # ciphertext hash.
        assert r1["gate"]["cid"] != r2["gate"]["cid"]


# ── Bug 7: top-level epoch in entity payload ─────────────────────────


class TestBug7TopLevelEpochInPayload:
    """``_build_payload`` publishes ``epoch`` + ``gate_version`` at the top
    level of the entity payload when the content gate is v3, so consumers
    can filter by corpus without JSON-parsing the ``encryption_metadata``
    string.
    """

    def _pipeline_context_with_v3_gate(self, tmp_path):
        from haven_cli.pipeline.context import (
            EncryptionMetadata,
            PipelineContext,
            UploadResult,
        )

        gate = build_gate_metadata_v3(
            cid="bafycid",
            chain="EthMainnet",
            token_address="0x" + "ab" * 20,
            threshold=1,
            epoch=17,
            encrypted_aes_key_b64="AA==",
        )
        # ``PipelineContext.source_path`` (not ``video_path``) is the
        # correct field name. ``__post_init__`` coerces a str to a Path.
        video_path = str(tmp_path / "video.mp4")
        ctx = PipelineContext(source_path=video_path)
        ctx.encryption_metadata = EncryptionMetadata(gate=gate)
        ctx.upload_result = UploadResult(
            video_path=video_path,
            root_cid="bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi",
            piece_cid=(
                "bafkzcibe2hzbcd4t6clvsb3mfrezyxl75gl3gzcsqi42dd27gktq4nk75rr62ciuaq"
            ),
        )
        return ctx

    def _pipeline_context_with_v1_gate(self, tmp_path):

        from haven_cli.pipeline.context import (
            EncryptionMetadata,
            PipelineContext,
            UploadResult,
        )

        gate = build_gate_metadata(
            cid="bafycid",
            chain="EthMainnet",
            token_address="0x" + "ab" * 20,
            threshold=1,
            encrypted_aes_key_b64="k",
        )
        video_path = str(tmp_path / "video.mp4")
        ctx = PipelineContext(source_path=video_path)
        ctx.encryption_metadata = EncryptionMetadata(gate=gate)
        ctx.upload_result = UploadResult(
            video_path=video_path,
            root_cid="bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi",
            piece_cid=(
                "bafkzcibe2hzbcd4t6clvsb3mfrezyxl75gl3gzcsqi42dd27gktq4nk75rr62ciuaq"
            ),
        )
        return ctx


    def test_v3_context_emits_top_level_epoch(self, tmp_path):

        from haven_cli.services.arkiv_sync import _build_attributes, _build_payload

        ctx = self._pipeline_context_with_v3_gate(tmp_path)
        payload = _build_payload(ctx)
        attributes = _build_attributes(ctx)

        # Bug 7: top-level fields on payload.
        assert payload["epoch"] == 17
        assert payload["gate_version"] == GATE_METADATA_VERSION_V3
        # And attribute-side exposure so on-chain queries can filter.
        assert attributes["gate_epoch"] == 17
        assert attributes["gate_version"] == GATE_METADATA_VERSION_V3
        # The full v3 encryption_metadata blob is also still there for
        # decryptors that want it whole.
        enc_meta = json.loads(payload["encryption_metadata"])
        assert enc_meta["version"] == GATE_METADATA_VERSION_V3
        assert enc_meta["epoch"] == 17

    def test_v1_context_does_not_emit_epoch(self, tmp_path):
        from haven_cli.services.arkiv_sync import _build_attributes, _build_payload

        ctx = self._pipeline_context_with_v1_gate(tmp_path)
        payload = _build_payload(ctx)
        attributes = _build_attributes(ctx)

        assert "epoch" not in payload
        # gate_version isn't emitted on payload for v1 (byte-identity with
        # pre-fix behaviour).
        assert "gate_version" not in payload
        # But the attribute-side ``gate_version`` IS emitted for both v1
        # and v3 (that's an additive extension, not a semantic change).
        assert attributes["gate_version"] == 1
        assert "gate_epoch" not in attributes


# ── Overall v1 byte-identity guard ──────────────────────────────────


class TestV1ByteIdentityPreserved:
    """Sanity check that the strict v1 surface still round-trips."""

    def test_v1_json_roundtrip(self):
        from haven_cli.crypto.gate_metadata import parse_gate_metadata

        gate = build_gate_metadata(
            cid="bafycid",
            chain="EthMainnet",
            token_address="0x" + "ab" * 20,
            threshold=1,
            encrypted_aes_key_b64="k",
        )
        serialized = gate_metadata_to_json(gate)
        parsed = parse_gate_metadata(serialized)
        assert parsed == gate

    def test_v1_merge_still_byte_identical(self):
        # This is the exact assertion the pre-Sprint-4 test held.
        partial = {
            "version": 1,
            "cid": "sha256:abc",
            "chain": "EthSepolia",
            "tokenAddress": "0x" + "cd" * 20,
            "threshold": "1",
        }
        gate = merge_encrypt_result_gate(partial, "key-b64")
        expected = build_gate_metadata(
            cid="sha256:abc",
            chain="EthSepolia",
            token_address="0x" + "cd" * 20,
            threshold=1,
            encrypted_aes_key_b64="key-b64",
        )
        assert gate == expected

