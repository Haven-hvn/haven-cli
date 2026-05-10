"""Tests for Haven-AOL ICP service configuration."""

import sys
import types
import pytest

from haven_cli.services.haven_aol_icp import (
    HAVEN_AOL_CANISTER_ID,
    get_vetkd_public_key_b64,
    load_haven_aol_icp_config,
)


def test_haven_aol_canister_is_fixed() -> None:
    assert HAVEN_AOL_CANISTER_ID == "bkyz2-fmaaa-aaaaa-qaaaq-cai"


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


def test_get_vetkd_public_key_calls_canister(monkeypatch, tmp_path) -> None:
    pem_path = tmp_path / "id.pem"
    pem_path.write_text("pem")
    monkeypatch.setenv("HAVEN_ICP_IDENTITY_PEM_PATH", str(pem_path))

    class FakeIdentity:
        anonymous = False

        @staticmethod
        def from_pem(pem: str):
            return FakeIdentity()

    class FakeClient:
        def __init__(self, url: str):
            self.url = url

    class FakeAgent:
        def __init__(self, identity, client):
            self.identity = identity
            self.client = client

    class FakeCanister:
        def __init__(self, agent, canister_id, candid=None):
            self.agent = agent
            self.canister_id = canister_id
            self.candid = candid

        def getVetKDPublicKey(self):
            return [b"\x01\x02"]

    monkeypatch.setitem(sys.modules, "ic.agent", types.SimpleNamespace(Agent=FakeAgent))
    monkeypatch.setitem(sys.modules, "ic.canister", types.SimpleNamespace(Canister=FakeCanister))
    monkeypatch.setitem(sys.modules, "ic.client", types.SimpleNamespace(Client=FakeClient))
    monkeypatch.setitem(sys.modules, "ic.identity", types.SimpleNamespace(Identity=FakeIdentity))

    assert get_vetkd_public_key_b64() == "AQI="
