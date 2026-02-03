"""
JSON-RPC 2.0 Protocol Implementation.

Provides the protocol layer for communication with the JS runtime subprocess.
Handles message serialization, request/response matching, and error handling.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional, Union


class JSONRPCErrorCode(IntEnum):
    """Standard JSON-RPC 2.0 error codes."""
    
    # Standard errors
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    
    # Server errors (reserved range: -32000 to -32099)
    SERVER_ERROR = -32000
    TIMEOUT_ERROR = -32001
    RUNTIME_NOT_READY = -32002
    SDK_ERROR = -32003
    ENCRYPTION_ERROR = -32004
    UPLOAD_ERROR = -32005


class JSONRPCError(Exception):
    """JSON-RPC 2.0 error with code and optional data."""
    
    def __init__(
        self,
        code: Union[JSONRPCErrorCode, int],
        message: str,
        data: Optional[Any] = None
    ):
        super().__init__(message)
        self.code = int(code)
        self.message = message
        self.data = data
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-RPC error object."""
        error = {
            "code": self.code,
            "message": self.message
        }
        if self.data is not None:
            error["data"] = self.data
        return error
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JSONRPCError":
        """Create from JSON-RPC error object."""
        return cls(
            code=data.get("code", JSONRPCErrorCode.INTERNAL_ERROR),
            message=data.get("message", "Unknown error"),
            data=data.get("data")
        )
    
    @classmethod
    def parse_error(cls, data: Optional[Any] = None) -> "JSONRPCError":
        """Create a parse error."""
        return cls(JSONRPCErrorCode.PARSE_ERROR, "Parse error", data)
    
    @classmethod
    def invalid_request(cls, data: Optional[Any] = None) -> "JSONRPCError":
        """Create an invalid request error."""
        return cls(JSONRPCErrorCode.INVALID_REQUEST, "Invalid request", data)
    
    @classmethod
    def method_not_found(cls, method: str) -> "JSONRPCError":
        """Create a method not found error."""
        return cls(JSONRPCErrorCode.METHOD_NOT_FOUND, f"Method not found: {method}")
    
    @classmethod
    def invalid_params(cls, message: str) -> "JSONRPCError":
        """Create an invalid params error."""
        return cls(JSONRPCErrorCode.INVALID_PARAMS, message)
    
    @classmethod
    def internal_error(cls, message: str, data: Optional[Any] = None) -> "JSONRPCError":
        """Create an internal error."""
        return cls(JSONRPCErrorCode.INTERNAL_ERROR, message, data)
    
    @classmethod
    def timeout_error(cls, timeout_seconds: float) -> "JSONRPCError":
        """Create a timeout error."""
        return cls(
            JSONRPCErrorCode.TIMEOUT_ERROR,
            f"Request timed out after {timeout_seconds}s"
        )


@dataclass
class JSONRPCRequest:
    """JSON-RPC 2.0 request object."""
    
    method: str
    params: Optional[Union[list[Any], dict[str, Any]]] = None
    id: Optional[str] = field(default_factory=lambda: str(uuid.uuid4()))
    jsonrpc: str = "2.0"
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-RPC request object."""
        request: dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "method": self.method
        }
        if self.params is not None:
            request["params"] = self.params
        if self.id is not None:
            request["id"] = self.id
        return request
    
    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict())
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JSONRPCRequest":
        """Create from dictionary."""
        return cls(
            method=data["method"],
            params=data.get("params"),
            id=data.get("id"),
            jsonrpc=data.get("jsonrpc", "2.0")
        )
    
    @classmethod
    def from_json(cls, json_str: str) -> "JSONRPCRequest":
        """Parse from JSON string."""
        try:
            data = json.loads(json_str)
            return cls.from_dict(data)
        except json.JSONDecodeError as e:
            raise JSONRPCError.parse_error(str(e))
    
    @property
    def is_notification(self) -> bool:
        """Check if this is a notification (no id)."""
        return self.id is None


@dataclass
class JSONRPCResponse:
    """JSON-RPC 2.0 response object."""
    
    id: Optional[str]
    result: Optional[Any] = None
    error: Optional[JSONRPCError] = None
    jsonrpc: str = "2.0"
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-RPC response object."""
        response: dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "id": self.id
        }
        if self.error is not None:
            response["error"] = self.error.to_dict()
        else:
            response["result"] = self.result
        return response
    
    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict())
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JSONRPCResponse":
        """Create from dictionary."""
        error = None
        if "error" in data:
            error = JSONRPCError.from_dict(data["error"])
        
        return cls(
            id=data.get("id"),
            result=data.get("result"),
            error=error,
            jsonrpc=data.get("jsonrpc", "2.0")
        )
    
    @classmethod
    def from_json(cls, json_str: str) -> "JSONRPCResponse":
        """Parse from JSON string."""
        try:
            data = json.loads(json_str)
            return cls.from_dict(data)
        except json.JSONDecodeError as e:
            raise JSONRPCError.parse_error(str(e))
    
    @classmethod
    def success(cls, id: Optional[str], result: Any) -> "JSONRPCResponse":
        """Create a success response."""
        return cls(id=id, result=result)
    
    @classmethod
    def failure(cls, id: Optional[str], error: JSONRPCError) -> "JSONRPCResponse":
        """Create an error response."""
        return cls(id=id, error=error)
    
    @property
    def is_success(self) -> bool:
        """Check if this is a success response."""
        return self.error is None
    
    def raise_for_error(self) -> None:
        """Raise exception if this is an error response."""
        if self.error is not None:
            raise self.error


class JSONRPCProtocol:
    """
    JSON-RPC 2.0 protocol handler.
    
    Manages request ID tracking and response matching for async communication.
    """
    
    def __init__(self):
        self._pending_requests: dict[str, JSONRPCRequest] = {}
    
    def create_request(
        self,
        method: str,
        params: Optional[Union[list[Any], dict[str, Any]]] = None,
        notification: bool = False
    ) -> JSONRPCRequest:
        """
        Create a new JSON-RPC request.
        
        Args:
            method: The method name to call
            params: Optional parameters (positional or named)
            notification: If True, create a notification (no response expected)
        
        Returns:
            The created request object
        """
        request = JSONRPCRequest(
            method=method,
            params=params,
            id=None if notification else str(uuid.uuid4())
        )
        
        if not notification and request.id:
            self._pending_requests[request.id] = request
        
        return request
    
    def match_response(self, response: JSONRPCResponse) -> Optional[JSONRPCRequest]:
        """
        Match a response to its original request.
        
        Args:
            response: The response to match
        
        Returns:
            The original request, or None if not found
        """
        if response.id is None:
            return None
        
        return self._pending_requests.pop(response.id, None)
    
    def cancel_request(self, request_id: str) -> Optional[JSONRPCRequest]:
        """
        Cancel a pending request.
        
        Args:
            request_id: The ID of the request to cancel
        
        Returns:
            The cancelled request, or None if not found
        """
        return self._pending_requests.pop(request_id, None)
    
    def clear_pending(self) -> list[JSONRPCRequest]:
        """
        Clear all pending requests.
        
        Returns:
            List of cancelled requests
        """
        requests = list(self._pending_requests.values())
        self._pending_requests.clear()
        return requests
    
    @property
    def pending_count(self) -> int:
        """Get the number of pending requests."""
        return len(self._pending_requests)
    
    @property
    def pending_ids(self) -> list[str]:
        """Get the IDs of all pending requests."""
        return list(self._pending_requests.keys())


# Pre-defined method names for the JS runtime
class JSRuntimeMethods:
    """Standard method names for JS runtime communication."""
    
    # Lifecycle
    PING = "ping"
    SHUTDOWN = "shutdown"
    GET_STATUS = "getStatus"
    
    # Lit Protocol
    LIT_CONNECT = "lit.connect"
    LIT_ENCRYPT = "lit.encrypt"
    LIT_DECRYPT = "lit.decrypt"
    LIT_GET_SESSION = "lit.getSession"
    
    # Synapse SDK
    SYNAPSE_CONNECT = "synapse.connect"
    SYNAPSE_UPLOAD = "synapse.upload"
    SYNAPSE_DOWNLOAD = "synapse.download"
    SYNAPSE_GET_STATUS = "synapse.getStatus"
    SYNAPSE_GET_CID = "synapse.getCid"
    
    # Arkiv
    ARKIV_SYNC = "arkiv.sync"
    ARKIV_VERIFY = "arkiv.verify"
    ARKIV_GET_RECORD = "arkiv.getRecord"
