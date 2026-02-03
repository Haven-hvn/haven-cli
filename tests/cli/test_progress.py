"""Tests for progress indicator module."""

import pytest
from unittest.mock import Mock, patch, MagicMock

from rich.console import Console

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


class TestSpinner:
    """Test spinner context manager."""
    
    def test_spinner_context_manager(self) -> None:
        """Test spinner as context manager."""
        mock_progress = MagicMock()
        mock_progress.__enter__ = Mock(return_value=mock_progress)
        mock_progress.__exit__ = Mock(return_value=None)
        
        with patch("haven_cli.cli.progress.Progress", return_value=mock_progress):
            with spinner("Loading...") as _:
                pass
        
        mock_progress.__enter__.assert_called_once()
        mock_progress.__exit__.assert_called_once()
    
    def test_spinner_adds_task(self) -> None:
        """Test that spinner adds a task."""
        mock_progress = MagicMock()
        mock_progress.__enter__ = Mock(return_value=mock_progress)
        mock_progress.__exit__ = Mock(return_value=None)
        
        with patch("haven_cli.cli.progress.Progress", return_value=mock_progress):
            with spinner("Loading...") as _:
                pass
        
        mock_progress.add_task.assert_called_once_with(description="Loading...", total=None)


class TestProgressBar:
    """Test progress_bar context manager."""
    
    def test_progress_bar_context_manager(self) -> None:
        """Test progress bar as context manager."""
        mock_progress = MagicMock()
        mock_task = Mock()
        mock_progress.add_task = Mock(return_value=mock_task)
        mock_progress.__enter__ = Mock(return_value=mock_progress)
        mock_progress.__exit__ = Mock(return_value=None)
        mock_progress.update = Mock()
        
        with patch("haven_cli.cli.progress.Progress", return_value=mock_progress):
            with progress_bar(total=100, description="Processing") as advance:
                advance(10)
        
        mock_progress.add_task.assert_called_once_with("Processing", total=100)
        mock_progress.update.assert_called_with(mock_task, advance=10)


class TestMultiStepProgress:
    """Test multi_step_progress context manager."""
    
    def test_multi_step_progress(self) -> None:
        """Test multi-step progress tracking."""
        mock_progress = MagicMock()
        mock_task = Mock()
        mock_progress.add_task = Mock(return_value=mock_task)
        mock_progress.__enter__ = Mock(return_value=mock_progress)
        mock_progress.__exit__ = Mock(return_value=None)
        mock_progress.update = Mock()
        
        steps = ["Step 1", "Step 2", "Step 3"]
        
        with patch("haven_cli.cli.progress.Progress", return_value=mock_progress):
            with multi_step_progress(steps) as advance:
                advance()  # Advance to next step
        
        # Should update progress
        mock_progress.update.assert_called()


class TestStatusMessage:
    """Test status_message function."""
    
    def test_status_info(self) -> None:
        """Test info status message."""
        mock_console = Mock()
        with patch("haven_cli.cli.progress.console", mock_console):
            status_message("Info message", "info")
        mock_console.print.assert_called_once()
        assert "Info message" in str(mock_console.print.call_args[0][0])
    
    def test_status_success(self) -> None:
        """Test success status message."""
        mock_console = Mock()
        with patch("haven_cli.cli.progress.console", mock_console):
            status_message("Success message", "success")
        mock_console.print.assert_called_once()
        call_arg = mock_console.print.call_args[0][0]
        assert "✓" in str(call_arg)
    
    def test_status_warning(self) -> None:
        """Test warning status message."""
        mock_console = Mock()
        with patch("haven_cli.cli.progress.console", mock_console):
            status_message("Warning message", "warning")
        call_arg = mock_console.print.call_args[0][0]
        assert "⚠" in str(call_arg)
    
    def test_status_error(self) -> None:
        """Test error status message."""
        mock_console = Mock()
        with patch("haven_cli.cli.progress.console", mock_console):
            status_message("Error message", "error")
        call_arg = mock_console.print.call_args[0][0]
        assert "✗" in str(call_arg)
    
    def test_status_unknown(self) -> None:
        """Test unknown status defaults to info."""
        mock_console = Mock()
        with patch("haven_cli.cli.progress.console", mock_console):
            status_message("Message", "unknown")
        mock_console.print.assert_called_once()


class TestStepComplete:
    """Test step_complete function."""
    
    def test_step_complete_without_details(self) -> None:
        """Test step complete without details."""
        mock_console = Mock()
        with patch("haven_cli.cli.progress.console", mock_console):
            step_complete("Processing")
        call_arg = mock_console.print.call_args[0][0]
        assert "✓" in str(call_arg)
        assert "Processing" in str(call_arg)
    
    def test_step_complete_with_details(self) -> None:
        """Test step complete with details."""
        mock_console = Mock()
        with patch("haven_cli.cli.progress.console", mock_console):
            step_complete("Processing", "50%")
        call_arg = mock_console.print.call_args[0][0]
        assert "✓" in str(call_arg)
        assert "Processing" in str(call_arg)
        assert "50%" in str(call_arg)


class TestStepFailed:
    """Test step_failed function."""
    
    def test_step_failed_without_reason(self) -> None:
        """Test step failed without reason."""
        mock_console = Mock()
        with patch("haven_cli.cli.progress.console", mock_console):
            step_failed("Processing")
        call_arg = mock_console.print.call_args[0][0]
        assert "✗" in str(call_arg)
        assert "Processing" in str(call_arg)
    
    def test_step_failed_with_reason(self) -> None:
        """Test step failed with reason."""
        mock_console = Mock()
        with patch("haven_cli.cli.progress.console", mock_console):
            step_failed("Processing", "Network error")
        call_arg = mock_console.print.call_args[0][0]
        assert "✗" in str(call_arg)
        assert "Processing" in str(call_arg)
        assert "Network error" in str(call_arg)


class TestStepSkipped:
    """Test step_skipped function."""
    
    def test_step_skipped_without_reason(self) -> None:
        """Test step skipped without reason."""
        mock_console = Mock()
        with patch("haven_cli.cli.progress.console", mock_console):
            step_skipped("Processing")
        call_arg = mock_console.print.call_args[0][0]
        assert "⊘" in str(call_arg)
        assert "Processing" in str(call_arg)
    
    def test_step_skipped_with_reason(self) -> None:
        """Test step skipped with reason."""
        mock_console = Mock()
        with patch("haven_cli.cli.progress.console", mock_console):
            step_skipped("Processing", "Already done")
        call_arg = mock_console.print.call_args[0][0]
        assert "⊘" in str(call_arg)
        assert "Processing" in str(call_arg)
        assert "Already done" in str(call_arg)


class TestPipelineProgress:
    """Test PipelineProgress class."""
    
    def test_init(self) -> None:
        """Test initialization."""
        steps = ["Step 1", "Step 2", "Step 3"]
        progress = PipelineProgress(steps)
        assert progress.steps == steps
        assert progress._current_step_index == 0
    
    def test_context_manager(self) -> None:
        """Test as context manager."""
        mock_progress = MagicMock()
        mock_progress.__enter__ = Mock(return_value=mock_progress)
        mock_progress.__exit__ = Mock(return_value=None)
        mock_progress.add_task = Mock(return_value=1)
        
        steps = ["Step 1", "Step 2"]
        
        with patch("haven_cli.cli.progress.Progress", return_value=mock_progress):
            with PipelineProgress(steps) as p:
                assert p._progress is not None
    
    def test_start_step(self) -> None:
        """Test starting a step."""
        mock_progress = MagicMock()
        mock_progress.add_task = Mock(return_value=1)
        mock_progress.update = Mock()
        mock_progress.__enter__ = Mock(return_value=mock_progress)
        mock_progress.__exit__ = Mock(return_value=None)
        
        steps = ["Step 1", "Step 2"]
        
        with patch("haven_cli.cli.progress.Progress", return_value=mock_progress):
            with PipelineProgress(steps) as p:
                p.start_step("Step 1")
                mock_progress.update.assert_called_with(1, description="Step 1...")
    
    def test_complete_step(self) -> None:
        """Test completing a step."""
        mock_progress = MagicMock()
        mock_progress.add_task = Mock(return_value=1)
        mock_progress.update = Mock()
        mock_progress.__enter__ = Mock(return_value=mock_progress)
        mock_progress.__exit__ = Mock(return_value=None)
        
        steps = ["Step 1", "Step 2"]
        
        with patch("haven_cli.cli.progress.Progress", return_value=mock_progress):
            with PipelineProgress(steps) as p:
                p.complete_step()
                mock_progress.update.assert_called_with(1, advance=1, description="Step 2")
    
    def test_update_description(self) -> None:
        """Test updating description."""
        mock_progress = MagicMock()
        mock_progress.add_task = Mock(return_value=1)
        mock_progress.update = Mock()
        mock_progress.__enter__ = Mock(return_value=mock_progress)
        mock_progress.__exit__ = Mock(return_value=None)
        
        steps = ["Step 1"]
        
        with patch("haven_cli.cli.progress.Progress", return_value=mock_progress):
            with PipelineProgress(steps) as p:
                p.update_description("Custom description")
                mock_progress.update.assert_called_with(1, description="Custom description")
