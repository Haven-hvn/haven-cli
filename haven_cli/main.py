"""Main CLI entry point for Haven."""

import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from haven_cli import __app_name__, __version__
from haven_cli.cli import config, download, jobs, plugins, run, upload
from haven_cli.cli.exit_codes import ExitCode

# Create the main Typer app
app = typer.Typer(
    name=__app_name__,
    help="Haven CLI - Event-driven data pipeline for media archival and processing.",
    add_completion=True,
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Console for CLI output
console = Console()

# Register command groups
app.add_typer(run.app, name="run")
app.add_typer(upload.app, name="upload")
app.add_typer(download.app, name="download")
app.add_typer(jobs.app, name="jobs")
app.add_typer(plugins.app, name="plugins")
app.add_typer(config.app, name="config")

# Global state for CLI options
_global_state: dict[str, bool] = {
    "verbose": False,
    "debug": False,
    "json": False,
    "quiet": False,
}


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        console.print(f"{__app_name__} v{__version__}")
        raise typer.Exit(code=ExitCode.SUCCESS)


def _setup_logging(
    verbose: bool = False,
    debug: bool = False,
    quiet: bool = False,
    log_file: Optional[Path] = None,
) -> None:
    """Set up logging configuration based on CLI options.
    
    Args:
        verbose: Enable INFO level logging
        debug: Enable DEBUG level logging
        quiet: Suppress non-error output (WARNING and above)
        log_file: Optional log file path
    """
    # Determine log level
    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    elif quiet:
        level = logging.ERROR
    else:
        level = logging.WARNING
    
    # Configure format
    if debug:
        format_str = "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
    else:
        format_str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # Set up handlers
    handlers: list[logging.Handler] = []
    
    # Add file handler if specified
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)  # Always log debug to file
        handlers.append(file_handler)
    
    # Add console handler unless quiet mode
    if not quiet:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)
        handlers.append(console_handler)
    elif log_file:
        # In quiet mode with log file, still log to file
        pass
    else:
        # In quiet mode without log file, add null handler to prevent warnings
        handlers.append(logging.NullHandler())
    
    # Configure root logger
    logging.basicConfig(
        level=level,
        format=format_str,
        handlers=handlers,
        force=True,  # Override any existing configuration
    )
    
    # Log the configuration
    logger = logging.getLogger(__name__)
    logger.debug(f"Logging configured: level={logging.getLevelName(level)}, debug={debug}")


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-V",
        help="Enable verbose output (INFO level logging).",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Enable debug mode (DEBUG level logging with full tracebacks).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output in JSON format where applicable.",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress non-error output.",
    ),
    log_file: Optional[Path] = typer.Option(
        None,
        "--log-file",
        help="Log to file (logs DEBUG level regardless of console settings).",
    ),
) -> None:
    """Haven CLI - Event-driven data pipeline for media archival and processing.
    
    Haven provides a complete pipeline for archiving media to Filecoin:
    
    [bold]Core Commands:[/bold]
    
    • [cyan]upload[/cyan] - Upload files to Filecoin with optional encryption
    • [cyan]download[/cyan] - Download and decrypt files from Filecoin
    • [cyan]run[/cyan] - Start the Haven daemon with scheduler
    • [cyan]jobs[/cyan] - Manage scheduled jobs for automated archiving
    • [cyan]plugins[/cyan] - Manage archiver plugins
    • [cyan]config[/cyan] - Manage configuration
    
    [bold]Global Options:[/bold]
    
    Use [cyan]--verbose[/cyan] for more detailed output,
    [cyan]--debug[/cyan] for full debug information,
    or [cyan]--json[/cyan] for machine-readable output.
    
    [bold]Examples:[/bold]
    
        haven upload video.mp4 --encrypt
        haven download bafybeig... --output video.mp4
        haven run --daemon
        haven jobs list
    
    For more help on a specific command, use: [cyan]haven <command> --help[/cyan]
    """
    # Store global state
    _global_state["verbose"] = verbose
    _global_state["debug"] = debug
    _global_state["json"] = json_output
    _global_state["quiet"] = quiet
    
    # Validate mutually exclusive options
    if quiet and verbose:
        console.print("[red]Error:[/red] --quiet and --verbose are mutually exclusive")
        raise typer.Exit(code=ExitCode.INVALID_ARGUMENT)
    
    if quiet and debug:
        console.print("[red]Error:[/red] --quiet and --debug are mutually exclusive")
        raise typer.Exit(code=ExitCode.INVALID_ARGUMENT)
    
    # Set up logging
    _setup_logging(verbose=verbose, debug=debug, quiet=quiet, log_file=log_file)
    
    # Log startup info in debug mode
    logger = logging.getLogger(__name__)
    logger.debug(f"Haven CLI v{__version__} starting")
    logger.debug(f"Options: verbose={verbose}, debug={debug}, json={json_output}, quiet={quiet}")


def get_global_option(name: str) -> bool:
    """Get the value of a global CLI option.
    
    Args:
        name: Name of the option (verbose, debug, json, quiet)
        
    Returns:
        True if the option is enabled, False otherwise
        
    Example:
        if get_global_option("verbose"):
            print_extra_info()
    """
    return _global_state.get(name, False)


def is_verbose() -> bool:
    """Check if verbose mode is enabled.
    
    Returns:
        True if verbose or debug mode is enabled
    """
    return _global_state.get("verbose", False) or _global_state.get("debug", False)


def is_debug() -> bool:
    """Check if debug mode is enabled.
    
    Returns:
        True if debug mode is enabled
    """
    return _global_state.get("debug", False)


def is_json() -> bool:
    """Check if JSON output mode is enabled.
    
    Returns:
        True if JSON output is requested
    """
    return _global_state.get("json", False)


def is_quiet() -> bool:
    """Check if quiet mode is enabled.
    
    Returns:
        True if quiet mode is enabled
    """
    return _global_state.get("quiet", False)


# Re-export for convenience
__all__ = [
    "app",
    "console",
    "get_global_option",
    "is_verbose",
    "is_debug",
    "is_json",
    "is_quiet",
]


if __name__ == "__main__":
    app()
