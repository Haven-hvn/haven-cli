"""JS Runtime Bridge Manager.

Manages the lifecycle of the JS Runtime Bridge with singleton pattern,
connection pooling, health monitoring, and automatic reconnection.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import weakref
from pathlib import Path
from typing import Any, Optional, Callable

from .bridge import JSRuntimeBridge, RuntimeConfig, RuntimeState
from .protocol import JSONRPCError

logger = logging.getLogger(__name__)


class JSBridgeManager:
    """Manages JS runtime bridge lifecycle and connection pooling.
    
    This class implements the singleton pattern to ensure a single bridge
    instance is reused across multiple operations. It provides:
    
    - Singleton bridge management for connection reuse
    - Background health checks with automatic restart
    - Automatic reconnection on failures with exponential backoff
    - Graceful shutdown with proper cleanup
    - Context manager support for easy usage
    
    Event Loop Safety:
        This manager is designed to work correctly when accessed from different
        event loops (e.g., main daemon loop and APScheduler job loops). It
        automatically detects event loop changes and recreates synchronization
        primitives as needed.
    
    Example:
        # Get bridge via singleton manager
        manager = JSBridgeManager.get_instance()
        bridge = await manager.get_bridge()
        result = await bridge.call("synapse.getStatus", {"cid": "..."})
        
        # Or use context manager
        async with JSBridgeManager.get_instance() as manager:
            result = await manager.call_with_retry("synapse.getStatus", {"cid": "..."})
    """
    
    _instance: Optional['JSBridgeManager'] = None
    _lock: Optional[asyncio.Lock] = None
    
    def __new__(cls) -> 'JSBridgeManager':
        """Create new instance with proper initialization."""
        instance = super().__new__(cls)
        instance._initialized = False
        return instance
    
    def __init__(self):
        """Initialize the bridge manager (called once due to singleton)."""
        if self._initialized:
            return
            
        self._bridge: Optional[JSRuntimeBridge] = None
        
        # Event-loop-aware synchronization primitives
        # We track the loop that created each primitive and recreate if needed
        self._bridge_lock: Optional[asyncio.Lock] = None
        self._bridge_lock_loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._shutdown_event_loop: Optional[asyncio.AbstractEventLoop] = None
        
        self._running = False
        self._health_task: Optional[asyncio.Task] = None
        self._health_check_interval = 120.0  # seconds - longer than typical Filecoin uploads
        
        # Configuration
        self._config: Optional[RuntimeConfig] = None
        self._services_path: Optional[Path] = None
        
        # Metrics and tracking
        self._reconnect_count = 0
        self._last_error: Optional[Exception] = None
        self._call_count = 0
        
        # Weak reference set to track active callers
        self._active_callers: weakref.WeakSet = weakref.WeakSet()
        
        # Thread safety for non-async operations
        self._thread_lock = threading.Lock()
        
        self._initialized = True
    
    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """Get or create the class-level lock lazily.
        
        This ensures the lock is created in the current event loop,
        avoiding 'bound to a different event loop' errors.
        """
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock
    
    def _get_bridge_lock(self) -> asyncio.Lock:
        """Get or create the bridge lock lazily with event loop safety.
        
        This method ensures that:
        1. The lock is created in the current event loop if it doesn't exist
        2. The lock is recreated if the event loop has changed
        3. Thread-safe access for non-async contexts
        
        Returns:
            An asyncio.Lock bound to the current event loop
        """
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop running - this shouldn't happen in async context
            # Return existing lock or create new one (will fail if actually used)
            if self._bridge_lock is None:
                raise RuntimeError("No event loop running - bridge lock cannot be created")
            return self._bridge_lock
        
        # Check if we need to create or recreate the lock
        if (self._bridge_lock is None or 
            self._bridge_lock_loop is None or 
            self._bridge_lock_loop != current_loop or
            self._bridge_lock_loop.is_closed()):
            
            with self._thread_lock:
                # Double-check after acquiring lock
                if (self._bridge_lock is None or 
                    self._bridge_lock_loop is None or 
                    self._bridge_lock_loop != current_loop or
                    self._bridge_lock_loop.is_closed()):
                    
                    old_loop = self._bridge_lock_loop
                    self._bridge_lock = asyncio.Lock()
                    self._bridge_lock_loop = current_loop
                    
                    if old_loop is not None and old_loop != current_loop:
                        logger.debug(
                            f"Bridge lock recreated for new event loop "
                            f"(old: {id(old_loop)}, new: {id(current_loop)})"
                        )
        
        return self._bridge_lock
    
    def _get_shutdown_event(self) -> asyncio.Event:
        """Get or create the shutdown event lazily with event loop safety.
        
        This method ensures that:
        1. The event is created in the current event loop if it doesn't exist
        2. The event is recreated if the event loop has changed
        3. Thread-safe access for non-async contexts
        
        Returns:
            An asyncio.Event bound to the current event loop
        """
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop running - this shouldn't happen in async context
            # Return existing event or raise error
            if self._shutdown_event is None:
                raise RuntimeError("No event loop running - shutdown event cannot be created")
            return self._shutdown_event
        
        # Check if we need to create or recreate the event
        if (self._shutdown_event is None or 
            self._shutdown_event_loop is None or 
            self._shutdown_event_loop != current_loop or
            self._shutdown_event_loop.is_closed()):
            
            with self._thread_lock:
                # Double-check after acquiring lock
                if (self._shutdown_event is None or 
                    self._shutdown_event_loop is None or 
                    self._shutdown_event_loop != current_loop or
                    self._shutdown_event_loop.is_closed()):
                    
                    old_loop = self._shutdown_event_loop
                    
                    # Preserve the old event's state if recreating
                    old_is_set = False
                    if self._shutdown_event is not None:
                        try:
                            old_is_set = self._shutdown_event.is_set()
                        except RuntimeError:
                            # Old event is bound to different loop, can't check
                            pass
                    
                    self._shutdown_event = asyncio.Event()
                    self._shutdown_event_loop = current_loop
                    
                    if old_is_set:
                        self._shutdown_event.set()
                    
                    if old_loop is not None and old_loop != current_loop:
                        logger.debug(
                            f"Shutdown event recreated for new event loop "
                            f"(old: {id(old_loop)}, new: {id(current_loop)})"
                        )
        
        return self._shutdown_event
    
    @classmethod
    def get_instance(cls) -> 'JSBridgeManager':
        """Get the singleton instance of the bridge manager.
        
        Returns:
            The singleton JSBridgeManager instance.
        """
        # Note: We don't need to acquire the lock here because
        # instance creation is idempotent and Python's GIL ensures
        # thread safety for the reference assignment
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (mainly for testing).
        
        This should be called with caution as it will break any
        existing references to the previous instance.
        """
        cls._instance = None
    
    def configure(
        self,
        services_path: Optional[Path] = None,
        startup_timeout: float = 30.0,
        request_timeout: float = 60.0,
        health_check_interval: float = 120.0,
        runtime_executable: Optional[str] = None,
        debug: bool = False,
        network_mode: str = "testnet",
    ) -> None:
        """Configure the bridge manager.
        
        This should be called before get_bridge() if custom configuration
        is needed. Configuration can only be changed when bridge is not running.
        
        Args:
            services_path: Path to JS services directory
            startup_timeout: Timeout for bridge startup
            request_timeout: Timeout for requests
            health_check_interval: Interval between health checks
            runtime_executable: Specific runtime to use (auto-detect if None)
            debug: Enable debug mode
            network_mode: Network mode for blockchain configuration ('mainnet' or 'testnet')
        """
        if self._bridge is not None and self._bridge.is_ready:
            raise RuntimeError("Cannot configure while bridge is running. Call shutdown() first.")
        
        self._services_path = services_path
        self._health_check_interval = health_check_interval
        
        # Pass through environment variables needed by JS services
        import os
        env_vars = {}
        for key in os.environ:
            # Pass through all HAVEN_*, FILECOIN_*, SYNAPSE_* vars and other important vars
            if key.startswith(('HAVEN_', 'FILECOIN_', 'SYNAPSE_')) or key in (
                'PATH', 'HOME', 'USER', 'DEBUG', 'LOG_LEVEL'
            ):
                env_vars[key] = os.environ[key]
        
        self._config = RuntimeConfig(
            services_path=services_path,
            runtime_executable=runtime_executable,
            startup_timeout=startup_timeout,
            request_timeout=request_timeout,
            env_vars=env_vars,
            debug=debug,
            network_mode=network_mode,
        )
        logger.debug(f"JSBridgeManager configured with health_check_interval={health_check_interval}s, network_mode={network_mode}")
    
    async def get_bridge(self) -> JSRuntimeBridge:
        """Get or create a bridge instance.
        
        This method ensures a single bridge instance is created and reused.
        If the bridge is not ready, it will be created and started.
        
        This method is event-loop-aware and will work correctly when called
        from different event loops (e.g., main daemon loop vs APScheduler jobs).
        
        Returns:
            A ready-to-use JSRuntimeBridge instance.
            
        Raises:
            RuntimeError: If bridge creation fails
        """
        async with self._get_bridge_lock():
            if self._bridge is None or not self._bridge.is_ready:
                self._bridge = await self._create_bridge()
                self._start_health_checks()
            return self._bridge
    
    async def _create_bridge(self) -> JSRuntimeBridge:
        """Create and start a new bridge.
        
        Returns:
            A started JSRuntimeBridge instance.
            
        Raises:
            RuntimeError: If bridge fails to start
        """
        config = self._config or self._create_default_config()
        bridge = JSRuntimeBridge(config)
        
        try:
            await bridge.start()
            logger.info("JS Runtime Bridge started successfully")
            self._last_error = None
            return bridge
        except Exception as e:
            logger.error(f"Failed to start JS Runtime Bridge: {e}")
            self._last_error = e
            raise RuntimeError(f"Failed to start JS Runtime Bridge: {e}") from e
    
    def _create_default_config(self) -> RuntimeConfig:
        """Create default runtime configuration."""
        services_path = self._services_path
        if services_path is None:
            # Default to js-services directory relative to this package
            services_path = Path(__file__).parent.parent.parent / "js-services"
        
        # Pass through environment variables needed by JS services
        import os
        env_vars = {}
        for key in os.environ:
            # Pass through all HAVEN_*, FILECOIN_*, SYNAPSE_* vars and other important vars
            if key.startswith(('HAVEN_', 'FILECOIN_', 'SYNAPSE_')) or key in (
                'PATH', 'HOME', 'USER', 'DEBUG', 'LOG_LEVEL'
            ):
                env_vars[key] = os.environ[key]
        
        return RuntimeConfig(
            services_path=services_path,
            startup_timeout=30.0,
            request_timeout=60.0,
            env_vars=env_vars,
            network_mode="testnet",
        )
    
    def _start_health_checks(self) -> None:
        """Start the health check background task."""
        if self._health_task is None or self._health_task.done():
            self._running = True
            self._get_shutdown_event().clear()
            
            # Create the health check task in the current event loop
            try:
                current_loop = asyncio.get_running_loop()
                self._health_task = current_loop.create_task(
                    self._health_check_loop(),
                    name="js_bridge_health_check"
                )
                logger.debug(f"Health check loop started in event loop {id(current_loop)}")
            except RuntimeError:
                logger.error("Cannot start health checks: no running event loop")
                raise
    
    async def _health_check_loop(self) -> None:
        """Periodically check bridge health.
        
        This runs as a background task and monitors the bridge health.
        If the bridge becomes unhealthy, it attempts to restart it.
        
        Health checks are skipped when there are pending operations to avoid
        restarting the bridge during long-running operations (e.g., Filecoin uploads).
        
        This method handles event loop changes gracefully by detecting when
        it's running in a different loop than expected.
        """
        try:
            # Track which loop started this health check
            start_loop = asyncio.get_running_loop()
            logger.debug(f"Health check loop running in event loop {id(start_loop)}")
        except RuntimeError:
            logger.error("Health check loop cannot run without an event loop")
            return
        
        while self._running:
            try:
                # Check if we're still in the same event loop
                try:
                    current_loop = asyncio.get_running_loop()
                    if current_loop != start_loop or current_loop.is_closed():
                        logger.warning(
                            f"Health check loop detected event loop change "
                            f"(started in {id(start_loop)}, now in {id(current_loop)}). "
                            f"Stopping this health check loop."
                        )
                        break
                except RuntimeError:
                    logger.warning("Health check loop: no event loop available, stopping")
                    break
                
                # Wait for the health check interval or shutdown signal
                try:
                    await asyncio.wait_for(
                        self._get_shutdown_event().wait(),
                        timeout=self._health_check_interval
                    )
                    # Shutdown event was set
                    break
                except asyncio.TimeoutError:
                    pass
                
                # Skip health check if there are pending operations
                # This prevents restarting the bridge during long-running operations
                if self._bridge and self._bridge.is_ready:
                    pending_count = self._bridge.pending_request_count
                    if pending_count > 0:
                        logger.debug(f"Health check skipped: {pending_count} operation(s) in progress")
                        continue
                
                # Perform health check
                if self._bridge and self._bridge.is_ready:
                    try:
                        is_healthy = await self._bridge.ping()
                        if not is_healthy:
                            logger.warning("Health check failed: bridge not responsive")
                            await self._restart_bridge()
                        else:
                            logger.debug("Health check passed")
                    except Exception as ping_error:
                        logger.warning(f"Health check ping failed: {ping_error}")
                        await self._restart_bridge()
                        
            except asyncio.CancelledError:
                logger.debug("Health check loop cancelled")
                break
            except Exception as e:
                logger.warning(f"Health check failed with exception: {e}")
                self._last_error = e
                # Don't let health check exceptions stop the loop
                try:
                    await asyncio.sleep(1)
                except asyncio.CancelledError:
                    break
        
        logger.debug("Health check loop stopped")
    
    async def _restart_bridge(self) -> None:
        """Restart the bridge after a failure.
        
        This stops the current bridge (if any) and creates a new one.
        It implements exponential backoff to avoid rapid restart loops.
        
        This method is event-loop-aware and will work correctly when called
        from different event loops.
        """
        async with self._get_bridge_lock():
            logger.info("Restarting JS Runtime Bridge...")
            self._reconnect_count += 1
            
            # Stop existing bridge if any
            if self._bridge:
                try:
                    await self._bridge.stop()
                except Exception as e:
                    logger.warning(f"Error stopping bridge during restart: {e}")
                finally:
                    self._bridge = None
            
            # Exponential backoff for retries
            if self._reconnect_count > 1:
                backoff = min(2 ** (self._reconnect_count - 1), 30)  # Max 30s backoff
                logger.info(f"Waiting {backoff}s before restart (attempt {self._reconnect_count})")
                await asyncio.sleep(backoff)
            
            # Create new bridge
            try:
                self._bridge = await self._create_bridge()
                logger.info(f"Bridge restarted successfully (attempt {self._reconnect_count})")
                # Reset reconnect count on success
                self._reconnect_count = 0
            except Exception as e:
                logger.error(f"Bridge restart failed: {e}")
                raise
    
    async def call(
        self,
        method: str,
        params: Optional[dict] = None,
        timeout: Optional[float] = None
    ) -> Any:
        """Call a method on the JS runtime bridge.
        
        This is a convenience method that gets the bridge and calls a method.
        For automatic retry on failure, use call_with_retry().
        
        This method is event-loop-aware and will work correctly when called
        from different event loops.
        
        Args:
            method: The method name to call
            params: Optional parameters for the method
            timeout: Optional timeout override
            
        Returns:
            The result from the JS runtime
            
        Raises:
            RuntimeError: If the bridge is not ready or call fails
        """
        bridge = await self.get_bridge()
        self._call_count += 1
        return await bridge.call(method, params, timeout)
    
    async def call_with_retry(
        self,
        method: str,
        params: Optional[dict] = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
        timeout: Optional[float] = None,
    ) -> Any:
        """Call method with automatic retry on failure.
        
        This method implements exponential backoff for retries and handles
        bridge restart on "not ready" errors.
        
        This method is event-loop-aware and will work correctly when called
        from different event loops.
        
        Args:
            method: The method name to call
            params: Optional parameters for the method
            max_retries: Maximum number of retry attempts
            base_delay: Base delay between retries (exponentially increases)
            timeout: Optional timeout override
            
        Returns:
            The result from the JS runtime
            
        Raises:
            RuntimeError: If all retry attempts fail
            JSONRPCError: If the JS runtime returns an error
        """
        last_exception: Optional[Exception] = None
        
        for attempt in range(max_retries):
            try:
                bridge = await self.get_bridge()
                self._call_count += 1
                return await bridge.call(method, params, timeout)
                
            except RuntimeError as e:
                error_msg = str(e).lower()
                last_exception = e
                
                # Check if bridge is not ready
                if "not ready" in error_msg or "stopped" in error_msg:
                    logger.warning(f"Bridge not ready on attempt {attempt + 1}, restarting...")
                    await self._restart_bridge()
                    continue
                
                # Other runtime errors - might be retryable
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)  # Exponential backoff
                    logger.warning(f"Runtime error on attempt {attempt + 1}, retrying in {delay}s: {e}")
                    await asyncio.sleep(delay)
                    continue
                
                raise
                
            except JSONRPCError as e:
                # JSON-RPC errors generally shouldn't be retried unless they're
                # server errors in the retryable range
                if e.code == -32000 and attempt < max_retries - 1:  # Server error
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Server error on attempt {attempt + 1}, retrying in {delay}s: {e}")
                    await asyncio.sleep(delay)
                    continue
                raise
                
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Unexpected error on attempt {attempt + 1}, retrying in {delay}s: {e}")
                    await asyncio.sleep(delay)
                    continue
                raise
        
        # All retries exhausted
        raise RuntimeError(
            f"Call to '{method}' failed after {max_retries} attempts"
        ) from last_exception
    
    async def shutdown(self) -> None:
        """Shutdown the bridge manager gracefully.
        
        This stops the health check loop and shuts down the bridge.
        Should be called during application shutdown.
        
        This method handles event loop changes gracefully.
        """
        logger.info("Shutting down JS Bridge Manager...")
        self._running = False
        
        # Signal shutdown to health check loop
        try:
            self._get_shutdown_event().set()
        except RuntimeError as e:
            logger.debug(f"Could not set shutdown event: {e}")
        
        # Cancel health check task
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            except RuntimeError as e:
                # Task may be bound to a different event loop
                logger.debug(f"Could not await health task: {e}")
            self._health_task = None
        
        # Stop the bridge
        try:
            async with self._get_bridge_lock():
                if self._bridge:
                    try:
                        await self._bridge.stop()
                    except Exception as e:
                        logger.warning(f"Error during bridge shutdown: {e}")
                    finally:
                        self._bridge = None
        except RuntimeError as e:
            # Lock may be bound to a different event loop
            logger.warning(f"Could not acquire bridge lock during shutdown: {e}")
            # Try to stop bridge without lock as last resort
            if self._bridge:
                try:
                    await self._bridge.stop()
                except Exception as bridge_error:
                    logger.warning(f"Error stopping bridge without lock: {bridge_error}")
                finally:
                    self._bridge = None
        
        logger.info("JS Bridge Manager shutdown complete")
    
    async def get_status(self) -> dict[str, Any]:
        """Get the current status of the bridge manager.
        
        Returns:
            Dictionary with status information including:
            - bridge_state: Current state of the bridge
            - is_ready: Whether bridge is ready for calls
            - reconnect_count: Number of reconnections since start
            - call_count: Total number of calls made
            - last_error: Last error encountered (if any)
        """
        bridge_state = RuntimeState.NOT_STARTED
        is_ready = False
        
        if self._bridge:
            bridge_state = self._bridge.state
            is_ready = self._bridge.is_ready
        
        return {
            "bridge_state": bridge_state.name,
            "is_ready": is_ready,
            "reconnect_count": self._reconnect_count,
            "call_count": self._call_count,
            "last_error": str(self._last_error) if self._last_error else None,
            "health_check_running": self._health_task is not None and not self._health_task.done(),
        }
    
    async def ping(self) -> bool:
        """Ping the bridge to check if it's responsive.
        
        Returns:
            True if bridge is responsive, False otherwise
        """
        if self._bridge is None or not self._bridge.is_ready:
            return False
        
        try:
            return await self._bridge.ping()
        except Exception:
            return False
    
    # Context manager support
    
    async def __aenter__(self) -> 'JSBridgeManager':
        """Enter async context manager.
        
        Ensures the bridge is ready before returning.
        """
        await self.get_bridge()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context manager.
        
        Note: This does NOT shutdown the manager to preserve the singleton.
        Use shutdown() explicitly when you want to completely stop the bridge.
        """
        # Don't shutdown - just release the context
        pass
    
    def register_caller(self, obj: object) -> None:
        """Register an object as an active caller.
        
        This is used to track how many components are using the bridge
        and can be used for reference counting in future enhancements.
        
        Args:
            obj: The caller object to register
        """
        self._active_callers.add(obj)
    
    def unregister_caller(self, obj: object) -> None:
        """Unregister a caller.
        
        Args:
            obj: The caller object to unregister
        """
        self._active_callers.discard(obj)
    
    @property
    def active_caller_count(self) -> int:
        """Get the number of active callers."""
        return len(self._active_callers)


# Convenience functions for simple use cases

async def get_bridge() -> JSRuntimeBridge:
    """Get the singleton bridge instance.
    
    This is a convenience function for quickly getting the bridge
    without managing the manager instance.
    
    Returns:
        A ready-to-use JSRuntimeBridge instance
    """
    return await JSBridgeManager.get_instance().get_bridge()


async def js_call(method: str, params: Optional[dict] = None, **kwargs) -> Any:
    """Make a JS runtime call with automatic retry.
    
    This is the simplest way to call JS runtime methods with all
    the benefits of the bridge manager (singleton, retry, etc.).
    
    Args:
        method: The method name to call
        params: Optional parameters
        **kwargs: Additional options passed to call_with_retry
        
    Returns:
        The result from the JS runtime
        
    Example:
        result = await js_call("synapse.getStatus", {"cid": "..."})
    """
    return await JSBridgeManager.get_instance().call_with_retry(method, params, **kwargs)


def configure_bridge(
    services_path: Optional[Path] = None,
    startup_timeout: float = 30.0,
    request_timeout: float = 60.0,
    health_check_interval: float = 120.0,
    runtime_executable: Optional[str] = None,
    debug: bool = False,
    network_mode: str = "testnet",
) -> None:
    """Configure the bridge manager (synchronous).
    
    This can be called during application startup to configure
    the bridge before it's first used.
    
    Args:
        services_path: Path to JS services directory
        startup_timeout: Timeout for bridge startup
        request_timeout: Timeout for requests
        health_check_interval: Interval between health checks
        runtime_executable: Specific runtime to use
        debug: Enable debug mode
        network_mode: Network mode for blockchain configuration ('mainnet' or 'testnet')
    """
    manager = JSBridgeManager.get_instance()
    manager.configure(
        services_path=services_path,
        startup_timeout=startup_timeout,
        request_timeout=request_timeout,
        health_check_interval=health_check_interval,
        runtime_executable=runtime_executable,
        debug=debug,
        network_mode=network_mode,
    )
