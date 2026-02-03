"""Tests for prompts module."""

import pytest
from unittest.mock import Mock, patch, MagicMock
import typer

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


class TestConfirmDangerous:
    """Test confirm_dangerous function."""
    
    def test_confirm_dangerous_yes(self) -> None:
        """Test confirming dangerous operation (yes)."""
        with patch("haven_cli.cli.prompts.typer.confirm", return_value=True):
            result = confirm_dangerous("Delete everything?")
        assert result is True
    
    def test_confirm_dangerous_no(self) -> None:
        """Test not confirming dangerous operation (no)."""
        with patch("haven_cli.cli.prompts.typer.confirm", return_value=False):
            result = confirm_dangerous("Delete everything?")
        assert result is False
    
    def test_confirm_dangerous_default_true(self) -> None:
        """Test dangerous confirmation with default True."""
        with patch("haven_cli.cli.prompts.typer.confirm", return_value=True) as mock_confirm:
            confirm_dangerous("Delete everything?", default=True)
        mock_confirm.assert_called_once()
        assert mock_confirm.call_args[1]["default"] is True
    
    def test_confirm_dangerous_without_warning(self) -> None:
        """Test dangerous confirmation without warning icon."""
        with patch("haven_cli.cli.prompts.typer.confirm", return_value=True):
            result = confirm_dangerous("Delete everything?", show_warning=False)
        assert result is True


class TestConfirmWithInput:
    """Test confirm_with_input function."""
    
    def test_confirm_with_input_correct(self) -> None:
        """Test confirmation with correct input."""
        with patch("haven_cli.cli.prompts.typer.prompt", return_value="DELETE"):
            result = confirm_with_input("Type DELETE to confirm", expected="DELETE")
        assert result is True
    
    def test_confirm_with_input_incorrect(self) -> None:
        """Test confirmation with incorrect input."""
        with patch("haven_cli.cli.prompts.typer.prompt", return_value="NO"):
            result = confirm_with_input("Type DELETE to confirm", expected="DELETE")
        assert result is False
    
    def test_confirm_with_input_case_insensitive(self) -> None:
        """Test confirmation with case insensitive comparison."""
        with patch("haven_cli.cli.prompts.typer.prompt", return_value="delete"):
            result = confirm_with_input(
                "Type DELETE to confirm",
                expected="DELETE",
                case_sensitive=False
            )
        assert result is True


class TestConfirmDestructiveOperation:
    """Test confirm_destructive_operation function."""
    
    def test_confirm_destructive_yes(self) -> None:
        """Test confirming destructive operation."""
        with patch("haven_cli.cli.prompts.typer.confirm", return_value=True):
            with patch("haven_cli.cli.prompts.console"):
                result = confirm_destructive_operation(
                    operation="Delete",
                    target="all data",
                    consequences=["Data will be lost"]
                )
        assert result is True
    
    def test_confirm_destructive_no(self) -> None:
        """Test not confirming destructive operation."""
        with patch("haven_cli.cli.prompts.typer.confirm", return_value=False):
            with patch("haven_cli.cli.prompts.console"):
                result = confirm_destructive_operation(
                    operation="Delete",
                    target="all data"
                )
        assert result is False
    
    def test_confirm_destructive_shows_panel(self) -> None:
        """Test that destructive confirmation shows a panel."""
        with patch("haven_cli.cli.prompts.typer.confirm", return_value=True):
            mock_console = Mock()
            with patch("haven_cli.cli.prompts.console", mock_console):
                confirm_destructive_operation(
                    operation="Delete",
                    target="all data",
                    consequences=["Data will be lost"]
                )
        mock_console.print.assert_called_once()


class TestSelectFromList:
    """Test select_from_list function."""
    
    def test_select_first_option(self) -> None:
        """Test selecting first option."""
        options = [("Option 1", "value1"), ("Option 2", "value2")]
        with patch("haven_cli.cli.prompts.typer.prompt", return_value="1"):
            with patch("haven_cli.cli.prompts.console"):
                result = select_from_list("Choose:", options)
        assert result == "value1"
    
    def test_select_second_option(self) -> None:
        """Test selecting second option."""
        options = [("Option 1", "value1"), ("Option 2", "value2")]
        with patch("haven_cli.cli.prompts.typer.prompt", return_value="2"):
            with patch("haven_cli.cli.prompts.console"):
                result = select_from_list("Choose:", options)
        assert result == "value2"
    
    def test_select_quit(self) -> None:
        """Test quitting selection."""
        options = [("Option 1", "value1")]
        with patch("haven_cli.cli.prompts.typer.prompt", return_value="q"):
            with patch("haven_cli.cli.prompts.console"):
                with pytest.raises(typer.Exit):
                    select_from_list("Choose:", options)
    
    def test_select_invalid_then_valid(self) -> None:
        """Test selecting invalid then valid option."""
        options = [("Option 1", "value1"), ("Option 2", "value2")]
        with patch("haven_cli.cli.prompts.typer.prompt", side_effect=["5", "1"]):
            with patch("haven_cli.cli.prompts.console"):
                result = select_from_list("Choose:", options)
        assert result == "value1"


class TestPromptForInput:
    """Test prompt_for_input function."""
    
    def test_prompt_basic(self) -> None:
        """Test basic prompt."""
        with patch("haven_cli.cli.prompts.typer.prompt", return_value="input"):
            result = prompt_for_input("Enter value:")
        assert result == "input"
    
    def test_prompt_with_default(self) -> None:
        """Test prompt with default value."""
        with patch("haven_cli.cli.prompts.typer.prompt", return_value="default") as mock_prompt:
            prompt_for_input("Enter value:", default="default")
        mock_prompt.assert_called_once()
    
    def test_prompt_with_validation(self) -> None:
        """Test prompt with validation."""
        def validate(x):
            return "Too short" if len(x) < 5 else None
        
        with patch("haven_cli.cli.prompts.typer.prompt", side_effect=["ab", "valid"]):
            with patch("haven_cli.cli.prompts.console"):
                result = prompt_for_input("Enter value:", validate=validate)
        assert result == "valid"
    
    def test_prompt_hide_input(self) -> None:
        """Test prompt with hidden input."""
        with patch("haven_cli.cli.prompts.typer.prompt", return_value="secret") as mock_prompt:
            prompt_for_input("Enter password:", hide_input=True)
        mock_prompt.assert_called_once()
        assert mock_prompt.call_args[1]["hide_input"] is True


class TestConfirmOverwrite:
    """Test confirm_overwrite function."""
    
    def test_confirm_overwrite_force(self) -> None:
        """Test overwrite with force flag."""
        result = confirm_overwrite("/path/to/file", force=True)
        assert result is True
    
    def test_confirm_overwrite_yes(self) -> None:
        """Test confirming overwrite."""
        with patch("haven_cli.cli.prompts.typer.confirm", return_value=True):
            result = confirm_overwrite("/path/to/file")
        assert result is True
    
    def test_confirm_overwrite_no(self) -> None:
        """Test not confirming overwrite."""
        with patch("haven_cli.cli.prompts.typer.confirm", return_value=False):
            result = confirm_overwrite("/path/to/file")
        assert result is False


class TestPromptWithHelp:
    """Test prompt_with_help function."""
    
    def test_prompt_with_help_text(self) -> None:
        """Test prompt with help text."""
        mock_console = Mock()
        with patch("haven_cli.cli.prompts.console", mock_console):
            with patch("haven_cli.cli.prompts.typer.prompt", return_value="input"):
                result = prompt_with_help("Enter value:", "This is help text")
        assert result == "input"
        mock_console.print.assert_called_once()


class TestAbortOperation:
    """Test AbortOperation exception."""
    
    def test_abort_operation_default_message(self) -> None:
        """Test AbortOperation with default message."""
        exc = AbortOperation()
        assert "cancelled" in exc.message.lower() or "abort" in exc.message.lower()
    
    def test_abort_operation_custom_message(self) -> None:
        """Test AbortOperation with custom message."""
        exc = AbortOperation("Custom abort message")
        assert exc.message == "Custom abort message"


class TestAbortIfNotConfirmed:
    """Test abort_if_not_confirmed function."""
    
    def test_abort_if_not_confirmed_yes(self) -> None:
        """Test not aborting when confirmed."""
        with patch("haven_cli.cli.prompts.typer.confirm", return_value=True):
            # Should not raise
            abort_if_not_confirmed("Proceed?")
    
    def test_abort_if_not_confirmed_no(self) -> None:
        """Test aborting when not confirmed."""
        with patch("haven_cli.cli.prompts.typer.confirm", return_value=False):
            with pytest.raises(AbortOperation):
                abort_if_not_confirmed("Proceed?")
