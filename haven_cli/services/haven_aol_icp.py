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
from typing import Any, Protocol, Self, Type

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
type GateResult = variant { ok : record { encrypted_key : blob; verification_key : blob }; err : GateError; };
type AttestRequest = record {
  chain : Chain; tokenAddress : text; threshold : nat; cidHash : text; evmAddress : text;
  nonce : nat; signature : blob; eip712ChainId : nat; eip712VerifyingContract : text;
};
type Attestation = record {
  evmAddress : text; chain : Chain; tokenAddress : text; threshold : nat;
  balanceAtCheck : nat; cidHash : text; timestamp : nat;
};
type AttestResult = variant {
  ok : record { attestation : Attestation; signature : blob };
  err : variant {
    InsufficientBalance : record { required : nat; actual : nat };
    InvalidAddress : text; InvalidThreshold; EvmRpcError : text; VetKDError : text;
    InvalidSignature : text; NonceAlreadyUsed;
  };
};
service : {
  requestDecryptionKey : (GateRequest) -> (GateResult);
  getVetKDPublicKey : () -> (blob) query;
  attestHolding : (AttestRequest) -> (AttestResult);
  getAttestationPublicKey : () -> (blob) query;
}
"""


@dataclass(frozen=True)
class DecryptionKeyResponse:
    """Response from requestDecryptionKey — bundled encrypted key + verification key."""

    encrypted_key: bytes
    verification_key: bytes


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
) -> DecryptionKeyResponse:
    """Request an encrypted derived key from Haven-AOL canister.

    Returns a ``DecryptionKeyResponse`` containing both the encrypted derived
    key and the verification key (bundled in the canister response).

    This performs an authenticated user call and EIP-712 signature proof.

    Retries on transient HTTP errors with exponential backoff.
    """
    if threshold < 0:
        raise ValueError(
            "Gate threshold must be >= 0 (Haven-AOL canister rejects negative values)"
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
        ok_record = gate_result["ok"]
        if not isinstance(ok_record, dict) or "encrypted_key" not in ok_record or "verification_key" not in ok_record:
            raise RuntimeError(
                f"Unexpected GateResult ok shape: expected record with "
                f"encrypted_key and verification_key, got {type(ok_record).__name__}"
            )
        return DecryptionKeyResponse(
            encrypted_key=candid_blob_to_bytes(
                ok_record["encrypted_key"], context="requestDecryptionKey encrypted_key"
            ),
            verification_key=candid_blob_to_bytes(
                ok_record["verification_key"], context="requestDecryptionKey verification_key"
            ),
        )
    if isinstance(gate_result, dict) and "err" in gate_result:
        raise RuntimeError(f"Haven-AOL requestDecryptionKey failed: {gate_result['err']}")
    raise RuntimeError("Unexpected GateResult payload")


def _sign_attest_request(
    *,
    private_key: str,
    evm_address: str,
    cid_hash: str,
    nonce: int,
    chain_id: int,
    verifying_contract: str,
) -> bytes:
    """Create EIP-712 signature for attestation request.

    Uses the AttestRequest type hash (separate from GateRequest to prevent
    cross-endpoint replay).

    Args:
        private_key: EVM private key (hex, with or without 0x prefix)
        evm_address: Wallet address being attested
        cid_hash: SHA-256 hash of content CID (hex string, 64 chars)
        nonce: Replay-prevention nonce
        chain_id: EIP-712 domain chain ID
        verifying_contract: EIP-712 domain verifying contract address

    Returns:
        65-byte EIP-712 signature (r || s || v)
    """
    try:
        from eth_account import Account
        from eth_account.messages import encode_typed_data
    except ImportError as exc:
        raise RuntimeError(
            "eth-account is required for EIP-712 signing. Install haven-cli[blockchain]."
        ) from exc

    full_message = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "AttestRequest": [
                {"name": "evmAddress", "type": "address"},
                {"name": "cidHash", "type": "bytes32"},
                {"name": "nonce", "type": "uint256"},
            ],
        },
        "primaryType": "AttestRequest",
        "domain": {
            "name": "HavenAOL",
            "chainId": chain_id,
            "verifyingContract": verifying_contract,
        },
        "message": {
            "evmAddress": evm_address,
            "cidHash": bytes.fromhex(cid_hash),
            "nonce": nonce,
        },
    }

    signable = encode_typed_data(full_message=full_message)
    normalized_key = private_key.strip()
    if not normalized_key.startswith("0x"):
        normalized_key = f"0x{normalized_key}"
    signed = Account.sign_message(signable, normalized_key)
    return signed.signature


@dataclass(frozen=True)
class AttestationResponse:
    """Response from attestHolding — attestation data + canister signature."""

    evm_address: str
    chain: str
    token_address: str
    threshold: int
    balance_at_check: int
    cid_hash: str
    timestamp: int
    signature: str  # hex-encoded Ed25519 signature


def attest_holding(
    *,
    private_key: str,
    chain: str,
    token_address: str,
    threshold: int,
    cid_hash: str,
    evm_address: str,
) -> dict[str, Any]:
    """Request a canister-signed attestation of token holding.

    The canister verifies EVM balance and returns a t-Schnorr/Ed25519 signed
    attestation proving the wallet held the token at call time.

    Args:
        private_key: EVM private key for EIP-712 signing
        chain: EVM chain name ("EthMainnet", "EthSepolia", etc.)
        token_address: Token contract address (0x...)
        threshold: Minimum balance required
        cid_hash: SHA-256 hash of content CID (binds attestation to entity)
        evm_address: Wallet address being attested

    Returns:
        Dict with attestation fields + signature (hex-encoded)

    Raises:
        RuntimeError: On canister error (insufficient balance, invalid sig, etc.)
    """
    if threshold <= 0:
        # The Haven-AOL canister returns #InvalidThreshold for threshold == 0
        # and would reject negative values. Validate at the boundary so we
        # produce a clear error rather than a confusing canister rejection.
        raise ValueError(
            "Gate threshold must be > 0 for attestation (canister returns InvalidThreshold for 0)"
        )

    # Normalize cid_hash: strip optional 0x prefix; canister expects 64-char hex.
    normalized_cid_hash = cid_hash.strip()
    if normalized_cid_hash.startswith("0x") or normalized_cid_hash.startswith("0X"):
        normalized_cid_hash = normalized_cid_hash[2:]
    if len(normalized_cid_hash) != 64:
        raise ValueError(
            f"cid_hash must be 64 hex chars (got {len(normalized_cid_hash)})"
        )
    try:
        bytes.fromhex(normalized_cid_hash)
    except ValueError as exc:
        raise ValueError(f"cid_hash is not valid hex: {exc}") from exc

    cfg = load_haven_aol_icp_config()
    eip712_chain_id = int(os.environ.get("HAVEN_AOL_EIP712_CHAIN_ID", "1"))
    eip712_verifying_contract = os.environ.get("HAVEN_AOL_EIP712_VERIFYING_CONTRACT", "").strip()
    if not eip712_verifying_contract:
        raise RuntimeError("HAVEN_AOL_EIP712_VERIFYING_CONTRACT is required")

    # Generate unique nonce
    nonce = (int(time.time_ns()) << 64) | int.from_bytes(secrets.token_bytes(8), "big")

    # Build EIP-712 signature for attestation request
    eip712_signature = _sign_attest_request(
        private_key=private_key,
        evm_address=evm_address,
        cid_hash=normalized_cid_hash,
        nonce=nonce,
        chain_id=eip712_chain_id,
        verifying_contract=eip712_verifying_contract,
    )
    if len(eip712_signature) != 65:
        # Canister enforces exactly 65 bytes (r || s || v).
        raise RuntimeError(
            f"EIP-712 attestation signature must be 65 bytes (got {len(eip712_signature)})"
        )

    # Set up ICP agent
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

    # Map chain string to Candid variant
    chain_variant = {chain: None}

    attest_request = {
        "chain": chain_variant,
        "tokenAddress": token_address,
        "threshold": threshold,
        "cidHash": normalized_cid_hash,
        "evmAddress": evm_address,
        "nonce": nonce,
        "signature": bytes(eip712_signature),
        "eip712ChainId": eip712_chain_id,
        "eip712VerifyingContract": eip712_verifying_contract,
    }
    logger.info(
        "attestHolding: chain=%s token=%s threshold=%d nonce=%d evmAddress=%s "
        "cidHash=%s eip712ChainId=%d eip712VerifyingContract=%s sig_len=%d",
        chain,
        token_address,
        threshold,
        nonce,
        evm_address,
        normalized_cid_hash,
        eip712_chain_id,
        eip712_verifying_contract,
        len(eip712_signature),
    )

    verify = _icp_verify_certificate()

    def _call():
        return canister.attestHolding(attest_request, verify_certificate=verify)

    response = _retry_on_transport_error(_call, context="attestHolding")
    attest_result = _first_return_slot(response, context="attestHolding")

    if isinstance(attest_result, dict) and "ok" in attest_result:
        ok_record = attest_result["ok"]
        if not isinstance(ok_record, dict) or "attestation" not in ok_record or "signature" not in ok_record:
            raise RuntimeError(
                f"Unexpected AttestResult ok shape: expected record with "
                f"attestation and signature, got {type(ok_record).__name__}"
            )

        attestation = ok_record["attestation"]
        # icp-py-core may wrap a returned record value as {"type":..., "value":...};
        # unwrap defensively so field access below is uniform.
        attestation = candid_return_item_to_value(attestation)
        if not isinstance(attestation, dict):
            raise RuntimeError(
                f"Unexpected attestation payload type {type(attestation).__name__}"
            )

        signature_bytes = candid_blob_to_bytes(
            ok_record["signature"], context="attestHolding signature"
        )

        # Extract chain name from Candid variant (e.g. {"EthMainnet": None} -> "EthMainnet")
        result_chain = chain
        attestation_chain = attestation.get("chain")
        if isinstance(attestation_chain, dict) and attestation_chain:
            result_chain = next(iter(attestation_chain))

        # Nat fields come back as int from icp-py-core; coerce defensively in case
        # they are returned as strings.
        def _as_int(value: Any, field: str) -> int:
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                return int(value)
            raise RuntimeError(
                f"Unexpected type for attestation.{field}: {type(value).__name__}"
            )

        return {
            "evmAddress": attestation["evmAddress"],
            "chain": result_chain,
            "tokenAddress": attestation["tokenAddress"],
            "threshold": _as_int(attestation["threshold"], "threshold"),
            "balanceAtCheck": _as_int(attestation["balanceAtCheck"], "balanceAtCheck"),
            "cidHash": attestation["cidHash"],
            "timestamp": _as_int(attestation["timestamp"], "timestamp"),
            "signature": signature_bytes.hex(),
        }

    if isinstance(attest_result, dict) and "err" in attest_result:
        err = attest_result["err"]
        # Unwrap icp-py-core's {"type": ..., "value": ...} wrapping if present.
        err = candid_return_item_to_value(err)
        if isinstance(err, dict) and err:
            err_variant = next(iter(err))
            err_detail = err[err_variant]
            raise RuntimeError(f"attestHolding failed: {err_variant}: {err_detail!r}")
        raise RuntimeError(f"attestHolding failed: {err!r}")

    raise RuntimeError(f"Unexpected AttestResult payload: {attest_result!r}")
