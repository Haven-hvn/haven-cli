"""Haven-AOL ICP client utilities with fixed canister target.

This module enforces authenticated user identity for ICP calls and does not
allow configurable canister targets.

Error handling strategy for ICP boundary-node HTTP 400:
  - ``icp-py-core`` wraps HTTP errors in ``icp_agent.client.TransportError``.
  - The original ``httpx.HTTPStatusError`` is available as ``.original_error``.
  - We catch ``TransportError``, extract the response body (which contains the
    boundary-node's specific rejection reason), and re-raise with a diagnostic
    message that preserves the root cause.
  - Transient errors (400 with certain rejection texts, 429, 5xx) are retried
    with exponential backoff; permanent errors (bad identity, bad canister ID)
    fail fast.
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Protocol, Self, Type

from haven_cli.services.evm_utils import sign_gate_request_typed_data

logger = logging.getLogger(__name__)


class _HavenAolIdentity(Protocol):
    """Structural type for ``icp_identity.identity.Identity`` (no upstream ``py.typed``)."""

    anonymous: bool

    @classmethod
    def from_pem(cls, pem: str) -> Self: ...


HAVEN_AOL_CANISTER_ID = "dciac-uaaaa-aaaad-qlzuq-cai"
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


def _icp_max_retries() -> int:
    """Maximum retry attempts for transient ICP HTTP errors.

    Set ``HAVEN_ICP_MAX_RETRIES`` to override (default: 3).
    Set to ``0`` to disable retries.
    """
    raw = os.environ.get("HAVEN_ICP_MAX_RETRIES", "3").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 3


def _icp_retry_base_delay() -> float:
    """Base delay in seconds for exponential backoff between retries.

    Set ``HAVEN_ICP_RETRY_BASE_DELAY`` to override (default: 1.0s).
    """
    raw = os.environ.get("HAVEN_ICP_RETRY_BASE_DELAY", "1.0").strip()
    try:
        return max(0.1, float(raw))
    except ValueError:
        return 1.0


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


def _extract_transport_error_detail(exc: object) -> str:
    """Extract the HTTP response body from an icp-py-core TransportError.

    ``icp_agent.client.TransportError`` wraps the original
    ``httpx.HTTPStatusError``, which in turn wraps the ``httpx.Response``.
    The response body often contains the boundary-node's specific rejection
    reason (e.g. "malformed CBOR", "invalid sender", "unknown API version").

    Returns a human-readable diagnostic string, or an empty string if the
    detail cannot be extracted.
    """
    # TransportError -> original_error is httpx.HTTPStatusError
    original = getattr(exc, "original_error", None)
    if original is None:
        return ""

    # httpx.HTTPStatusError -> response is httpx.Response
    response = getattr(original, "response", None)
    if response is None:
        # Fallback: try reading .request from the original
        return ""

    try:
        body = response.text
    except Exception:
        return ""

    if not body:
        return ""

    # Truncate long bodies to avoid flooding logs
    max_len = 512
    if len(body) > max_len:
        body = body[:max_len] + "…[truncated]"
    return body


def _classify_transport_error(exc: object) -> str:
    """Classify a TransportError as 'transient' or 'permanent'.

    Uses the HTTP status code and response body to decide.
    """
    original = getattr(exc, "original_error", None)
    if original is None:
        return "transient"  # network-level failure, retry

    response = getattr(original, "response", None)
    if response is None:
        return "transient"

    status = response.status_code

    # 429 Too Many Requests — always transient
    if status == 429:
        return "transient"

    # 5xx — transient (server-side)
    if 500 <= status < 600:
        return "transient"

    # 400 — inspect the body for clues
    if status == 400:
        try:
            body = (response.text or "").lower()
        except Exception:
            body = ""

        # These rejection reasons are typically transient (boundary-node
        # routing, temporary unavailability, rate limiting).
        transient_signs = [
            "temporarily unavailable",
            "try again",
            "timeout",
            "overloaded",
            "rate limit",
            "throttl",
        ]
        for sign in transient_signs:
            if sign in body:
                return "transient"

        # These rejection reasons are permanent (bad request structure).
        permanent_signs = [
            "malformed",
            "invalid sender",
            "unknown api version",
            "bad encoding",
            "invalid canister",
            "canister_not_found",
            "specified canister does not exist",
            "invalid principal",
            "unauthorized",
            "forbidden",
        ]
        for sign in permanent_signs:
            if sign in body:
                return "permanent"

        # Unknown 400 — treat as transient (could be a temporary gateway issue)
        return "transient"

    # Other 4xx — permanent
    if 400 <= status < 500:
        return "permanent"

    return "transient"


def _retry_on_transport_error(func, *, context: str):
    """Execute *func* with retry logic for transient TransportError failures.

    Extracts the boundary-node response body from each failure and includes it
    in the final error message for diagnostics.

    Args:
        func: Callable that performs the ICP call (may raise TransportError).
        context: Short description of the operation for error messages.

    Returns:
        The return value of *func*.

    Raises:
        RuntimeError: On permanent failures or when retries are exhausted.
    """
    max_retries = _icp_max_retries()
    base_delay = _icp_retry_base_delay()
    last_detail = ""
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as exc:
            # Save exc to a variable that persists beyond the except block
            # (Python 3 deletes the except-as variable at block end).
            last_exc = exc

            # Only retry TransportError (icp-py-core HTTP failures).
            # Use duck-typing rather than class name to avoid coupling to
            # the exact icp-py-core exception hierarchy.
            if not hasattr(exc, "original_error"):
                raise

            classification = _classify_transport_error(exc)
            last_detail = _extract_transport_error_detail(exc)

            if classification == "permanent" or attempt >= max_retries:
                break

            delay = base_delay * (2 ** attempt)
            logger.warning(
                "ICP %s transient error (attempt %d/%d, retry in %.1fs): %s%s",
                context,
                attempt + 1,
                max_retries + 1,
                delay,
                exc,
                f" — body: {last_detail}" if last_detail else "",
            )
            time.sleep(delay)

    # Retries exhausted or permanent — build a diagnostic error message
    assert last_exc is not None
    url = getattr(getattr(last_exc, "original_error", None), "url", "unknown")
    msg = f"ICP {context} failed (after {max_retries + 1} attempt(s))"
    if url and url != "unknown":
        msg += f" [url={url}]"
    msg += f": {last_exc}"
    if last_detail:
        msg += f"\nBoundary-node response body: {last_detail}"
    msg += _diagnostic_hints(last_exc)
    raise RuntimeError(msg) from last_exc


def _diagnostic_hints(exc: object) -> str:
    """Return actionable diagnostic hints based on the error details."""
    detail = _extract_transport_error_detail(exc).lower()
    hints = []

    if "malformed" in detail or "cbor" in detail:
        hints.append(
            "HINT: The request body was rejected as malformed. "
            "Check icp-py-core version compatibility with the boundary node."
        )
    if "invalid sender" in detail or "unauthorized" in detail:
        hints.append(
            "HINT: The ICP identity was rejected. "
            "Verify HAVEN_ICP_IDENTITY_PEM_PATH points to a valid non-anonymous PEM."
        )
    if "api version" in detail:
        hints.append(
            "HINT: The API version in the URL path may be incompatible. "
            "Check HAVEN_ICP_HOST matches the expected boundary-node format."
        )
    if "canister_not_found" in detail or "specified canister does not exist" in detail:
        hints.append(
            f"HINT: The boundary node reports this canister does not exist on this network. "
            f"Configured ID: {HAVEN_AOL_CANISTER_ID}. "
            "Confirm HAVEN_ICP_HOST (mainnet vs local) matches the deployment in the ICP dashboard."
        )
    elif "canister" in detail and "invalid" in detail:
        hints.append(
            f"HINT: The canister ID may be invalid. "
            f"Current: {HAVEN_AOL_CANISTER_ID}. "
            "Verify the canister is deployed on mainnet."
        )
    if not hints:
        hints.append(
            "HINT: Check HAVEN_ICP_HOST, HAVEN_ICP_IDENTITY_PEM_PATH, "
            "and icp-py-core version. See docs for ICP boundary-node troubleshooting."
        )

    return "\n" + "\n".join(hints)


def get_vetkd_public_key_b64() -> str:
    """Fetch Haven-AOL VetKD public key from fixed canister.

    Requires authenticated user ICP identity. Anonymous access is not allowed.

    Retries on transient HTTP errors (429, 5xx, certain 400s) with exponential
    backoff. Permanent errors (bad identity, malformed requests) fail fast.

    Raises:
        RuntimeError: On failure, with boundary-node response body included
            for diagnostics.
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

    def _call():
        return canister.getVetKDPublicKey(verify_certificate=verify)

    response = _retry_on_transport_error(_call, context="getVetKDPublicKey")
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

    Retries on transient HTTP errors with exponential backoff.
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

    def _call():
        return canister.requestDecryptionKey(gate_request, verify_certificate=verify)

    response = _retry_on_transport_error(_call, context="requestDecryptionKey")
    gate_result = _first_return_slot(response, context="requestDecryptionKey")
    if isinstance(gate_result, dict) and "ok" in gate_result:
        return candid_blob_to_bytes(
            gate_result["ok"], context="requestDecryptionKey ok"
        )
    if isinstance(gate_result, dict) and "err" in gate_result:
        raise RuntimeError(f"Haven-AOL requestDecryptionKey failed: {gate_result['err']}")
    raise RuntimeError("Unexpected GateResult payload")
