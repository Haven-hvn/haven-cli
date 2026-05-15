"""Haven-AOL ICP client utilities with fixed canister target.

This module enforces authenticated user identity for ICP calls and does not
allow configurable canister targets.
"""

from __future__ import annotations

import base64
import os
import secrets
import time
from dataclasses import dataclass
from typing import Protocol, Self, Type

from haven_cli.services.evm_utils import sign_gate_request_typed_data


class _HavenAolIdentity(Protocol):
    """Structural type for ``icp_identity.identity.Identity`` (no upstream ``py.typed``)."""

    anonymous: bool

    @classmethod
    def from_pem(cls, pem: str) -> Self: ...


HAVEN_AOL_CANISTER_ID = "bkyz2-fmaaa-aaaaa-qaaaq-cai"
HAVEN_AOL_DID = """type Chain = variant { EthMainnet; EthSepolia; ArbitrumOne; BaseMainnet; OptimismMainnet; };
type GateRequest = record {
  chain : Chain; tokenAddress : text; threshold : nat; cid : text; evmAddress : text;
  transportPublicKey : blob; nonce : nat; signature : blob; eip712ChainId : nat; eip712VerifyingContract : text;
};
type GateError = variant {
  InsufficientBalance : record { required : nat; actual : nat };
  InvalidAddress : text; InvalidThreshold; EvmRpcError : text; VetKDError : text;
  InvalidSignature : text; NonceAlreadyUsed;
};
type GateResult = variant { ok : blob; err : GateError; };
service : { requestDecryptionKey : (GateRequest) -> (GateResult); getVetKDPublicKey : () -> (blob); }
"""


@dataclass(frozen=True)
class HavenAolIcpConfig:
    """Runtime config for Haven-AOL ICP calls."""

    host: str
    identity_pem_path: str


def load_haven_aol_icp_config() -> HavenAolIcpConfig:
    """Load ICP identity config for Haven-AOL calls."""
    host = os.environ.get("HAVEN_ICP_HOST", "https://icp-api.io").strip()
    identity_pem_path = os.environ.get("HAVEN_ICP_IDENTITY_PEM_PATH", "").strip()
    if not identity_pem_path:
        raise RuntimeError(
            "HAVEN_ICP_IDENTITY_PEM_PATH is required for Haven-AOL ICP requests"
        )
    return HavenAolIcpConfig(host=host, identity_pem_path=identity_pem_path)


def _import_icp_core() -> tuple[type, type, type, Type[_HavenAolIdentity]]:
    from icp_agent.agent import Agent  # type: ignore[import-untyped]
    from icp_agent.client import Client  # type: ignore[import-untyped]
    from icp_canister.canister import Canister  # type: ignore[import-untyped]
    from icp_identity.identity import Identity  # type: ignore[import-untyped]

    return Agent, Canister, Client, Identity


def _icp_sdk() -> tuple[type, type, type, Type[_HavenAolIdentity]]:
    """Return icp-py-core SDK classes (optional ``haven-cli[icp]`` dependency)."""
    try:
        return _import_icp_core()
    except ImportError as exc:
        raise RuntimeError(
            "icp-py-core is required for authenticated Haven-AOL ICP calls (install haven-cli[icp])"
        ) from exc


def _icp_verify_certificate() -> bool:
    """Whether to verify IC certificates on update calls (requires ``blst``).

    Defaults to false so ``pip install icp-py-core`` works without building blst.
    Set ``HAVEN_ICP_VERIFY_CERTIFICATE`` to ``1`` / ``true`` / ``yes`` when blst is installed.
    """
    flag = os.environ.get("HAVEN_ICP_VERIFY_CERTIFICATE", "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def candid_return_item_to_value(item: object) -> object:
    """Normalize one Candid return slot from icp-py-core's decoded reply.

    icp-py-core ``decode()`` wraps each return value as ``{"type": str, "value": ...}``.
    Callers may also pass an already-unwrapped value (e.g. in tests); that is returned
    unchanged.
    """
    if isinstance(item, dict) and "type" in item and "value" in item:
        return item["value"]
    return item


def candid_blob_to_bytes(raw: object, *, context: str) -> bytes:
    """Convert Candid ``blob`` values to ``bytes``.

    The IC Python SDKs may decode Candid blobs as ``list[int]`` (or ``tuple[int, ...]``)
    rather than ``bytes``. VetKD and other crypto paths require ``bytes``.
    """
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if isinstance(raw, (list, tuple)):
        try:
            return bytes(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"{context}: Candid blob sequence must contain only integers in 0..255"
            ) from exc
    raise RuntimeError(
        f"{context}: unexpected payload type {type(raw).__name__!r}, "
        "expected bytes, bytearray, list[int], or tuple[int, ...]"
    )


def _first_return_slot(response: object, *, context: str) -> object:
    if not isinstance(response, list) or len(response) != 1:
        raise RuntimeError(f"Unexpected {context} response shape")
    return candid_return_item_to_value(response[0])


def get_vetkd_public_key_b64() -> str:
    """Fetch Haven-AOL VetKD public key from fixed canister.

    Requires authenticated user ICP identity. Anonymous access is not allowed.
    """
    cfg = load_haven_aol_icp_config()
    Agent, Canister, Client, Identity = _icp_sdk()

    try:
        pem = open(cfg.identity_pem_path, "r", encoding="utf-8").read()
    except OSError as exc:
        raise RuntimeError(
            f"Failed to read ICP identity PEM at {cfg.identity_pem_path!r}"
        ) from exc

    identity = Identity.from_pem(pem)
    if getattr(identity, "anonymous", False):
        raise RuntimeError("Anonymous ICP identity is not allowed for Haven-AOL requests")

    client = Client(url=cfg.host)
    agent = Agent(identity, client)
    canister = Canister(agent, HAVEN_AOL_CANISTER_ID, candid_str=HAVEN_AOL_DID)
    verify = _icp_verify_certificate()
    response = canister.getVetKDPublicKey(verify_certificate=verify)
    key_raw = _first_return_slot(response, context="getVetKDPublicKey")
    key_bytes = candid_blob_to_bytes(key_raw, context="getVetKDPublicKey")
    return base64.b64encode(key_bytes).decode("ascii")


def request_decryption_key(
    *,
    chain: str,
    token_address: str,
    threshold: int,
    cid: str,
) -> bytes:
    """Request an encrypted derived key from Haven-AOL canister.

    This performs an authenticated user call and EIP-712 signature proof.
    """
    if threshold < 1:
        raise ValueError(
            "Gate threshold must be >= 1 (Haven-AOL canister rejects 0 as InvalidThreshold)"
        )
    cfg = load_haven_aol_icp_config()
    evm_private_key = os.environ.get("HAVEN_PRIVATE_KEY", "").strip()
    if not evm_private_key:
        raise RuntimeError("HAVEN_PRIVATE_KEY is required for GateRequest signing")
    eip712_chain_id = int(os.environ.get("HAVEN_AOL_EIP712_CHAIN_ID", "1"))
    eip712_verifying_contract = os.environ.get("HAVEN_AOL_EIP712_VERIFYING_CONTRACT", "").strip()
    if not eip712_verifying_contract:
        raise RuntimeError("HAVEN_AOL_EIP712_VERIFYING_CONTRACT is required")
    transport_public_key_b64 = os.environ.get("HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64", "").strip()
    if not transport_public_key_b64:
        raise RuntimeError("HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64 is required")
    transport_public_key = base64.b64decode(transport_public_key_b64)
    # Combine time_ns with 8 random bytes to avoid collisions on clock
    # skew, low-resolution clocks, or rapid successive calls.
    nonce = (int(time.time_ns()) << 64) | int.from_bytes(secrets.token_bytes(8), "big")
    proof = sign_gate_request_typed_data(
        private_key=evm_private_key,
        transport_public_key=transport_public_key,
        nonce=nonce,
        chain_id=eip712_chain_id,
        verifying_contract=eip712_verifying_contract,
    )

    Agent, Canister, Client, Identity = _icp_sdk()

    try:
        pem = open(cfg.identity_pem_path, "r", encoding="utf-8").read()
    except OSError as exc:
        raise RuntimeError(f"Failed to read ICP identity PEM at {cfg.identity_pem_path!r}") from exc

    identity = Identity.from_pem(pem)
    if getattr(identity, "anonymous", False):
        raise RuntimeError("Anonymous ICP identity is not allowed for Haven-AOL requests")
    client = Client(url=cfg.host)
    agent = Agent(identity, client)
    canister = Canister(agent, HAVEN_AOL_CANISTER_ID, candid_str=HAVEN_AOL_DID)
    chain_variant = {chain: None}
    # Pass a single dict matching the Candid GateRequest record type.
    gate_request = {
        "chain": chain_variant,
        "tokenAddress": token_address,
        "threshold": threshold,
        "cid": cid,
        "evmAddress": proof.evm_address,
        "transportPublicKey": transport_public_key,
        "nonce": nonce,
        "signature": bytes.fromhex(proof.signature_hex.removeprefix("0x")),
        "eip712ChainId": proof.eip712_chain_id,
        "eip712VerifyingContract": proof.eip712_verifying_contract,
    }
    verify = _icp_verify_certificate()
    response = canister.requestDecryptionKey(gate_request, verify_certificate=verify)
    gate_result = _first_return_slot(response, context="requestDecryptionKey")
    if isinstance(gate_result, dict) and "ok" in gate_result:
        return candid_blob_to_bytes(
            gate_result["ok"], context="requestDecryptionKey ok"
        )
    if isinstance(gate_result, dict) and "err" in gate_result:
        raise RuntimeError(f"Haven-AOL requestDecryptionKey failed: {gate_result['err']}")
    raise RuntimeError("Unexpected GateResult payload")
