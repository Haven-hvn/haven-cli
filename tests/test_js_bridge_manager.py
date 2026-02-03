"""Unit tests for the JS Bridge Manager.

Tests the singleton pattern, health monitoring, reconnection logic,
and graceful shutdown of the JSBridgeManager.
"""

import asyncio
import pytest
import weakref
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from haven_cli.js_runtime.manager import JSBridgeManager, get_bridge, js_call, configure_bridge
from haven_cli.js_runtime.bridge import JSRuntimeBridge, RuntimeConfig, RuntimeState
from haven_cli.js_runtime.protocol import JSONRPCError


class TestJSBridgeManagerSingleton:
    """Tests for the singleton pattern implementation."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        JSBridgeManager.reset_instance()
    
    def teardown_method(self):
        """Reset singleton after each test."""
        JSBridgeManager.reset_instance()
    
    def test_singleton_returns_same_instance(self):
        """Test that get_instance returns the same object."""
        manager1 = JSBridgeManager.get_instance()
        manager2 = JSBridgeManager.get_instance()
        
        assert manager1 is manager2
    
    def test_singleton_across_multiple_calls(self):
        """Test singleton behavior across multiple calls."""
        instances = [JSBridgeManager.get_instance() for _ in range(5)]
        
        # All instances should be the same object
        first = instances[0]
        for instance in instances[1:]:
            assert instance is first
    
    def test_reset_creates_new_instance(self):
        """Test that reset creates a new instance."""
        manager1 = JSBridgeManager.get_instance()
        JSBridgeManager.reset_instance()
        manager2 = JSBridgeManager.get_instance()
        
        assert manager1 is not manager2


class TestJSBridgeManagerConfiguration:
    """Tests for bridge manager configuration."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        JSBridgeManager.reset_instance()
    
    def teardown_method(self):
        """Reset singleton after each test."""
        JSBridgeManager.reset_instance()
    
    def test_configure_sets_values(self):
        """Test that configure sets configuration values."""
        manager = JSBridgeManager.get_instance()
        
        manager.configure(
            services_path=Path("/test/services"),
            startup_timeout=60.0,
            request_timeout=120.0,
            health_check_interval=45.0,
            debug=True,
        )
        
        assert manager._services_path == Path("/test/services")
        assert manager._health_check_interval == 45.0
        assert manager._config is not None
        assert manager._config.startup_timeout == 60.0
        assert manager._config.request_timeout == 120.0
        assert manager._config.debug is True
    
    def test_configure_raises_when_bridge_running(self):
        """Test that configure raises when bridge is running."""
        manager = JSBridgeManager.get_instance()
        
        # Mock a running bridge
        mock_bridge = MagicMock()
        mock_bridge.is_ready = True
        manager._bridge = mock_bridge
        
        with pytest.raises(RuntimeError, match="Cannot configure while bridge is running"):
            manager.configure(services_path=Path("/test"))


class TestJSBridgeManagerBridgeLifecycle:
    """Tests for bridge lifecycle management."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        JSBridgeManager.reset_instance()
    
    def teardown_method(self):
        """Reset singleton after each test."""
        JSBridgeManager.reset_instance()
    
    @pytest.mark.asyncio
    async def test_get_bridge_creates_new_bridge(self):
        """Test that get_bridge creates a bridge when none exists."""
        manager = JSBridgeManager.get_instance()
        
        with patch.object(manager, '_create_bridge', new_callable=AsyncMock) as mock_create:
            mock_bridge = MagicMock()
            mock_bridge.is_ready = True
            mock_create.return_value = mock_bridge
            
            bridge = await manager.get_bridge()
            
            mock_create.assert_called_once()
            assert bridge is mock_bridge
    
    @pytest.mark.asyncio
    async def test_get_bridge_reuses_existing_bridge(self):
        """Test that get_bridge reuses an existing ready bridge."""
        manager = JSBridgeManager.get_instance()
        
        # Set up an existing ready bridge
        mock_bridge = MagicMock()
        mock_bridge.is_ready = True
        manager._bridge = mock_bridge
        
        with patch.object(manager, '_create_bridge', new_callable=AsyncMock) as mock_create:
            bridge = await manager.get_bridge()
            
            # Should not create a new bridge
            mock_create.assert_not_called()
            assert bridge is mock_bridge
    
    @pytest.mark.asyncio
    async def test_get_bridge_creates_new_when_not_ready(self):
        """Test that get_bridge creates a new bridge when existing is not ready."""
        manager = JSBridgeManager.get_instance()
        
        # Set up an existing not-ready bridge
        mock_bridge = MagicMock()
        mock_bridge.is_ready = False
        manager._bridge = mock_bridge
        
        with patch.object(manager, '_create_bridge', new_callable=AsyncMock) as mock_create:
            new_bridge = MagicMock()
            new_bridge.is_ready = True
            mock_create.return_value = new_bridge
            
            bridge = await manager.get_bridge()
            
            mock_create.assert_called_once()
            assert bridge is new_bridge
    
    @pytest.mark.asyncio
    async def test_create_bridge_success(self):
        """Test successful bridge creation."""
        manager = JSBridgeManager.get_instance()
        
        with patch.object(JSRuntimeBridge, 'start', new_callable=AsyncMock) as mock_start:
            bridge = await manager._create_bridge()
            
            assert bridge is not None
            mock_start.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_create_bridge_failure(self):
        """Test bridge creation failure."""
        manager = JSBridgeManager.get_instance()
        
        with patch.object(JSRuntimeBridge, 'start', new_callable=AsyncMock) as mock_start:
            mock_start.side_effect = RuntimeError("Failed to start")
            
            with pytest.raises(RuntimeError, match="Failed to start JS Runtime Bridge"):
                await manager._create_bridge()
    
    @pytest.mark.asyncio
    async def test_shutdown_stops_health_check(self):
        """Test that shutdown stops the health check task."""
        manager = JSBridgeManager.get_instance()
        
        # Set up a running health check task
        async def mock_health_task():
            while True:
                await asyncio.sleep(1)
        
        task = asyncio.create_task(mock_health_task())
        manager._health_task = task
        
        await manager.shutdown()
        
        # Task should be cancelled or done (it may be set to None after shutdown)
        if task:
            assert task.cancelled() or task.done()
    
    @pytest.mark.asyncio
    async def test_shutdown_stops_bridge(self):
        """Test that shutdown stops the bridge."""
        manager = JSBridgeManager.get_instance()
        
        mock_bridge = MagicMock()
        mock_bridge.stop = AsyncMock()
        manager._bridge = mock_bridge
        
        await manager.shutdown()
        
        mock_bridge.stop.assert_called_once()
        assert manager._bridge is None


class TestJSBridgeManagerHealthChecks:
    """Tests for health monitoring functionality."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        JSBridgeManager.reset_instance()
    
    def teardown_method(self):
        """Reset singleton after each test."""
        JSBridgeManager.reset_instance()
    
    @pytest.mark.asyncio
    async def test_health_check_restarts_unhealthy_bridge(self):
        """Test that health check restarts an unhealthy bridge."""
        manager = JSBridgeManager.get_instance()
        manager._health_check_interval = 0.1
        
        # Set up an unhealthy bridge
        mock_bridge = MagicMock()
        mock_bridge.is_ready = True
        mock_bridge.ping = AsyncMock(return_value=False)  # Unhealthy
        manager._bridge = mock_bridge
        manager._running = True
        
        with patch.object(manager, '_restart_bridge', new_callable=AsyncMock) as mock_restart:
            # Run health check once
            manager._shutdown_event = asyncio.Event()
            
            # Set up to stop after one iteration
            async def stop_after_delay():
                await asyncio.sleep(0.15)
                manager._running = False
                manager._shutdown_event.set()
            
            await asyncio.gather(
                manager._health_check_loop(),
                stop_after_delay()
            )
            
            mock_restart.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_health_check_passes_for_healthy_bridge(self):
        """Test that health check passes for a healthy bridge."""
        manager = JSBridgeManager.get_instance()
        manager._health_check_interval = 0.1
        
        # Set up a healthy bridge
        mock_bridge = MagicMock()
        mock_bridge.is_ready = True
        mock_bridge.ping = AsyncMock(return_value=True)  # Healthy
        manager._bridge = mock_bridge
        manager._running = True
        
        with patch.object(manager, '_restart_bridge', new_callable=AsyncMock) as mock_restart:
            manager._shutdown_event = asyncio.Event()
            
            async def stop_after_delay():
                await asyncio.sleep(0.15)
                manager._running = False
                manager._shutdown_event.set()
            
            await asyncio.gather(
                manager._health_check_loop(),
                stop_after_delay()
            )
            
            # Should not restart a healthy bridge
            mock_restart.assert_not_called()


class TestJSBridgeManagerCallWithRetry:
    """Tests for the call_with_retry functionality."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        JSBridgeManager.reset_instance()
    
    def teardown_method(self):
        """Reset singleton after each test."""
        JSBridgeManager.reset_instance()
    
    @pytest.mark.asyncio
    async def test_call_with_retry_success_first_attempt(self):
        """Test successful call on first attempt."""
        manager = JSBridgeManager.get_instance()
        
        mock_bridge = MagicMock()
        mock_bridge.is_ready = True
        mock_bridge.call = AsyncMock(return_value={"result": "success"})
        manager._bridge = mock_bridge
        
        result = await manager.call_with_retry("test.method", {"param": "value"})
        
        assert result == {"result": "success"}
        mock_bridge.call.assert_called_once_with("test.method", {"param": "value"}, None)
    
    @pytest.mark.asyncio
    async def test_call_with_retry_restarts_on_not_ready(self):
        """Test that call_with_retry restarts bridge on 'not ready' error."""
        manager = JSBridgeManager.get_instance()
        
        mock_bridge = MagicMock()
        mock_bridge.is_ready = True
        mock_bridge.call = AsyncMock(side_effect=[
            RuntimeError("Runtime not ready"),
            {"result": "success"}
        ])
        manager._bridge = mock_bridge
        
        with patch.object(manager, '_restart_bridge', new_callable=AsyncMock) as mock_restart:
            result = await manager.call_with_retry("test.method", max_retries=3)
            
            assert result == {"result": "success"}
            mock_restart.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_call_with_retry_exhausts_retries(self):
        """Test that call_with_retry raises after exhausting retries."""
        manager = JSBridgeManager.get_instance()
        
        mock_bridge = MagicMock()
        mock_bridge.is_ready = True
        # Use a consistent error that's not "not ready" (which triggers restart)
        mock_bridge.call = AsyncMock(side_effect=RuntimeError("Connection timeout"))
        manager._bridge = mock_bridge
        
        with pytest.raises(RuntimeError) as exc_info:
            await manager.call_with_retry("test.method", max_retries=3, base_delay=0.001)
        
        # After exhausting retries, the last error should be raised
        assert "Connection timeout" in str(exc_info.value)
        # Bridge.call should be called 3 times (max_retries)
        assert mock_bridge.call.call_count == 3
    
    @pytest.mark.asyncio
    async def test_call_with_retry_non_retryable_error(self):
        """Test that non-retryable errors are raised immediately."""
        manager = JSBridgeManager.get_instance()
        
        mock_bridge = MagicMock()
        mock_bridge.is_ready = True
        # JSON-RPC errors generally aren't retried (except server errors)
        mock_bridge.call = AsyncMock(
            side_effect=JSONRPCError(-32601, "Method not found")
        )
        manager._bridge = mock_bridge
        
        with pytest.raises(JSONRPCError):
            await manager.call_with_retry("test.method")


class TestJSBridgeManagerContextManager:
    """Tests for context manager support."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        JSBridgeManager.reset_instance()
    
    def teardown_method(self):
        """Reset singleton after each test."""
        JSBridgeManager.reset_instance()
    
    @pytest.mark.asyncio
    async def test_context_manager_enters_with_ready_bridge(self):
        """Test that context manager ensures bridge is ready."""
        manager = JSBridgeManager.get_instance()
        
        with patch.object(manager, 'get_bridge', new_callable=AsyncMock) as mock_get:
            mock_bridge = MagicMock()
            mock_get.return_value = mock_bridge
            
            async with manager as mgr:
                assert mgr is manager
                mock_get.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_context_manager_exit_does_not_shutdown(self):
        """Test that exiting context manager doesn't shutdown the bridge."""
        manager = JSBridgeManager.get_instance()
        
        # Mock get_bridge to avoid actual bridge creation
        with patch.object(manager, 'get_bridge', new_callable=AsyncMock):
            async with manager:
                pass
            
            # After exiting context, manager should still have bridge reference
            # (shutdown is not called on context exit)


class TestJSBridgeManagerStatus:
    """Tests for status reporting."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        JSBridgeManager.reset_instance()
    
    def teardown_method(self):
        """Reset singleton after each test."""
        JSBridgeManager.reset_instance()
    
    @pytest.mark.asyncio
    async def test_get_status_no_bridge(self):
        """Test status when no bridge exists."""
        manager = JSBridgeManager.get_instance()
        
        status = await manager.get_status()
        
        assert status["bridge_state"] == "NOT_STARTED"
        assert status["is_ready"] is False
        assert status["reconnect_count"] == 0
        assert status["call_count"] == 0
    
    @pytest.mark.asyncio
    async def test_get_status_with_bridge(self):
        """Test status when bridge exists."""
        manager = JSBridgeManager.get_instance()
        
        mock_bridge = MagicMock()
        mock_bridge.state = RuntimeState.READY
        mock_bridge.is_ready = True
        manager._bridge = mock_bridge
        manager._reconnect_count = 2
        manager._call_count = 10
        
        status = await manager.get_status()
        
        assert status["bridge_state"] == "READY"
        assert status["is_ready"] is True
        assert status["reconnect_count"] == 2
        assert status["call_count"] == 10


class TestConvenienceFunctions:
    """Tests for convenience functions."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        JSBridgeManager.reset_instance()
    
    def teardown_method(self):
        """Reset singleton after each test."""
        JSBridgeManager.reset_instance()
    
    @pytest.mark.asyncio
    async def test_get_bridge_convenience(self):
        """Test the get_bridge convenience function."""
        with patch.object(JSBridgeManager.get_instance(), 'get_bridge', new_callable=AsyncMock) as mock_get:
            mock_bridge = MagicMock()
            mock_get.return_value = mock_bridge
            
            bridge = await get_bridge()
            
            assert bridge is mock_bridge
    
    @pytest.mark.asyncio
    async def test_js_call_convenience(self):
        """Test the js_call convenience function."""
        with patch.object(JSBridgeManager.get_instance(), 'call_with_retry', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"result": "success"}
            
            result = await js_call("test.method", {"param": "value"}, max_retries=5)
            
            assert result == {"result": "success"}
            mock_call.assert_called_once_with("test.method", {"param": "value"}, max_retries=5)
    
    def test_configure_bridge_convenience(self):
        """Test the configure_bridge convenience function."""
        manager = JSBridgeManager.get_instance()
        
        with patch.object(manager, 'configure') as mock_configure:
            configure_bridge(
                services_path=Path("/test"),
                startup_timeout=45.0,
                debug=True,
            )
            
            mock_configure.assert_called_once_with(
                services_path=Path("/test"),
                startup_timeout=45.0,
                request_timeout=60.0,
                health_check_interval=30.0,
                runtime_executable=None,
                debug=True,
            )


class TestJSBridgeManagerReconnection:
    """Tests for reconnection logic."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        JSBridgeManager.reset_instance()
    
    def teardown_method(self):
        """Reset singleton after each test."""
        JSBridgeManager.reset_instance()
    
    @pytest.mark.asyncio
    async def test_restart_bridge_stops_existing(self):
        """Test that restart stops the existing bridge."""
        manager = JSBridgeManager.get_instance()
        
        mock_bridge = MagicMock()
        mock_bridge.stop = AsyncMock()
        manager._bridge = mock_bridge
        
        with patch.object(manager, '_create_bridge', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = MagicMock()
            await manager._restart_bridge()
            
            mock_bridge.stop.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_restart_bridge_increments_count(self):
        """Test that restart increments and resets reconnect count."""
        manager = JSBridgeManager.get_instance()
        
        # Reset count manually before test
        manager._reconnect_count = 0
        manager._bridge = None  # Ensure no existing bridge
        
        with patch.object(manager, '_create_bridge', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = MagicMock()
            await manager._restart_bridge()
            
            # After successful restart, count should be reset to 0
            assert manager._reconnect_count == 0
    
    @pytest.mark.asyncio
    async def test_restart_bridge_backoff(self):
        """Test exponential backoff on multiple restarts."""
        manager = JSBridgeManager.get_instance()
        
        with patch.object(manager, '_create_bridge', new_callable=AsyncMock) as mock_create:
            with patch.object(manager, '_bridge_lock'):  # Skip actual locking
                with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
                    mock_create.return_value = MagicMock()
                    
                    # Manually set count and test backoff logic
                    manager._reconnect_count = 0
                    manager._bridge = None
                    
                    # First restart - count becomes 1, no backoff since count > 1 is false
                    await manager._restart_bridge()
                    mock_sleep.assert_not_called()
                    
                    # Manually set count for second test (simulating previous failure)
                    manager._reconnect_count = 1
                    
                    # Second restart - count becomes 2, backoff of 2 seconds
                    await manager._restart_bridge()
                    mock_sleep.assert_called_once_with(2)


class TestJSBridgeManagerCallerTracking:
    """Tests for caller tracking functionality."""
    
    def setup_method(self):
        """Reset singleton before each test."""
        JSBridgeManager.reset_instance()
    
    def teardown_method(self):
        """Reset singleton after each test."""
        JSBridgeManager.reset_instance()
    
    class MockCaller:
        """Mock class that can be weakly referenced."""
        pass
    
    def test_register_caller(self):
        """Test registering a caller."""
        manager = JSBridgeManager.get_instance()
        
        # Clear existing callers
        manager._active_callers = weakref.WeakSet()
        
        caller = self.MockCaller()
        manager.register_caller(caller)
        
        assert manager.active_caller_count == 1
    
    def test_unregister_caller(self):
        """Test unregistering a caller."""
        manager = JSBridgeManager.get_instance()
        
        # Clear existing callers
        manager._active_callers = weakref.WeakSet()
        
        caller = self.MockCaller()
        manager.register_caller(caller)
        manager.unregister_caller(caller)
        
        assert manager.active_caller_count == 0
    
    def test_multiple_callers(self):
        """Test tracking multiple callers."""
        manager = JSBridgeManager.get_instance()
        
        # Clear existing callers
        manager._active_callers = weakref.WeakSet()
        
        callers = [self.MockCaller() for _ in range(5)]
        for caller in callers:
            manager.register_caller(caller)
        
        assert manager.active_caller_count == 5
        
        # Unregister some
        for caller in callers[:3]:
            manager.unregister_caller(caller)
        
        assert manager.active_caller_count == 2
