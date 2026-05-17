"""Tests for Haven-AOL gate v1 metadata helpers."""

import json

import pytest

from haven_cli.crypto.gate_metadata import (
    GATE_METADATA_VERSION,
    build_gate_metadata,
    gate_metadata_to_json,
    is_gate_metadata,
    merge_encrypt_result_gate,
    parse_gate_metadata,
)


def _sample_gate() -> dict:
    return build_gate_metadata(
        cid="bafytest",
        chain="EthMainnet",
        token_address="0x" + "ab" * 20,
        threshold=1,
        encrypted_aes_key_b64="encrypted-key-b64",
    )


class TestGateMetadata:
    def test_build_gate_metadata(self):
        gate = _sample_gate()
        assert gate["version"] == GATE_METADATA_VERSION
        assert gate["threshold"] == "1"
        assert is_gate_metadata(gate)

    def test_merge_encrypt_result_gate(self):
        partial = {
            "version": 1,
            "cid": "sha256:abc",
            "chain": "EthSepolia",
            "tokenAddress": "0x" + "cd" * 20,
            "threshold": "1",
        }
        gate = merge_encrypt_result_gate(partial, "key-b64")
        assert gate["encryptedAesKey"] == "key-b64"
        assert is_gate_metadata(gate)

    def test_parse_gate_metadata_string(self):
        gate = _sample_gate()
        parsed = parse_gate_metadata(json.dumps(gate))
        assert parsed == gate

    def test_parse_rejects_hybrid_v1(self):
        legacy = {
            "version": "hybrid-v1",
            "encryptedKey": "x",
            "keyHash": "y",
            "iv": "z",
            "accessControlConditions": [],
            "chain": "ethereum",
        }
        assert parse_gate_metadata(legacy) is None

    def test_gate_metadata_to_json_roundtrip(self):
        gate = _sample_gate()
        raw = gate_metadata_to_json(gate)
        assert parse_gate_metadata(raw) == gate

    def test_build_gate_metadata_requires_fields(self):
        with pytest.raises(ValueError):
            build_gate_metadata(
                cid="",
                chain="EthMainnet",
                token_address="0x" + "ab" * 20,
                threshold=1,
                encrypted_aes_key_b64="k",
            )
