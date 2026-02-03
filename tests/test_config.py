"""Tests for configuration management."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from haven_cli.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    DEFAULT_CONFIG_DIR,
    DEFAULT_CONFIG_FILE,
    DEFAULT_DATA_DIR,
    HavenConfig,
    PipelineConfig,
    SchedulerConfig,
    ValidationError,
    _config_to_dict,
    _validate_cron,
    _validate_url,
    ensure_directories,
    export_config_json,
    export_config_yaml,
    get_config,
    load_config,
    save_config,
    set_config_value,
    validate_config,
)


class TestConfigConstants:
    """Test configuration constants."""
    
    def test_config_dir_defined(self):
        """Test that CONFIG_DIR is defined."""
        assert CONFIG_DIR is not None
        assert isinstance(CONFIG_DIR, Path)
    
    def test_config_file_defined(self):
        """Test that CONFIG_FILE is defined."""
        assert CONFIG_FILE is not None
        assert isinstance(CONFIG_FILE, str)
    
    def test_default_config_dir(self):
        """Test default config directory."""
        assert DEFAULT_CONFIG_DIR == Path.home() / ".config" / "haven"
    
    def test_default_config_file(self):
        """Test default config file."""
        assert DEFAULT_CONFIG_FILE == "config.toml"


class TestValidationError:
    """Test ValidationError dataclass."""
    
    def test_validation_error_creation(self):
        """Test creating a ValidationError."""
        error = ValidationError(
            field="pipeline.vlm_api_key",
            message="API key not set",
            severity="warning"
        )
        assert error.field == "pipeline.vlm_api_key"
        assert error.message == "API key not set"
        assert error.severity == "warning"
    
    def test_validation_error_str(self):
        """Test ValidationError string representation."""
        error = ValidationError(
            field="test.field",
            message="test message",
            severity="error"
        )
        assert str(error) == "[ERROR] test.field: test message"


class TestHavenConfig:
    """Test HavenConfig dataclass."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = HavenConfig()
        assert config.config_dir == DEFAULT_CONFIG_DIR
        assert config.data_dir == DEFAULT_DATA_DIR
        assert isinstance(config.pipeline, PipelineConfig)
        assert isinstance(config.scheduler, SchedulerConfig)
    
    def test_post_init_database_url(self):
        """Test that database_url is set in post_init."""
        config = HavenConfig()
        assert config.database_url == f"sqlite:///{config.data_dir}/haven.db"
    
    def test_post_init_scheduler_state_file(self):
        """Test that scheduler state_file is set in post_init."""
        config = HavenConfig()
        assert config.scheduler.state_file == config.data_dir / "scheduler_state.json"


class TestConfigLoading:
    """Test configuration loading."""
    
    def test_load_default_config(self):
        """Test loading default config when no file exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "nonexistent.toml"
            config = load_config(config_path)
            assert isinstance(config, HavenConfig)
            assert config.pipeline.vlm_enabled is True
    
    def test_load_from_file(self):
        """Test loading config from a file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            # Create a config file
            config_content = """
[pipeline]
vlm_enabled = false
vlm_model = "test-model"
max_concurrent_videos = 10

[scheduler]
enabled = false
check_interval = 120
"""
            config_path.write_text(config_content)
            
            config = load_config(config_path)
            assert config.pipeline.vlm_enabled is False
            assert config.pipeline.vlm_model == "test-model"
            assert config.pipeline.max_concurrent_videos == 10
            assert config.scheduler.enabled is False
            assert config.scheduler.check_interval == 120


class TestConfigSaving:
    """Test configuration saving."""
    
    def test_save_config(self):
        """Test saving configuration to file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config = HavenConfig()
            config.pipeline.vlm_model = "custom-model"
            config.pipeline.max_concurrent_videos = 8
            
            save_config(config, config_path)
            
            assert config_path.exists()
            content = config_path.read_text()
            assert "vlm_model = \"custom-model\"" in content
            assert "max_concurrent_videos = 8" in content
    
    def test_save_and_load_roundtrip(self):
        """Test saving and loading config preserves values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            original = HavenConfig()
            original.pipeline.vlm_model = "test-model"
            original.pipeline.vlm_enabled = False
            
            save_config(original, config_path)
            loaded = load_config(config_path)
            
            assert loaded.pipeline.vlm_model == "test-model"
            assert loaded.pipeline.vlm_enabled is False


class TestSetConfigValue:
    """Test set_config_value function."""
    
    def test_set_boolean_value(self):
        """Test setting a boolean config value."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            # Create initial config
            config = HavenConfig()
            save_config(config, config_path)
            
            # Set boolean value
            set_config_value("pipeline", "vlm_enabled", "false", config_path)
            
            # Load and verify
            loaded = load_config(config_path)
            assert loaded.pipeline.vlm_enabled is False
    
    def test_set_integer_value(self):
        """Test setting an integer config value."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config = HavenConfig()
            save_config(config, config_path)
            
            set_config_value("pipeline", "max_concurrent_videos", "16", config_path)
            
            loaded = load_config(config_path)
            assert loaded.pipeline.max_concurrent_videos == 16
    
    def test_set_string_value(self):
        """Test setting a string config value."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config = HavenConfig()
            save_config(config, config_path)
            
            set_config_value("pipeline", "vlm_model", "gpt-4o", config_path)
            
            loaded = load_config(config_path)
            assert loaded.pipeline.vlm_model == "gpt-4o"
    
    def test_set_invalid_section(self):
        """Test setting value in invalid section."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config = HavenConfig()
            save_config(config, config_path)
            
            with pytest.raises(ValueError, match="Unknown configuration section"):
                set_config_value("invalid_section", "key", "value", config_path)
    
    def test_set_invalid_key(self):
        """Test setting invalid config key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config = HavenConfig()
            save_config(config, config_path)
            
            with pytest.raises(ValueError, match="Unknown configuration key"):
                set_config_value("pipeline", "invalid_key", "value", config_path)


class TestValidationHelpers:
    """Test validation helper functions."""
    
    def test_validate_cron_valid_standard(self):
        """Test valid standard cron expressions."""
        assert _validate_cron("0 */6 * * *") is True  # Every 6 hours
        assert _validate_cron("0 0 * * *") is True   # Daily at midnight
        assert _validate_cron("*/5 * * * *") is True # Every 5 minutes
    
    def test_validate_cron_valid_special(self):
        """Test valid special cron expressions."""
        assert _validate_cron("@hourly") is True
        assert _validate_cron("@daily") is True
        assert _validate_cron("@weekly") is True
        assert _validate_cron("@monthly") is True
    
    def test_validate_cron_invalid(self):
        """Test invalid cron expressions."""
        assert _validate_cron("invalid") is False
        assert _validate_cron("* * *") is False  # Too few fields
        assert _validate_cron("") is False
    
    def test_validate_url_valid(self):
        """Test valid URLs."""
        assert _validate_url("https://api.example.com") is True
        assert _validate_url("http://localhost:8080") is True
        assert _validate_url("https://api.node.glif.io/rpc/v0") is True
    
    def test_validate_url_invalid(self):
        """Test invalid URLs."""
        assert _validate_url("not-a-url") is False
        assert _validate_url("ftp://files.example.com") is False
        assert _validate_url("") is False


class TestConfigValidation:
    """Test configuration validation."""
    
    def test_validate_default_config(self):
        """Test validating default config."""
        config = HavenConfig()
        errors = validate_config(config)
        # Default config should have warnings but no errors
        assert all(e.severity != "error" for e in errors)
    
    def test_validate_invalid_cron(self):
        """Test validation catches invalid cron."""
        config = HavenConfig()
        config.scheduler.default_cron = "invalid-cron"
        errors = validate_config(config)
        
        cron_errors = [e for e in errors if e.field == "scheduler.default_cron"]
        assert len(cron_errors) == 1
        assert cron_errors[0].severity == "error"
    
    def test_validate_invalid_url(self):
        """Test validation catches invalid URL."""
        config = HavenConfig()
        config.pipeline.upload_enabled = True
        config.pipeline.synapse_endpoint = "not-a-url"
        errors = validate_config(config)
        
        url_errors = [e for e in errors if "synapse_endpoint" in e.field]
        assert len(url_errors) >= 1
        assert url_errors[0].severity == "error"
    
    def test_validate_missing_api_key_warning(self):
        """Test validation warns about missing API key."""
        config = HavenConfig()
        config.pipeline.vlm_api_key = None
        errors = validate_config(config)
        
        api_key_warnings = [e for e in errors if "vlm_api_key" in e.field]
        assert len(api_key_warnings) >= 1
        assert api_key_warnings[0].severity == "warning"


class TestConfigExport:
    """Test configuration export functions."""
    
    def test_export_config_json(self):
        """Test exporting config as JSON."""
        config = HavenConfig()
        config.pipeline.vlm_model = "test-model"
        
        json_output = export_config_json(config)
        data = json.loads(json_output)
        
        assert data["pipeline"]["vlm_model"] == "test-model"
        assert "scheduler" in data
        assert "logging" in data
    
    def test_export_config_json_masks_secrets(self):
        """Test JSON export masks sensitive values."""
        config = HavenConfig()
        config.pipeline.vlm_api_key = "sk-secret123456"
        
        json_output = export_config_json(config, mask_secrets=True)
        data = json.loads(json_output)
        
        assert "****" in data["pipeline"]["vlm_api_key"]
        assert "secret" not in data["pipeline"]["vlm_api_key"].lower()
    
    def test_export_config_json_unmasked(self):
        """Test JSON export with unmasked secrets."""
        config = HavenConfig()
        config.pipeline.vlm_api_key = "sk-secret123456"
        
        json_output = export_config_json(config, mask_secrets=False)
        data = json.loads(json_output)
        
        assert data["pipeline"]["vlm_api_key"] == "sk-secret123456"
    
    def test_export_config_yaml(self):
        """Test exporting config as YAML."""
        pytest.importorskip("yaml")
        
        config = HavenConfig()
        config.pipeline.vlm_model = "test-model"
        
        yaml_output = export_config_yaml(config)
        
        assert "vlm_model: test-model" in yaml_output
        assert "pipeline:" in yaml_output
    
    def test_export_config_yaml_import_error(self):
        """Test YAML export raises error if pyyaml not installed."""
        # This test would require mocking yaml to None
        # For now, just check that it raises if yaml is None
        pass  # Would need mocking


class TestEnvironmentVariables:
    """Test environment variable loading."""
    
    def test_env_vlm_enabled(self, monkeypatch):
        """Test HAVEN_VLM_ENABLED env var."""
        monkeypatch.setenv("HAVEN_VLM_ENABLED", "false")
        config = load_config()
        assert config.pipeline.vlm_enabled is False
    
    def test_env_vlm_model(self, monkeypatch):
        """Test HAVEN_VLM_MODEL env var."""
        monkeypatch.setenv("HAVEN_VLM_MODEL", "custom-model")
        config = load_config()
        assert config.pipeline.vlm_model == "custom-model"
    
    def test_env_vlm_api_key(self, monkeypatch):
        """Test HAVEN_VLM_API_KEY env var."""
        monkeypatch.setenv("HAVEN_VLM_API_KEY", "test-api-key")
        config = load_config()
        assert config.pipeline.vlm_api_key == "test-api-key"
    
    def test_env_log_level(self, monkeypatch):
        """Test HAVEN_LOG_LEVEL env var."""
        monkeypatch.setenv("HAVEN_LOG_LEVEL", "DEBUG")
        config = load_config()
        assert config.logging.level == "DEBUG"


class TestEnsureDirectories:
    """Test ensure_directories function."""
    
    def test_ensure_directories_creates_dirs(self):
        """Test that ensure_directories creates missing directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = HavenConfig()
            config.config_dir = Path(tmpdir) / "config"
            config.data_dir = Path(tmpdir) / "data"
            
            ensure_directories(config)
            
            assert config.config_dir.exists()
            assert config.data_dir.exists()


class TestConfigToDict:
    """Test _config_to_dict helper function."""
    
    def test_config_to_dict_structure(self):
        """Test that _config_to_dict returns correct structure."""
        config = HavenConfig()
        data = _config_to_dict(config)
        
        assert "pipeline" in data
        assert "scheduler" in data
        assert "logging" in data
        assert "config_dir" in data
        assert "data_dir" in data
    
    def test_config_to_dict_masks_secrets(self):
        """Test that _config_to_dict masks sensitive values."""
        config = HavenConfig()
        config.pipeline.vlm_api_key = "super-secret-key"
        
        data = _config_to_dict(config, mask_secrets=True)
        
        assert "****" in data["pipeline"]["vlm_api_key"]
    
    def test_config_to_dict_converts_paths(self):
        """Test that _config_to_dict converts Path objects to strings."""
        config = HavenConfig()
        data = _config_to_dict(config)
        
        assert isinstance(data["config_dir"], str)
        assert isinstance(data["data_dir"], str)
