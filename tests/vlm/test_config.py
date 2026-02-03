"""Tests for VLM configuration."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from haven_cli.vlm.config import (
    VLMConfig,
    VLMEngineConfig,
    VLMProcessingConfig,
    VLMMultiplexerConfig,
    VLMMultiplexerEndpoint,
    create_analysis_config,
    get_engine_config,
    get_processing_params,
    load_vlm_config,
    validate_vlm_config,
    save_multiplexer_config,
    load_multiplexer_config,
    get_example_multiplexer_config,
    _infer_model_type,
    _apply_env_overrides,
)


class TestVLMEngineConfig:
    """Tests for VLMEngineConfig dataclass."""
    
    def test_default_values(self):
        """Test default configuration values."""
        config = VLMEngineConfig()
        
        assert config.model_type == "openai"
        assert config.model_name == "gpt-4-vision-preview"
        assert config.api_key is None
        assert config.base_url is None
        assert config.timeout == 120.0
        assert config.max_tokens == 4096
    
    def test_custom_values(self):
        """Test custom configuration values."""
        config = VLMEngineConfig(
            model_type="gemini",
            model_name="gemini-pro-vision",
            api_key="test-key",
            timeout=60.0,
        )
        
        assert config.model_type == "gemini"
        assert config.model_name == "gemini-pro-vision"
        assert config.api_key == "test-key"
        assert config.timeout == 60.0


class TestVLMProcessingConfig:
    """Tests for VLMProcessingConfig dataclass."""
    
    def test_default_values(self):
        """Test default processing configuration."""
        config = VLMProcessingConfig()
        
        assert config.enabled is True
        assert config.frame_count == 20
        assert config.frame_interval == 2.0
        assert config.threshold == 0.5
        assert config.return_timestamps is True
        assert config.return_confidence is True
        assert config.save_to_file is True


class TestVLMMultiplexerEndpoint:
    """Tests for VLMMultiplexerEndpoint dataclass."""
    
    def test_default_values(self):
        """Test default endpoint configuration."""
        config = VLMMultiplexerEndpoint(
            base_url="http://localhost:8000/v1",
            name="default",
        )
        
        assert config.base_url == "http://localhost:8000/v1"
        assert config.name == "default"
        assert config.weight == 1
        assert config.max_concurrent == 5
        assert config.api_key is None


class TestInferModelType:
    """Tests for model type inference."""
    
    def test_openai_models(self):
        """Test inferring OpenAI models."""
        assert _infer_model_type("gpt-4-vision-preview") == "openai"
        assert _infer_model_type("gpt-4o") == "openai"
        assert _infer_model_type("openai-gpt-4") == "openai"
    
    def test_gemini_models(self):
        """Test inferring Gemini models."""
        assert _infer_model_type("gemini-pro-vision") == "gemini"
        assert _infer_model_type("gemini-1.5-flash") == "gemini"
    
    def test_claude_models(self):
        """Test inferring Claude models."""
        assert _infer_model_type("claude-3-opus") == "anthropic"
    
    def test_local_models(self):
        """Test inferring local models."""
        assert _infer_model_type("llava-v1.5") == "local"
        assert _infer_model_type("local-llm") == "local"
    
    def test_unknown_models(self):
        """Test default for unknown models."""
        assert _infer_model_type("unknown-model") == "openai"


class TestApplyEnvOverrides:
    """Tests for environment variable overrides."""
    
    def test_openai_api_key_override(self):
        """Test OPENAI_API_KEY environment variable."""
        config = VLMConfig()
        config.engine.model_type = "openai"
        
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-openai-key"}):
            result = _apply_env_overrides(config)
        
        assert result.engine.api_key == "test-openai-key"
    
    def test_google_api_key_override(self):
        """Test GOOGLE_API_KEY environment variable."""
        config = VLMConfig()
        config.engine.model_type = "gemini"
        
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-google-key"}):
            result = _apply_env_overrides(config)
        
        assert result.engine.api_key == "test-google-key"
    
    def test_vlm_api_key_override(self):
        """Test VLM_API_KEY environment variable."""
        config = VLMConfig()
        
        with patch.dict(os.environ, {"VLM_API_KEY": "test-vlm-key"}):
            result = _apply_env_overrides(config)
        
        assert result.engine.api_key == "test-vlm-key"
    
    def test_base_url_override(self):
        """Test VLM_BASE_URL environment variable."""
        config = VLMConfig()
        
        with patch.dict(os.environ, {"VLM_BASE_URL": "http://custom:8000/v1"}):
            result = _apply_env_overrides(config)
        
        assert result.engine.base_url == "http://custom:8000/v1"
    
    def test_frame_count_override(self):
        """Test VLM_FRAME_COUNT environment variable."""
        config = VLMConfig()
        
        with patch.dict(os.environ, {"VLM_FRAME_COUNT": "15"}):
            result = _apply_env_overrides(config)
        
        assert result.processing.frame_count == 15
    
    def test_threshold_override(self):
        """Test VLM_THRESHOLD environment variable."""
        config = VLMConfig()
        
        with patch.dict(os.environ, {"VLM_THRESHOLD": "0.7"}):
            result = _apply_env_overrides(config)
        
        assert result.processing.threshold == 0.7
    
    def test_enabled_override(self):
        """Test VLM_ENABLED environment variable."""
        config = VLMConfig()
        config.processing.enabled = True
        
        with patch.dict(os.environ, {"VLM_ENABLED": "false"}):
            result = _apply_env_overrides(config)
        
        assert result.processing.enabled is False


class TestLoadVlmConfig:
    """Tests for load_vlm_config function."""
    
    def test_load_config(self):
        """Test loading configuration."""
        mock_pipeline_config = MagicMock()
        mock_pipeline_config.vlm_enabled = True
        mock_pipeline_config.vlm_model = "gpt-4-vision-preview"
        mock_pipeline_config.vlm_api_key = "test-key"
        mock_pipeline_config.vlm_timeout = 120.0
        
        mock_haven_config = MagicMock()
        mock_haven_config.pipeline = mock_pipeline_config
        
        with patch("haven_cli.vlm.config.get_config", return_value=mock_haven_config):
            config = load_vlm_config()
        
        assert config.processing.enabled is True
        assert config.engine.model_name == "gpt-4-vision-preview"
        assert config.engine.api_key == "test-key"
    
    def test_load_config_with_gemini(self):
        """Test loading configuration for Gemini model."""
        mock_pipeline_config = MagicMock()
        mock_pipeline_config.vlm_enabled = True
        mock_pipeline_config.vlm_model = "gemini-pro-vision"
        mock_pipeline_config.vlm_api_key = "test-key"
        mock_pipeline_config.vlm_timeout = 120.0
        
        mock_haven_config = MagicMock()
        mock_haven_config.pipeline = mock_pipeline_config
        
        with patch("haven_cli.vlm.config.get_config", return_value=mock_haven_config):
            config = load_vlm_config()
        
        assert config.engine.model_type == "gemini"


class TestGetEngineConfig:
    """Tests for get_engine_config function."""
    
    def test_get_engine_config(self):
        """Test getting engine configuration."""
        config = VLMConfig()
        config.engine.model_name = "custom-model"
        
        result = get_engine_config(config)
        
        assert result.model_name == "custom-model"
    
    def test_get_engine_config_from_global(self):
        """Test getting engine config from global."""
        mock_pipeline_config = MagicMock()
        mock_pipeline_config.vlm_model = "gpt-4o"
        mock_pipeline_config.vlm_api_key = "key"
        mock_pipeline_config.vlm_timeout = 120.0
        
        mock_haven_config = MagicMock()
        mock_haven_config.pipeline = mock_pipeline_config
        
        with patch("haven_cli.vlm.config.get_config", return_value=mock_haven_config):
            result = get_engine_config()
        
        assert result.model_name == "gpt-4o"


class TestGetProcessingParams:
    """Tests for get_processing_params function."""
    
    def test_get_params(self):
        """Test getting processing parameters."""
        config = VLMConfig()
        config.processing.frame_count = 15
        config.processing.threshold = 0.7
        
        result = get_processing_params(config)
        
        assert result["frame_count"] == 15
        assert result["threshold"] == 0.7
        assert "enabled" in result
        assert "return_timestamps" in result


class TestCreateAnalysisConfig:
    """Tests for create_analysis_config function."""
    
    def test_create_config(self):
        """Test creating AnalysisConfig from VLMConfig."""
        config = VLMConfig()
        config.processing.frame_count = 25
        config.processing.threshold = 0.6
        config.engine.max_tokens = 2048
        
        result = create_analysis_config(config)
        
        assert result.frame_count == 25
        assert result.threshold == 0.6
        assert result.max_tokens == 2048


class TestValidateVlmConfig:
    """Tests for validate_vlm_config function."""
    
    def test_valid_config(self):
        """Test validation of valid configuration."""
        config = VLMConfig()
        config.processing.enabled = False  # Disable to avoid API key warning
        
        errors = validate_vlm_config(config)
        
        assert errors == []
    
    def test_missing_api_key_warning(self):
        """Test warning when API key missing."""
        config = VLMConfig()
        config.processing.enabled = True
        config.engine.api_key = None
        config.engine.model_type = "openai"
        
        errors = validate_vlm_config(config)
        
        assert any("API key" in e for e in errors)
    
    def test_invalid_frame_count(self):
        """Test validation of invalid frame count."""
        config = VLMConfig()
        config.processing.frame_count = 0
        
        errors = validate_vlm_config(config)
        
        assert any("frame_count" in e for e in errors)
    
    def test_high_frame_count_warning(self):
        """Test warning for high frame count."""
        config = VLMConfig()
        config.processing.frame_count = 150
        
        errors = validate_vlm_config(config)
        
        assert any("frame_count" in e.lower() for e in errors)
    
    def test_invalid_threshold(self):
        """Test validation of invalid threshold."""
        config = VLMConfig()
        config.processing.threshold = 1.5
        
        errors = validate_vlm_config(config)
        
        assert any("threshold" in e for e in errors)
    
    def test_invalid_timeout(self):
        """Test validation of invalid timeout."""
        config = VLMConfig()
        config.engine.timeout = 0
        
        errors = validate_vlm_config(config)
        
        assert any("timeout" in e for e in errors)
    
    def test_multiplexer_no_endpoints(self):
        """Test validation of multiplexer without endpoints."""
        config = VLMConfig()
        config.multiplexer.enabled = True
        config.multiplexer.endpoints = []
        
        errors = validate_vlm_config(config)
        
        assert any("endpoints" in e for e in errors)


class TestMultiplexerConfig:
    """Tests for multiplexer configuration functions."""
    
    def test_save_and_load_multiplexer_config(self, tmp_path):
        """Test saving and loading multiplexer configuration."""
        endpoints = [
            {
                "base_url": "http://server1:8000/v1",
                "name": "server1",
                "weight": 2,
                "max_concurrent": 5,
            },
            {
                "base_url": "http://server2:8000/v1",
                "name": "server2",
                "weight": 1,
                "max_concurrent": 3,
            },
        ]
        
        config_path = tmp_path / "multiplexer.json"
        
        with patch("haven_cli.vlm.config.get_config") as mock_get_config:
            mock_config = MagicMock()
            mock_config.data_dir = tmp_path
            mock_get_config.return_value = mock_config
            
            save_multiplexer_config(endpoints, config_path)
        
        assert config_path.exists()
        
        with patch("haven_cli.vlm.config.get_config") as mock_get_config:
            mock_config = MagicMock()
            mock_config.data_dir = tmp_path
            mock_get_config.return_value = mock_config
            
            loaded = load_multiplexer_config(config_path)
        
        assert len(loaded) == 2
        assert loaded[0].name == "server1"
        assert loaded[1].weight == 1
    
    def test_load_nonexistent_config(self, tmp_path):
        """Test loading non-existent configuration."""
        config_path = tmp_path / "nonexistent.json"
        
        with patch("haven_cli.vlm.config.get_config") as mock_get_config:
            mock_config = MagicMock()
            mock_config.data_dir = tmp_path
            mock_get_config.return_value = mock_config
            
            loaded = load_multiplexer_config(config_path)
        
        assert loaded == []
    
    def test_load_invalid_config(self, tmp_path):
        """Test loading invalid configuration file."""
        config_path = tmp_path / "invalid.json"
        config_path.write_text("not valid json")
        
        with patch("haven_cli.vlm.config.get_config") as mock_get_config:
            mock_config = MagicMock()
            mock_config.data_dir = tmp_path
            mock_get_config.return_value = mock_config
            
            loaded = load_multiplexer_config(config_path)
        
        assert loaded == []
    
    def test_get_example_multiplexer_config(self):
        """Test getting example multiplexer configuration."""
        example = get_example_multiplexer_config()
        
        # Should be valid JSON
        parsed = json.loads(example)
        
        assert parsed["enabled"] is True
        assert "endpoints" in parsed
        assert len(parsed["endpoints"]) == 3
