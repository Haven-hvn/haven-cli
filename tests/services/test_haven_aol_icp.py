"""Tests for Haven-AOL ICP service configuration."""

import base64

import pytest

from haven_cli.services.evm_utils import GateRequestProof, build_gate_request_typed_data
from haven_cli.services.haven_aol_icp import (
    HAVEN_AOL_CANISTER_ID,
    DecryptionKeyResponse,
    _classify_transport_error,
    _extract_transport_error_detail,
    _icp_max_retries,
    _icp_retry_base_delay,
    _sign_attest_request,
    attest_holding,
    candid_blob_to_bytes,
    candid_return_item_to_value,
    get_vetkd_public_key_b64,
    load_haven_aol_icp_config,
    request_decryption_key,
)
from haven_cli.services import haven_aol_icp as haven_aol_icp_module


def test_haven_aol_canister_is_fixed() -> None:
    assert HAVEN_AOL_CANISTER_ID == "dciac-uaaaa-aaaad-qlzuq-cai"


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


def test_request_decryption_key_rejects_negative_threshold(monkeypatch, tmp_path) -> None:
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))
    with pytest.raises(ValueError, match="threshold must be >= 0"):
        request_decryption_key(
            chain="EthMainnet",
            token_address="0x" + "c" * 40,
            threshold=-1,
            cid="cid",
        )


def test_request_decryption_key_ok_record(monkeypatch, tmp_path) -> None:
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
            return [{"ok": {"encrypted_key": [7, 8, 9], "verification_key": [1, 2, 3]}}]

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
    assert isinstance(out, DecryptionKeyResponse)
    assert out.encrypted_key == b"\x07\x08\x09"
    assert out.verification_key == b"\x01\x02\x03"


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


def test_request_decryption_key_ok_invalid_shape(monkeypatch, tmp_path) -> None:
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

    with pytest.raises(RuntimeError, match="Unexpected GateResult ok shape"):
        request_decryption_key(
            chain="EthMainnet",
            token_address="0x" + "c" * 40,
            threshold=1,
            cid="cid",
        )


# ---------------------------------------------------------------------------
# Tests for TransportError extraction and classification helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for httpx.Response."""

    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeHTTPStatusError(Exception):
    """Minimal stand-in for httpx.HTTPStatusError."""

    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        super().__init__(f"HTTP {response.status_code}")


class _FakeTransportError(Exception):
    """Minimal stand-in for icp_agent.client.TransportError."""

    def __init__(self, url: str, original_error: Exception) -> None:
        self.url = url
        self.original_error = original_error
        super().__init__(f"Transport error: {original_error}")


def test_extract_transport_error_detail_400_body() -> None:
    body = "malformed CBOR in request envelope"
    resp = _FakeResponse(400, body)
    err = _FakeTransportError("https://icp-api.io/api/v4/canister/.../call", _FakeHTTPStatusError(resp))
    detail = _extract_transport_error_detail(err)
    assert detail == body


def test_extract_transport_error_detail_truncates_long_body() -> None:
    body = "x" * 600
    resp = _FakeResponse(400, body)
    err = _FakeTransportError("https://icp-api.io", _FakeHTTPStatusError(resp))
    detail = _extract_transport_error_detail(err)
    assert len(detail) <= 512 + len("…[truncated]")
    assert detail.endswith("…[truncated]")


def test_extract_transport_error_detail_no_original() -> None:
    err = _FakeTransportError("https://icp-api.io", None)
    assert _extract_transport_error_detail(err) == ""


def test_extract_transport_error_detail_no_response() -> None:
    err = _FakeTransportError("https://icp-api.io", Exception("no response"))
    assert _extract_transport_error_detail(err) == ""


def test_classify_transport_error_429_is_transient() -> None:
    resp = _FakeResponse(429, "rate limited")
    err = _FakeTransportError("https://icp-api.io", _FakeHTTPStatusError(resp))
    assert _classify_transport_error(err) == "transient"


def test_classify_transport_error_500_is_transient() -> None:
    resp = _FakeResponse(500, "internal error")
    err = _FakeTransportError("https://icp-api.io", _FakeHTTPStatusError(resp))
    assert _classify_transport_error(err) == "transient"


def test_classify_transport_error_400_malformed_is_permanent() -> None:
    resp = _FakeResponse(400, "malformed CBOR envelope")
    err = _FakeTransportError("https://icp-api.io", _FakeHTTPStatusError(resp))
    assert _classify_transport_error(err) == "permanent"


def test_classify_transport_error_400_invalid_sender_is_permanent() -> None:
    resp = _FakeResponse(400, "invalid sender principal")
    err = _FakeTransportError("https://icp-api.io", _FakeHTTPStatusError(resp))
    assert _classify_transport_error(err) == "permanent"


def test_classify_transport_error_400_canister_not_found_is_permanent() -> None:
    body = '{"error":"canister_not_found","details":"The specified canister does not exist"}'
    resp = _FakeResponse(400, body)
    err = _FakeTransportError("https://icp-api.io", _FakeHTTPStatusError(resp))
    assert _classify_transport_error(err) == "permanent"


def test_classify_transport_error_400_temporarily_unavailable_is_transient() -> None:
    resp = _FakeResponse(400, "temporarily unavailable, try again later")
    err = _FakeTransportError("https://icp-api.io", _FakeHTTPStatusError(resp))
    assert _classify_transport_error(err) == "transient"


def test_classify_transport_error_400_unknown_is_transient() -> None:
    resp = _FakeResponse(400, "something went wrong")
    err = _FakeTransportError("https://icp-api.io", _FakeHTTPStatusError(resp))
    assert _classify_transport_error(err) == "transient"


def test_classify_transport_error_401_is_permanent() -> None:
    resp = _FakeResponse(401, "unauthorized")
    err = _FakeTransportError("https://icp-api.io", _FakeHTTPStatusError(resp))
    assert _classify_transport_error(err) == "permanent"


def test_classify_transport_error_no_response_is_transient() -> None:
    err = _FakeTransportError("https://icp-api.io", Exception("connection refused"))
    assert _classify_transport_error(err) == "transient"


def test_icp_max_retries_default(monkeypatch) -> None:
    monkeypatch.delenv("HAVEN_ICP_MAX_RETRIES", raising=False)
    assert _icp_max_retries() == 3


def test_icp_max_retries_override(monkeypatch) -> None:
    monkeypatch.setenv("HAVEN_ICP_MAX_RETRIES", "5")
    assert _icp_max_retries() == 5


def test_icp_max_retries_zero(monkeypatch) -> None:
    monkeypatch.setenv("HAVEN_ICP_MAX_RETRIES", "0")
    assert _icp_max_retries() == 0


def test_icp_max_retries_invalid(monkeypatch) -> None:
    monkeypatch.setenv("HAVEN_ICP_MAX_RETRIES", "abc")
    assert _icp_max_retries() == 3


def test_icp_retry_base_delay_default(monkeypatch) -> None:
    monkeypatch.delenv("HAVEN_ICP_RETRY_BASE_DELAY", raising=False)
    assert _icp_retry_base_delay() == 1.0


def test_icp_retry_base_delay_override(monkeypatch) -> None:
    monkeypatch.setenv("HAVEN_ICP_RETRY_BASE_DELAY", "2.5")
    assert _icp_retry_base_delay() == 2.5


def test_icp_retry_base_delay_invalid(monkeypatch) -> None:
    monkeypatch.setenv("HAVEN_ICP_RETRY_BASE_DELAY", "abc")
    assert _icp_retry_base_delay() == 1.0


def test_get_vetkd_public_key_retries_on_transient_400(monkeypatch, tmp_path) -> None:
    """Transient 400 triggers retry and eventually succeeds."""
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))
    monkeypatch.setenv("HAVEN_ICP_MAX_RETRIES", "2")
    monkeypatch.setenv("HAVEN_ICP_RETRY_BASE_DELAY", "0.01")

    call_count = 0

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
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise _FakeTransportError(
                    "https://icp-api.io",
                    _FakeHTTPStatusError(_FakeResponse(400, "temporarily unavailable")),
                )
            return [b"\x01\x02"]

    def _fake_import():
        return (FakeAgent, FakeCanister, FakeClient, FakeIdentity)

    monkeypatch.setattr(haven_aol_icp_module, "_import_icp_core", _fake_import)

    result = get_vetkd_public_key_b64()
    assert result == "AQI="
    assert call_count == 2


def test_get_vetkd_public_key_permanent_400_fails_fast(monkeypatch, tmp_path) -> None:
    """Permanent 400 (malformed) fails without retry."""
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))
    monkeypatch.setenv("HAVEN_ICP_MAX_RETRIES", "3")
    monkeypatch.setenv("HAVEN_ICP_RETRY_BASE_DELAY", "0.01")

    call_count = 0

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
            nonlocal call_count
            call_count += 1
            raise _FakeTransportError(
                "https://icp-api.io",
                _FakeHTTPStatusError(_FakeResponse(400, "malformed CBOR envelope")),
            )

    def _fake_import():
        return (FakeAgent, FakeCanister, FakeClient, FakeIdentity)

    monkeypatch.setattr(haven_aol_icp_module, "_import_icp_core", _fake_import)

    with pytest.raises(RuntimeError, match="malformed CBOR"):
        get_vetkd_public_key_b64()
    assert call_count == 1  # no retries for permanent errors


def test_get_vetkd_public_key_retries_exhausted(monkeypatch, tmp_path) -> None:
    """After all retries are exhausted, RuntimeError includes diagnostic info."""
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))
    monkeypatch.setenv("HAVEN_ICP_MAX_RETRIES", "2")
    monkeypatch.setenv("HAVEN_ICP_RETRY_BASE_DELAY", "0.01")

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
            raise _FakeTransportError(
                "https://icp-api.io",
                _FakeHTTPStatusError(_FakeResponse(500, "internal server error")),
            )

    def _fake_import():
        return (FakeAgent, FakeCanister, FakeClient, FakeIdentity)

    monkeypatch.setattr(haven_aol_icp_module, "_import_icp_core", _fake_import)

    with pytest.raises(RuntimeError, match="after 3 attempt"):
        get_vetkd_public_key_b64()


# ---------------------------------------------------------------------------
# Tests for attest_holding (canister-signed attestation)
# ---------------------------------------------------------------------------


_ATTEST_TEST_PRIVATE_KEY = "0x" + "11" * 32
_ATTEST_TEST_CID_HASH = "ab" * 32
_ATTEST_TEST_VERIFYING_CONTRACT = "0x" + "00" * 20


def _attest_env(monkeypatch, tmp_path) -> None:
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))
    monkeypatch.setenv("HAVEN_AOL_EIP712_CHAIN_ID", "1")
    monkeypatch.setenv("HAVEN_AOL_EIP712_VERIFYING_CONTRACT", _ATTEST_TEST_VERIFYING_CONTRACT)


def _install_fake_attest_canister(monkeypatch, attest_response):
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
        def __init__(self, agent, canister_id, candid_str=None):
            self.last_request = None

        def attestHolding(self, req, *, verify_certificate=True):
            self.last_request = req
            return attest_response

    monkeypatch.setattr(
        haven_aol_icp_module,
        "_import_icp_core",
        lambda: (FakeAgent, FakeCanister, FakeClient, FakeIdentity),
    )


def test_sign_attest_request_returns_65_byte_signature() -> None:
    sig = _sign_attest_request(
        private_key=_ATTEST_TEST_PRIVATE_KEY,
        evm_address="0x" + "aa" * 20,
        cid_hash=_ATTEST_TEST_CID_HASH,
        nonce=42,
        chain_id=1,
        verifying_contract=_ATTEST_TEST_VERIFYING_CONTRACT,
    )
    assert len(sig) == 65
    # Last byte is the EIP-155 recovery id; backend requires 27 or 28.
    assert sig[-1] in (27, 28)


def test_attest_holding_rejects_zero_threshold(monkeypatch, tmp_path) -> None:
    _attest_env(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="threshold must be > 0"):
        attest_holding(
            private_key=_ATTEST_TEST_PRIVATE_KEY,
            chain="EthMainnet",
            token_address="0x" + "c" * 40,
            threshold=0,
            cid_hash=_ATTEST_TEST_CID_HASH,
            evm_address="0x" + "aa" * 20,
        )


def test_attest_holding_rejects_bad_cid_hash_length(monkeypatch, tmp_path) -> None:
    _attest_env(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="cid_hash must be 64 hex chars"):
        attest_holding(
            private_key=_ATTEST_TEST_PRIVATE_KEY,
            chain="EthMainnet",
            token_address="0x" + "c" * 40,
            threshold=1,
            cid_hash="abcd",
            evm_address="0x" + "aa" * 20,
        )


def test_attest_holding_strips_0x_from_cid_hash(monkeypatch, tmp_path) -> None:
    _attest_env(monkeypatch, tmp_path)
    _install_fake_attest_canister(
        monkeypatch,
        attest_response=[
            {
                "ok": {
                    "attestation": {
                        "evmAddress": "0x" + "aa" * 20,
                        "chain": {"EthMainnet": None},
                        "tokenAddress": "0x" + "c" * 40,
                        "threshold": 1,
                        "balanceAtCheck": 5,
                        "cidHash": _ATTEST_TEST_CID_HASH,
                        "timestamp": 1700000000,
                    },
                    "signature": [1, 2, 3],
                }
            }
        ],
    )

    out = attest_holding(
        private_key=_ATTEST_TEST_PRIVATE_KEY,
        chain="EthMainnet",
        token_address="0x" + "c" * 40,
        threshold=1,
        cid_hash="0x" + _ATTEST_TEST_CID_HASH,
        evm_address="0x" + "aa" * 20,
    )
    assert out["cidHash"] == _ATTEST_TEST_CID_HASH
    assert out["signature"] == "010203"
    assert out["balanceAtCheck"] == 5
    assert out["chain"] == "EthMainnet"


def test_attest_holding_surfaces_canister_err(monkeypatch, tmp_path) -> None:
    _attest_env(monkeypatch, tmp_path)
    _install_fake_attest_canister(
        monkeypatch,
        attest_response=[{"err": {"InvalidSignature": "ecrecover failed"}}],
    )

    with pytest.raises(RuntimeError, match="InvalidSignature.*ecrecover"):
        attest_holding(
            private_key=_ATTEST_TEST_PRIVATE_KEY,
            chain="EthMainnet",
            token_address="0x" + "c" * 40,
            threshold=1,
            cid_hash=_ATTEST_TEST_CID_HASH,
            evm_address="0x" + "aa" * 20,
        )


def test_attest_holding_surfaces_insufficient_balance(monkeypatch, tmp_path) -> None:
    _attest_env(monkeypatch, tmp_path)
    _install_fake_attest_canister(
        monkeypatch,
        attest_response=[
            {"err": {"InsufficientBalance": {"required": 1, "actual": 0}}}
        ],
    )

    with pytest.raises(RuntimeError, match="InsufficientBalance"):
        attest_holding(
            private_key=_ATTEST_TEST_PRIVATE_KEY,
            chain="EthMainnet",
            token_address="0x" + "c" * 40,
            threshold=1,
            cid_hash=_ATTEST_TEST_CID_HASH,
            evm_address="0x" + "aa" * 20,
        )


def test_attest_holding_unexpected_payload(monkeypatch, tmp_path) -> None:
    _attest_env(monkeypatch, tmp_path)
    _install_fake_attest_canister(monkeypatch, attest_response=["unexpected"])

    with pytest.raises(RuntimeError, match="Unexpected AttestResult payload"):
        attest_holding(
            private_key=_ATTEST_TEST_PRIVATE_KEY,
            chain="EthMainnet",
            token_address="0x" + "c" * 40,
            threshold=1,
            cid_hash=_ATTEST_TEST_CID_HASH,
            evm_address="0x" + "aa" * 20,
        )
