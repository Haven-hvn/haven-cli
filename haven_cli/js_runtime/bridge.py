"""
JS Runtime Bridge.

Manages the Deno/Node subprocess that runs browser-dependent SDKs
(Lit Protocol, Synapse) with a browser environment shim.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Optional, Union

from .protocol import (
    JSONRPCError,
    JSONRPCErrorCode,
    JSONRPCProtocol,
    JSONRPCRequest,
    JSONRPCResponse,
    JSRuntimeMethods,
)

logger = logging.getLogger(__name__)


class RuntimeState(Enum):
    """State of the JS runtime subprocess."""
    
    NOT_STARTED = auto()
    STARTING = auto()
    READY = auto()
    ERROR = auto()
    SHUTTING_DOWN = auto()
    STOPPED = auto()


@dataclass
class RuntimeConfig:
    """Configuration for the JS runtime."""
    
    # Path to the JS services directory
    services_path: Optional[Path] = None
    
    # Runtime executable (auto-detected if not specified)
    runtime_executable: Optional[str] = None
    
    # Timeout for startup (seconds)
    startup_timeout: float = 30.0
    
    # Timeout for requests (seconds)
    request_timeout: float = 60.0
    
    # Environment variables to pass to the subprocess
    env_vars: dict[str, str] = field(default_factory=dict)
    
    # Whether to enable debug logging in the JS runtime
    debug: bool = False


@dataclass
class RuntimeStatus:
    """Status information from the JS runtime."""
    
    state: RuntimeState
    version: Optional[str] = None
    uptime_seconds: float = 0.0
    pending_requests: int = 0
    lit_connected: bool = False
    synapse_connected: bool = False
    error_message: Optional[str] = None


class JSRuntimeBridge:
    """
    Bridge to the JavaScript runtime subprocess.
    
    Manages lifecycle and communication with a Deno subprocess that
    provides browser SDK functionality (Lit Protocol, Synapse).
    
    Example:
        async with JSRuntimeBridge(config) as bridge:
            result = await bridge.call("lit.encrypt", {"data": "..."})
    """
    
    def __init__(self, config: Optional[RuntimeConfig] = None):
        self._config = config or RuntimeConfig()
        self._protocol = JSONRPCProtocol()
        self._state = RuntimeState.NOT_STARTED
        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._pending_futures: dict[str, asyncio.Future] = {}
        self._notification_handlers: dict[str, list[Callable]] = {}
        self._lock = asyncio.Lock()
        self._ready_event = asyncio.Event()
        self._error_message: Optional[str] = None
    
    @property
    def state(self) -> RuntimeState:
        """Get the current runtime state."""
        return self._state
    
    @property
    def is_ready(self) -> bool:
        """Check if the runtime is ready to accept requests."""
        return self._state == RuntimeState.READY
    
    async def start(self) -> None:
        """
        Start the JS runtime subprocess.
        
        Raises:
            RuntimeError: If the runtime fails to start
            TimeoutError: If startup times out
        """
        async with self._lock:
            if self._state not in (RuntimeState.NOT_STARTED, RuntimeState.STOPPED):
                raise RuntimeError(f"Cannot start runtime in state: {self._state}")
            
            self._state = RuntimeState.STARTING
            self._ready_event.clear()
            
            try:
                await self._spawn_process()
                
                # Wait for ready signal
                await asyncio.wait_for(
                    self._ready_event.wait(),
                    timeout=self._config.startup_timeout
                )
                
                self._state = RuntimeState.READY
                logger.info("JS runtime started successfully")
                
            except asyncio.TimeoutError:
                self._state = RuntimeState.ERROR
                self._error_message = "Startup timeout"
                await self._cleanup()
                raise TimeoutError(
                    f"JS runtime failed to start within {self._config.startup_timeout}s"
                )
            except Exception as e:
                self._state = RuntimeState.ERROR
                self._error_message = str(e)
                await self._cleanup()
                raise RuntimeError(f"Failed to start JS runtime: {e}")
    
    async def stop(self) -> None:
        """Stop the JS runtime subprocess gracefully."""
        async with self._lock:
            if self._state in (RuntimeState.NOT_STARTED, RuntimeState.STOPPED):
                return
            
            self._state = RuntimeState.SHUTTING_DOWN
            
            try:
                # Send shutdown request
                if self._process and self._process.returncode is None:
                    try:
                        await asyncio.wait_for(
                            self._send_notification(JSRuntimeMethods.SHUTDOWN),
                            timeout=5.0
                        )
                    except Exception:
                        pass
                
                await self._cleanup()
                
            finally:
                self._state = RuntimeState.STOPPED
                logger.info("JS runtime stopped")
    
    async def call(
        self,
        method: str,
        params: Optional[Union[list[Any], dict[str, Any]]] = None,
        timeout: Optional[float] = None
    ) -> Any:
        """
        Call a method on the JS runtime.
        
        Args:
            method: The method name to call
            params: Optional parameters
            timeout: Optional timeout override
        
        Returns:
            The result from the JS runtime
        
        Raises:
            JSONRPCError: If the call fails
            RuntimeError: If the runtime is not ready
            TimeoutError: If the call times out
        """
        if not self.is_ready:
            raise RuntimeError(f"Runtime not ready (state: {self._state})")
        
        request = self._protocol.create_request(method, params)
        timeout = timeout or self._config.request_timeout
        
        # Create future for response
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_futures[request.id] = future
        
        try:
            # Send request
            await self._send_request(request)
            
            # Wait for response
            response = await asyncio.wait_for(future, timeout=timeout)
            
            # Check for error
            response.raise_for_error()
            
            return response.result
            
        except asyncio.TimeoutError:
            self._protocol.cancel_request(request.id)
            raise JSONRPCError.timeout_error(timeout)
        finally:
            self._pending_futures.pop(request.id, None)
    
    async def notify(
        self,
        method: str,
        params: Optional[Union[list[Any], dict[str, Any]]] = None
    ) -> None:
        """
        Send a notification to the JS runtime (no response expected).
        
        Args:
            method: The method name
            params: Optional parameters
        """
        if not self.is_ready:
            raise RuntimeError(f"Runtime not ready (state: {self._state})")
        
        await self._send_notification(method, params)
    
    def on_notification(
        self,
        method: str,
        handler: Callable[[dict[str, Any]], None]
    ) -> Callable[[], None]:
        """
        Register a handler for notifications from the JS runtime.
        
        Args:
            method: The notification method to handle
            handler: The handler function
        
        Returns:
            A function to unregister the handler
        """
        if method not in self._notification_handlers:
            self._notification_handlers[method] = []
        
        self._notification_handlers[method].append(handler)
        
        def unregister():
            self._notification_handlers[method].remove(handler)
        
        return unregister
    
    async def get_status(self) -> RuntimeStatus:
        """Get the current status of the JS runtime."""
        if not self.is_ready:
            return RuntimeStatus(
                state=self._state,
                error_message=self._error_message
            )
        
        try:
            result = await self.call(JSRuntimeMethods.GET_STATUS, timeout=5.0)
            return RuntimeStatus(
                state=self._state,
                version=result.get("version"),
                uptime_seconds=result.get("uptimeSeconds", 0),
                pending_requests=self._protocol.pending_count,
                lit_connected=result.get("litConnected", False),
                synapse_connected=result.get("synapseConnected", False)
            )
        except Exception as e:
            return RuntimeStatus(
                state=self._state,
                pending_requests=self._protocol.pending_count,
                error_message=str(e)
            )
    
    async def ping(self) -> bool:
        """Ping the JS runtime to check if it's responsive."""
        try:
            result = await self.call(JSRuntimeMethods.PING, timeout=5.0)
            return result == "pong"
        except Exception:
            return False
    
    # Alias for stop() for consistency with context managers
    async def close(self) -> None:
        """Alias for stop()."""
        await self.stop()
    
    # Context manager support
    async def __aenter__(self) -> "JSRuntimeBridge":
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()
    
    # Private methods
    
    async def _spawn_process(self) -> None:
        """Spawn the JS runtime subprocess."""
        from .discovery import discover_runtime, get_runtime_args
        
        # Discover runtime if not specified
        runtime = self._config.runtime_executable
        if not runtime:
            runtime = await discover_runtime()
        
        # Get the entry point script
        services_path = self._config.services_path
        if not services_path:
            # Default to js-services directory relative to this package
            services_path = Path(__file__).parent.parent.parent / "js-services"
        
        entry_point = services_path / "main.ts"
        
        # Build command
        args = get_runtime_args(runtime, entry_point, self._config.debug)
        
        # Prepare environment
        env = dict(self._config.env_vars)
        if self._config.debug:
            env["DEBUG"] = "1"
        
        logger.debug(f"Starting JS runtime: {' '.join(args)}")
        
        # Start process
        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env if env else None
        )
        
        # Start reader task
        self._reader_task = asyncio.create_task(self._read_loop())
    
    async def _read_loop(self) -> None:
        """Read and process messages from the subprocess."""
        if not self._process or not self._process.stdout:
            return
        
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                
                try:
                    await self._handle_message(line.decode().strip())
                except Exception as e:
                    logger.error(f"Error handling message: {e}")
                    
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Read loop error: {e}")
            self._state = RuntimeState.ERROR
            self._error_message = str(e)
    
    async def _handle_message(self, message: str) -> None:
        """Handle a message from the subprocess."""
        if not message:
            return
        
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            # Might be a log message
            logger.debug(f"JS runtime: {message}")
            return
        
        # Check if it's a response or notification
        if "id" in data and ("result" in data or "error" in data):
            # It's a response
            response = JSONRPCResponse.from_dict(data)
            await self._handle_response(response)
        elif "method" in data:
            # It's a notification or request from JS
            await self._handle_notification(data)
    
    async def _handle_response(self, response: JSONRPCResponse) -> None:
        """Handle a response from the subprocess."""
        # Check for ready signal
        if response.id == "ready":
            self._ready_event.set()
            return
        
        # Match to pending request
        if response.id and response.id in self._pending_futures:
            future = self._pending_futures[response.id]
            if not future.done():
                future.set_result(response)
    
    async def _handle_notification(self, data: dict[str, Any]) -> None:
        """Handle a notification from the subprocess."""
        method = data.get("method", "")
        params = data.get("params", {})
        
        # Special handling for ready notification
        if method == "ready":
            self._ready_event.set()
            return
        
        # Call registered handlers
        handlers = self._notification_handlers.get(method, [])
        for handler in handlers:
            try:
                handler(params)
            except Exception as e:
                logger.error(f"Notification handler error: {e}")
    
    async def _send_request(self, request: JSONRPCRequest) -> None:
        """Send a request to the subprocess."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Process not running")
        
        message = request.to_json() + "\n"
        self._process.stdin.write(message.encode())
        await self._process.stdin.drain()
    
    async def _send_notification(
        self,
        method: str,
        params: Optional[Union[list[Any], dict[str, Any]]] = None
    ) -> None:
        """Send a notification to the subprocess."""
        request = self._protocol.create_request(method, params, notification=True)
        await self._send_request(request)
    
    async def _cleanup(self) -> None:
        """Clean up subprocess resources."""
        # Cancel reader task
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        
        # Terminate process
        if self._process:
            if self._process.returncode is None:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._process.kill()
                    await self._process.wait()
            self._process = None
        
        # Cancel pending futures
        for future in self._pending_futures.values():
            if not future.done():
                future.set_exception(
                    RuntimeError("Runtime stopped")
                )
        self._pending_futures.clear()
        
        # Clear protocol state
        self._protocol.clear_pending()


# Convenience functions for common operations

async def create_bridge(
    services_path: Optional[Path] = None,
    debug: bool = False
) -> JSRuntimeBridge:
    """
    Create and start a JS runtime bridge.
    
    Args:
        services_path: Path to the JS services directory
        debug: Enable debug mode
    
    Returns:
        A started JSRuntimeBridge instance
    """
    config = RuntimeConfig(
        services_path=services_path,
        debug=debug
    )
    bridge = JSRuntimeBridge(config)
    await bridge.start()
    return bridge
