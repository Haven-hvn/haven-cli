"""Tests for Haven-AOL ICP service configuration."""

import base64

import pytest

from haven_cli.services.evm_utils import GateRequestProof, build_gate_request_typed_data
from haven_cli.services.haven_aol_icp import (
    HAVEN_AOL_CANISTER_ID,
    candid_blob_to_bytes,
    candid_return_item_to_value,
    get_vetkd_public_key_b64,
    load_haven_aol_icp_config,
    request_decryption_key,
)
from haven_cli.services import haven_aol_icp as haven_aol_icp_module


def test_haven_aol_canister_is_fixed() -> None:
    assert HAVEN_AOL_CANISTER_ID == "bkyz2-fmaaa-aaaaa-qaaaq-cai"


def test_candid_blob_to_bytes_from_bytes() -> None:
    assert candid_blob_to_bytes(b"\xab\xcd", context="t") == b"\xab\xcd"


def test_candid_blob_to_bytes_from_bytearray() -> None:
    assert candid_blob_to_bytes(bytearray([1, 2]), context="t") == b"\x01\x02"


def test_candid_blob_to_bytes_from_int_list() -> None:
    assert candid_blob_to_bytes([1, 2, 255], context="t") == b"\x01\x02\xff"


def test_candid_blob_to_bytes_from_int_tuple() -> None:
    assert candid_blob_to_bytes((9, 10), context="t") == b"\x09\x0a"


def test_candid_blob_to_bytes_empty_sequence() -> None:
    assert candid_blob_to_bytes([], context="t") == b""


def test_candid_blob_to_bytes_rejects_bad_type() -> None:
    with pytest.raises(RuntimeError, match="unexpected payload type 'str'"):
        candid_blob_to_bytes("not-bytes", context="ctx")


def test_candid_blob_to_bytes_rejects_out_of_range_int() -> None:
    with pytest.raises(RuntimeError, match="must contain only integers"):
        candid_blob_to_bytes([1, 256], context="ctx")


def test_candid_blob_to_bytes_rejects_non_int_element() -> None:
    with pytest.raises(RuntimeError, match="must contain only integers"):
        candid_blob_to_bytes([1, "x"], context="ctx")


def test_candid_return_item_to_value_icp_decode_shape() -> None:
    assert candid_return_item_to_value({"type": "vec", "value": [1, 2]}) == [1, 2]


def test_candid_return_item_to_value_passthrough_raw() -> None:
    assert candid_return_item_to_value(b"raw") == b"raw"


def test_candid_return_item_to_value_dict_without_value_key() -> None:
    assert candid_return_item_to_value({"ok": 1}) == {"ok": 1}


def test_icp_verify_certificate_env(monkeypatch) -> None:
    monkeypatch.delenv("HAVEN_ICP_VERIFY_CERTIFICATE", raising=False)
    assert haven_aol_icp_module._icp_verify_certificate() is False
    monkeypatch.setenv("HAVEN_ICP_VERIFY_CERTIFICATE", "true")
    assert haven_aol_icp_module._icp_verify_certificate() is True


def test_load_haven_aol_icp_config_requires_identity_path(monkeypatch) -> None:
    monkeypatch.delenv("HAVEN_ICP_IDENTITY_PEM_PATH", raising=False)
    with pytest.raises(RuntimeError, match="HAVEN_ICP_IDENTITY_PEM_PATH"):
        load_haven_aol_icp_config()


def test_load_haven_aol_icp_config_defaults_host(monkeypatch) -> None:
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", "/tmp/id.pem")
    monkeypatch.delenv("HAVEN_ICP_HOST", raising=False)
    cfg = load_haven_aol_icp_config()
    assert cfg.identity_pem_path == "/tmp/id.pem"
    assert cfg.host == "https://icp-api.io"


def test_get_vetkd_public_key_requires_readable_identity(monkeypatch) -> None:
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", "/tmp/id.pem")
    with pytest.raises(RuntimeError, match="Failed to read ICP identity PEM"):
        get_vetkd_public_key_b64()


def _install_fake_ic_stack(monkeypatch, *, vetkd_response: object) -> None:
    class FakeIdentity:
        anonymous = False

        @staticmethod
        def from_pem(pem: str) -> FakeIdentity:
            return FakeIdentity()

    class FakeClient:
        def __init__(self, url: str = "") -> None:
            self.url = url

    class FakeAgent:
        def __init__(self, identity: object, client: object) -> None:
            self.identity = identity
            self.client = client

    class FakeCanister:
        def __init__(self, agent: object, canister_id: str, candid_str: str | None = None) -> None:
            self.agent = agent
            self.canister_id = canister_id
            self.candid_str = candid_str

        def getVetKDPublicKey(self, *, verify_certificate: bool = True) -> object:
            return vetkd_response

    def _fake_import() -> tuple[type, type, type, type]:
        return (FakeAgent, FakeCanister, FakeClient, FakeIdentity)

    monkeypatch.setattr(haven_aol_icp_module, "_import_icp_core", _fake_import)


def test_get_vetkd_public_key_calls_canister(monkeypatch, tmp_path) -> None:
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))
    _install_fake_ic_stack(monkeypatch, vetkd_response=[b"\x01\x02"])

    assert get_vetkd_public_key_b64() == "AQI="


def test_get_vetkd_public_key_accepts_int_list_blob(monkeypatch, tmp_path) -> None:
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))
    _install_fake_ic_stack(monkeypatch, vetkd_response=[[1, 2]])

    assert get_vetkd_public_key_b64() == "AQI="


def test_get_vetkd_public_key_bad_inner_payload(monkeypatch, tmp_path) -> None:
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))
    _install_fake_ic_stack(monkeypatch, vetkd_response=["not-a-blob"])

    with pytest.raises(RuntimeError, match="getVetKDPublicKey"):
        get_vetkd_public_key_b64()


def test_get_vetkd_public_key_bad_response_shape(monkeypatch, tmp_path) -> None:
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))
    _install_fake_ic_stack(monkeypatch, vetkd_response=[])

    with pytest.raises(RuntimeError, match="Unexpected getVetKDPublicKey response shape"):
        get_vetkd_public_key_b64()


def test_get_vetkd_public_key_icp_decode_wrapper_shape(monkeypatch, tmp_path) -> None:
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))
    _install_fake_ic_stack(monkeypatch, vetkd_response=[{"type": "vec", "value": [1, 2]}])

    assert get_vetkd_public_key_b64() == "AQI="


def test_get_vetkd_public_key_missing_icp_dependency(monkeypatch, tmp_path) -> None:
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))

    def _raise() -> tuple[type, type, type, type]:
        raise ImportError("simulated")

    monkeypatch.setattr(haven_aol_icp_module, "_import_icp_core", _raise)
    with pytest.raises(RuntimeError, match="icp-py-core is required"):
        get_vetkd_public_key_b64()


def _fake_gate_proof() -> GateRequestProof:
    evm = "0x" + "a" * 40
    contract = "0x" + "b" * 40
    transport = b"\x00" * 32
    typed = build_gate_request_typed_data(
        evm_address=evm,
        transport_public_key=transport,
        nonce=1,
        chain_id=1,
        verifying_contract=contract,
    )
    return GateRequestProof(
        evm_address=evm,
        nonce=1,
        signature_hex="0x" + "01" * 65,
        eip712_chain_id=1,
        eip712_verifying_contract=contract,
        typed_data=typed,
    )


def test_request_decryption_key_rejects_zero_threshold(monkeypatch, tmp_path) -> None:
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))
    with pytest.raises(ValueError, match="threshold must be >= 1"):
        request_decryption_key(
            chain="EthMainnet",
            token_address="0x" + "c" * 40,
            threshold=0,
            cid="cid",
        )


def test_request_decryption_key_ok_blob_as_int_list(monkeypatch, tmp_path) -> None:
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))
    monkeypatch.setenv("HAVEN_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.setenv("HAVEN_AOL_EIP712_VERIFYING_CONTRACT", "0x" + "b" * 40)
    monkeypatch.setenv(
        "HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64",
        base64.b64encode(b"\x00" * 32).decode("ascii"),
    )
    monkeypatch.setattr(
        "haven_cli.services.haven_aol_icp.sign_gate_request_typed_data",
        lambda **kwargs: _fake_gate_proof(),
    )

    class FakeIdentity:
        anonymous = False

        @staticmethod
        def from_pem(pem: str):
            return FakeIdentity()

    class FakeClient:
        def __init__(self, url: str = "") -> None:
            self.url = url

    class FakeAgent:
        def __init__(self, identity, client):
            self.identity = identity
            self.client = client

    class FakeCanister:
        def __init__(self, agent: object, canister_id: str, candid_str: str | None = None) -> None:
            pass

        def requestDecryptionKey(self, gate_request: object, *, verify_certificate: bool = True) -> object:
            return [{"ok": [7, 8, 9]}]

    monkeypatch.setattr(
        haven_aol_icp_module,
        "_import_icp_core",
        lambda: (FakeAgent, FakeCanister, FakeClient, FakeIdentity),
    )

    out = request_decryption_key(
        chain="EthMainnet",
        token_address="0x" + "c" * 40,
        threshold=1,
        cid="cid",
    )
    assert out == b"\x07\x08\x09"


def test_request_decryption_key_propagates_canister_err(monkeypatch, tmp_path) -> None:
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))
    monkeypatch.setenv("HAVEN_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.setenv("HAVEN_AOL_EIP712_VERIFYING_CONTRACT", "0x" + "b" * 40)
    monkeypatch.setenv(
        "HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64",
        base64.b64encode(b"\x00" * 32).decode("ascii"),
    )
    monkeypatch.setattr(
        "haven_cli.services.haven_aol_icp.sign_gate_request_typed_data",
        lambda **kwargs: _fake_gate_proof(),
    )

    class FakeIdentity:
        anonymous = False

        @staticmethod
        def from_pem(pem: str):
            return FakeIdentity()

    class FakeClient:
        def __init__(self, url: str = "") -> None:
            self.url = url

    class FakeAgent:
        def __init__(self, identity, client):
            self.identity = identity
            self.client = client

    class FakeCanister:
        def __init__(self, agent: object, canister_id: str, candid_str: str | None = None) -> None:
            pass

        def requestDecryptionKey(self, gate_request: object, *, verify_certificate: bool = True) -> object:
            return [{"err": {"InvalidThreshold": None}}]

    monkeypatch.setattr(
        haven_aol_icp_module,
        "_import_icp_core",
        lambda: (FakeAgent, FakeCanister, FakeClient, FakeIdentity),
    )

    with pytest.raises(RuntimeError, match="InvalidThreshold"):
        request_decryption_key(
            chain="EthMainnet",
            token_address="0x" + "c" * 40,
            threshold=1,
            cid="cid",
        )


def test_request_decryption_key_unexpected_gate_result(monkeypatch, tmp_path) -> None:
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))
    monkeypatch.setenv("HAVEN_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.setenv("HAVEN_AOL_EIP712_VERIFYING_CONTRACT", "0x" + "b" * 40)
    monkeypatch.setenv(
        "HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64",
        base64.b64encode(b"\x00" * 32).decode("ascii"),
    )
    monkeypatch.setattr(
        "haven_cli.services.haven_aol_icp.sign_gate_request_typed_data",
        lambda **kwargs: _fake_gate_proof(),
    )

    class FakeIdentity:
        anonymous = False

        @staticmethod
        def from_pem(pem: str):
            return FakeIdentity()

    class FakeClient:
        def __init__(self, url: str = "") -> None:
            self.url = url

    class FakeAgent:
        def __init__(self, identity, client):
            self.identity = identity
            self.client = client

    class FakeCanister:
        def __init__(self, agent: object, canister_id: str, candid_str: str | None = None) -> None:
            pass

        def requestDecryptionKey(self, gate_request: object, *, verify_certificate: bool = True) -> object:
            return ["nope"]

    monkeypatch.setattr(
        haven_aol_icp_module,
        "_import_icp_core",
        lambda: (FakeAgent, FakeCanister, FakeClient, FakeIdentity),
    )

    with pytest.raises(RuntimeError, match="Unexpected GateResult payload"):
        request_decryption_key(
            chain="EthMainnet",
            token_address="0x" + "c" * 40,
            threshold=1,
            cid="cid",
        )


def test_request_decryption_key_bad_response_shape(monkeypatch, tmp_path) -> None:
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))
    monkeypatch.setenv("HAVEN_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.setenv("HAVEN_AOL_EIP712_VERIFYING_CONTRACT", "0x" + "b" * 40)
    monkeypatch.setenv(
        "HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64",
        base64.b64encode(b"\x00" * 32).decode("ascii"),
    )
    monkeypatch.setattr(
        "haven_cli.services.haven_aol_icp.sign_gate_request_typed_data",
        lambda **kwargs: _fake_gate_proof(),
    )

    class FakeIdentity:
        anonymous = False

        @staticmethod
        def from_pem(pem: str):
            return FakeIdentity()

    class FakeClient:
        def __init__(self, url: str = "") -> None:
            self.url = url

    class FakeAgent:
        def __init__(self, identity, client):
            self.identity = identity
            self.client = client

    class FakeCanister:
        def __init__(self, agent: object, canister_id: str, candid_str: str | None = None) -> None:
            pass

        def requestDecryptionKey(self, gate_request: object, *, verify_certificate: bool = True) -> object:
            return []

    monkeypatch.setattr(
        haven_aol_icp_module,
        "_import_icp_core",
        lambda: (FakeAgent, FakeCanister, FakeClient, FakeIdentity),
    )

    with pytest.raises(RuntimeError, match="Unexpected requestDecryptionKey response shape"):
        request_decryption_key(
            chain="EthMainnet",
            token_address="0x" + "c" * 40,
            threshold=1,
            cid="cid",
        )


def test_request_decryption_key_ok_invalid_blob_type(monkeypatch, tmp_path) -> None:
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))
    monkeypatch.setenv("HAVEN_PRIVATE_KEY", "0x" + "1" * 64)
    monkeypatch.setenv("HAVEN_AOL_EIP712_VERIFYING_CONTRACT", "0x" + "b" * 40)
    monkeypatch.setenv(
        "HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64",
        base64.b64encode(b"\x00" * 32).decode("ascii"),
    )
    monkeypatch.setattr(
        "haven_cli.services.haven_aol_icp.sign_gate_request_typed_data",
        lambda **kwargs: _fake_gate_proof(),
    )

    class FakeIdentity:
        anonymous = False

        @staticmethod
        def from_pem(pem: str):
            return FakeIdentity()

    class FakeClient:
        def __init__(self, url: str = "") -> None:
            self.url = url

    class FakeAgent:
        def __init__(self, identity, client):
            self.identity = identity
            self.client = client

    class FakeCanister:
        def __init__(self, agent: object, canister_id: str, candid_str: str | None = None) -> None:
            pass

        def requestDecryptionKey(self, gate_request: object, *, verify_certificate: bool = True) -> object:
            return [{"ok": "broken"}]

    monkeypatch.setattr(
        haven_aol_icp_module,
        "_import_icp_core",
        lambda: (FakeAgent, FakeCanister, FakeClient, FakeIdentity),
    )

    with pytest.raises(RuntimeError, match="requestDecryptionKey ok"):
        request_decryption_key(
            chain="EthMainnet",
            token_address="0x" + "c" * 40,
            threshold=1,
            cid="cid",
        )
