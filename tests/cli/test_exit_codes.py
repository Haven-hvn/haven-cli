"""Tests for exit codes module."""

import pytest

from haven_cli.cli.exit_codes import ExitCode


class TestExitCode:
    """Test exit code constants."""
    
    def test_success_code(self) -> None:
        """Test success exit code."""
        assert ExitCode.SUCCESS == 0
    
    def test_general_error_code(self) -> None:
        """Test general error exit code."""
        assert ExitCode.GENERAL_ERROR == 1
    
    def test_configuration_error_code(self) -> None:
        """Test configuration error exit code."""
        assert ExitCode.CONFIGURATION_ERROR == 2
    
    def test_plugin_error_code(self) -> None:
        """Test plugin error exit code."""
        assert ExitCode.PLUGIN_ERROR == 3
    
    def test_pipeline_error_code(self) -> None:
        """Test pipeline error exit code."""
        assert ExitCode.PIPELINE_ERROR == 4
    
    def test_network_error_code(self) -> None:
        """Test network error exit code."""
        assert ExitCode.NETWORK_ERROR == 5
    
    def test_storage_error_code(self) -> None:
        """Test storage error exit code."""
        assert ExitCode.STORAGE_ERROR == 6
    
    def test_invalid_argument_code(self) -> None:
        """Test invalid argument exit code."""
        assert ExitCode.INVALID_ARGUMENT == 7
    
    def test_not_found_code(self) -> None:
        """Test not found exit code."""
        assert ExitCode.NOT_FOUND == 8
    
    def test_permission_denied_code(self) -> None:
        """Test permission denied exit code."""
        assert ExitCode.PERMISSION_DENIED == 9
    
    def test_cancelled_code(self) -> None:
        """Test cancelled exit code."""
        assert ExitCode.CANCELLED == 130


class TestExitCodeGetName:
    """Test get_name method."""
    
    def test_get_name_success(self) -> None:
        """Test getting name for success code."""
        assert ExitCode.get_name(ExitCode.SUCCESS) == "SUCCESS"
    
    def test_get_name_general_error(self) -> None:
        """Test getting name for general error code."""
        assert ExitCode.get_name(ExitCode.GENERAL_ERROR) == "GENERAL_ERROR"
    
    def test_get_name_configuration_error(self) -> None:
        """Test getting name for configuration error code."""
        assert ExitCode.get_name(ExitCode.CONFIGURATION_ERROR) == "CONFIGURATION_ERROR"
    
    def test_get_name_unknown(self) -> None:
        """Test getting name for unknown code."""
        assert ExitCode.get_name(999) == "UNKNOWN(999)"


class TestExitCodeGetDescription:
    """Test get_description method."""
    
    def test_get_description_success(self) -> None:
        """Test getting description for success code."""
        desc = ExitCode.get_description(ExitCode.SUCCESS)
        assert "success" in desc.lower()
    
    def test_get_description_general_error(self) -> None:
        """Test getting description for general error code."""
        desc = ExitCode.get_description(ExitCode.GENERAL_ERROR)
        assert "error" in desc.lower()
    
    def test_get_description_configuration_error(self) -> None:
        """Test getting description for configuration error code."""
        desc = ExitCode.get_description(ExitCode.CONFIGURATION_ERROR)
        assert "config" in desc.lower()
    
    def test_get_description_unknown(self) -> None:
        """Test getting description for unknown code."""
        desc = ExitCode.get_description(999)
        assert "unknown" in desc.lower()
        assert "999" in desc


class TestExitCodeAllCodes:
    """Test that all codes have names and descriptions."""
    
    def test_all_codes_have_names(self) -> None:
        """Test that all defined codes have names."""
        codes = [
            ExitCode.SUCCESS,
            ExitCode.GENERAL_ERROR,
            ExitCode.CONFIGURATION_ERROR,
            ExitCode.PLUGIN_ERROR,
            ExitCode.PIPELINE_ERROR,
            ExitCode.NETWORK_ERROR,
            ExitCode.STORAGE_ERROR,
            ExitCode.INVALID_ARGUMENT,
            ExitCode.NOT_FOUND,
            ExitCode.PERMISSION_DENIED,
            ExitCode.CANCELLED,
        ]
        
        for code in codes:
            name = ExitCode.get_name(code)
            assert name != f"UNKNOWN({code})"
            assert name is not None
            assert len(name) > 0
    
    def test_all_codes_have_descriptions(self) -> None:
        """Test that all defined codes have descriptions."""
        codes = [
            ExitCode.SUCCESS,
            ExitCode.GENERAL_ERROR,
            ExitCode.CONFIGURATION_ERROR,
            ExitCode.PLUGIN_ERROR,
            ExitCode.PIPELINE_ERROR,
            ExitCode.NETWORK_ERROR,
            ExitCode.STORAGE_ERROR,
            ExitCode.INVALID_ARGUMENT,
            ExitCode.NOT_FOUND,
            ExitCode.PERMISSION_DENIED,
            ExitCode.CANCELLED,
        ]
        
        for code in codes:
            desc = ExitCode.get_description(code)
            assert "unknown" not in desc.lower() or code == 999
            assert desc is not None
            assert len(desc) > 0
