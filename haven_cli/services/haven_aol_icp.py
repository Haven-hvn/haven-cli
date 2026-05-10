"""Haven-AOL ICP client utilities with fixed canister target.

This module enforces authenticated user identity for ICP calls and does not
allow configurable canister targets.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass

from haven_cli.services.evm_utils import sign_gate_request_typed_data


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


def get_vetkd_public_key_b64() -> str:
    """Fetch Haven-AOL VetKD public key from fixed canister.

    Requires authenticated user ICP identity. Anonymous access is not allowed.
    """
    cfg = load_haven_aol_icp_config()
    try:
        from ic.agent import Agent
        from ic.canister import Canister
        from ic.client import Client
        from ic.identity import Identity
    except ImportError as exc:  # pragma: no cover - runtime dependency guard
        raise RuntimeError(
            "ic-py is required for authenticated Haven-AOL ICP calls"
        ) from exc

    try:
        pem = open(cfg.identity_pem_path, "r", encoding="utf-8").read()
    except OSError as exc:
        raise RuntimeError(
            f"Failed to read ICP identity PEM at {cfg.identity_pem_path!r}"
        ) from exc

    identity = Identity.from_pem(pem)
    if getattr(identity, "anonymous", False):
        raise RuntimeError("Anonymous ICP identity is not allowed for Haven-AOL requests")

    client = Client(cfg.host)
    agent = Agent(identity, client)
    canister = Canister(agent=agent, canister_id=HAVEN_AOL_CANISTER_ID, candid=HAVEN_AOL_DID)
    response = canister.getVetKDPublicKey()
    if not isinstance(response, list) or len(response) != 1:
        raise RuntimeError("Unexpected getVetKDPublicKey response shape")
    key_bytes = response[0]
    if not isinstance(key_bytes, (bytes, bytearray)):
        raise RuntimeError("Unexpected getVetKDPublicKey payload type")
    return base64.b64encode(bytes(key_bytes)).decode("ascii")


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
    nonce = int(time.time_ns())
    proof = sign_gate_request_typed_data(
        private_key=evm_private_key,
        transport_public_key=transport_public_key,
        nonce=nonce,
        chain_id=eip712_chain_id,
        verifying_contract=eip712_verifying_contract,
    )

    try:
        from ic.agent import Agent
        from ic.canister import Canister
        from ic.client import Client
        from ic.identity import Identity
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("ic-py is required for requestDecryptionKey call") from exc

    try:
        pem = open(cfg.identity_pem_path, "r", encoding="utf-8").read()
    except OSError as exc:
        raise RuntimeError(f"Failed to read ICP identity PEM at {cfg.identity_pem_path!r}") from exc

    identity = Identity.from_pem(pem)
    if getattr(identity, "anonymous", False):
        raise RuntimeError("Anonymous ICP identity is not allowed for Haven-AOL requests")
    client = Client(cfg.host)
    agent = Agent(identity, client)
    canister = Canister(agent=agent, canister_id=HAVEN_AOL_CANISTER_ID, candid=HAVEN_AOL_DID)
    chain_variant = {chain: None}
    response = canister.requestDecryptionKey(
        chain_variant,
        token_address,
        threshold,
        cid,
        proof.evm_address,
        transport_public_key,
        nonce,
        bytes.fromhex(proof.signature_hex.removeprefix("0x")),
        proof.eip712_chain_id,
        proof.eip712_verifying_contract,
    )
    if not isinstance(response, list) or len(response) != 1:
        raise RuntimeError("Unexpected requestDecryptionKey response shape")
    gate_result = response[0]
    if isinstance(gate_result, dict) and "ok" in gate_result:
        return gate_result["ok"]
    if isinstance(gate_result, dict) and "err" in gate_result:
        raise RuntimeError(f"Haven-AOL requestDecryptionKey failed: {gate_result['err']}")
    raise RuntimeError("Unexpected GateResult payload")

