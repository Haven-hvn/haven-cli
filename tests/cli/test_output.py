"""Tests for output formatting module."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, MagicMock

import pytest
from rich.console import Console
from rich.table import Table

from haven_cli.cli.output import (
    OutputFormatter,
    print_json,
    print_table,
    print_result,
    print_key_value,
    print_tree,
    print_list,
    print_panel,
    format_file_size,
    format_duration,
    format_path,
)


class TestOutputFormatter:
    """Test OutputFormatter class."""
    
    def test_init_default(self) -> None:
        """Test default initialization."""
        formatter = OutputFormatter()
        assert formatter.json_mode is False
        assert formatter.console is not None
    
    def test_init_json_mode(self) -> None:
        """Test initialization with JSON mode."""
        formatter = OutputFormatter(json_mode=True)
        assert formatter.json_mode is True
    
    def test_init_custom_console(self) -> None:
        """Test initialization with custom console."""
        custom_console = Console()
        formatter = OutputFormatter(console_instance=custom_console)
        assert formatter.console is custom_console


class TestPrintJson:
    """Test print_json function."""
    
    def test_print_simple_dict(self, capsys) -> None:
        """Test printing simple dictionary as JSON."""
        mock_console = Mock()
        data = {"key": "value", "number": 42}
        print_json(data, console_instance=mock_console)
        # Verify console.print was called
        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args[0][0]
        # The call should contain a Rich JSON object
        assert "JSON" in type(call_args).__name__ or "json" in type(call_args).__module__


class TestPrintTable:
    """Test print_table function."""
    
    def test_print_basic_table(self) -> None:
        """Test printing basic table."""
        mock_console = Mock()
        data = [
            {"name": "item1", "value": 10},
            {"name": "item2", "value": 20},
        ]
        print_table(data, ["name", "value"], console_instance=mock_console)
        mock_console.print.assert_called_once()
        # Verify a Table was printed
        call_arg = mock_console.print.call_args[0][0]
        assert isinstance(call_arg, Table)
    
    def test_print_table_with_title(self) -> None:
        """Test printing table with title."""
        mock_console = Mock()
        data = [{"name": "item1"}]
        print_table(data, ["name"], title="Test Table", console_instance=mock_console)
        call_arg = mock_console.print.call_args[0][0]
        assert isinstance(call_arg, Table)
        assert call_arg.title == "Test Table"


class TestPrintResult:
    """Test print_result function."""
    
    def test_print_success(self) -> None:
        """Test printing success result."""
        mock_console = Mock()
        print_result(True, "Operation complete", console_instance=mock_console)
        mock_console.print.assert_called()
        # First call should contain the success message
        first_call = mock_console.print.call_args_list[0][0][0]
        assert "✓" in str(first_call)
        assert "Operation complete" in str(first_call)
    
    def test_print_failure(self) -> None:
        """Test printing failure result."""
        mock_console = Mock()
        print_result(False, "Operation failed", console_instance=mock_console)
        mock_console.print.assert_called()
        first_call = mock_console.print.call_args_list[0][0][0]
        assert "✗" in str(first_call)
        assert "Operation failed" in str(first_call)
    
    def test_print_with_details(self) -> None:
        """Test printing result with details."""
        mock_console = Mock()
        details = {"key": "value", "count": 42}
        print_result(True, "Complete", details=details, console_instance=mock_console)
        # Should print multiple lines
        assert mock_console.print.call_count >= 3


class TestPrintKeyValue:
    """Test print_key_value function."""
    
    def test_print_basic(self) -> None:
        """Test basic key-value printing."""
        mock_console = Mock()
        data = {"name": "test", "value": 42}
        print_key_value(data, console_instance=mock_console)
        # Should print title and key-value pairs
        assert mock_console.print.call_count >= 2
    
    def test_print_with_title(self) -> None:
        """Test key-value printing with title."""
        mock_console = Mock()
        data = {"name": "test"}
        print_key_value(data, title="Info", console_instance=mock_console)
        # First call should be the title
        first_call = mock_console.print.call_args_list[0][0][0]
        assert "Info" in str(first_call)
    
    def test_print_boolean_values(self) -> None:
        """Test key-value printing with boolean values."""
        mock_console = Mock()
        data = {"enabled": True, "disabled": False}
        print_key_value(data, console_instance=mock_console)
        # Check that boolean values are formatted
        calls = " ".join(str(call[0][0]) for call in mock_console.print.call_args_list)
        # Rich will format True/False with color codes
        assert "enabled" in calls
        assert "disabled" in calls


class TestPrintTree:
    """Test print_tree function."""
    
    def test_print_simple_tree(self) -> None:
        """Test printing simple tree."""
        mock_console = Mock()
        data = {"root": {"child": "value"}}
        print_tree(data, console_instance=mock_console)
        mock_console.print.assert_called_once()
    
    def test_print_with_title(self) -> None:
        """Test printing tree with title."""
        mock_console = Mock()
        data = {"key": "value"}
        print_tree(data, title="Tree", console_instance=mock_console)
        call_arg = mock_console.print.call_args[0][0]
        # The tree should have the title
        assert "Tree" in str(call_arg)


class TestPrintList:
    """Test print_list function."""
    
    def test_print_basic_list(self) -> None:
        """Test printing basic list."""
        mock_console = Mock()
        items = ["item1", "item2", "item3"]
        print_list(items, console_instance=mock_console)
        # Should print each item
        assert mock_console.print.call_count == len(items)
    
    def test_print_numbered_list(self) -> None:
        """Test printing numbered list."""
        mock_console = Mock()
        items = ["item1", "item2"]
        print_list(items, numbered=True, console_instance=mock_console)
        # Check that numbers are in the output
        calls = " ".join(str(call[0][0]) for call in mock_console.print.call_args_list)
        assert "1" in calls or "2" in calls


class TestPrintPanel:
    """Test print_panel function."""
    
    def test_print_basic_panel(self) -> None:
        """Test printing basic panel."""
        mock_console = Mock()
        print_panel("Content", console_instance=mock_console)
        mock_console.print.assert_called_once()
    
    def test_print_panel_with_title(self) -> None:
        """Test printing panel with title."""
        mock_console = Mock()
        print_panel("Content", title="Panel Title", console_instance=mock_console)
        call_arg = mock_console.print.call_args[0][0]
        # Verify it's a Panel object
        assert "Panel" in type(call_arg).__name__


class TestFormatFileSize:
    """Test format_file_size function."""
    
    def test_format_bytes(self) -> None:
        """Test formatting bytes."""
        assert format_file_size(500) == "500 B"
    
    def test_format_kilobytes(self) -> None:
        """Test formatting kilobytes."""
        result = format_file_size(1024)
        assert "KB" in result
        assert "1.00" in result
    
    def test_format_megabytes(self) -> None:
        """Test formatting megabytes."""
        result = format_file_size(1024 * 1024)
        assert "MB" in result
        assert "1.00" in result
    
    def test_format_gigabytes(self) -> None:
        """Test formatting gigabytes."""
        result = format_file_size(1024 * 1024 * 1024)
        assert "GB" in result
        assert "1.00" in result


class TestFormatDuration:
    """Test format_duration function."""
    
    def test_format_seconds(self) -> None:
        """Test formatting seconds."""
        result = format_duration(45.5)
        assert "s" in result
        assert "45.5" in result
    
    def test_format_minutes(self) -> None:
        """Test formatting minutes."""
        result = format_duration(90)
        assert "m" in result
        assert "s" in result
    
    def test_format_hours(self) -> None:
        """Test formatting hours."""
        result = format_duration(3661)
        assert "h" in result
        assert "m" in result


class TestFormatPath:
    """Test format_path function."""
    
    def test_format_home_path(self) -> None:
        """Test formatting path in home directory."""
        home = Path.home()
        test_path = home / "documents" / "file.txt"
        result = format_path(test_path)
        assert result.startswith("~")
    
    def test_format_absolute_path(self) -> None:
        """Test formatting absolute path."""
        test_path = Path("/usr/local/bin/haven")
        result = format_path(test_path)
        # May or may not start with ~ depending on home
        assert isinstance(result, str)
    
    def test_format_relative_path(self) -> None:
        """Test formatting path relative to another."""
        base = Path("/home/user")
        test_path = Path("/home/user/documents/file.txt")
        result = format_path(test_path, relative_to=base)
        assert "documents" in result
