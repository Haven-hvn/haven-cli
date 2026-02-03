#!/usr/bin/env python3
"""
Script to generate CLI reference documentation from Typer app.

Usage:
    python generate_cli_ref.py > cli-reference.md
"""

import sys
from pathlib import Path

# Add parent directory to path to import haven_cli
sys.path.insert(0, str(Path(__file__).parent.parent))

from typing import Any

import typer
from click import Command, Group

from haven_cli.main import app as main_app


def get_param_type(param: typer.models.OptionInfo | typer.models.ArgumentInfo) -> str:
    """Get the type name for a parameter."""
    if hasattr(param, "annotation"):
        annotation = param.annotation
        if hasattr(annotation, "__name__"):
            return annotation.__name__
        return str(annotation)
    return "str"


def format_default(default: Any) -> str:
    """Format a default value for display."""
    if default is None:
        return ""
    if isinstance(default, str):
        return f'"{default}"'
    if isinstance(default, bool):
        return "true" if default else "false"
    return str(default)


def document_command(
    cmd: Command,
    name: str,
    parent_name: str = "",
    level: int = 2,
) -> str:
    """Generate documentation for a single command."""
    lines = []
    
    full_name = f"{parent_name} {name}" if parent_name else name
    header = "#" * level
    
    lines.append(f"{header} {full_name}")
    lines.append("")
    
    # Help text
    if cmd.help:
        lines.append(cmd.help)
        lines.append("")
    
    # Usage
    lines.append("### Usage")
    lines.append("")
    lines.append(f"```bash")
    lines.append(f"{full_name} [OPTIONS]")
    lines.append("```")
    lines.append("")
    
    # Arguments
    if hasattr(cmd, "params") and cmd.params:
        args = [p for p in cmd.params if getattr(p, "param_type_name", "") == "argument"]
        opts = [p for p in cmd.params if getattr(p, "param_type_name", "") == "option"]
        
        if args:
            lines.append("### Arguments")
            lines.append("")
            lines.append("| Argument | Description |")
            lines.append("|----------|-------------|")
            for arg in args:
                help_text = arg.help or ""
                lines.append(f"| `{arg.name}` | {help_text} |")
            lines.append("")
        
        if opts:
            lines.append("### Options")
            lines.append("")
            lines.append("| Option | Short | Description |")
            lines.append("|--------|-------|-------------|")
            for opt in opts:
                opts_list = getattr(opt, "opts", [])
                long_opt = next((o for o in opts_list if o.startswith("--")), "")
                short_opt = next((o for o in opts_list if o.startswith("-") and not o.startswith("--")), "")
                help_text = opt.help or ""
                lines.append(f"| `{long_opt}` | `{short_opt}` | {help_text} |")
            lines.append("")
    
    # Description/Help continuation
    if cmd.callback and cmd.callback.__doc__:
        doc = cmd.callback.__doc__.strip()
        if doc:
            lines.append("### Description")
            lines.append("")
            lines.append(doc)
            lines.append("")
    
    # Examples section (if help contains examples)
    if cmd.help and "Example" in cmd.help:
        lines.append("### Examples")
        lines.append("")
        lines.append("```bash")
        # Extract examples from help text
        in_example = False
        for line in cmd.help.split("\n"):
            if "Example" in line:
                in_example = True
            elif in_example and line.strip():
                if line.strip().startswith("$"):
                    lines.append(line.strip().lstrip("$ "))
                else:
                    lines.append(line.strip())
        lines.append("```")
        lines.append("")
    
    return "\n".join(lines)


def document_group(
    group: Group,
    name: str,
    parent_name: str = "",
    level: int = 2,
) -> str:
    """Generate documentation for a command group."""
    lines = []
    
    full_name = f"{parent_name} {name}" if parent_name else name
    header = "#" * level
    
    lines.append(f"{header} {full_name}")
    lines.append("")
    
    if group.help:
        lines.append(group.help)
        lines.append("")
    
    # If it's a group with subcommands
    if hasattr(group, "commands") and group.commands:
        lines.append("### Subcommands")
        lines.append("")
        lines.append("| Subcommand | Description |")
        lines.append("|------------|-------------|")
        for subname, subcmd in group.commands.items():
            help_text = subcmd.help or ""
            lines.append(f"| `{subname}` | {help_text} |")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # Document each subcommand
        for subname, subcmd in group.commands.items():
            if isinstance(subcmd, Group):
                lines.append(document_group(subcmd, subname, full_name, level + 1))
            else:
                lines.append(document_command(subcmd, subname, full_name, level + 1))
            lines.append("")
            lines.append("---")
            lines.append("")
    
    return "\n".join(lines)


def generate_cli_reference() -> str:
    """Generate complete CLI reference documentation."""
    lines = []
    
    # Header
    lines.append("# CLI Reference (Auto-Generated)")
    lines.append("")
    lines.append("This document is auto-generated from the CLI source code.")
    lines.append("")
    lines.append("## Global Options")
    lines.append("")
    lines.append("These options are available for all commands:")
    lines.append("")
    lines.append("| Option | Short | Description |")
    lines.append("|--------|-------|-------------|")
    lines.append("| `--version` | `-v` | Show version and exit |")
    lines.append("| `--verbose` | `-V` | Enable verbose output (INFO level logging) |")
    lines.append("| `--debug` | | Enable debug mode (DEBUG level logging with full tracebacks) |")
    lines.append("| `--json` | | Output in JSON format where applicable |")
    lines.append("| `--quiet` | `-q` | Suppress non-error output |")
    lines.append("| `--log-file` | | Log to file (logs DEBUG level regardless of console settings) |")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # Document main app commands
    if hasattr(main_app, "registered_commands"):
        for cmd_info in main_app.registered_commands:
            name = cmd_info.name
            cmd = cmd_info.command
            if isinstance(cmd, Group):
                lines.append(document_group(cmd, f"haven {name}"))
            else:
                lines.append(document_command(cmd, f"haven {name}"))
            lines.append("")
            lines.append("---")
            lines.append("")
    
    # Document registered groups
    if hasattr(main_app, "registered_groups"):
        for group_info in main_app.registered_groups:
            name = group_info.name
            group = group_info.typer_instance
            # Access the click group
            if hasattr(group, "registered_groups"):
                for g in group.registered_groups:
                    lines.append(document_group(g, f"haven {name}"))
                lines.append("")
                lines.append("---")
                lines.append("")
    
    return "\n".join(lines)


def main():
    """Main entry point."""
    print(generate_cli_reference())


if __name__ == "__main__":
    main()
