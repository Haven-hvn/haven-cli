"""Sprint 4 — gate_metadata v3 dispatcher + v1 byte-identity tests.

Two complementary suites:

  * **v1 snapshot regression** — for each v1 fixture we build with the
    pre-Sprint-4 helpers, the dispatching ``parse_gate_metadata`` must
    return the *same dict* it did before. The v1 behaviour is frozen.
  * **v3 routing + parsing** — ``parse_gate_metadata`` recognises
    ``version == 3`` records, validates the v3 fields, and returns a
    canonical v3 dict. ``parse_gate_metadata_v3`` is re-exported from
    the SDK and behaves identically.

The cross-stack derivation parity is already covered by the Python SDK
tests (``packages/python/tests/test_haven_aol_v3.py``) and the Sprint 0
fixture, so we don't re-test the hash byte values here.
"""

from __future__ import annotations

import json

import pytest

from haven_cli.crypto.gate_metadata import (
    GATE_METADATA_VERSION,
    GATE_METADATA_VERSION_V3,
    build_gate_metadata,
    build_gate_metadata_v3,
    gate_metadata_to_json,
    is_gate_metadata,
    parse_gate_metadata,
    parse_gate_metadata_v3,
)


# ── Fixture builders ────────────────────────────────────────────────


# v3 base64 regex (^[A-Za-z0-9+/]+={0,2}$) rejects dashes. The v1 helper
# is more permissive, so we use a base64-valid token throughout so the
# same string works on both sides of the dispatcher.
_AES_KEY_B64 = "ZW5jcnlwdGVkLWtleS1iNjQ="  # base64("encrypted-key-b64")


def _v1_sample() -> dict:
    return build_gate_metadata(
        cid="bafyV1Sample",
        chain="EthMainnet",
        token_address="0x" + "ab" * 20,
        threshold=1,
        encrypted_aes_key_b64=_AES_KEY_B64,
    )


def _v3_sample(threshold: int = 1, epoch: int = 687) -> dict:
    return build_gate_metadata_v3(
        cid="bafyV3Sample",
        chain="EthMainnet",
        token_address="0x" + "ab" * 20,
        threshold=threshold,
        epoch=epoch,
        encrypted_aes_key_b64=_AES_KEY_B64,
    )


# ── v1 frozen-behaviour tests ───────────────────────────────────────


class TestV1Unchanged:
    """The pre-Sprint-4 v1 paths must remain bit-for-bit identical. The
    snapshot below is the *literal* dict the v1 builder has emitted since
    before this sprint; if any of these fields change in shape, the v1
    consumers (haven-dapp legacy reads, pipeline decrypt step) break."""

    def test_v1_constants(self) -> None:
        assert GATE_METADATA_VERSION == 1

    def test_v1_build_snapshot(self) -> None:
        gate = _v1_sample()
        assert gate == {
            "version": 1,
            "cid": "bafyV1Sample",
            "chain": "EthMainnet",
            "tokenAddress": "0x" + "ab" * 20,
            "threshold": "1",
            "encryptedAesKey": _AES_KEY_B64,
        }
        assert is_gate_metadata(gate)

    def test_v1_parse_round_trip(self) -> None:
        gate = _v1_sample()
        parsed = parse_gate_metadata(json.dumps(gate))
        assert parsed == gate

    def test_v1_parse_from_dict_round_trip(self) -> None:
        gate = _v1_sample()
        parsed = parse_gate_metadata(dict(gate))
        assert parsed == gate

    def test_v1_to_json_round_trip(self) -> None:
        gate = _v1_sample()
        as_json = gate_metadata_to_json(gate)
        assert json.loads(as_json) == gate

    def test_v1_rejects_hybrid_legacy_shape(self) -> None:
        legacy = {
            "version": "hybrid-v1",
            "encryptedKey": "x",
            "keyHash": "y",
            "iv": "z",
            "accessControlConditions": [],
            "chain": "ethereum",
        }
        # Pre-Sprint-4 behaviour: parse returns None on this shape.
        assert parse_gate_metadata(legacy) is None
        assert parse_gate_metadata(json.dumps(legacy)) is None

    def test_v1_rejects_missing_required_key(self) -> None:
        gate = _v1_sample()
        del gate["threshold"]
        assert parse_gate_metadata(json.dumps(gate)) is None

    def test_v1_parse_empty_returns_none(self) -> None:
        assert parse_gate_metadata("") is None
        assert parse_gate_metadata(None) is None
        assert parse_gate_metadata({}) is None

    def test_v1_parse_bad_json_returns_none(self) -> None:
        assert parse_gate_metadata("{not json") is None


# ── v3 dispatcher tests ─────────────────────────────────────────────


class TestV3Dispatcher:
    def test_v3_constants_re_exported(self) -> None:
        assert GATE_METADATA_VERSION_V3 == 3

    def test_v3_build_field_order_and_shape(self) -> None:
        gate = _v3_sample(threshold=1, epoch=687)
        # Field order is pinned by tasking/README.md cross-stack contracts.
        assert list(gate.keys()) == [
            "version",
            "cid",
            "chain",
            "tokenAddress",
            "threshold",
            "epoch",
            "encryptedAesKey",
        ]
        assert gate["version"] == 3
        assert gate["threshold"] == "1"  # decimal string
        assert gate["epoch"] == 687
        assert isinstance(gate["epoch"], int)

    def test_dispatcher_routes_v3_to_v3_parser(self) -> None:
        gate = _v3_sample(threshold=2, epoch=42)
        parsed = parse_gate_metadata(json.dumps(gate))
        assert parsed is not None
        assert parsed["version"] == 3
        assert parsed["threshold"] == "2"
        assert parsed["epoch"] == 42

    def test_v3_parse_from_dict(self) -> None:
        gate = _v3_sample()
        parsed = parse_gate_metadata(dict(gate))
        assert parsed is not None
        assert parsed["version"] == 3

    def test_v3_strict_parser_re_exported_from_sdk(self) -> None:
        gate = _v3_sample()
        # Module-level strict parser is the SDK's parse_gate_metadata_v3.
        parsed = parse_gate_metadata_v3(gate)
        assert parsed is not None
        assert parsed["version"] == 3
        # Strict parser rejects v1 records.
        assert parse_gate_metadata_v3(_v1_sample()) is None

    def test_dispatcher_rejects_unknown_version(self) -> None:
        bogus = dict(_v3_sample())
        bogus["version"] = 99
        assert parse_gate_metadata(json.dumps(bogus)) is None

    def test_dispatcher_rejects_version_as_string(self) -> None:
        bogus = dict(_v3_sample())
        bogus["version"] = "3"
        assert parse_gate_metadata(json.dumps(bogus)) is None

    def test_dispatcher_rejects_version_as_bool(self) -> None:
        # True == 1 in Python; pre-empt that ambiguity.
        bogus = dict(_v3_sample())
        bogus["version"] = True
        assert parse_gate_metadata(json.dumps(bogus)) is None


# ── v3 builder validation ───────────────────────────────────────────


class TestV3BuilderValidation:
    def test_threshold_zero_with_nonzero_epoch_raises(self) -> None:
        # The Sprint 4 brief mandates this rule: canister forces epoch=0
        # when threshold=0; uploader metadata must match.
        with pytest.raises(ValueError):
            build_gate_metadata_v3(
                cid="x",
                chain="EthMainnet",
                token_address="0x" + "ab" * 20,
                threshold=0,
                epoch=10,
                encrypted_aes_key_b64="abc",
            )

    def test_threshold_zero_with_zero_epoch_ok(self) -> None:
        gate = build_gate_metadata_v3(
            cid="x",
            chain="EthMainnet",
            token_address="0x" + "ab" * 20,
            threshold=0,
            epoch=0,
            encrypted_aes_key_b64="abc",
        )
        assert gate["threshold"] == "0"
        assert gate["epoch"] == 0

    def test_invalid_chain_rejected(self) -> None:
        with pytest.raises(ValueError):
            build_gate_metadata_v3(
                cid="x",
                chain="NotAChain",
                token_address="0x" + "ab" * 20,
                threshold=1,
                epoch=0,
                encrypted_aes_key_b64="abc",
            )

    def test_invalid_token_address_rejected(self) -> None:
        with pytest.raises(ValueError):
            build_gate_metadata_v3(
                cid="x",
                chain="EthMainnet",
                token_address="not-an-address",
                threshold=1,
                epoch=0,
                encrypted_aes_key_b64="abc",
            )

    def test_negative_epoch_rejected(self) -> None:
        with pytest.raises(ValueError):
            build_gate_metadata_v3(
                cid="x",
                chain="EthMainnet",
                token_address="0x" + "ab" * 20,
                threshold=1,
                epoch=-1,
                encrypted_aes_key_b64="abc",
            )

    def test_threshold_zero_metadata_round_trips_through_dispatcher(self) -> None:
        gate = build_gate_metadata_v3(
            cid="x",
            chain="EthMainnet",
            token_address="0x" + "ab" * 20,
            threshold=0,
            epoch=0,
            encrypted_aes_key_b64="abc",
        )
        parsed = parse_gate_metadata(json.dumps(gate))
        assert parsed is not None
        assert parsed["threshold"] == "0"
        assert parsed["epoch"] == 0
