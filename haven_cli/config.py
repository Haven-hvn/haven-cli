"""
Haven CLI Configuration Management.

Handles loading, saving, and validating configuration from various sources:
- Default values
- Configuration files (TOML/YAML)
- Environment variables
- Command-line arguments
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional, List

# Try to import tomllib (Python 3.11+) or tomli as fallback
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore

# Try to import tomli_w for writing TOML
try:
    import tomli_w
except ImportError:
    tomli_w = None  # type: ignore

# Try to import pyyaml
try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


# Configuration directory and file constants
CONFIG_DIR = Path.home() / ".config" / "haven"
CONFIG_FILE = "config.toml"

# Default configuration directory
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "haven"
DEFAULT_CONFIG_FILE = "config.toml"
DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "haven"


@dataclass
class ValidationError:
    """Validation error for configuration."""
    field: str
    message: str
    severity: str  # "error" or "warning"
    
    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.field}: {self.message}"


@dataclass
class BlockchainConfig:
    """Configuration for blockchain network settings.
    
    Provides unified mainnet/testnet configuration across all
    blockchain integrations (Lit, Filecoin, Arkiv).
    
    When network_mode is set, it automatically configures:
    - Lit Protocol network (datil for mainnet, datil-dev for testnet)
    - Filecoin RPC endpoint (mainnet or calibration testnet)
    - Arkiv RPC endpoint (mainnet or hoodi testnet)
    
    Individual endpoint settings can still override the defaults.
    """
    
    # Network mode - 'mainnet' or 'testnet'
    # This single setting propagates to all blockchain integrations
    network_mode: str = "testnet"
    
    # Optional: Override specific endpoints (if not set, uses network_mode defaults)
    lit_network_override: Optional[str] = None
    filecoin_rpc_override: Optional[str] = None
    arkiv_rpc_override: Optional[str] = None
    
    @property
    def is_mainnet(self) -> bool:
        """Check if configured for mainnet."""
        return self.network_mode.lower() == "mainnet"
    
    @property
    def is_testnet(self) -> bool:
        """Check if configured for testnet."""
        return self.network_mode.lower() in ("testnet", "test", "dev")
    
    def get_lit_network(self) -> str:
        """Get Lit Protocol network based on configuration."""
        if self.lit_network_override:
            return self.lit_network_override
        return "naga" if self.is_mainnet else "naga-dev"
    
    def get_filecoin_rpc_url(self) -> str:
        """Get Filecoin RPC URL based on configuration."""
        if self.filecoin_rpc_override:
            return self.filecoin_rpc_override
        return (
            "https://api.node.glif.io/rpc/v1"  # Mainnet
            if self.is_mainnet
            else "https://api.calibration.node.glif.io/rpc/v1"  # Testnet
        )
    
    def get_arkiv_rpc_url(self) -> str:
        """Get Arkiv RPC URL based on configuration."""
        if self.arkiv_rpc_override:
            return self.arkiv_rpc_override
        return (
            "https://mainnet.arkiv.network/rpc"  # Mainnet
            if self.is_mainnet
            else "https://mendoza.hoodi.arkiv.network/rpc"  # Hoodi testnet
        )


@dataclass
class PipelineConfig:
    """Configuration for the processing pipeline."""
    
    # VLM Analysis
    vlm_enabled: bool = True
    vlm_model: str = "gpt-4-vision-preview"
    vlm_api_key: Optional[str] = None
    vlm_timeout: float = 120.0
    
    # Encryption (Lit Protocol)
    # Note: lit_network is now derived from blockchain.network_mode
    # Set blockchain.lit_network_override to override
    encryption_enabled: bool = True
    lit_network: str = "datil-dev"  # Deprecated: use blockchain.network_mode
    
    # Upload (Filecoin via Synapse)
    # Note: synapse_endpoint defaults from blockchain.network_mode
    upload_enabled: bool = True
    synapse_endpoint: Optional[str] = None
    synapse_api_key: Optional[str] = None
    
    # Blockchain Sync (Arkiv)
    # Note: arkiv_endpoint defaults from blockchain.network_mode
    sync_enabled: bool = True
    arkiv_endpoint: Optional[str] = None
    arkiv_contract: Optional[str] = None
    
    # Processing
    max_concurrent_videos: int = 4
    retry_attempts: int = 3
    retry_delay: float = 5.0


@dataclass
class SchedulerConfig:
    """Configuration for the job scheduler."""
    
    # Scheduler settings
    enabled: bool = True
    check_interval: int = 60  # seconds
    max_concurrent_jobs: int = 2
    
    # Job defaults
    default_cron: str = "0 */6 * * *"  # Every 6 hours
    job_timeout: int = 3600  # 1 hour
    
    # Persistence
    state_file: Optional[Path] = None


@dataclass
class PluginConfig:
    """Configuration for the plugin system."""
    
    # Plugin directories
    plugin_dirs: list[Path] = field(default_factory=list)
    
    # Enabled plugins
    enabled_plugins: list[str] = field(default_factory=list)
    disabled_plugins: list[str] = field(default_factory=list)
    
    # Plugin-specific settings
    plugin_settings: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class JSRuntimeConfig:
    """Configuration for the JavaScript runtime bridge."""
    
    # Runtime settings
    runtime: Optional[str] = None  # Auto-detect if None
    services_path: Optional[Path] = None
    
    # Timeouts
    startup_timeout: float = 30.0
    request_timeout: float = 60.0
    
    # Debug
    debug: bool = False


@dataclass
class LoggingConfig:
    """Configuration for logging."""
    
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: Optional[Path] = None
    max_size: int = 10 * 1024 * 1024  # 10MB
    backup_count: int = 5


@dataclass
class HavenConfig:
    """Main configuration container for Haven CLI."""
    
    # Paths
    config_dir: Path = DEFAULT_CONFIG_DIR
    data_dir: Path = DEFAULT_DATA_DIR
    
    # Sub-configurations
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    plugins: PluginConfig = field(default_factory=PluginConfig)
    js_runtime: JSRuntimeConfig = field(default_factory=JSRuntimeConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    blockchain: BlockchainConfig = field(default_factory=BlockchainConfig)
    
    # Database
    database_url: str = ""
    
    def __post_init__(self):
        """Initialize derived values and propagate network mode."""
        if not self.database_url:
            self.database_url = f"sqlite:///{self.data_dir}/haven.db"
        
        if self.scheduler.state_file is None:
            self.scheduler.state_file = self.data_dir / "scheduler_state.json"
        
        # Propagate network mode to legacy pipeline settings for backward compatibility
        self._propagate_network_mode()
    
    def _propagate_network_mode(self) -> None:
        """Propagate blockchain network mode to individual service configs.
        
        This ensures that setting blockchain.network_mode automatically
        updates all the individual blockchain service configurations
        (Lit, Filecoin, Arkiv) unless they have explicit overrides.
        """
        # Update Lit network if no override is set
        if not self.blockchain.lit_network_override:
            self.pipeline.lit_network = self.blockchain.get_lit_network()
        
        # Update Filecoin endpoint if not explicitly set
        if not self.pipeline.synapse_endpoint and not self.blockchain.filecoin_rpc_override:
            self.pipeline.synapse_endpoint = self.blockchain.get_filecoin_rpc_url()
        
        # Update Arkiv endpoint if not explicitly set
        if not self.pipeline.arkiv_endpoint and not self.blockchain.arkiv_rpc_override:
            self.pipeline.arkiv_endpoint = self.blockchain.get_arkiv_rpc_url()


def load_config(
    config_path: Optional[Path] = None,
    env_prefix: str = "HAVEN_"
) -> HavenConfig:
    """
    Load configuration from file and environment variables.
    
    Priority (highest to lowest):
    1. Environment variables
    2. Config file
    3. Default values
    
    Args:
        config_path: Path to config file (default: ~/.config/haven/config.toml)
        env_prefix: Prefix for environment variables
    
    Returns:
        Loaded configuration
    """
    config = HavenConfig()
    
    # Determine config file path
    if config_path is None:
        # Check for environment variable override
        env_config_dir = os.environ.get(f"{env_prefix}CONFIG_DIR")
        if env_config_dir:
            config_path = Path(env_config_dir) / DEFAULT_CONFIG_FILE
        else:
            config_path = DEFAULT_CONFIG_DIR / DEFAULT_CONFIG_FILE
    
    # Load from file if exists
    if config_path.exists() and tomllib is not None:
        config = _load_from_file(config_path, config)
    
    # Override with environment variables
    config = _load_from_env(config, env_prefix)
    
    return config


def _load_from_file(path: Path, config: HavenConfig) -> HavenConfig:
    """Load configuration from a TOML file."""
    if tomllib is None:
        return config
    
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        
        # Update config from file data
        if "blockchain" in data:
            for key, value in data["blockchain"].items():
                if hasattr(config.blockchain, key):
                    setattr(config.blockchain, key, value)
        
        if "pipeline" in data:
            for key, value in data["pipeline"].items():
                if hasattr(config.pipeline, key):
                    setattr(config.pipeline, key, value)
        
        if "scheduler" in data:
            for key, value in data["scheduler"].items():
                if hasattr(config.scheduler, key):
                    setattr(config.scheduler, key, value)
        
        if "plugins" in data:
            for key, value in data["plugins"].items():
                if key == "settings" and isinstance(value, dict):
                    # Handle nested plugin settings (plugins.settings.*)
                    for plugin_name, plugin_settings in value.items():
                        if isinstance(plugin_settings, dict):
                            config.plugins.plugin_settings[plugin_name] = plugin_settings
                elif hasattr(config.plugins, key):
                    setattr(config.plugins, key, value)
        
        if "js_runtime" in data:
            for key, value in data["js_runtime"].items():
                if hasattr(config.js_runtime, key):
                    setattr(config.js_runtime, key, value)
        
        if "logging" in data:
            for key, value in data["logging"].items():
                if hasattr(config.logging, key):
                    setattr(config.logging, key, value)
        
        # Top-level settings
        if "config_dir" in data:
            config.config_dir = Path(data["config_dir"])
        if "data_dir" in data:
            config.data_dir = Path(data["data_dir"])
        if "database_url" in data:
            config.database_url = data["database_url"]
            
    except Exception as e:
        print(f"Warning: Failed to load config from {path}: {e}")
    
    return config


def _load_from_env(config: HavenConfig, prefix: str) -> HavenConfig:
    """Load configuration from environment variables."""
    
    # Blockchain network mode - this is the primary network setting
    if env_val := os.environ.get(f"{prefix}NETWORK_MODE"):
        config.blockchain.network_mode = env_val
    # Also check legacy HAVEN_NETWORK_MODE without prefix for convenience
    if env_val := os.environ.get("HAVEN_NETWORK_MODE"):
        config.blockchain.network_mode = env_val
    
    # Blockchain endpoint overrides
    if env_val := os.environ.get(f"{prefix}LIT_NETWORK_OVERRIDE"):
        config.blockchain.lit_network_override = env_val
    if env_val := os.environ.get(f"{prefix}FILECOIN_RPC_OVERRIDE"):
        config.blockchain.filecoin_rpc_override = env_val
    if env_val := os.environ.get(f"{prefix}ARKIV_RPC_OVERRIDE"):
        config.blockchain.arkiv_rpc_override = env_val
    
    # Pipeline settings
    if env_val := os.environ.get(f"{prefix}VLM_ENABLED"):
        config.pipeline.vlm_enabled = env_val.lower() in ("true", "1", "yes")
    if env_val := os.environ.get(f"{prefix}VLM_MODEL"):
        config.pipeline.vlm_model = env_val
    if env_val := os.environ.get(f"{prefix}VLM_API_KEY"):
        config.pipeline.vlm_api_key = env_val
    
    if env_val := os.environ.get(f"{prefix}ENCRYPTION_ENABLED"):
        config.pipeline.encryption_enabled = env_val.lower() in ("true", "1", "yes")
    # LIT_NETWORK env var can override the network mode default
    if env_val := os.environ.get(f"{prefix}LIT_NETWORK"):
        config.blockchain.lit_network_override = env_val
    
    if env_val := os.environ.get(f"{prefix}UPLOAD_ENABLED"):
        config.pipeline.upload_enabled = env_val.lower() in ("true", "1", "yes")
    if env_val := os.environ.get(f"{prefix}SYNAPSE_ENDPOINT"):
        config.pipeline.synapse_endpoint = env_val
    if env_val := os.environ.get(f"{prefix}SYNAPSE_API_KEY"):
        config.pipeline.synapse_api_key = env_val
    
    if env_val := os.environ.get(f"{prefix}SYNC_ENABLED"):
        config.pipeline.sync_enabled = env_val.lower() in ("true", "1", "yes")
    
    # Scheduler settings
    if env_val := os.environ.get(f"{prefix}SCHEDULER_ENABLED"):
        config.scheduler.enabled = env_val.lower() in ("true", "1", "yes")
    
    # Logging settings
    if env_val := os.environ.get(f"{prefix}LOG_LEVEL"):
        config.logging.level = env_val.upper()
    
    # JS Runtime settings
    if env_val := os.environ.get(f"{prefix}JS_RUNTIME"):
        config.js_runtime.runtime = env_val
    if env_val := os.environ.get(f"{prefix}JS_DEBUG"):
        config.js_runtime.debug = env_val.lower() in ("true", "1", "yes")
    
    # Paths
    if env_val := os.environ.get(f"{prefix}CONFIG_DIR"):
        config.config_dir = Path(env_val)
    if env_val := os.environ.get(f"{prefix}DATA_DIR"):
        config.data_dir = Path(env_val)
    if env_val := os.environ.get(f"{prefix}DATABASE_URL"):
        config.database_url = env_val
    
    # After loading env vars, propagate network mode to individual settings
    config._propagate_network_mode()
    
    return config


def save_config(config: HavenConfig, path: Optional[Path] = None) -> None:
    """
    Save configuration to a TOML file.
    
    Args:
        config: Configuration to save
        path: Path to save to (default: config.config_dir / config.toml)
    """
    if path is None:
        path = config.config_dir / DEFAULT_CONFIG_FILE
    
    # Ensure directory exists
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Build TOML content
    lines = [
        "# Haven CLI Configuration",
        "# Generated automatically - edit with care",
        "",
        f'config_dir = "{config.config_dir}"',
        f'data_dir = "{config.data_dir}"',
        f'database_url = "{config.database_url}"',
        "",
        "# Blockchain Network Configuration",
        "# Set network_mode to 'mainnet' or 'testnet' to configure all blockchain integrations",
        "[blockchain]",
        f'network_mode = "{config.blockchain.network_mode}"',
        f'lit_network_override = "{config.blockchain.lit_network_override or ""}"',
        f'filecoin_rpc_override = "{config.blockchain.filecoin_rpc_override or ""}"',
        f'arkiv_rpc_override = "{config.blockchain.arkiv_rpc_override or ""}"',
        "",
        "[pipeline]",
        f"vlm_enabled = {str(config.pipeline.vlm_enabled).lower()}",
        f'vlm_model = "{config.pipeline.vlm_model}"',
        f"encryption_enabled = {str(config.pipeline.encryption_enabled).lower()}",
        f'lit_network = "{config.pipeline.lit_network}"  # Auto-set from blockchain.network_mode',
        f"upload_enabled = {str(config.pipeline.upload_enabled).lower()}",
        f"sync_enabled = {str(config.pipeline.sync_enabled).lower()}",
        f"max_concurrent_videos = {config.pipeline.max_concurrent_videos}",
        f"retry_attempts = {config.pipeline.retry_attempts}",
        "",
        "[scheduler]",
        f"enabled = {str(config.scheduler.enabled).lower()}",
        f"check_interval = {config.scheduler.check_interval}",
        f"max_concurrent_jobs = {config.scheduler.max_concurrent_jobs}",
        f'default_cron = "{config.scheduler.default_cron}"',
        "",
        "[plugins]",
        f"enabled_plugins = {config.plugins.enabled_plugins}",
        f"disabled_plugins = {config.plugins.disabled_plugins}",
        "",
        "[plugins.settings]",
    ]
    
    # Add plugin-specific settings
    for plugin_name, settings in config.plugins.plugin_settings.items():
        lines.append(f"\n[plugins.settings.{plugin_name}]")
        for key, value in settings.items():
            if isinstance(value, str):
                lines.append(f'{key} = "{value}"')
            elif isinstance(value, bool):
                lines.append(f"{key} = {str(value).lower()}")
            elif isinstance(value, (list, tuple)):
                lines.append(f"{key} = {list(value)}")
            else:
                lines.append(f"{key} = {value}")
    
    lines.extend([
        "",
        "[logging]",
        f'level = "{config.logging.level}"',
        "",
        "[js_runtime]",
        f"startup_timeout = {config.js_runtime.startup_timeout}",
        f"request_timeout = {config.js_runtime.request_timeout}",
        f"debug = {str(config.js_runtime.debug).lower()}",
    ])
    
    with open(path, "w") as f:
        f.write("\n".join(lines))


def ensure_directories(config: HavenConfig) -> None:
    """Ensure all required directories exist."""
    config.config_dir.mkdir(parents=True, exist_ok=True)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    
    # Plugin directories
    for plugin_dir in config.plugins.plugin_dirs:
        plugin_dir.mkdir(parents=True, exist_ok=True)


def get_default_config() -> HavenConfig:
    """Get the default configuration."""
    return HavenConfig()


# Global configuration instance (lazy-loaded)
_global_config: Optional[HavenConfig] = None


def get_config() -> HavenConfig:
    """Get the global configuration instance."""
    global _global_config
    if _global_config is None:
        _global_config = load_config()
    return _global_config


def set_config(config: HavenConfig) -> None:
    """Set the global configuration instance."""
    global _global_config
    _global_config = config


def clear_config_cache() -> None:
    """Clear the global configuration cache."""
    global _global_config
    _global_config = None


def set_config_value(section: str, key: str, value: Any, config_path: Optional[Path] = None) -> None:
    """
    Set a single configuration value and persist to file.
    
    Args:
        section: Configuration section (e.g., 'pipeline', 'scheduler', 'blockchain')
        key: Configuration key within the section
        value: Value to set (will be converted to appropriate type)
        config_path: Path to config file (default: CONFIG_DIR / CONFIG_FILE)
    """
    if config_path is None:
        config_path = CONFIG_DIR / CONFIG_FILE
    
    # Load current config
    config = load_config(config_path)
    
    # Get the section object
    section_obj = getattr(config, section, None)
    if section_obj is None:
        raise ValueError(f"Unknown configuration section: {section}")
    
    # Get current value to determine type
    if not hasattr(section_obj, key):
        raise ValueError(f"Unknown configuration key: {section}.{key}")
    
    current_value = getattr(section_obj, key)
    current_type = type(current_value)
    
    # Convert value to appropriate type
    if current_type == bool:
        converted_value = value.lower() in ("true", "1", "yes", "on")
    elif current_type == int:
        converted_value = int(value)
    elif current_type == float:
        converted_value = float(value)
    elif current_type == Path:
        converted_value = Path(value)
    else:
        converted_value = value
    
    # Set the new value
    setattr(section_obj, key, converted_value)
    
    # If setting blockchain.network_mode, propagate to individual settings
    if section == "blockchain" and key == "network_mode":
        config._propagate_network_mode()
    
    # Save config
    save_config(config, config_path)


def _validate_cron(cron: str) -> bool:
    """Validate a cron expression."""
    # Basic cron validation: 5 fields (minute hour day month day_of_week)
    # Supports standard cron format and some extensions
    cron_pattern = r"^((\*|\d{1,2}|\d{1,2}-\d{1,2}|\*/\d+|\d{1,2}/\d+)(,\d{1,2})*\s+){4}(\*|\d{1,2}|\d{1,2}-\d{1,2}|\*/\d+|\d{1,2}/\d+|\d{1,2},\d{1,2})+$"
    
    # Special cases
    special_cases = ["@yearly", "@annually", "@monthly", "@weekly", "@daily", "@midnight", "@hourly"]
    
    if cron in special_cases:
        return True
    
    return bool(re.match(cron_pattern, cron))


def _validate_url(url: str) -> bool:
    """Validate a URL format."""
    url_pattern = r"^https?://[^\s/$.?#].[^\s]*$"
    return bool(re.match(url_pattern, url))


def validate_config(config: Optional[HavenConfig] = None) -> List[ValidationError]:
    """
    Validate configuration and return list of errors.
    
    Args:
        config: Configuration to validate (default: loaded from file)
        
    Returns:
        List of validation errors (empty if valid)
    """
    if config is None:
        config = load_config()
    
    errors: List[ValidationError] = []
    
    # Pipeline validation
    # VLM API key check (warning if not set)
    if not config.pipeline.vlm_api_key:
        errors.append(ValidationError(
            field="pipeline.vlm_api_key",
            message="VLM API key not set. VLM analysis may fail.",
            severity="warning"
        ))
    
    # Synapse endpoint validation (if upload enabled)
    if config.pipeline.upload_enabled:
        if not config.pipeline.synapse_endpoint:
            errors.append(ValidationError(
                field="pipeline.synapse_endpoint",
                message="Synapse endpoint not set but upload is enabled.",
                severity="warning"
            ))
        elif config.pipeline.synapse_endpoint and not _validate_url(config.pipeline.synapse_endpoint):
            errors.append(ValidationError(
                field="pipeline.synapse_endpoint",
                message=f"Invalid URL format: {config.pipeline.synapse_endpoint}",
                severity="error"
            ))
        
        if not config.pipeline.synapse_api_key:
            errors.append(ValidationError(
                field="pipeline.synapse_api_key",
                message="Synapse API key not set but upload is enabled.",
                severity="warning"
            ))
    
    # Arkiv endpoint validation (if sync enabled)
    if config.pipeline.sync_enabled:
        if config.pipeline.arkiv_endpoint and not _validate_url(config.pipeline.arkiv_endpoint):
            errors.append(ValidationError(
                field="pipeline.arkiv_endpoint",
                message=f"Invalid URL format: {config.pipeline.arkiv_endpoint}",
                severity="error"
            ))
    
    # Scheduler validation
    if config.scheduler.enabled:
        if not _validate_cron(config.scheduler.default_cron):
            errors.append(ValidationError(
                field="scheduler.default_cron",
                message=f"Invalid cron expression: {config.scheduler.default_cron}",
                severity="error"
            ))
    
    # Path validation
    if not config.config_dir.exists():
        errors.append(ValidationError(
            field="config_dir",
            message=f"Config directory does not exist: {config.config_dir}",
            severity="warning"
        ))
    
    if not config.data_dir.exists():
        errors.append(ValidationError(
            field="data_dir",
            message=f"Data directory does not exist: {config.data_dir}",
            severity="warning"
        ))
    
    # Check if directories are writable
    try:
        if config.config_dir.exists():
            test_file = config.config_dir / ".write_test"
            test_file.touch()
            test_file.unlink()
    except (PermissionError, OSError):
        errors.append(ValidationError(
            field="config_dir",
            message=f"Config directory is not writable: {config.config_dir}",
            severity="error"
        ))
    
    try:
        if config.data_dir.exists():
            test_file = config.data_dir / ".write_test"
            test_file.touch()
            test_file.unlink()
    except (PermissionError, OSError):
        errors.append(ValidationError(
            field="data_dir",
            message=f"Data directory is not writable: {config.data_dir}",
            severity="error"
        ))
    
    return errors


def _config_to_dict(config: HavenConfig, mask_secrets: bool = True) -> dict[str, Any]:
    """
    Convert configuration to dictionary.
    
    Args:
        config: Configuration to convert
        mask_secrets: If True, mask sensitive values like API keys
        
    Returns:
        Dictionary representation of config
    """
    def mask_value(key: str, value: Any) -> Any:
        """Mask sensitive values."""
        if not mask_secrets:
            return value
        sensitive_keys = {"api_key", "private_key", "password", "secret", "token"}
        if value and any(sk in key.lower() for sk in sensitive_keys):
            if isinstance(value, str) and len(value) > 4:
                return value[:4] + "****"
            return "****"
        if isinstance(value, Path):
            return str(value)
        return value
    
    result: dict[str, Any] = {
        "config_dir": str(config.config_dir),
        "data_dir": str(config.data_dir),
        "database_url": mask_value("database_url", config.database_url),
        "blockchain": {
            "network_mode": config.blockchain.network_mode,
            "is_mainnet": config.blockchain.is_mainnet,
            "is_testnet": config.blockchain.is_testnet,
            "lit_network": config.blockchain.get_lit_network(),
            "filecoin_rpc_url": config.blockchain.get_filecoin_rpc_url(),
            "arkiv_rpc_url": config.blockchain.get_arkiv_rpc_url(),
            "lit_network_override": config.blockchain.lit_network_override,
            "filecoin_rpc_override": config.blockchain.filecoin_rpc_override,
            "arkiv_rpc_override": config.blockchain.arkiv_rpc_override,
        },
        "pipeline": {
            "vlm_enabled": config.pipeline.vlm_enabled,
            "vlm_model": config.pipeline.vlm_model,
            "vlm_api_key": mask_value("vlm_api_key", config.pipeline.vlm_api_key),
            "vlm_timeout": config.pipeline.vlm_timeout,
            "encryption_enabled": config.pipeline.encryption_enabled,
            "lit_network": config.pipeline.lit_network,
            "upload_enabled": config.pipeline.upload_enabled,
            "synapse_endpoint": config.pipeline.synapse_endpoint,
            "synapse_api_key": mask_value("synapse_api_key", config.pipeline.synapse_api_key),
            "sync_enabled": config.pipeline.sync_enabled,
            "arkiv_endpoint": config.pipeline.arkiv_endpoint,
            "arkiv_contract": config.pipeline.arkiv_contract,
            "max_concurrent_videos": config.pipeline.max_concurrent_videos,
            "retry_attempts": config.pipeline.retry_attempts,
            "retry_delay": config.pipeline.retry_delay,
        },
        "scheduler": {
            "enabled": config.scheduler.enabled,
            "check_interval": config.scheduler.check_interval,
            "max_concurrent_jobs": config.scheduler.max_concurrent_jobs,
            "default_cron": config.scheduler.default_cron,
            "job_timeout": config.scheduler.job_timeout,
            "state_file": str(config.scheduler.state_file) if config.scheduler.state_file else None,
        },
        "plugins": {
            "plugin_dirs": [str(p) for p in config.plugins.plugin_dirs],
            "enabled_plugins": config.plugins.enabled_plugins,
            "disabled_plugins": config.plugins.disabled_plugins,
            "plugin_settings": config.plugins.plugin_settings,
        },
        "js_runtime": {
            "runtime": config.js_runtime.runtime,
            "services_path": str(config.js_runtime.services_path) if config.js_runtime.services_path else None,
            "startup_timeout": config.js_runtime.startup_timeout,
            "request_timeout": config.js_runtime.request_timeout,
            "debug": config.js_runtime.debug,
        },
        "logging": {
            "level": config.logging.level,
            "format": config.logging.format,
            "file": str(config.logging.file) if config.logging.file else None,
            "max_size": config.logging.max_size,
            "backup_count": config.logging.backup_count,
        },
    }
    
    return result


def export_config_yaml(config: HavenConfig, mask_secrets: bool = True) -> str:
    """
    Export configuration as YAML string.
    
    Args:
        config: Configuration to export
        mask_secrets: If True, mask sensitive values
        
    Returns:
        YAML string representation of config
        
    Raises:
        ImportError: If pyyaml is not installed
    """
    if yaml is None:
        raise ImportError("pyyaml is required for YAML export. Install with: pip install pyyaml")
    
    config_dict = _config_to_dict(config, mask_secrets)
    return yaml.dump(config_dict, default_flow_style=False, sort_keys=False, allow_unicode=True)


def export_config_json(config: HavenConfig, mask_secrets: bool = True) -> str:
    """
    Export configuration as JSON string.
    
    Args:
        config: Configuration to export
        mask_secrets: If True, mask sensitive values
        
    Returns:
        JSON string representation of config
    """
    config_dict = _config_to_dict(config, mask_secrets)
    return json.dumps(config_dict, indent=2)
