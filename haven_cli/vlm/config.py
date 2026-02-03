"""VLM configuration management for Haven CLI.

This module provides configuration loading and management for VLM analysis,
adapted from the backend VLM configuration system.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from haven_cli.config import get_config, PipelineConfig


@dataclass
class VLMEngineConfig:
    """Configuration for a VLM engine.
    
    Attributes:
        model_type: Type of model (openai, gemini, local)
        model_name: Specific model identifier
        api_key: API key for the service
        base_url: Base URL for API calls
        timeout: Request timeout in seconds
        max_tokens: Maximum tokens in response
        max_concurrent: Maximum concurrent requests
    """
    
    model_type: str = "openai"
    model_name: str = "gpt-4-vision-preview"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    timeout: float = 120.0
    max_tokens: int = 4096
    max_concurrent: int = 5


@dataclass
class VLMProcessingConfig:
    """Configuration for VLM video processing.
    
    Attributes:
        enabled: Whether VLM analysis is enabled
        frame_count: Number of frames to sample for analysis
        frame_interval: Seconds between frame samples
        threshold: Confidence threshold for tag detection
        return_timestamps: Whether to extract timestamps
        return_confidence: Whether to return confidence scores
        save_to_file: Whether to save results to .AI.json file
    """
    
    enabled: bool = True
    frame_count: int = 20
    frame_interval: float = 2.0
    threshold: float = 0.5
    return_timestamps: bool = True
    return_confidence: bool = True
    save_to_file: bool = True


@dataclass
class VLMMultiplexerEndpoint:
    """Configuration for a multiplexer endpoint.
    
    Used for load balancing across multiple VLM servers.
    
    Attributes:
        base_url: Endpoint URL
        api_key: API key for this endpoint
        name: Human-readable name
        weight: Load balancing weight
        max_concurrent: Maximum concurrent requests for this endpoint
    """
    
    base_url: str
    name: str
    weight: int = 1
    max_concurrent: int = 5
    api_key: Optional[str] = None


@dataclass
class VLMMultiplexerConfig:
    """Configuration for VLM multiplexer (load balancing).
    
    Attributes:
        enabled: Whether multiplexer is enabled
        endpoints: List of endpoint configurations
        max_concurrent_requests: Global limit on concurrent requests
    """
    
    enabled: bool = False
    endpoints: List[VLMMultiplexerEndpoint] = field(default_factory=list)
    max_concurrent_requests: int = 10


@dataclass
class VLMConfig:
    """Complete VLM configuration.
    
    Combines engine, processing, and multiplexer configurations.
    """
    
    engine: VLMEngineConfig = field(default_factory=VLMEngineConfig)
    processing: VLMProcessingConfig = field(default_factory=VLMProcessingConfig)
    multiplexer: VLMMultiplexerConfig = field(default_factory=VLMMultiplexerConfig)
    
    # Additional settings
    cache_enabled: bool = True
    cache_dir: Optional[Path] = None


def load_vlm_config() -> VLMConfig:
    """Load VLM configuration from Haven CLI config.
    
    Returns:
        VLMConfig instance with loaded settings
    """
    config = get_config()
    pipeline = config.pipeline
    
    # Determine model type from model name
    model_type = _infer_model_type(pipeline.vlm_model)
    
    # Build engine config
    engine_config = VLMEngineConfig(
        model_type=model_type,
        model_name=pipeline.vlm_model,
        api_key=pipeline.vlm_api_key,
        timeout=pipeline.vlm_timeout,
    )
    
    # Build processing config
    processing_config = VLMProcessingConfig(
        enabled=pipeline.vlm_enabled,
        threshold=0.5,  # Default threshold
        return_timestamps=True,
        return_confidence=True,
        save_to_file=True,
    )
    
    # Build complete config
    vlm_config = VLMConfig(
        engine=engine_config,
        processing=processing_config,
        cache_dir=Path(config.data_dir) / "vlm_cache" if config.data_dir else None,
    )
    
    # Override with environment variables if present
    vlm_config = _apply_env_overrides(vlm_config)
    
    return vlm_config


def _infer_model_type(model_name: str) -> str:
    """Infer model type from model name.
    
    Args:
        model_name: Model identifier
        
    Returns:
        Model type string
    """
    model_lower = model_name.lower()
    
    if "gpt" in model_lower or model_lower.startswith("openai"):
        return "openai"
    elif "gemini" in model_lower:
        return "gemini"
    elif "claude" in model_lower:
        return "anthropic"
    elif "llava" in model_lower or "local" in model_lower:
        return "local"
    else:
        return "openai"  # Default to OpenAI-compatible API


def _apply_env_overrides(config: VLMConfig) -> VLMConfig:
    """Apply environment variable overrides to config.
    
    Args:
        config: Current configuration
        
    Returns:
        Updated configuration
    """
    # API key overrides
    if api_key := os.environ.get("OPENAI_API_KEY"):
        if config.engine.model_type == "openai":
            config.engine.api_key = api_key
    
    if api_key := os.environ.get("GOOGLE_API_KEY"):
        if config.engine.model_type == "gemini":
            config.engine.api_key = api_key
    
    if api_key := os.environ.get("VLM_API_KEY"):
        config.engine.api_key = api_key
    
    # Base URL override
    if base_url := os.environ.get("VLM_BASE_URL"):
        config.engine.base_url = base_url
    
    # Processing config overrides
    if frame_count := os.environ.get("VLM_FRAME_COUNT"):
        try:
            config.processing.frame_count = int(frame_count)
        except ValueError:
            pass
    
    if threshold := os.environ.get("VLM_THRESHOLD"):
        try:
            config.processing.threshold = float(threshold)
        except ValueError:
            pass
    
    if interval := os.environ.get("VLM_FRAME_INTERVAL"):
        try:
            config.processing.frame_interval = float(interval)
        except ValueError:
            pass
    
    # Enable/disable overrides
    if enabled := os.environ.get("VLM_ENABLED"):
        config.processing.enabled = enabled.lower() in ("true", "1", "yes")
    
    return config


def get_engine_config(config: Optional[VLMConfig] = None) -> VLMEngineConfig:
    """Get the engine configuration.
    
    Args:
        config: Optional VLMConfig (loads from global if not provided)
        
    Returns:
        VLMEngineConfig instance
    """
    if config is None:
        config = load_vlm_config()
    return config.engine


def get_processing_params(config: Optional[VLMConfig] = None) -> Dict[str, Any]:
    """Get processing parameters as a dictionary.
    
    Args:
        config: Optional VLMConfig (loads from global if not provided)
        
    Returns:
        Dictionary of processing parameters
    """
    if config is None:
        config = load_vlm_config()
    
    return {
        "enabled": config.processing.enabled,
        "frame_count": config.processing.frame_count,
        "frame_interval": config.processing.frame_interval,
        "threshold": config.processing.threshold,
        "return_timestamps": config.processing.return_timestamps,
        "return_confidence": config.processing.return_confidence,
        "save_to_file": config.processing.save_to_file,
    }


def create_analysis_config(config: Optional[VLMConfig] = None) -> "AnalysisConfig":
    """Create an AnalysisConfig from VLMConfig.
    
    Args:
        config: Optional VLMConfig (loads from global if not provided)
        
    Returns:
        AnalysisConfig instance for use with VLM engines
    """
    from haven_cli.vlm.engine import AnalysisConfig
    
    if config is None:
        config = load_vlm_config()
    
    return AnalysisConfig(
        frame_count=config.processing.frame_count,
        frame_interval=config.processing.frame_interval,
        threshold=config.processing.threshold,
        return_timestamps=config.processing.return_timestamps,
        return_confidence=config.processing.return_confidence,
        max_tokens=config.engine.max_tokens,
        timeout=config.engine.timeout,
    )


def save_multiplexer_config(endpoints: List[Dict[str, Any]], config_path: Optional[Path] = None) -> None:
    """Save multiplexer endpoint configuration.
    
    Args:
        endpoints: List of endpoint dictionaries
        config_path: Path to save configuration (default: data_dir/multiplexer.json)
    """
    config = get_config()
    
    if config_path is None:
        config_path = Path(config.data_dir) / "vlm_multiplexer.json"
    
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(config_path, "w") as f:
        json.dump({"endpoints": endpoints}, f, indent=2)


def load_multiplexer_config(config_path: Optional[Path] = None) -> List[VLMMultiplexerEndpoint]:
    """Load multiplexer endpoint configuration.
    
    Args:
        config_path: Path to configuration file
        
    Returns:
        List of multiplexer endpoints
    """
    config = get_config()
    
    if config_path is None:
        config_path = Path(config.data_dir) / "vlm_multiplexer.json"
    
    if not config_path.exists():
        return []
    
    try:
        with open(config_path) as f:
            data = json.load(f)
        
        endpoints = []
        for ep_data in data.get("endpoints", []):
            endpoints.append(VLMMultiplexerEndpoint(**ep_data))
        
        return endpoints
    except (json.JSONDecodeError, TypeError) as e:
        print(f"Warning: Failed to load multiplexer config: {e}")
        return []


def get_example_multiplexer_config() -> str:
    """Get example multiplexer configuration as JSON string.
    
    Returns:
        Example configuration JSON
    """
    example = {
        "enabled": True,
        "max_concurrent_requests": 25,
        "endpoints": [
            {
                "base_url": "http://primary-server:1234/v1",
                "name": "primary-server",
                "weight": 8,
                "max_concurrent": 10,
            },
            {
                "base_url": "http://secondary-server:1234/v1",
                "name": "secondary-server",
                "weight": 1,
                "max_concurrent": 8,
            },
            {
                "base_url": "http://fallback-server:1234/v1",
                "name": "fallback-server",
                "weight": 1,
                "max_concurrent": 2,
            },
        ],
    }
    
    return json.dumps(example, indent=2)


def validate_vlm_config(config: Optional[VLMConfig] = None) -> List[str]:
    """Validate VLM configuration and return list of issues.
    
    Args:
        config: VLMConfig to validate (loads from global if not provided)
        
    Returns:
        List of validation error messages (empty if valid)
    """
    if config is None:
        config = load_vlm_config()
    
    errors: List[str] = []
    
    # Check if VLM is enabled but no API key
    if config.processing.enabled:
        if not config.engine.api_key:
            # Only error if not using local model
            if config.engine.model_type != "local":
                errors.append(
                    f"VLM is enabled but no API key set for {config.engine.model_type} model. "
                    "Set the appropriate API key environment variable or in config."
                )
    
    # Validate processing parameters
    if config.processing.frame_count < 1:
        errors.append("frame_count must be at least 1")
    
    if config.processing.frame_count > 100:
        errors.append("frame_count seems high (>100), this may be slow/expensive")
    
    if not 0 <= config.processing.threshold <= 1:
        errors.append("threshold must be between 0 and 1")
    
    # Validate timeout
    if config.engine.timeout < 1:
        errors.append("timeout must be at least 1 second")
    
    # Validate multiplexer endpoints if enabled
    if config.multiplexer.enabled:
        if not config.multiplexer.endpoints:
            errors.append("Multiplexer enabled but no endpoints configured")
        
        for i, ep in enumerate(config.multiplexer.endpoints):
            if not ep.base_url:
                errors.append(f"Endpoint {i}: base_url is required")
            if ep.weight < 1:
                errors.append(f"Endpoint {i}: weight must be positive")
            if ep.max_concurrent < 1:
                errors.append(f"Endpoint {i}: max_concurrent must be positive")
    
    return errors
