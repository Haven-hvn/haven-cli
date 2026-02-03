"""JS Runtime Bridge for communicating with Deno/Node subprocess.

The JS Runtime Bridge enables Python to communicate with JavaScript
SDKs (Lit Protocol, Synapse) running in a Deno subprocess using
JSON-RPC over stdio.
"""

from haven_cli.js_runtime.bridge import JSRuntimeBridge, RuntimeConfig, RuntimeState
from haven_cli.js_runtime.discovery import (
    RuntimeInfo,
    RuntimeType,
    discover_runtime,
    discover_all_runtimes,
    check_runtime_available,
)
from haven_cli.js_runtime.protocol import JSONRPCError, JSONRPCProtocol
from haven_cli.js_runtime.manager import (
    JSBridgeManager,
    get_bridge,
    js_call,
    configure_bridge,
)

__all__ = [
    # Bridge
    "JSRuntimeBridge",
    "RuntimeConfig",
    "RuntimeState",
    # Protocol
    "JSONRPCError",
    "JSONRPCProtocol",
    # Discovery
    "RuntimeInfo",
    "RuntimeType",
    "discover_runtime",
    "discover_all_runtimes",
    "check_runtime_available",
    # Manager
    "JSBridgeManager",
    "get_bridge",
    "js_call",
    "configure_bridge",
]
