"""Progress indicators and status utilities for Haven CLI.

This module provides standardized progress display components using Rich,
including spinners for indeterminate operations, progress bars for
determinate operations, and status messages.
"""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Callable, TypeVar

from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeRemainingColumn,
    TimeElapsedColumn,
)

# Default console for progress output
console = Console()

T = TypeVar("T")


@contextmanager
def spinner(
    message: str,
    transient: bool = True,
    console_instance: Console | None = None,
) -> Generator[None, None, None]:
    """Show a spinner for indeterminate operations.
    
    Use this for operations where the duration is unknown or the
    operation doesn't have discrete progress steps.
    
    Args:
        message: The message to display next to the spinner
        transient: If True, remove the spinner after completion
        console_instance: Optional custom console instance
        
    Yields:
        None
        
    Example:
        with spinner("Connecting to Filecoin network..."):
            result = await connect()
    """
    prog_console = console_instance or console
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=transient,
        console=prog_console,
    ) as progress:
        progress.add_task(description=message, total=None)
        yield


@contextmanager
def progress_bar(
    total: int,
    description: str = "Processing",
    show_time: bool = True,
    console_instance: Console | None = None,
) -> Generator[Callable[[int], None], None, None]:
    """Show a progress bar for determinate operations.
    
    Use this for operations with known total work that can be
    reported incrementally.
    
    Args:
        total: Total number of steps/items
        description: Description to show with the progress bar
        show_time: Whether to show time elapsed/remaining
        console_instance: Optional custom console instance
        
    Yields:
        Function to advance the progress bar by N steps
        
    Example:
        with progress_bar(total=100, description="Uploading") as advance:
            for chunk in upload():
                advance(1)  # Advance by 1 for each chunk
    """
    prog_console = console_instance or console
    
    columns = [
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
    ]
    
    if show_time:
        columns.extend([
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        ])
    
    with Progress(
        *columns,
        console=prog_console,
    ) as progress:
        task = progress.add_task(description, total=total)
        
        def advance(n: int = 1) -> None:
            """Advance the progress bar by n steps."""
            progress.update(task, advance=n)
        
        yield advance


@contextmanager
def multi_step_progress(
    steps: list[str],
    console_instance: Console | None = None,
) -> Generator[Callable[[int], None], None, None]:
    """Show progress through multiple named steps.
    
    This is useful for pipelines or multi-stage operations where
    each stage has a name.
    
    Args:
        steps: List of step names
        console_instance: Optional custom console instance
        
    Yields:
        Function to advance to the next step
        
    Example:
        steps = ["Ingest", "Analyze", "Encrypt", "Upload", "Sync"]
        with multi_step_progress(steps) as next_step:
            for step_func in pipeline_steps:
                step_func()
                next_step()  # Advance to next step
    """
    prog_console = console_instance or console
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=prog_console,
    ) as progress:
        task = progress.add_task(steps[0], total=len(steps))
        current_step = 0
        
        def advance(_n: int = 1) -> None:
            """Advance to the next step."""
            nonlocal current_step
            current_step += 1
            if current_step < len(steps):
                progress.update(task, advance=1, description=steps[current_step])
            else:
                progress.update(task, advance=1)
        
        yield advance


def status_message(message: str, status: str = "info") -> None:
    """Print a status message with an appropriate icon.
    
    Args:
        message: The message to display
        status: Status type - one of: info, success, warning, error
        
    Example:
        status_message("Starting operation...", "info")
        status_message("Operation complete!", "success")
        status_message("Low disk space", "warning")
        status_message("Failed to connect", "error")
    """
    icons = {
        "info": "[blue]ℹ[/blue]",
        "success": "[green]✓[/green]",
        "warning": "[yellow]⚠[/yellow]",
        "error": "[red]✗[/red]",
        "pending": "[cyan]○[/cyan]",
        "running": "[blue]◉[/blue]",
    }
    
    icon = icons.get(status, "[blue]ℹ[/blue]")
    console.print(f"{icon} {message}")


def step_complete(step_name: str, details: str | None = None) -> None:
    """Print a step completion message.
    
    Args:
        step_name: Name of the completed step
        details: Optional additional details
    """
    if details:
        console.print(f"  [green]✓[/green] {step_name} [dim]{details}[/dim]")
    else:
        console.print(f"  [green]✓[/green] {step_name}")


def step_failed(step_name: str, reason: str | None = None) -> None:
    """Print a step failure message.
    
    Args:
        step_name: Name of the failed step
        reason: Optional failure reason
    """
    if reason:
        console.print(f"  [red]✗[/red] {step_name} [dim]- {reason}[/dim]")
    else:
        console.print(f"  [red]✗[/red] {step_name}")


def step_skipped(step_name: str, reason: str | None = None) -> None:
    """Print a step skipped message.
    
    Args:
        step_name: Name of the skipped step
        reason: Optional reason for skipping
    """
    if reason:
        console.print(f"  [yellow]⊘[/yellow] {step_name} [dim]- {reason}[/dim]")
    else:
        console.print(f"  [yellow]⊘[/yellow] {step_name}")


class PipelineProgress:
    """Helper class for displaying pipeline progress.
    
    This class provides a higher-level interface for tracking
    progress through a multi-stage pipeline.
    
    Example:
        progress = PipelineProgress(["Ingest", "Analyze", "Upload"])
        with progress:
            progress.start_step("Ingest")
            # ... do ingest ...
            progress.complete_step()
            
            progress.start_step("Analyze")
            # ... do analyze ...
            progress.complete_step()
    """
    
    def __init__(
        self,
        steps: list[str],
        console_instance: Console | None = None,
    ) -> None:
        """Initialize pipeline progress tracker.
        
        Args:
            steps: List of step names
            console_instance: Optional custom console instance
        """
        self.steps = steps
        self.console = console_instance or console
        self._current_step_index = 0
        self._progress: Progress | None = None
        self._task_id: int | None = None
    
    def __enter__(self) -> "PipelineProgress":
        """Enter context manager."""
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=self.console,
        )
        self._progress.__enter__()
        self._task_id = self._progress.add_task(
            self.steps[0],
            total=len(self.steps),
        )
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[no-untyped-def]
        """Exit context manager."""
        if self._progress:
            self._progress.__exit__(exc_type, exc_val, exc_tb)
    
    def start_step(self, step_name: str) -> None:
        """Mark a step as started.
        
        Args:
            step_name: Name of the step starting
        """
        if self._progress and self._task_id is not None:
            self._progress.update(self._task_id, description=f"{step_name}...")
    
    def complete_step(self) -> None:
        """Mark the current step as complete and advance."""
        self._current_step_index += 1
        if self._progress and self._task_id is not None:
            if self._current_step_index < len(self.steps):
                next_step = self.steps[self._current_step_index]
                self._progress.update(
                    self._task_id,
                    advance=1,
                    description=next_step,
                )
            else:
                self._progress.update(self._task_id, advance=1)
    
    def update_description(self, description: str) -> None:
        """Update the current step description.
        
        Args:
            description: New description to display
        """
        if self._progress and self._task_id is not None:
            self._progress.update(self._task_id, description=description)
