"""Output formatting utilities for Haven CLI.

This module provides standardized output formatting for various data types,
including JSON, tables, and operation results. It supports both human-readable
and machine-readable output formats.
"""

import json
from typing import Any, Dict, List, Protocol, runtime_checkable
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.tree import Tree
from rich.panel import Panel
from rich.json import JSON as RichJSON
from rich.syntax import Syntax

# Default console for output
console = Console()


class OutputFormatter:
    """Base class for output formatters.
    
    This class provides a consistent interface for formatting output
    in different formats (human-readable, JSON, etc.).
    """
    
    def __init__(self, json_mode: bool = False, console_instance: Console | None = None) -> None:
        """Initialize the formatter.
        
        Args:
            json_mode: Whether to output in JSON format
            console_instance: Optional custom console instance
        """
        self.json_mode = json_mode
        self.console = console_instance or console
    
    def print(self, data: Any) -> None:
        """Print data in the appropriate format.
        
        Args:
            data: Data to print
        """
        if self.json_mode:
            print_json(data, self.console)
        else:
            self._print_human(data)
    
    def _print_human(self, data: Any) -> None:
        """Print data in human-readable format.
        
        Args:
            data: Data to print
        """
        self.console.print(data)


def print_json(data: Any, console_instance: Console | None = None) -> None:
    """Print data as formatted JSON.
    
    Args:
        data: Data to print (must be JSON-serializable)
        console_instance: Optional custom console instance
    """
    prog_console = console_instance or console
    
    # Use Rich's built-in JSON support for syntax highlighting
    json_str = json.dumps(data, indent=2, default=str)
    prog_console.print(RichJSON(json_str))


def print_table(
    data: List[Dict[str, Any]],
    columns: List[str],
    title: str | None = None,
    column_styles: Dict[str, str] | None = None,
    console_instance: Console | None = None,
) -> None:
    """Print data as a formatted table.
    
    Args:
        data: List of dictionaries containing row data
        columns: List of column keys to display
        title: Optional table title
        column_styles: Optional dict mapping column names to Rich styles
        console_instance: Optional custom console instance
        
    Example:
        data = [
            {"name": "video1.mp4", "size": "100MB", "status": "uploaded"},
            {"name": "video2.mp4", "size": "200MB", "status": "pending"},
        ]
        print_table(data, ["name", "size", "status"], title="Videos")
    """
    prog_console = console_instance or console
    column_styles = column_styles or {}
    
    table = Table(title=title)
    
    for col in columns:
        style = column_styles.get(col)
        header = col.replace("_", " ").title()
        table.add_column(header, style=style)
    
    for row in data:
        values = []
        for col in columns:
            value = row.get(col, "")
            # Handle None values
            if value is None:
                value = ""
            # Handle boolean values with styling
            elif isinstance(value, bool):
                value = "[green]Yes[/green]" if value else "[red]No[/red]"
            values.append(str(value))
        
        table.add_row(*values)
    
    prog_console.print(table)


def print_result(
    success: bool,
    message: str,
    details: Dict[str, Any] | None = None,
    console_instance: Console | None = None,
) -> None:
    """Print operation result with appropriate styling.
    
    Args:
        success: Whether the operation succeeded
        message: Result message
        details: Optional dictionary of additional details
        console_instance: Optional custom console instance
        
    Example:
        print_result(True, "Upload complete", {"cid": "bafybeig..."})
        print_result(False, "Upload failed", {"error": "Network timeout"})
    """
    prog_console = console_instance or console
    
    icon = "[green]✓[/green]" if success else "[red]✗[/red]"
    prog_console.print(f"{icon} {message}")
    
    if details:
        for key, value in details.items():
            if value is not None:
                prog_console.print(f"  [dim]{key}:[/dim] {value}")


def print_key_value(
    data: Dict[str, Any],
    title: str | None = None,
    key_style: str = "cyan",
    console_instance: Console | None = None,
) -> None:
    """Print data as key-value pairs.
    
    Args:
        data: Dictionary of key-value pairs
        title: Optional title
        key_style: Style for keys
        console_instance: Optional custom console instance
        
    Example:
        print_key_value({
            "Name": "video.mp4",
            "Size": "100MB",
            "CID": "bafybeig..."
        }, title="File Info")
    """
    prog_console = console_instance or console
    
    if title:
        prog_console.print(f"[bold]{title}[/bold]")
        prog_console.print()
    
    max_key_len = max(len(str(k)) for k in data.keys()) if data else 0
    
    for key, value in data.items():
        # Format value based on type
        if isinstance(value, bool):
            formatted = "[green]Yes[/green]" if value else "[red]No[/red]"
        elif isinstance(value, (int, float)):
            formatted = f"[yellow]{value}[/yellow]"
        elif isinstance(value, datetime):
            formatted = value.strftime("%Y-%m-%d %H:%M:%S")
        else:
            formatted = str(value) if value is not None else "[dim]N/A[/dim]"
        
        padded_key = str(key).ljust(max_key_len)
        prog_console.print(f"  [{key_style}]{padded_key}[/{key_style}] : {formatted}")


def print_tree(
    data: Dict[str, Any],
    title: str | None = None,
    console_instance: Console | None = None,
) -> None:
    """Print hierarchical data as a tree.
    
    Args:
        data: Nested dictionary of data
        title: Optional tree title
        console_instance: Optional custom console instance
        
    Example:
        print_tree({
            "Pipeline": {
                "Ingest": "Complete",
                "Analyze": "Complete",
                "Upload": "Pending"
            }
        }, title="Status")
    """
    prog_console = console_instance or console
    
    tree = Tree(f"[bold]{title}[/bold]" if title else "Root")
    
    def add_nodes(parent: Tree, node_data: Any) -> None:
        """Recursively add nodes to the tree."""
        if isinstance(node_data, dict):
            for key, value in node_data.items():
                if isinstance(value, (dict, list)):
                    branch = parent.add(f"[cyan]{key}[/cyan]")
                    add_nodes(branch, value)
                else:
                    parent.add(f"[cyan]{key}[/cyan]: {value}")
        elif isinstance(node_data, list):
            for i, item in enumerate(node_data):
                if isinstance(item, (dict, list)):
                    branch = parent.add(f"[dim][{i}][/dim]")
                    add_nodes(branch, item)
                else:
                    parent.add(f"[dim][{i}][/dim]: {item}")
        else:
            parent.add(str(node_data))
    
    add_nodes(tree, data)
    prog_console.print(tree)


def print_yaml(
    data: Any,
    title: str | None = None,
    console_instance: Console | None = None,
) -> None:
    """Print data as YAML-formatted output.
    
    Args:
        data: Data to print (must be YAML-serializable)
        title: Optional title
        console_instance: Optional custom console instance
    """
    prog_console = console_instance or console
    
    try:
        import yaml
        yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False)
        
        if title:
            prog_console.print(f"[bold]{title}[/bold]")
        
        prog_console.print(Syntax(yaml_str, "yaml", theme="monokai"))
    except ImportError:
        # Fallback to JSON if PyYAML is not available
        prog_console.print("[yellow]PyYAML not installed, using JSON format[/yellow]")
        print_json(data, console_instance)


def print_list(
    items: List[str],
    title: str | None = None,
    numbered: bool = False,
    console_instance: Console | None = None,
) -> None:
    """Print a list of items.
    
    Args:
        items: List of items to print
        title: Optional title
        numbered: Whether to number the items
        console_instance: Optional custom console instance
        
    Example:
        print_list(["item1", "item2", "item3"], title="Items", numbered=True)
    """
    prog_console = console_instance or console
    
    if title:
        prog_console.print(f"[bold]{title}[/bold]")
    
    for i, item in enumerate(items, 1):
        if numbered:
            prog_console.print(f"  [dim]{i}.[/dim] {item}")
        else:
            prog_console.print(f"  • {item}")


def print_panel(
    content: str,
    title: str | None = None,
    border_style: str = "blue",
    console_instance: Console | None = None,
) -> None:
    """Print content in a bordered panel.
    
    Args:
        content: Content to display
        title: Optional panel title
        border_style: Border color/style
        console_instance: Optional custom console instance
    """
    prog_console = console_instance or console
    panel = Panel(content, title=title, border_style=border_style)
    prog_console.print(panel)


def print_error_details(
    error: Exception,
    verbose: bool = False,
    console_instance: Console | None = None,
) -> None:
    """Print detailed error information.
    
    Args:
        error: The exception to display
        verbose: If True, show full traceback
        console_instance: Optional custom console instance
    """
    prog_console = console_instance or console
    import traceback
    
    if verbose:
        # Show full traceback
        tb = traceback.format_exception(type(error), error, error.__traceback__)
        content = "".join(tb)
        prog_console.print(Panel(
            Syntax(content, "python", theme="monokai"),
            title="[red]Error Details[/red]",
            border_style="red",
        ))
    else:
        # Show just the error message
        prog_console.print(f"[red]Error:[/red] {error}")


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format.
    
    Args:
        size_bytes: Size in bytes
        
    Returns:
        Human-readable size string
        
    Example:
        format_file_size(1024)  # Returns "1.00 KB"
        format_file_size(1024 * 1024)  # Returns "1.00 MB"
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format.
    
    Args:
        seconds: Duration in seconds
        
    Returns:
        Human-readable duration string
        
    Example:
        format_duration(90)  # Returns "1m 30s"
        format_duration(3661)  # Returns "1h 1m 1s"
    """
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


def format_path(path: Path, relative_to: Path | None = None) -> str:
    """Format a path for display.
    
    Args:
        path: Path to format
        relative_to: If provided, show path relative to this
        
    Returns:
        Formatted path string
        
    Example:
        format_path(Path("/home/user/file.txt"))  # Returns "~/file.txt"
    """
    import os
    
    # Expand home directory
    home = Path.home()
    try:
        if path.relative_to(home):
            return "~" + str(path.relative_to(home))
    except ValueError:
        pass
    
    # Try relative path
    if relative_to:
        try:
            return str(path.relative_to(relative_to))
        except ValueError:
            pass
    
    return str(path)
