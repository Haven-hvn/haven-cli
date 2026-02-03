"""Global exception handling for Haven CLI.

This module provides centralized error handling through custom exception
classes and a decorator that ensures consistent error reporting and
exit codes across all CLI commands.
"""

from functools import wraps
from typing import Callable, TypeVar, Any
import logging
import traceback

import typer
from rich.console import Console

from haven_cli.cli.exit_codes import ExitCode

# Console for error output (stderr)
console = Console(stderr=True)

# Logger for error logging
logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class HavenError(Exception):
    """Base exception for Haven CLI.
    
    All custom exceptions in Haven CLI should inherit from this class
    to ensure proper error handling and exit codes.
    
    Attributes:
        message: Error message
        exit_code: Exit code to use when exiting
        details: Optional dictionary of additional error details
    """
    
    exit_code: int = ExitCode.GENERAL_ERROR
    
    def __init__(
        self,
        message: str,
        exit_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the exception.
        
        Args:
            message: Error message
            exit_code: Optional override for exit code
            details: Optional dictionary of additional error details
        """
        super().__init__(message)
        self.message = message
        if exit_code is not None:
            self.exit_code = exit_code
        self.details = details or {}
    
    def __str__(self) -> str:
        """Return string representation of the error."""
        if self.details:
            details_str = ", ".join(f"{k}={v}" for k, v in self.details.items())
            return f"{self.message} ({details_str})"
        return self.message


class ConfigurationError(HavenError):
    """Configuration-related error.
    
    Raised when there's an issue with configuration files,
    environment variables, or config validation.
    
    Examples:
        - Missing required configuration
        - Invalid configuration file format
        - Configuration validation failure
    """
    
    exit_code = ExitCode.CONFIGURATION_ERROR


class PluginError(HavenError):
    """Plugin-related error.
    
    Raised when there's an issue with plugin loading,
    initialization, or execution.
    
    Examples:
        - Plugin not found
        - Plugin initialization failure
        - Plugin configuration error
    """
    
    exit_code = ExitCode.PLUGIN_ERROR


class PipelineError(HavenError):
    """Pipeline processing error.
    
    Raised when there's an issue during pipeline execution,
    including processing, analysis, or transformation steps.
    
    Examples:
        - Processing step failure
        - Analysis error
        - Pipeline context error
    """
    
    exit_code = ExitCode.PIPELINE_ERROR


class NetworkError(HavenError):
    """Network/connectivity error.
    
    Raised when there's an issue with network operations,
    including HTTP requests, API calls, or connectivity.
    
    Examples:
        - Connection timeout
        - API unavailable
        - Network unreachable
    """
    
    exit_code = ExitCode.NETWORK_ERROR


class StorageError(HavenError):
    """Storage/Filecoin error.
    
    Raised when there's an issue with storage operations,
    including Filecoin uploads, downloads, or sync.
    
    Examples:
        - Upload failure
        - Download failure
        - Filecoin network error
    """
    
    exit_code = ExitCode.STORAGE_ERROR


class ValidationError(HavenError):
    """Validation error for user input.
    
    Raised when user input fails validation checks.
    
    Examples:
        - Invalid file path
        - Invalid CID format
        - Invalid option combination
    """
    
    exit_code = ExitCode.INVALID_ARGUMENT


class NotFoundError(HavenError):
    """Resource not found error.
    
    Raised when a requested resource cannot be found.
    
    Examples:
        - Job not found
        - Video not found
        - Plugin not found
    """
    
    exit_code = ExitCode.NOT_FOUND


class PermissionError(HavenError):
    """Permission denied error.
    
    Raised when an operation fails due to insufficient permissions.
    
    Examples:
        - File permission denied
        - API access denied
        - Configuration file not readable
    """
    
    exit_code = ExitCode.PERMISSION_DENIED


def handle_errors(func: F) -> F:
    """Decorator for consistent error handling across CLI commands.
    
    This decorator catches all exceptions and converts them to appropriate
    error messages and exit codes. It handles:
    
    - HavenError subclasses: Display error message with appropriate exit code
    - KeyboardInterrupt: Show cancellation message with exit code 130
    - Other exceptions: Show generic error with option for verbose details
    
    Args:
        func: The function to wrap
        
    Returns:
        Wrapped function with error handling
        
    Example:
        @app.command()
        @handle_errors
        def my_command():
            raise ConfigurationError("Invalid config")
    """
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except HavenError as e:
            # Log the error for debugging
            logger.error(
                f"HavenError: {e.message}",
                extra={"exit_code": e.exit_code, "details": e.details},
            )
            
            # Display user-friendly error
            console.print(f"[red]Error:[/red] {e.message}")
            
            # Show details if available
            if e.details:
                for key, value in e.details.items():
                    console.print(f"  [dim]{key}:[/dim] {value}")
            
            raise typer.Exit(code=e.exit_code)
            
        except KeyboardInterrupt:
            console.print("\n[yellow]Operation cancelled by user.[/yellow]")
            logger.info("Operation cancelled by user (KeyboardInterrupt)")
            raise typer.Exit(code=ExitCode.CANCELLED)
            
        except typer.Exit:
            # Re-raise typer.Exit as-is
            raise
            
        except Exception as e:
            # Log full exception for debugging
            logger.exception("Unexpected error occurred")
            
            # Display generic error to user
            console.print(f"[red]Unexpected error:[/red] {e}")
            console.print("[dim]Run with --verbose for more details[/dim]")
            
            raise typer.Exit(code=ExitCode.GENERAL_ERROR)
    
    return wrapper  # type: ignore[return-value]


def handle_errors_async(func: F) -> F:
    """Decorator for consistent error handling in async CLI commands.
    
    This is a variant of handle_errors for async functions. It provides
    the same error handling behavior but works with coroutines.
    
    Args:
        func: The async function to wrap
        
    Returns:
        Wrapped async function with error handling
    """
    @wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except HavenError as e:
            logger.error(
                f"HavenError: {e.message}",
                extra={"exit_code": e.exit_code, "details": e.details},
            )
            console.print(f"[red]Error:[/red] {e.message}")
            
            if e.details:
                for key, value in e.details.items():
                    console.print(f"  [dim]{key}:[/dim] {value}")
            
            raise typer.Exit(code=e.exit_code)
            
        except KeyboardInterrupt:
            console.print("\n[yellow]Operation cancelled by user.[/yellow]")
            logger.info("Operation cancelled by user (KeyboardInterrupt)")
            raise typer.Exit(code=ExitCode.CANCELLED)
            
        except typer.Exit:
            raise
            
        except Exception as e:
            logger.exception("Unexpected error occurred")
            console.print(f"[red]Unexpected error:[/red] {e}")
            console.print("[dim]Run with --verbose for more details[/dim]")
            raise typer.Exit(code=ExitCode.GENERAL_ERROR)
    
    return async_wrapper  # type: ignore[return-value]


def get_error_context(verbose: bool = False) -> str:
    """Get formatted error context for debugging.
    
    Args:
        verbose: If True, include full traceback
        
    Returns:
        Formatted error context string
    """
    exc_info = traceback.format_exc()
    
    if verbose:
        return exc_info
    
    # Get just the exception type and message
    lines = exc_info.strip().split("\n")
    if len(lines) >= 2:
        return f"{lines[-2]}: {lines[-1]}"
    
    return exc_info
