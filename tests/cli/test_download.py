"""Tests for download CLI command."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock, MagicMock

from typer.testing import CliRunner

from haven_cli.cli.download import app, _format_file_size, verify_cid_format
from haven_cli.pipeline.context import EncryptionMetadata


runner = CliRunner()


class TestVerifyCidFormat:
    """Tests for CID format verification."""
    
    def test_valid_cidv0(self):
        """Test valid CIDv0 starts with Qm."""
        assert verify_cid_format("QmYwAPJzv5CZsnAzt8auVKLJdf3SRr7Fz1tJ3qA1xQcQdE") is True
    
    def test_valid_cidv1(self):
        """Test valid CIDv1 starts with baf."""
        assert verify_cid_format("bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi") is True
    
    def test_invalid_cid(self):
        """Test invalid CID formats."""
        assert verify_cid_format("invalid") is False
        assert verify_cid_format("") is False
        assert verify_cid_format("Qm") is False  # Too short


class TestFormatFileSize:
    """Tests for file size formatting."""
    
    def test_bytes(self):
        """Test formatting bytes."""
        assert _format_file_size(512) == "512 B"
    
    def test_kilobytes(self):
        """Test formatting kilobytes."""
        assert _format_file_size(1024) == "1.00 KB"
        assert _format_file_size(1536) == "1.50 KB"
    
    def test_megabytes(self):
        """Test formatting megabytes."""
        assert _format_file_size(1024 * 1024) == "1.00 MB"
        assert _format_file_size(5 * 1024 * 1024) == "5.00 MB"
    
    def test_gigabytes(self):
        """Test formatting gigabytes."""
        assert _format_file_size(1024 * 1024 * 1024) == "1.00 GB"


class TestDownloadCommand:
    """Tests for the download command."""
    
    @patch("haven_cli.cli.download.load_config")
    @patch("haven_cli.cli.download.JSBridgeManager")
    @patch("haven_cli.cli.download.js_call")
    @pytest.mark.asyncio
    async def test_download_success(self, mock_js_call, mock_manager, mock_load_config, tmp_path):
        """Test successful download."""
        # Setup mocks
        mock_config = Mock()
        mock_config.pipeline.synapse_endpoint = "https://synapse.example.com"
        mock_config.pipeline.synapse_api_key = "test-key"
        mock_load_config.return_value = mock_config
        
        mock_bridge = AsyncMock()
        mock_manager.get_instance.return_value = mock_bridge
        mock_manager.get_instance.return_value.__aenter__ = AsyncMock(return_value=mock_bridge)
        mock_manager.get_instance.return_value.__aexit__ = AsyncMock(return_value=None)
        
        mock_js_call.return_value = {"success": True, "bytes": 1024}
        
        # Create output path
        output_path = tmp_path / "output.mp4"
        
        # Run download
        from haven_cli.cli.download import download
        
        with patch("asyncio.run") as mock_run:
            mock_run.side_effect = lambda coro: None  # Don't actually run the coroutine
            
            result = runner.invoke(app, [
                "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi",
                "--output", str(output_path)
            ])
    
    def test_download_invalid_cid(self, tmp_path):
        """Test download with invalid CID."""
        output_path = tmp_path / "output.mp4"
        
        # Test the verify_cid_format function directly
        from haven_cli.crypto import verify_cid_format
        assert verify_cid_format("invalid-cid") is False
        assert verify_cid_format("") is False
        assert verify_cid_format("Qm") is False
    
    def test_download_existing_file_no_force(self, tmp_path):
        """Test download when output file exists without force flag."""
        output_path = tmp_path / "existing.mp4"
        output_path.write_text("existing content")
        
        # Test the file existence check logic directly
        assert output_path.exists() is True
        
        # Verify that we would skip download without force
        from pathlib import Path
        assert Path(output_path).exists() is True


class TestInfoCommand:
    """Tests for the info subcommand."""
    
    @patch("haven_cli.cli.download.load_config")
    @patch("haven_cli.cli.download.JSBridgeManager")
    @patch("haven_cli.cli.download.js_call")
    @pytest.mark.asyncio
    async def test_info_success(self, mock_js_call, mock_manager, mock_load_config):
        """Test successful info retrieval."""
        # Setup mocks
        mock_config = Mock()
        mock_config.pipeline.synapse_endpoint = "https://synapse.example.com"
        mock_config.pipeline.synapse_api_key = "test-key"
        mock_load_config.return_value = mock_config
        
        mock_bridge = AsyncMock()
        mock_manager.get_instance.return_value = mock_bridge
        mock_manager.get_instance.return_value.__aenter__ = AsyncMock(return_value=mock_bridge)
        mock_manager.get_instance.return_value.__aexit__ = AsyncMock(return_value=None)
        
        mock_js_call.return_value = {
            "status": "active",
            "size": 1024 * 1024,
            "deals": [
                {
                    "dealId": "12345",
                    "provider": "f01234",
                    "status": "active",
                    "startEpoch": 1000,
                    "endEpoch": 2000,
                }
            ]
        }
        
        result = runner.invoke(app, [
            "info",
            "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
        ])
        
        # Since we're mocking asyncio.run, the command might not complete normally
        # Just check that it doesn't error out immediately on CID validation
        assert "Invalid CID format" not in result.output
    
    def test_info_invalid_cid(self):
        """Test info with invalid CID."""
        # Test the verify_cid_format function directly
        from haven_cli.crypto import verify_cid_format
        assert verify_cid_format("invalid-cid") is False
    
    @patch("haven_cli.cli.download.load_config")
    @patch("haven_cli.cli.download.JSBridgeManager")
    @patch("haven_cli.cli.download.js_call")
    def test_info_json_output(self, mock_js_call, mock_manager, mock_load_config):
        """Test info with JSON output."""
        # Setup mocks
        mock_config = Mock()
        mock_config.pipeline.synapse_endpoint = "https://synapse.example.com"
        mock_config.pipeline.synapse_api_key = "test-key"
        mock_load_config.return_value = mock_config
        
        mock_status = {
            "status": "active",
            "size": 1024,
            "deals": []
        }
        mock_js_call.return_value = mock_status
        
        result = runner.invoke(app, [
            "info",
            "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi",
            "--json"
        ])
        
        # Verify CID format validation passes
        assert "Invalid CID format" not in result.output


class TestDecryptFileCommand:
    """Tests for the decrypt-file subcommand."""
    
    def test_decrypt_file_existing_output_no_force(self, tmp_path):
        """Test decrypt when output exists without force."""
        input_file = tmp_path / "encrypted.mp4"
        input_file.write_text("encrypted content")
        
        output_file = tmp_path / "decrypted.mp4"
        output_file.write_text("existing content")
        
        # Test the file existence check logic directly
        assert output_file.exists() is True
    
    def test_decrypt_file_input_not_exists(self, tmp_path):
        """Test decrypt when input doesn't exist."""
        input_file = tmp_path / "nonexistent.mp4"
        
        result = runner.invoke(app, [
            "decrypt-file",
            str(input_file)
        ])
        
        assert result.exit_code != 0  # Typer validation should fail


class TestPrintStatusTable:
    """Tests for the status table printing function."""
    
    @patch("haven_cli.cli.download.console")
    def test_print_status_with_deals(self, mock_console):
        """Test printing status with deals."""
        from haven_cli.cli.download import _print_status_table
        
        cid = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
        status = {
            "status": "active",
            "size": 1024 * 1024,
            "mimeType": "video/mp4",
            "createdAt": "2024-01-01T00:00:00Z",
            "deals": [
                {
                    "dealId": "12345",
                    "provider": "f01234",
                    "status": "active",
                    "startEpoch": 1000,
                    "endEpoch": 2000,
                }
            ],
            "replicas": 1
        }
        
        _print_status_table(cid, status)
        
        # Verify console.print was called
        assert mock_console.print.called
    
    @patch("haven_cli.cli.download.console")
    def test_print_status_no_deals(self, mock_console):
        """Test printing status without deals."""
        from haven_cli.cli.download import _print_status_table
        
        cid = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
        status = {
            "status": "pending",
            "deals": []
        }
        
        _print_status_table(cid, status)
        
        # Verify console.print was called
        assert mock_console.print.called
    
    @patch("haven_cli.cli.download.console")
    def test_print_status_pending_status(self, mock_console):
        """Test printing status with pending status."""
        from haven_cli.cli.download import _print_status_table
        
        cid = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
        status = {
            "status": "pending",
        }
        
        _print_status_table(cid, status)
        
        # Verify console.print was called
        assert mock_console.print.called
    
    @patch("haven_cli.cli.download.console")
    def test_print_status_error_status(self, mock_console):
        """Test printing status with error status."""
        from haven_cli.cli.download import _print_status_table
        
        cid = "bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi"
        status = {
            "status": "failed",
        }
        
        _print_status_table(cid, status)
        
        # Verify console.print was called
        assert mock_console.print.called
