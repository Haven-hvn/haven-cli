"""Tests for error handler module."""

import pytest
import typer
from unittest.mock import Mock, patch

from haven_cli.cli.error_handler import (
    HavenError,
    ConfigurationError,
    PluginError,
    PipelineError,
    NetworkError,
    StorageError,
    ValidationError,
    NotFoundError,
    PermissionError,
    handle_errors,
    handle_errors_async,
    get_error_context,
)
from haven_cli.cli.exit_codes import ExitCode


class TestHavenError:
    """Test base HavenError class."""
    
    def test_basic_error(self) -> None:
        """Test basic error creation."""
        error = HavenError("Test error")
        assert error.message == "Test error"
        assert error.exit_code == ExitCode.GENERAL_ERROR
        assert error.details == {}
    
    def test_error_with_exit_code(self) -> None:
        """Test error with custom exit code."""
        error = HavenError("Test error", exit_code=ExitCode.CONFIGURATION_ERROR)
        assert error.exit_code == ExitCode.CONFIGURATION_ERROR
    
    def test_error_with_details(self) -> None:
        """Test error with details."""
        details = {"key": "value", "count": 42}
        error = HavenError("Test error", details=details)
        assert error.details == details
    
    def test_error_str_without_details(self) -> None:
        """Test string representation without details."""
        error = HavenError("Test error")
        assert str(error) == "Test error"
    
    def test_error_str_with_details(self) -> None:
        """Test string representation with details."""
        error = HavenError("Test error", details={"key": "value"})
        assert "Test error" in str(error)
        assert "key=value" in str(error)


class TestConfigurationError:
    """Test ConfigurationError class."""
    
    def test_default_exit_code(self) -> None:
        """Test default exit code is CONFIGURATION_ERROR."""
        error = ConfigurationError("Config error")
        assert error.exit_code == ExitCode.CONFIGURATION_ERROR


class TestPluginError:
    """Test PluginError class."""
    
    def test_default_exit_code(self) -> None:
        """Test default exit code is PLUGIN_ERROR."""
        error = PluginError("Plugin error")
        assert error.exit_code == ExitCode.PLUGIN_ERROR


class TestPipelineError:
    """Test PipelineError class."""
    
    def test_default_exit_code(self) -> None:
        """Test default exit code is PIPELINE_ERROR."""
        error = PipelineError("Pipeline error")
        assert error.exit_code == ExitCode.PIPELINE_ERROR


class TestNetworkError:
    """Test NetworkError class."""
    
    def test_default_exit_code(self) -> None:
        """Test default exit code is NETWORK_ERROR."""
        error = NetworkError("Network error")
        assert error.exit_code == ExitCode.NETWORK_ERROR


class TestStorageError:
    """Test StorageError class."""
    
    def test_default_exit_code(self) -> None:
        """Test default exit code is STORAGE_ERROR."""
        error = StorageError("Storage error")
        assert error.exit_code == ExitCode.STORAGE_ERROR


class TestValidationError:
    """Test ValidationError class."""
    
    def test_default_exit_code(self) -> None:
        """Test default exit code is INVALID_ARGUMENT."""
        error = ValidationError("Validation error")
        assert error.exit_code == ExitCode.INVALID_ARGUMENT


class TestNotFoundError:
    """Test NotFoundError class."""
    
    def test_default_exit_code(self) -> None:
        """Test default exit code is NOT_FOUND."""
        error = NotFoundError("Not found error")
        assert error.exit_code == ExitCode.NOT_FOUND


class TestPermissionError:
    """Test PermissionError class."""
    
    def test_default_exit_code(self) -> None:
        """Test default exit code is PERMISSION_DENIED."""
        error = PermissionError("Permission error")
        assert error.exit_code == ExitCode.PERMISSION_DENIED


class TestHandleErrors:
    """Test handle_errors decorator."""
    
    def test_successful_execution(self) -> None:
        """Test decorator with successful execution."""
        @handle_errors
        def test_func():
            return "success"
        
        result = test_func()
        assert result == "success"
    
    def test_haven_error_handling(self) -> None:
        """Test decorator handles HavenError."""
        @handle_errors
        def test_func():
            raise ConfigurationError("Test config error")
        
        with pytest.raises(typer.Exit) as exc_info:
            with patch("haven_cli.cli.error_handler.console"):
                test_func()
        
        assert exc_info.value.exit_code == ExitCode.CONFIGURATION_ERROR
    
    def test_keyboard_interrupt_handling(self) -> None:
        """Test decorator handles KeyboardInterrupt."""
        @handle_errors
        def test_func():
            raise KeyboardInterrupt()
        
        with pytest.raises(typer.Exit) as exc_info:
            with patch("haven_cli.cli.error_handler.console"):
                test_func()
        
        assert exc_info.value.exit_code == ExitCode.CANCELLED
    
    def test_generic_exception_handling(self) -> None:
        """Test decorator handles generic exceptions."""
        @handle_errors
        def test_func():
            raise ValueError("Test error")
        
        with pytest.raises(typer.Exit) as exc_info:
            with patch("haven_cli.cli.error_handler.console"):
                test_func()
        
        assert exc_info.value.exit_code == ExitCode.GENERAL_ERROR
    
    def test_typer_exit_re_raised(self) -> None:
        """Test that typer.Exit is re-raised as-is."""
        @handle_errors
        def test_func():
            raise typer.Exit(code=42)
        
        with pytest.raises(typer.Exit) as exc_info:
            test_func()
        
        assert exc_info.value.exit_code == 42


class TestHandleErrorsAsync:
    """Test handle_errors_async decorator."""
    
    @pytest.mark.asyncio
    async def test_successful_execution(self) -> None:
        """Test async decorator with successful execution."""
        @handle_errors_async
        async def test_func():
            return "success"
        
        result = await test_func()
        assert result == "success"
    
    @pytest.mark.asyncio
    async def test_haven_error_handling(self) -> None:
        """Test async decorator handles HavenError."""
        @handle_errors_async
        async def test_func():
            raise ConfigurationError("Test config error")
        
        with pytest.raises(typer.Exit) as exc_info:
            with patch("haven_cli.cli.error_handler.console"):
                await test_func()
        
        assert exc_info.value.exit_code == ExitCode.CONFIGURATION_ERROR
    
    @pytest.mark.asyncio
    async def test_keyboard_interrupt_handling(self) -> None:
        """Test async decorator handles KeyboardInterrupt."""
        @handle_errors_async
        async def test_func():
            raise KeyboardInterrupt()
        
        with pytest.raises(typer.Exit) as exc_info:
            with patch("haven_cli.cli.error_handler.console"):
                await test_func()
        
        assert exc_info.value.exit_code == ExitCode.CANCELLED
    
    @pytest.mark.asyncio
    async def test_generic_exception_handling(self) -> None:
        """Test async decorator handles generic exceptions."""
        @handle_errors_async
        async def test_func():
            raise ValueError("Test error")
        
        with pytest.raises(typer.Exit) as exc_info:
            with patch("haven_cli.cli.error_handler.console"):
                await test_func()
        
        assert exc_info.value.exit_code == ExitCode.GENERAL_ERROR


class TestGetErrorContext:
    """Test get_error_context function."""
    
    def test_context_without_verbose(self) -> None:
        """Test getting error context without verbose mode."""
        try:
            raise ValueError("Test error")
        except ValueError:
            context = get_error_context(verbose=False)
            assert "ValueError" in context or "Test error" in context
    
    def test_context_with_verbose(self) -> None:
        """Test getting error context with verbose mode."""
        try:
            raise ValueError("Test error")
        except ValueError:
            context = get_error_context(verbose=True)
            # In verbose mode, should have full traceback
            assert "Traceback" in context or "ValueError" in context
    
    def test_context_no_exception(self) -> None:
        """Test getting error context when no exception is active."""
        context = get_error_context(verbose=False)
        # Should return something even if no exception
        assert isinstance(context, str)
