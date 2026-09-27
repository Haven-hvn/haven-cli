"""Tests for the Tiramisu write dialect (direct ``execute`` encoding).

The bundled ``arkiv`` SDK only speaks the pre-Tiramisu ``execute`` ABI,
whose selector the live engine rejects. These tests pin the direct
encoding in :mod:`haven_cli.services.arkiv_sync` against the
``@arkiv-network/sdk`` wire format. No network access.
"""

import pytest
from eth_abi import decode as abi_decode
from eth_utils import keccak

from haven_cli.services.arkiv_sync import (
    TIRAMISU_MAX_ATTRIBUTES,
    TIRAMISU_MAX_PAYLOAD_BYTES,
    TIRAMISU_OP_CREATE,
    TIRAMISU_OP_PATCH,
    TIRAMISU_TYPE_BYTES,
    TIRAMISU_TYPE_I32,
    TIRAMISU_TYPE_STR,
    _tiramisu_encode_i32,
    _tiramisu_ident32,
    _tiramisu_min_lifetime,
    tiramisu_encode_cells,
    tiramisu_encode_create,
    tiramisu_encode_patch,
)

EXECUTE_SELECTOR = keccak(b"execute((uint8,bytes)[])")[:4]
ATTRS_TYPE = "(bytes32,uint8,bytes)[]"


def _outer(calldata: bytes):
    assert calldata[:4] == EXECUTE_SELECTOR
    (ops,) = abi_decode(["(uint8,bytes)[]"], calldata[4:])
    return ops


class TestIdent32:
    def test_left_aligned_and_padded(self):
        assert _tiramisu_ident32("grp") == b"grp" + b"\x00" * 29

    @pytest.mark.parametrize(
        "name", ["", "$payload", "0abc", "has space", "x" * 33, "str", "NOT"]
    )
    def test_rejects_bad_names(self, name):
        with pytest.raises(ValueError):
            _tiramisu_ident32(name)


class TestI32:
    def test_zero_and_positive(self):
        assert _tiramisu_encode_i32(0) == b"\x00" * 32
        assert _tiramisu_encode_i32(1) == b"\x00" * 31 + b"\x01"

    def test_negative_is_sign_extended(self):
        assert _tiramisu_encode_i32(-1) == b"\xff" * 32

    @pytest.mark.parametrize("value", [2**31, -(2**31) - 1])
    def test_range_checked(self, value):
        with pytest.raises(ValueError):
            _tiramisu_encode_i32(value)


class TestCells:
    def test_types_sorting_and_system_cells(self):
        cells = tiramisu_encode_cells(
            {"title": "Sintel", "dur_s": 889},
            b'{"fcid":"bafy"}',
            "application/json",
        )
        by_name = {name.rstrip(b"\x00").decode(): (tid, val) for name, tid, val in cells}
        assert by_name["title"] == (TIRAMISU_TYPE_STR, b"Sintel")
        assert by_name["dur_s"] == (TIRAMISU_TYPE_I32, _tiramisu_encode_i32(889))
        assert by_name["$payload"] == (TIRAMISU_TYPE_BYTES, b'{"fcid":"bafy"}')
        assert by_name["$contentType"] == (
            TIRAMISU_TYPE_STR,
            b"application/json",
        )
        names = [name for name, _, _ in cells]
        assert names == sorted(names)

    def test_rejects_bool_and_big_payload(self):
        with pytest.raises(ValueError):
            tiramisu_encode_cells({"flag": True}, b"{}", "application/json")
        with pytest.raises(ValueError):
            tiramisu_encode_cells({}, b"x" * (TIRAMISU_MAX_PAYLOAD_BYTES + 1), "t")

    def test_attribute_cap(self):
        with pytest.raises(ValueError):
            tiramisu_encode_cells(
                {f"a{i}": "v" for i in range(TIRAMISU_MAX_ATTRIBUTES)},
                b"{}",
                "t",
            )


class TestMinLifetime:
    def test_even_is_exact_and_odd_rounds_up(self):
        assert _tiramisu_min_lifetime(4) == 2
        assert _tiramisu_min_lifetime(5) == 3

    def test_rejects_non_positive(self):
        with pytest.raises(ValueError):
            _tiramisu_min_lifetime(0)


class TestEncodeCreate:
    def test_tagged_union_shape(self):
        calldata = tiramisu_encode_create(
            {"grp": "haven.video.full"},
            b"{}",
            "application/json",
            120,
            salt=7,
        )
        [(tag, operation_data)] = _outer(calldata)
        assert tag == TIRAMISU_OP_CREATE
        [(salt, expires_at, min_lifetime, flags, attrs)] = abi_decode(
            ["(uint128,uint64,uint64,uint8," + ATTRS_TYPE + ")"],
            operation_data,
        )
        assert (salt, expires_at, min_lifetime, flags) == (7, 0, 60, 0)
        assert len(attrs) == 3  # grp + $payload + $contentType

    def test_default_salt_is_128_bits(self):
        seen = {
            abi_decode(
                ["(uint128,uint64,uint64,uint8," + ATTRS_TYPE + ")"],
                _outer(
                    tiramisu_encode_create({}, b"{}", "t", 120),
                )[0][1],
            )[0][0]
            for _ in range(2)
        }
        assert len(seen) == 2
        assert all(0 <= s < 2**128 for s in seen)


class TestEncodePatch:
    def test_patch_shape_and_key(self):
        key = "0x" + "ab" * 32
        calldata = tiramisu_encode_patch(key, {"grp": "g"}, b"{}", "t")
        [(tag, operation_data)] = _outer(calldata)
        assert tag == TIRAMISU_OP_PATCH
        [(embedded, _attrs)] = abi_decode(
            ["(bytes32," + ATTRS_TYPE + ")"], operation_data
        )
        assert embedded == bytes.fromhex("ab" * 32)

    def test_rejects_bad_key(self):
        with pytest.raises(ValueError):
            tiramisu_encode_patch("0x1234", {}, b"{}", "t")
