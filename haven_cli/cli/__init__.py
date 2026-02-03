"""CLI command modules for Haven.

This package contains all CLI command implementations and supporting utilities
for improved error handling, progress display, and user interaction.
"""

from haven_cli.cli import config, download, jobs, plugins, run, upload

# Export new UX modules for easy access
from haven_cli.cli.exit_codes import ExitCode
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
)
from haven_cli.cli.progress import (
    spinner,
    progress_bar,
    multi_step_progress,
    status_message,
    step_complete,
    step_failed,
    step_skipped,
    PipelineProgress,
)
from haven_cli.cli.prompts import (
    confirm_dangerous,
    confirm_with_input,
    confirm_destructive_operation,
    select_from_list,
    prompt_for_input,
    confirm_overwrite,
    prompt_with_help,
    AbortOperation,
    abort_if_not_confirmed,
)
from haven_cli.cli.output import (
    OutputFormatter,
    print_json,
    print_table,
    print_result,
    print_key_value,
    print_tree,
    print_yaml,
    print_list,
    print_panel,
    print_error_details,
    format_file_size,
    format_duration,
    format_path,
)

__all__ = [
    # Command modules
    "config",
    "download",
    "jobs",
    "plugins",
    "run",
    "upload",
    # Exit codes
    "ExitCode",
    # Error handling
    "HavenError",
    "ConfigurationError",
    "PluginError",
    "PipelineError",
    "NetworkError",
    "StorageError",
    "ValidationError",
    "NotFoundError",
    "PermissionError",
    "handle_errors",
    "handle_errors_async",
    # Progress
    "spinner",
    "progress_bar",
    "multi_step_progress",
    "status_message",
    "step_complete",
    "step_failed",
    "step_skipped",
    "PipelineProgress",
    # Prompts
    "confirm_dangerous",
    "confirm_with_input",
    "confirm_destructive_operation",
    "select_from_list",
    "prompt_for_input",
    "confirm_overwrite",
    "prompt_with_help",
    "AbortOperation",
    "abort_if_not_confirmed",
    # Output
    "OutputFormatter",
    "print_json",
    "print_table",
    "print_result",
    "print_key_value",
    "print_tree",
    "print_yaml",
    "print_list",
    "print_panel",
    "print_error_details",
    "format_file_size",
    "format_duration",
    "format_path",
]
