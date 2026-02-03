"""Haven run command - Start daemon with scheduler and pipeline processing."""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(help="Start Haven daemon with scheduler and pipeline processing.")
console = Console()


def _setup_logging(verbose: bool, log_file: Optional[Path] = None) -> None:
    """Set up logging configuration.
    
    Args:
        verbose: Enable verbose (DEBUG) logging
        log_file: Optional log file path
    """
    level = logging.DEBUG if verbose else logging.INFO
    format_str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=level,
        format=format_str,
        handlers=handlers,
    )


@app.callback(invoke_without_command=True)
def run(
    config_file: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to configuration file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        "-d",
        help="Run in background as daemon.",
    ),
    max_concurrent: int = typer.Option(
        4,
        "--max-concurrent",
        "-m",
        help="Maximum concurrent pipeline executions.",
        min=1,
        max=32,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose logging.",
    ),
) -> None:
    """Start the Haven daemon with job scheduler and pipeline processing.
    
    This command starts the main Haven service which:
    - Loads and executes scheduled jobs (plugin polling)
    - Processes videos through the pipeline (ingest → analyze → encrypt → upload → sync)
    - Manages parallel execution of multiple pipelines
    
    Example:
        haven run --config config.yaml
        haven run --daemon --max-concurrent 8
        haven run --verbose
    """
    from haven_cli.config import load_config, ensure_directories
    from haven_cli.daemon.pid import PIDFile
    from haven_cli.daemon.service import run_daemon, daemonize

    # Load configuration
    config = load_config(config_file)
    ensure_directories(config)
    
    # Set up PID file
    pid_file = PIDFile(config.data_dir / "haven.pid")
    
    # Check if already running
    if pid_file.is_running():
        console.print("[red]Error: Daemon is already running[/red]")
        running_pid = pid_file.read()
        if running_pid:
            console.print(f"[yellow]PID: {running_pid}[/yellow]")
        raise typer.Exit(code=1)
    
    # Clear stale PID file if exists
    if pid_file.read() is not None:
        pid_file.remove()
    
    console.print("[bold green]Starting Haven daemon...[/bold green]")
    
    if verbose:
        console.print(f"Config: {config_file or 'default'}")
        console.print(f"Max concurrent pipelines: {max_concurrent}")
        console.print(f"Daemon mode: {daemon}")
        console.print(f"Data directory: {config.data_dir}")
    
    # Set up logging before daemonization
    log_file = config.data_dir / "daemon.log" if daemon else None
    _setup_logging(verbose, log_file)
    
    if daemon:
        # Check if daemon mode is supported
        if sys.platform == "win32":
            console.print("[yellow]Warning: Daemon mode not supported on Windows, running in foreground[/yellow]")
        else:
            console.print("[dim]Forking to background...[/dim]")
            # Fork to background
            daemonize(log_file)
            # Note: After daemonize(), we're in the child process
    
    # Create PID file (in child process if daemonized)
    try:
        pid_file.create()
    except OSError as e:
        console.print(f"[red]Error: Failed to create PID file: {e}[/red]")
        raise typer.Exit(code=1)
    
    # Set up cleanup on exit
    import atexit
    atexit.register(pid_file.remove)
    
    try:
        # Run the daemon
        asyncio.run(run_daemon(config, {
            "max_concurrent": max_concurrent,
            "verbose": verbose,
        }))
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    except Exception as e:
        logging.exception("Daemon error")
        console.print(f"[red]Daemon error: {e}[/red]")
        raise typer.Exit(code=1)
    finally:
        # PID file will be removed by atexit handler
        pass


@app.command()
def status(
    config_file: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to configuration file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
) -> None:
    """Check daemon status.
    
    Shows whether the daemon is running and its PID if available.
    
    Example:
        haven run status
    """
    from haven_cli.config import load_config
    from haven_cli.daemon.pid import PIDFile

    # Load configuration
    config = load_config(config_file)
    pid_file = PIDFile(config.data_dir / "haven.pid")
    
    if pid_file.is_running():
        pid = pid_file.read()
        console.print(f"[green]● Daemon is running[/green] (PID: {pid})")
        
        # Show additional info
        console.print(f"  Data directory: {config.data_dir}")
        console.print(f"  Config directory: {config.config_dir}")
        console.print(f"  Database: {config.database_url}")
    else:
        console.print("[yellow]○ Daemon is not running[/yellow]")
        
        # Check for stale PID file
        if pid_file.read() is not None:
            if pid_file.clear_if_stale():
                console.print("[dim]  (removed stale PID file)[/dim]")


@app.command()
def stop(
    config_file: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to configuration file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force kill the daemon (SIGKILL).",
    ),
) -> None:
    """Stop the daemon.
    
    Sends a shutdown signal to the running daemon process.
    By default, sends SIGTERM for graceful shutdown.
    Use --force to send SIGKILL for immediate termination.
    
    Example:
        haven run stop
        haven run stop --force
    """
    from haven_cli.config import load_config
    from haven_cli.daemon.pid import PIDFile

    # Load configuration
    config = load_config(config_file)
    pid_file = PIDFile(config.data_dir / "haven.pid")
    
    pid = pid_file.read()
    
    if pid is None:
        console.print("[yellow]Daemon is not running (no PID file found)[/yellow]")
        raise typer.Exit()
    
    if not pid_file.is_running():
        console.print("[yellow]Daemon is not running (stale PID file)[/yellow]")
        pid_file.remove()
        raise typer.Exit()
    
    # Send signal
    sig = signal.SIGKILL if force else signal.SIGTERM
    sig_name = "SIGKILL" if force else "SIGTERM"
    
    try:
        os.kill(pid, sig)
        if force:
            console.print(f"[red]Force killed daemon (PID: {pid})[/red]")
            pid_file.remove()
        else:
            console.print(f"[green]Shutdown signal sent to daemon (PID: {pid})[/green]")
            console.print("[dim]Daemon will shut down gracefully...[/dim]")
    except ProcessLookupError:
        console.print("[yellow]Daemon process not found (already stopped)[/yellow]")
        pid_file.remove()
    except PermissionError:
        console.print(f"[red]Permission denied: cannot signal process {pid}[/red]")
        raise typer.Exit(code=1)
    except OSError as e:
        console.print(f"[red]Error signaling daemon: {e}[/red]")
        raise typer.Exit(code=1)


@app.command()
def restart(
    config_file: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to configuration file.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        "-d",
        help="Run in background as daemon.",
    ),
    max_concurrent: int = typer.Option(
        4,
        "--max-concurrent",
        "-m",
        help="Maximum concurrent pipeline executions.",
        min=1,
        max=32,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose logging.",
    ),
) -> None:
    """Restart the daemon.
    
    Stops the running daemon (if any) and starts a new one.
    
    Example:
        haven run restart
        haven run restart --daemon
    """
    from haven_cli.config import load_config
    from haven_cli.daemon.pid import PIDFile

    # Load configuration
    config = load_config(config_file)
    pid_file = PIDFile(config.data_dir / "haven.pid")
    
    # Stop if running
    pid = pid_file.read()
    if pid and pid_file.is_running():
        console.print("[yellow]Stopping existing daemon...[/yellow]")
        try:
            os.kill(pid, signal.SIGTERM)
            # Wait briefly for shutdown
            import time
            for _ in range(10):
                time.sleep(0.5)
                if not pid_file.is_running():
                    break
            
            if pid_file.is_running():
                console.print("[red]Daemon did not stop gracefully, forcing...[/red]")
                os.kill(pid, signal.SIGKILL)
                pid_file.remove()
            else:
                console.print("[green]Daemon stopped[/green]")
        except (ProcessLookupError, OSError):
            pid_file.remove()
    
    # Clear stale PID file if any
    if pid_file.read() is not None:
        pid_file.remove()
    
    # Start new daemon
    console.print("[green]Starting daemon...[/green]")
    
    # Re-invoke the run command
    ctx = typer.Context(run)
    ctx.invoke(
        run,
        config_file=config_file,
        daemon=daemon,
        max_concurrent=max_concurrent,
        verbose=verbose,
    )
