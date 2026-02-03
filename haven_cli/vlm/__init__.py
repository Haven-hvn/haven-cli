"""VLM (Visual Language Model) module for Haven CLI.

This module provides Visual Language Model integration for video analysis,
including:
- Frame sampling and analysis
- Timestamp extraction
- Content tag classification
- Multiple backend support (OpenAI, Gemini, Local)

Example:
    >>> from haven_cli.vlm import VLMProcessor, process_video
    >>> 
    >>> # Simple usage
    >>> results = await process_video("video.mp4")
    >>> 
    >>> # Advanced usage with custom config
    >>> processor = VLMProcessor()
    >>> results = await processor.process_video("video.mp4")
    >>> print(results["tags"])
    {'sports': 0.95, 'action': 0.88}
"""

from haven_cli.vlm.engine import (
    VLMEngine,
    OpenAIVLMEngine,
    GeminiVLMEngine,
    LocalVLMEngine,
    VLMResponse,
    AnalysisConfig,
    create_vlm_engine,
)
from haven_cli.vlm.processor import (
    VLMProcessor,
    process_video,
    save_results_to_db,
)
from haven_cli.vlm.config import (
    VLMConfig,
    VLMEngineConfig,
    VLMProcessingConfig,
    VLMMultiplexerConfig,
    VLMMultiplexerEndpoint,
    load_vlm_config,
    get_engine_config,
    create_analysis_config,
    validate_vlm_config,
)
from haven_cli.vlm.parsing import (
    parse_vlm_response,
    parse_timestamp_segments,
    parse_content_tags,
    filter_segments_by_confidence,
    filter_tags_by_confidence,
    merge_overlapping_segments,
    ResponseValidator,
)
from haven_cli.vlm.prompts import (
    build_timestamp_prompt,
    build_tag_extraction_prompt,
    build_detailed_analysis_prompt,
    get_prompt_for_use_case,
)

__all__ = [
    # Engines
    "VLMEngine",
    "OpenAIVLMEngine",
    "GeminiVLMEngine",
    "LocalVLMEngine",
    "VLMResponse",
    "AnalysisConfig",
    "create_vlm_engine",
    
    # Processor
    "VLMProcessor",
    "process_video",
    "save_results_to_db",
    
    # Config
    "VLMConfig",
    "VLMEngineConfig",
    "VLMProcessingConfig",
    "VLMMultiplexerConfig",
    "VLMMultiplexerEndpoint",
    "load_vlm_config",
    "get_engine_config",
    "create_analysis_config",
    "validate_vlm_config",
    
    # Parsing
    "parse_vlm_response",
    "parse_timestamp_segments",
    "parse_content_tags",
    "filter_segments_by_confidence",
    "filter_tags_by_confidence",
    "merge_overlapping_segments",
    "ResponseValidator",
    
    # Prompts
    "build_timestamp_prompt",
    "build_tag_extraction_prompt",
    "build_detailed_analysis_prompt",
    "get_prompt_for_use_case",
]

__version__ = "0.1.0"
