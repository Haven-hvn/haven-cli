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
    
    Filecoin storage and Arkiv sync can use different mainnet/testnet modes.
    Leave ``filecoin_network_mode`` / ``arkiv_network_mode`` empty to inherit
    ``network_mode`` (backward compatible default).
    
    ``network_mode`` remains the legacy primary knob; it should match Filecoin
    for older tooling that only reads that field.
    """
    
    # Legacy default used when per-chain fields are empty; keep aligned with Filecoin.
    network_mode: str = "testnet"
    # Per-chain modes: "mainnet", "testnet", or "" to use network_mode
    filecoin_network_mode: str = ""
    arkiv_network_mode: str = ""
    
    # Optional: Override RPC endpoints (if not set, derived from effective per-chain mode)
    filecoin_rpc_override: Optional[str] = None
    arkiv_rpc_override: Optional[str] = None
    
    @property
    def effective_filecoin_network_mode(self) -> str:
        """Resolved Filecoin network (mainnet or testnet)."""
        raw = (self.filecoin_network_mode or "").strip()
        base = raw if raw else self.network_mode
        return base.lower()
    
    @property
    def effective_arkiv_network_mode(self) -> str:
        """Resolved Arkiv network (mainnet or testnet)."""
        raw = (self.arkiv_network_mode or "").strip()
        base = raw if raw else self.network_mode
        return base.lower()
    
    @property
    def is_mainnet(self) -> bool:
        """True when Filecoin is configured for mainnet."""
        return self.effective_filecoin_network_mode == "mainnet"
    
    @property
    def is_testnet(self) -> bool:
        """True when Filecoin is configured for a non-mainnet preset."""
        return self.effective_filecoin_network_mode in ("testnet", "test", "dev")
    
    def get_filecoin_rpc_url(self) -> str:
        """Get Filecoin RPC URL based on configuration."""
        if self.filecoin_rpc_override:
            return self.filecoin_rpc_override
        main = self.effective_filecoin_network_mode == "mainnet"
        return (
            "https://api.node.glif.io/rpc/v1"
            if main
            else "https://api.calibration.node.glif.io/rpc/v1"
        )
    
    def get_arkiv_rpc_url(self) -> str:
        """Get Arkiv RPC URL based on configuration."""
        if self.arkiv_rpc_override:
            return self.arkiv_rpc_override
        main = self.effective_arkiv_network_mode == "mainnet"
        return (
            "https://mainnet.arkiv.network/rpc"
            if main
            else "https://kaolin.hoodi.arkiv.network/rpc"
        )


@dataclass
class PipelineConfig:
    """Configuration for the processing pipeline."""
    
    # VLM Analysis - Core Settings
    # Matches backend AppConfig defaults
    vlm_enabled: bool = True
    vlm_model: str = "zai-org/glm-4.6v-flash"  # Matches backend llm_model default
    vlm_api_key: Optional[str] = None
    vlm_timeout: float = 70.0  # Matches backend request_timeout
    
    # VLM Analysis - Tags Configuration (comma-separated list of tags to detect)
    # Matches backend AppConfig.analysis_tags default
    vlm_analysis_tags: str = "person,car,bicycle,motorcycle,airplane,bus,train,truck,boat,traffic_light,stop_sign,walking,running,standing,sitting,talking,eating,drinking,phone,laptop,book,bag,umbrella,skateboard,surfboard,tennis_racket"
    
    # VLM Analysis - Processing Parameters
    # Matches backend AppConfig defaults
    vlm_frame_interval: float = 2.0  # Seconds between frame samples
    vlm_threshold: float = 0.5  # Confidence threshold for tag detection (0-1)
    vlm_return_timestamps: bool = True  # Include timestamp information in results
    vlm_return_confidence: bool = True  # Include confidence scores in results
    
    # VLM Analysis - Advanced Settings
    vlm_max_new_tokens: int = 128  # Matches backend model config
    vlm_detected_tag_confidence: float = 0.99  # Matches backend vlm_detected_tag_confidence
    
    # VLM Multiplexer - Load Balancing Configuration
    # Multiplexer is the ONLY supported method for VLM processing
    vlm_multiplexer_enabled: bool = True  # Enabled by default with localhost endpoint
    vlm_max_concurrent_requests: int = 15  # Matches backend vlm_max_concurrent_requests
    # Default endpoint: local LLM server (e.g., LM Studio, LocalAI)
    vlm_multiplexer_endpoints: List[Dict[str, Any]] = field(default_factory=lambda: [
        {"base_url": "http://localhost:1234/v1", "name": "local-llm", "weight": 1, "max_concurrent": 5}
    ])
    
    # Encryption (Haven-AOL on Internet Computer mainnet)
    encryption_enabled: bool = True
    # EVM chain where access-control conditions are enforced (token/NFT contract lives here).
    # Canonical names: EthMainnet, EthSepolia, ArbitrumOne, BaseMainnet, OptimismMainnet.
    evm_chain: Optional[str] = None
    access_pattern: Optional[str] = None
    token_contract: Optional[str] = None
    min_balance: Optional[str] = None
    token_standard: Optional[str] = None
    owner_wallet: Optional[str] = None
    nft_contract: Optional[str] = None
    
    # Upload (Filecoin via Synapse)
    # Note: Synapse RPC is derived from blockchain.filecoin_network_mode (or network_mode)
    # Set blockchain.filecoin_rpc_override to override
    # Note: Authentication via HAVEN_PRIVATE_KEY environment variable ONLY
    upload_enabled: bool = True
    
    # Blockchain Sync (Arkiv)
    # Note: Arkiv RPC is derived from blockchain.arkiv_network_mode (or network_mode)
    # Set blockchain.arkiv_rpc_override to override
    sync_enabled: bool = True
    arkiv_contract: Optional[str] = None
    
    # Processing
    max_concurrent_videos: int = 4
    retry_attempts: int = 3
    retry_delay: float = 5.0
    
    # Cleanup (remove local files after successful upload)
    cleanup_enabled: bool = False


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
    
def load_config(
    config_path: Optional[Path] = None,
    env_prefix: str = "HAVEN_"
) -> HavenConfig:
    """
    Load configuration from file and environment variables.
    
    Priority (highest to lowest):
    1. Environment variables
    2. Config file (from --config, HAVEN_CONFIG_DIR, or ~/.config/haven/config.toml)
    3. Default values
    
    Args:
        config_path: Path to config file (default: auto-detect)
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


def get_config_path(
    config_path: Optional[Path] = None,
    env_prefix: str = "HAVEN_"
) -> Optional[Path]:
    """
    Get the effective config file path that would be used.
    
    Returns the path to the config file that exists and would be loaded,
    or None if no config file exists (default values would be used).
    
    Args:
        config_path: Explicit path to config file (optional)
        env_prefix: Prefix for environment variables
    
    Returns:
        Path to the config file that exists, or None
    """
    # Determine config file path
    if config_path is None:
        # Check for environment variable override
        env_config_dir = os.environ.get(f"{env_prefix}CONFIG_DIR")
        if env_config_dir:
            config_path = Path(env_config_dir) / DEFAULT_CONFIG_FILE
        else:
            config_path = DEFAULT_CONFIG_DIR / DEFAULT_CONFIG_FILE
    
    # Return the path only if it exists
    if config_path.exists():
        return config_path
    
    return None


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
    
    # Blockchain network mode - legacy primary setting; also default for per-chain modes
    if env_val := os.environ.get(f"{prefix}NETWORK_MODE"):
        config.blockchain.network_mode = env_val
    if env_val := os.environ.get(f"{prefix}FILECOIN_NETWORK_MODE"):
        config.blockchain.filecoin_network_mode = env_val
    if env_val := os.environ.get(f"{prefix}ARKIV_NETWORK_MODE"):
        config.blockchain.arkiv_network_mode = env_val
    # Blockchain endpoint overrides
    if env_val := os.environ.get(f"{prefix}FILECOIN_RPC_OVERRIDE"):
        config.blockchain.filecoin_rpc_override = env_val
    if env_val := os.environ.get(f"{prefix}ARKIV_RPC_OVERRIDE"):
        config.blockchain.arkiv_rpc_override = env_val
    
    # Pipeline settings - VLM Core
    if env_val := os.environ.get(f"{prefix}VLM_ENABLED"):
        config.pipeline.vlm_enabled = env_val.lower() in ("true", "1", "yes")
    if env_val := os.environ.get(f"{prefix}VLM_MODEL"):
        config.pipeline.vlm_model = env_val
    if env_val := os.environ.get(f"{prefix}VLM_API_KEY"):
        config.pipeline.vlm_api_key = env_val
    # Note: VLM_BASE_URL removed - use vlm_multiplexer_endpoints instead
    if env_val := os.environ.get(f"{prefix}VLM_TIMEOUT"):
        try:
            config.pipeline.vlm_timeout = float(env_val)
        except ValueError:
            pass
    
    # Pipeline settings - VLM Tags
    if env_val := os.environ.get(f"{prefix}VLM_ANALYSIS_TAGS"):
        config.pipeline.vlm_analysis_tags = env_val
    
    # Pipeline settings - VLM Processing Parameters
    if env_val := os.environ.get(f"{prefix}VLM_FRAME_INTERVAL"):
        try:
            config.pipeline.vlm_frame_interval = float(env_val)
        except ValueError:
            pass
    if env_val := os.environ.get(f"{prefix}VLM_THRESHOLD"):
        try:
            config.pipeline.vlm_threshold = float(env_val)
        except ValueError:
            pass
    if env_val := os.environ.get(f"{prefix}VLM_RETURN_TIMESTAMPS"):
        config.pipeline.vlm_return_timestamps = env_val.lower() in ("true", "1", "yes")
    if env_val := os.environ.get(f"{prefix}VLM_RETURN_CONFIDENCE"):
        config.pipeline.vlm_return_confidence = env_val.lower() in ("true", "1", "yes")
    
    # Pipeline settings - VLM Advanced
    if env_val := os.environ.get(f"{prefix}VLM_MAX_NEW_TOKENS"):
        try:
            config.pipeline.vlm_max_new_tokens = int(env_val)
        except ValueError:
            pass
    if env_val := os.environ.get(f"{prefix}VLM_DETECTED_TAG_CONFIDENCE"):
        try:
            config.pipeline.vlm_detected_tag_confidence = float(env_val)
        except ValueError:
            pass
    
    # Pipeline settings - VLM Multiplexer
    if env_val := os.environ.get(f"{prefix}VLM_MULTIPLEXER_ENABLED"):
        config.pipeline.vlm_multiplexer_enabled = env_val.lower() in ("true", "1", "yes")
    if env_val := os.environ.get(f"{prefix}VLM_MAX_CONCURRENT_REQUESTS"):
        try:
            config.pipeline.vlm_max_concurrent_requests = int(env_val)
        except ValueError:
            pass
    
    if env_val := os.environ.get(f"{prefix}ENCRYPTION_ENABLED"):
        config.pipeline.encryption_enabled = env_val.lower() in ("true", "1", "yes")
    if env_val := os.environ.get(f"{prefix}EVM_CHAIN"):
        config.pipeline.evm_chain = env_val
    if env_val := os.environ.get(f"{prefix}ACCESS_PATTERN"):
        config.pipeline.access_pattern = env_val
    if env_val := os.environ.get(f"{prefix}TOKEN_CONTRACT"):
        config.pipeline.token_contract = env_val
    if env_val := os.environ.get(f"{prefix}MIN_BALANCE"):
        config.pipeline.min_balance = env_val
    if env_val := os.environ.get(f"{prefix}TOKEN_STANDARD"):
        config.pipeline.token_standard = env_val
    if env_val := os.environ.get(f"{prefix}OWNER_WALLET"):
        config.pipeline.owner_wallet = env_val
    if env_val := os.environ.get(f"{prefix}NFT_CONTRACT"):
        config.pipeline.nft_contract = env_val
    if env_val := os.environ.get(f"{prefix}UPLOAD_ENABLED"):
        config.pipeline.upload_enabled = env_val.lower() in ("true", "1", "yes")
    # Note: Filecoin RPC endpoint is configured via blockchain.filecoin_rpc_override
    # or HAVEN_FILECOIN_RPC_OVERRIDE environment variable
    
    if env_val := os.environ.get(f"{prefix}SYNC_ENABLED"):
        config.pipeline.sync_enabled = env_val.lower() in ("true", "1", "yes")
    # Note: Arkiv RPC endpoint is configured via blockchain.arkiv_rpc_override
    # or HAVEN_ARKIV_RPC_OVERRIDE environment variable
    
    if env_val := os.environ.get(f"{prefix}CLEANUP_ENABLED"):
        config.pipeline.cleanup_enabled = env_val.lower() in ("true", "1", "yes")
    
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
    
    return config


def _toml_escape_basic_string(raw: str) -> str:
    """Escape a string for a TOML basic string (double quotes)."""
    return raw.replace("\\", "\\\\").replace('"', '\\"')


def _format_toml_inline_array(elements: list[Any]) -> str:
    """Format a TOML array of booleans, integers, floats, or strings (no nested tables)."""
    parts: list[str] = []
    for el in elements:
        if isinstance(el, bool):
            parts.append("true" if el else "false")
        elif isinstance(el, int):
            parts.append(str(el))
        elif isinstance(el, float):
            parts.append(repr(el))
        elif isinstance(el, str):
            parts.append(f'"{_toml_escape_basic_string(el)}"')
        else:
            raise TypeError(
                f"Unsupported TOML array element type: {type(el).__name__}"
            )
    return "[" + ", ".join(parts) + "]"


def _plugin_setting_scalar_line(key: str, value: Any) -> str:
    """One ``key = value`` line for a plugin settings table (non-list values)."""
    if isinstance(value, str):
        return f'{key} = "{_toml_escape_basic_string(value)}"'
    if isinstance(value, bool):
        return f"{key} = {str(value).lower()}"
    if isinstance(value, int):
        return f"{key} = {value}"
    if isinstance(value, float):
        return f"{key} = {repr(value)}"
    raise TypeError(
        f"Unsupported plugin setting scalar for {key!r}: {type(value).__name__}"
    )


def _plugin_settings_toml_lines(plugin_name: str, settings: dict[str, Any]) -> list[str]:
    """Build TOML lines for ``[plugins.settings.{plugin_name}]`` and nested array-of-tables."""
    lines: list[str] = [f"\n[plugins.settings.{plugin_name}]"]
    deferred_aot: list[tuple[str, list[dict[str, Any]]]] = []

    for key, value in settings.items():
        if isinstance(value, (list, tuple)):
            if len(value) == 0:
                lines.append(f"{key} = []")
            elif isinstance(value[0], dict):
                deferred_aot.append((key, [dict(row) for row in value]))
            else:
                lines.append(f"{key} = {_format_toml_inline_array(list(value))}")
        else:
            lines.append(_plugin_setting_scalar_line(key, value))

    for aot_key, rows in deferred_aot:
        table_base = f"plugins.settings.{plugin_name}.{aot_key}"
        for row in rows:
            lines.append(f"[[{table_base}]]")
            for rk, rv in row.items():
                lines.append(_plugin_setting_scalar_line(str(rk), rv))
    return lines


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
        f'config_dir = "{_toml_escape_basic_string(str(config.config_dir))}"',
        f'data_dir = "{_toml_escape_basic_string(str(config.data_dir))}"',
        f'database_url = "{_toml_escape_basic_string(config.database_url)}"',
        "",
        "# Blockchain Network Configuration",
        "# network_mode: legacy default; used when filecoin_network_mode / arkiv_network_mode are empty",
        "[blockchain]",
        f'network_mode = "{config.blockchain.network_mode}"',
        f'filecoin_network_mode = "{config.blockchain.filecoin_network_mode}"',
        f'arkiv_network_mode = "{config.blockchain.arkiv_network_mode}"',
        f'filecoin_rpc_override = "{config.blockchain.filecoin_rpc_override or ""}"',
        f'arkiv_rpc_override = "{config.blockchain.arkiv_rpc_override or ""}"',
        "",
        "[pipeline]",
        f"vlm_enabled = {str(config.pipeline.vlm_enabled).lower()}",
        f'vlm_model = "{config.pipeline.vlm_model}"',
        f'vlm_analysis_tags = "{config.pipeline.vlm_analysis_tags}"',
        f"vlm_frame_interval = {config.pipeline.vlm_frame_interval}",
        f"vlm_threshold = {config.pipeline.vlm_threshold}",
        f"vlm_return_timestamps = {str(config.pipeline.vlm_return_timestamps).lower()}",
        f"vlm_return_confidence = {str(config.pipeline.vlm_return_confidence).lower()}",
        f"vlm_max_new_tokens = {config.pipeline.vlm_max_new_tokens}",
        f"vlm_detected_tag_confidence = {config.pipeline.vlm_detected_tag_confidence}",
        f"vlm_multiplexer_enabled = {str(config.pipeline.vlm_multiplexer_enabled).lower()}",
        f"vlm_max_concurrent_requests = {config.pipeline.vlm_max_concurrent_requests}",
        f"encryption_enabled = {str(config.pipeline.encryption_enabled).lower()}",
        f'evm_chain = "{config.pipeline.evm_chain or ""}"',
        f'access_pattern = "{config.pipeline.access_pattern or ""}"',
        f'token_contract = "{config.pipeline.token_contract or ""}"',
        f'min_balance = "{config.pipeline.min_balance or ""}"',
        f'token_standard = "{config.pipeline.token_standard or ""}"',
        f'owner_wallet = "{config.pipeline.owner_wallet or ""}"',
        f'nft_contract = "{config.pipeline.nft_contract or ""}"',
        f"upload_enabled = {str(config.pipeline.upload_enabled).lower()}",
        f"sync_enabled = {str(config.pipeline.sync_enabled).lower()}",
        f"cleanup_enabled = {str(config.pipeline.cleanup_enabled).lower()}",
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
        f"enabled_plugins = {_format_toml_inline_array(list(config.plugins.enabled_plugins))}",
        f"disabled_plugins = {_format_toml_inline_array(list(config.plugins.disabled_plugins))}",
        "",
        "[plugins.settings]",
    ]
    
    # Add plugin-specific settings
    for plugin_name, settings in config.plugins.plugin_settings.items():
        lines.extend(_plugin_settings_toml_lines(plugin_name, settings))
    
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
    # VLM API key check (warning if not set and not using local model)
    # Note: When multiplexer is enabled, API keys should be configured per-endpoint
    if config.pipeline.vlm_enabled and not config.pipeline.vlm_api_key:
        if not config.pipeline.vlm_multiplexer_enabled:
            errors.append(ValidationError(
                field="pipeline.vlm_api_key",
                message="VLM API key not set. VLM analysis may fail.",
                severity="warning"
            ))
    
    # VLM processing parameters validation
    if not 0 <= config.pipeline.vlm_threshold <= 1:
        errors.append(ValidationError(
            field="pipeline.vlm_threshold",
            message=f"VLM threshold must be between 0 and 1, got {config.pipeline.vlm_threshold}",
            severity="error"
        ))
    
    if config.pipeline.vlm_frame_interval <= 0:
        errors.append(ValidationError(
            field="pipeline.vlm_frame_interval",
            message=f"VLM frame interval must be positive, got {config.pipeline.vlm_frame_interval}",
            severity="error"
        ))
    
    if not 0 <= config.pipeline.vlm_detected_tag_confidence <= 1:
        errors.append(ValidationError(
            field="pipeline.vlm_detected_tag_confidence",
            message=f"VLM detected tag confidence must be between 0 and 1, got {config.pipeline.vlm_detected_tag_confidence}",
            severity="error"
        ))
    
    if config.pipeline.vlm_max_concurrent_requests <= 0:
        errors.append(ValidationError(
            field="pipeline.vlm_max_concurrent_requests",
            message=f"VLM max concurrent requests must be positive, got {config.pipeline.vlm_max_concurrent_requests}",
            severity="error"
        ))
    
    # VLM multiplexer validation
    if config.pipeline.vlm_multiplexer_enabled:
        if not config.pipeline.vlm_multiplexer_endpoints:
            errors.append(ValidationError(
                field="pipeline.vlm_multiplexer_endpoints",
                message="Multiplexer is enabled but no endpoints configured. "
                        "Add endpoints to your config:\n"
                        "  [[pipeline.vlm_multiplexer_endpoints]]\n"
                        "  base_url = \"http://your-server:1234/v1\"\n"
                        "  name = \"default\"\n"
                        "  weight = 1\n"
                        "  max_concurrent = 5\n"
                        "Or set vlm_multiplexer_enabled = false to disable multiplexer.",
                severity="error"
            ))
        else:
            for i, ep in enumerate(config.pipeline.vlm_multiplexer_endpoints):
                if not ep.get("base_url"):
                    errors.append(ValidationError(
                        field=f"pipeline.vlm_multiplexer_endpoints[{i}].base_url",
                        message="Multiplexer endpoint base_url is required",
                        severity="error"
                    ))
                if ep.get("weight", 1) <= 0:
                    errors.append(ValidationError(
                        field=f"pipeline.vlm_multiplexer_endpoints[{i}].weight",
                        message="Multiplexer endpoint weight must be positive",
                        severity="error"
                    ))
                if ep.get("max_concurrent", 5) <= 0:
                    errors.append(ValidationError(
                        field=f"pipeline.vlm_multiplexer_endpoints[{i}].max_concurrent",
                        message="Multiplexer endpoint max_concurrent must be positive",
                        severity="error"
                    ))
    
    # Upload validation (if enabled)
    if config.pipeline.upload_enabled:
        # Validate Filecoin RPC override if set
        if config.blockchain.filecoin_rpc_override and not _validate_url(config.blockchain.filecoin_rpc_override):
            errors.append(ValidationError(
                field="blockchain.filecoin_rpc_override",
                message=f"Invalid URL format: {config.blockchain.filecoin_rpc_override}",
                severity="error"
            ))
        
        # Authentication: HAVEN_PRIVATE_KEY environment variable REQUIRED
        if not os.environ.get("HAVEN_PRIVATE_KEY"):
            errors.append(ValidationError(
                field="HAVEN_PRIVATE_KEY",
                message="HAVEN_PRIVATE_KEY environment variable not set. "
                        "Upload to Filecoin requires a private key for blockchain authentication. "
                        "Set it with: export HAVEN_PRIVATE_KEY=0x...",
                severity="error"
            ))
    
    # Sync validation (if enabled)
    # Access-control asset chain (pipeline.evm_chain) when encryption is enabled
    if config.pipeline.encryption_enabled:
        if not os.environ.get("HAVEN_ICP_IDENTITY_PEM_PATH"):
            errors.append(ValidationError(
                field="HAVEN_ICP_IDENTITY_PEM_PATH",
                message=(
                    "HAVEN_ICP_IDENTITY_PEM_PATH environment variable is required when encryption is enabled. "
                    "Haven-AOL ICP uses authenticated user identity only."
                ),
                severity="error"
            ))
        # Transport key validation for decrypt-capable mode
        transport_secret = os.environ.get("HAVEN_AOL_TRANSPORT_SECRET_KEY_B64", "").strip()
        transport_public = os.environ.get("HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64", "").strip()
        if transport_secret and not transport_public:
            errors.append(ValidationError(
                field="HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64",
                message=(
                    "HAVEN_AOL_TRANSPORT_SECRET_KEY_B64 is set but HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64 is missing. "
                    "Both must be set for decrypt-capable mode. "
                    "Derive public from secret: vetkd_py.transport_public_key_from_secret(secret)"
                ),
                severity="error"
            ))
        if transport_public and not transport_secret:
            errors.append(ValidationError(
                field="HAVEN_AOL_TRANSPORT_SECRET_KEY_B64",
                message=(
                    "HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64 is set but HAVEN_AOL_TRANSPORT_SECRET_KEY_B64 is missing. "
                    "Both must be set for decrypt-capable mode."
                ),
                severity="error"
            ))
        if transport_secret and transport_public:
            # Validate base64 encoding
            import base64 as _b64
            try:
                _b64.b64decode(transport_secret)
            except Exception:
                errors.append(ValidationError(
                    field="HAVEN_AOL_TRANSPORT_SECRET_KEY_B64",
                    message="Value is not valid base64.",
                    severity="error"
                ))
            try:
                _b64.b64decode(transport_public)
            except Exception:
                errors.append(ValidationError(
                    field="HAVEN_AOL_TRANSPORT_PUBLIC_KEY_B64",
                    message="Value is not valid base64.",
                    severity="error"
                ))
        if not config.pipeline.evm_chain:
            errors.append(ValidationError(
                field="pipeline.evm_chain",
                message=(
                    "pipeline.evm_chain is required when encryption is enabled "
                    "(EVM chain where access-control assets live). "
                    "Use one of: EthMainnet, EthSepolia, ArbitrumOne, BaseMainnet, OptimismMainnet."
                ),
                severity="error"
            ))
        else:
            from haven_cli.services.evm_utils import normalize_haven_aol_chain

            try:
                normalize_haven_aol_chain(config.pipeline.evm_chain)
            except ValueError as exc:
                errors.append(ValidationError(
                    field="pipeline.evm_chain",
                    message=str(exc),
                    severity="error"
                ))
        pattern = (config.pipeline.access_pattern or "").strip().lower()
        valid_patterns = {"token_gated", "nft_gated", "owner_only", "public"}
        if pattern == "":
            errors.append(ValidationError(
                field="pipeline.access_pattern",
                message=(
                    "pipeline.access_pattern is required when encryption is enabled. "
                    "Use one of: token_gated, nft_gated, owner_only, public."
                ),
                severity="error"
            ))
        elif pattern not in valid_patterns:
            errors.append(ValidationError(
                field="pipeline.access_pattern",
                message=(
                    f"Unsupported access pattern {config.pipeline.access_pattern!r}. "
                    "Use one of: token_gated, nft_gated, owner_only, public."
                ),
                severity="error"
            ))
        elif pattern in {"token_gated", "owner_only"} and not config.pipeline.token_contract:
            errors.append(ValidationError(
                field="pipeline.token_contract",
                message="pipeline.token_contract is required for token_gated and owner_only patterns.",
                severity="error"
            ))
        if pattern == "token_gated":
            if not config.pipeline.min_balance:
                errors.append(ValidationError(
                    field="pipeline.min_balance",
                    message="pipeline.min_balance is required for token_gated pattern.",
                    severity="error"
                ))
            if not config.pipeline.token_standard:
                errors.append(ValidationError(
                    field="pipeline.token_standard",
                    message="pipeline.token_standard is required for token_gated pattern (ERC20 or ERC721).",
                    severity="error"
                ))
            elif config.pipeline.token_standard not in {"ERC20", "ERC721"}:
                errors.append(ValidationError(
                    field="pipeline.token_standard",
                    message="pipeline.token_standard must be ERC20 or ERC721.",
                    severity="error"
                ))
        if pattern == "owner_only" and not config.pipeline.owner_wallet:
            errors.append(ValidationError(
                field="pipeline.owner_wallet",
                message="pipeline.owner_wallet is required for owner_only pattern.",
                severity="error"
            ))
        if pattern == "nft_gated" and not config.pipeline.nft_contract:
            errors.append(ValidationError(
                field="pipeline.nft_contract",
                message="pipeline.nft_contract is required for nft_gated pattern.",
                severity="error"
            ))

    if config.pipeline.sync_enabled:
        # Validate Arkiv RPC override if set
        if config.blockchain.arkiv_rpc_override and not _validate_url(config.blockchain.arkiv_rpc_override):
            errors.append(ValidationError(
                field="blockchain.arkiv_rpc_override",
                message=f"Invalid URL format: {config.blockchain.arkiv_rpc_override}",
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
            "filecoin_network_mode": config.blockchain.filecoin_network_mode,
            "arkiv_network_mode": config.blockchain.arkiv_network_mode,
            "effective_filecoin_network_mode": config.blockchain.effective_filecoin_network_mode,
            "effective_arkiv_network_mode": config.blockchain.effective_arkiv_network_mode,
            "is_mainnet": config.blockchain.is_mainnet,
            "is_testnet": config.blockchain.is_testnet,
            "filecoin_rpc_url": config.blockchain.get_filecoin_rpc_url(),
            "arkiv_rpc_url": config.blockchain.get_arkiv_rpc_url(),
            "filecoin_rpc_override": config.blockchain.filecoin_rpc_override,
            "arkiv_rpc_override": config.blockchain.arkiv_rpc_override,
        },
        "pipeline": {
            "vlm_enabled": config.pipeline.vlm_enabled,
            "vlm_model": config.pipeline.vlm_model,
            "vlm_api_key": mask_value("vlm_api_key", config.pipeline.vlm_api_key),
            "vlm_timeout": config.pipeline.vlm_timeout,
            "vlm_analysis_tags": config.pipeline.vlm_analysis_tags,
            "vlm_frame_interval": config.pipeline.vlm_frame_interval,
            "vlm_threshold": config.pipeline.vlm_threshold,
            "vlm_return_timestamps": config.pipeline.vlm_return_timestamps,
            "vlm_return_confidence": config.pipeline.vlm_return_confidence,
            "vlm_max_new_tokens": config.pipeline.vlm_max_new_tokens,
            "vlm_detected_tag_confidence": config.pipeline.vlm_detected_tag_confidence,
            "vlm_multiplexer_enabled": config.pipeline.vlm_multiplexer_enabled,
            "vlm_max_concurrent_requests": config.pipeline.vlm_max_concurrent_requests,
            "vlm_multiplexer_endpoints": config.pipeline.vlm_multiplexer_endpoints,
            "encryption_enabled": config.pipeline.encryption_enabled,
            "evm_chain": config.pipeline.evm_chain,
            "access_pattern": config.pipeline.access_pattern,
            "token_contract": config.pipeline.token_contract,
            "min_balance": config.pipeline.min_balance,
            "token_standard": config.pipeline.token_standard,
            "owner_wallet": config.pipeline.owner_wallet,
            "nft_contract": config.pipeline.nft_contract,
            "upload_enabled": config.pipeline.upload_enabled,
            "sync_enabled": config.pipeline.sync_enabled,
            "cleanup_enabled": config.pipeline.cleanup_enabled,
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
