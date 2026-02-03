"""Confirmation prompts and user interaction utilities for Haven CLI.

This module provides standardized prompts for dangerous operations,
confirmation workflows, and user input handling.
"""

from typing import TypeVar

import typer
from rich.console import Console
from rich.panel import Panel

# Console for prompt output
console = Console()

T = TypeVar("T")


def confirm_dangerous(
    message: str,
    default: bool = False,
    show_warning: bool = True,
) -> bool:
    """Confirm a dangerous operation.
    
    This should be used for operations that could result in data loss
    or other significant consequences.
    
    Args:
        message: The confirmation message describing the operation
        default: Default value if user just presses enter
        show_warning: Whether to show a warning prefix
        
    Returns:
        True if user confirmed, False otherwise
        
    Example:
        if confirm_dangerous("Delete all uploaded videos?"):
            delete_videos()
    """
    prefix = "[yellow]⚠[/yellow] " if show_warning else ""
    return typer.confirm(f"{prefix}{message}", default=default)


def confirm_with_input(
    message: str,
    expected: str,
    case_sensitive: bool = True,
) -> bool:
    """Confirm by typing expected value.
    
    This is for extremely dangerous operations where a simple Y/n
    confirmation might not be sufficient.
    
    Args:
        message: The message explaining what will happen
        expected: The value the user must type to confirm
        case_sensitive: Whether the comparison should be case-sensitive
        
    Returns:
        True if user typed the expected value, False otherwise
        
    Example:
        if confirm_with_input(
            "This will DELETE ALL DATA permanently!",
            expected="DELETE"
        ):
            wipe_database()
    """
    console.print(f"[yellow]⚠[/yellow] {message}")
    console.print(f"[dim]Type '{expected}' to confirm, or anything else to cancel:[/dim]")
    
    response = typer.prompt("Confirm")
    
    if case_sensitive:
        return response == expected
    return response.lower() == expected.lower()


def confirm_destructive_operation(
    operation: str,
    target: str,
    consequences: list[str] | None = None,
) -> bool:
    """Confirm a destructive operation with full details.
    
    This provides a detailed confirmation dialog for destructive
    operations, showing what will be affected and the consequences.
    
    Args:
        operation: Description of the operation (e.g., "Delete")
        target: What will be affected (e.g., "5 videos")
        consequences: List of potential consequences
        
    Returns:
        True if user confirmed, False otherwise
        
    Example:
        if confirm_destructive_operation(
            operation="Delete",
            target="dataset 'production_videos'",
            consequences=[
                "5 videos will be permanently deleted",
                "Associated metadata will be lost",
                "This action cannot be undone"
            ]
        ):
            delete_dataset()
    """
    # Build the warning panel
    content_lines = [f"[bold]{operation}:[/bold] {target}"]
    
    if consequences:
        content_lines.append("")
        content_lines.append("[bold red]Consequences:[/bold red]")
        for consequence in consequences:
            content_lines.append(f"  • {consequence}")
    
    content_lines.append("")
    content_lines.append("[bold]This action cannot be undone.[/bold]")
    
    panel = Panel(
        "\n".join(content_lines),
        title="[yellow]⚠ Destructive Operation[/yellow]",
        border_style="red",
    )
    
    console.print(panel)
    
    return typer.confirm("Do you want to proceed?", default=False)


def select_from_list(
    message: str,
    options: list[tuple[str, T]],
    default: int = 0,
) -> T:
    """Let user select an option from a list.
    
    Args:
        message: The prompt message
        options: List of (display_name, value) tuples
        default: Default selection index (0-based)
        
    Returns:
        The selected value
        
    Raises:
        typer.Exit: If user cancels
        
    Example:
        choice = select_from_list(
            "Select upload mode:",
            [
                ("Fast (skip analysis)", UploadMode.FAST),
                ("Full (with analysis)", UploadMode.FULL),
            ]
        )
    """
    console.print(f"[bold]{message}[/bold]")
    
    for i, (name, _) in enumerate(options):
        marker = "[green]›[/green]" if i == default else " "
        console.print(f"  {marker} {i + 1}. {name}")
    
    console.print("[dim]Enter number or 'q' to cancel[/dim]")
    
    while True:
        response = typer.prompt("Choice", default=str(default + 1))
        
        if response.lower() in ("q", "quit", "cancel"):
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit()
        
        try:
            index = int(response) - 1
            if 0 <= index < len(options):
                return options[index][1]
            else:
                console.print(f"[red]Please enter a number between 1 and {len(options)}[/red]")
        except ValueError:
            console.print("[red]Please enter a valid number[/red]")


def prompt_for_input(
    message: str,
    default: str | None = None,
    hide_input: bool = False,
    confirmation_prompt: bool = False,
    validate: callable | None = None,  # type: ignore[type-arg]
) -> str:
    """Prompt for user input with optional validation.
    
    Args:
        message: The prompt message
        default: Default value if user just presses enter
        hide_input: Whether to hide the input (for passwords)
        confirmation_prompt: Whether to ask for confirmation
        validate: Optional validation function that returns error message or None
        
    Returns:
        The user's input
        
    Raises:
        ValueError: If validation fails
        
    Example:
        api_key = prompt_for_input(
            "Enter API key:",
            hide_input=True,
            validate=lambda x: "API key required" if not x else None
        )
    """
    while True:
        value = typer.prompt(
            message,
            default=default or "",
            hide_input=hide_input,
            confirmation_prompt=confirmation_prompt,
        )
        
        if validate:
            error = validate(value)
            if error:
                console.print(f"[red]{error}[/red]")
                continue
        
        return value


def confirm_overwrite(path: str, force: bool = False) -> bool:
    """Confirm overwriting an existing file.
    
    Args:
        path: Path to the file that would be overwritten
        force: If True, skip confirmation and return True
        
    Returns:
        True if should proceed (overwrite), False otherwise
    """
    if force:
        return True
    
    return confirm_dangerous(
        f"File already exists: {path}\nOverwrite?",
        default=False,
    )


def prompt_with_help(
    message: str,
    help_text: str,
    default: str | None = None,
) -> str:
    """Prompt for input with inline help text.
    
    Args:
        message: The prompt message
        help_text: Help text to display
        default: Default value
        
    Returns:
        The user's input
    """
    console.print(f"[dim]{help_text}[/dim]")
    return typer.prompt(message, default=default or "")


class AbortOperation(Exception):
    """Exception raised when user aborts an operation."""
    
    def __init__(self, message: str = "Operation cancelled by user") -> None:
        """Initialize the exception.
        
        Args:
            message: Optional custom message
        """
        self.message = message
        super().__init__(message)


def abort_if_not_confirmed(
    message: str,
    default: bool = False,
) -> None:
    """Abort operation if user doesn't confirm.
    
    This is a convenience function that raises AbortOperation if
    the user doesn't confirm.
    
    Args:
        message: Confirmation message
        default: Default value
        
    Raises:
        AbortOperation: If user doesn't confirm
    """
    if not confirm_dangerous(message, default=default):
        raise AbortOperation()
