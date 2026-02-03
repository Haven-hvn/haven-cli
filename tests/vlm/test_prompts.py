"""Tests for VLM prompts."""

import pytest
from PIL import Image

from haven_cli.vlm.prompts import (
    build_timestamp_prompt,
    build_tag_extraction_prompt,
    build_detailed_analysis_prompt,
    format_timestamp,
    get_prompt_for_use_case,
    TIMESTAMP_EXTRACTION_PROMPT,
    TAG_EXTRACTION_PROMPT,
    SIMPLE_ANALYSIS_PROMPT,
)


class TestBuildTimestampPrompt:
    """Tests for timestamp prompt building."""
    
    def test_build_basic_prompt(self):
        """Test building basic timestamp prompt."""
        frames = [
            (0.0, Image.new("RGB", (100, 100))),
            (10.0, Image.new("RGB", (100, 100))),
            (20.0, Image.new("RGB", (100, 100))),
        ]
        
        prompt = build_timestamp_prompt(frames, video_duration=60.0)
        
        assert "Analyze this video sequence" in prompt
        assert "60 seconds" in prompt or "01:00" in prompt
        assert "3 timestamps" in prompt
        assert "JSON" in prompt
        assert "segments" in prompt
    
    def test_build_with_custom_categories(self):
        """Test building prompt with custom categories."""
        frames = [(0.0, Image.new("RGB", (100, 100)))]
        categories = ["action", "dialogue"]
        
        prompt = build_timestamp_prompt(frames, video_duration=30.0, categories=categories)
        
        assert "action, dialogue" in prompt
    
    def test_build_with_zero_duration(self):
        """Test building prompt with zero duration."""
        frames = [(0.0, Image.new("RGB", (100, 100)))]
        
        prompt = build_timestamp_prompt(frames, video_duration=0.0)
        
        assert "Unknown" in prompt or "0 seconds" in prompt


class TestBuildTagExtractionPrompt:
    """Tests for tag extraction prompt building."""
    
    def test_build_basic_prompt(self):
        """Test building basic tag extraction prompt."""
        frames = [
            (0.0, Image.new("RGB", (100, 100))),
            (15.0, Image.new("RGB", (100, 100))),
        ]
        
        prompt = build_tag_extraction_prompt(frames, video_duration=30.0)
        
        assert "2 frames" in prompt
        assert "content classification" in prompt
        assert "JSON" in prompt
        assert "tags" in prompt
    
    def test_prompt_contains_categories(self):
        """Test that prompt mentions tag categories."""
        frames = [(0.0, Image.new("RGB", (100, 100)))]
        
        prompt = build_tag_extraction_prompt(frames)
        
        assert "genre" in prompt.lower()
        assert "setting" in prompt.lower()
        assert "mood" in prompt.lower()


class TestBuildDetailedAnalysisPrompt:
    """Tests for detailed analysis prompt building."""
    
    def test_comprehensive_analysis(self):
        """Test building comprehensive analysis prompt."""
        frames = [(0.0, Image.new("RGB", (100, 100)))]
        
        prompt = build_detailed_analysis_prompt(
            frames,
            video_duration=60.0,
            analysis_type="comprehensive",
        )
        
        assert "comprehensive analysis" in prompt.lower()
        assert "segments" in prompt
        assert "tags" in prompt
    
    def test_action_detection(self):
        """Test building action detection prompt."""
        frames = [(0.0, Image.new("RGB", (100, 100)))]
        
        prompt = build_detailed_analysis_prompt(
            frames,
            video_duration=60.0,
            analysis_type="action_detection",
        )
        
        assert "action" in prompt.lower()
        assert "action_segments" in prompt
        assert "intensity" in prompt
    
    def test_unknown_analysis_type_fallback(self):
        """Test fallback for unknown analysis type."""
        frames = [(0.0, Image.new("RGB", (100, 100)))]
        
        prompt = build_detailed_analysis_prompt(
            frames,
            video_duration=60.0,
            analysis_type="unknown",
        )
        
        # Should fall back to timestamp prompt
        assert "segments" in prompt or "timestamps" in prompt


class TestFormatTimestamp:
    """Tests for timestamp formatting."""
    
    def test_format_seconds_only(self):
        """Test formatting seconds-only timestamp."""
        assert format_timestamp(45.0) == "00:45"
        assert format_timestamp(5.0) == "00:05"
    
    def test_format_minutes_and_seconds(self):
        """Test formatting minutes and seconds."""
        assert format_timestamp(65.0) == "01:05"
        assert format_timestamp(125.0) == "02:05"
    
    def test_format_hours_minutes_seconds(self):
        """Test formatting hours, minutes, and seconds."""
        assert format_timestamp(3665.0) == "01:01:05"
        assert format_timestamp(7322.0) == "02:02:02"
    
    def test_format_zero(self):
        """Test formatting zero."""
        assert format_timestamp(0.0) == "00:00"
    
    def test_format_negative(self):
        """Test formatting negative time."""
        assert format_timestamp(-5.0) == "00:00"


class TestGetPromptForUseCase:
    """Tests for prompt retrieval by use case."""
    
    def test_get_timestamps_prompt(self):
        """Test getting timestamps prompt."""
        prompt = get_prompt_for_use_case("timestamps")
        
        assert prompt == TIMESTAMP_EXTRACTION_PROMPT
    
    def test_get_tags_prompt(self):
        """Test getting tags prompt."""
        prompt = get_prompt_for_use_case("tags")
        
        assert prompt == TAG_EXTRACTION_PROMPT
    
    def test_get_simple_prompt(self):
        """Test getting simple prompt."""
        prompt = get_prompt_for_use_case("simple")
        
        assert prompt == SIMPLE_ANALYSIS_PROMPT
    
    def test_get_detailed_timestamps_prompt(self):
        """Test getting detailed timestamps prompt with frames."""
        frames = [(0.0, Image.new("RGB", (100, 100)))]
        
        prompt = get_prompt_for_use_case("detailed_timestamps", frames, video_duration=60.0)
        
        assert "Analyze this video sequence" in prompt
    
    def test_get_detailed_tags_prompt(self):
        """Test getting detailed tags prompt with frames."""
        frames = [(0.0, Image.new("RGB", (100, 100)))]
        
        prompt = get_prompt_for_use_case("detailed_tags", frames, video_duration=60.0)
        
        assert "content classification" in prompt
    
    def test_get_comprehensive_prompt(self):
        """Test getting comprehensive prompt with frames."""
        frames = [(0.0, Image.new("RGB", (100, 100)))]
        
        prompt = get_prompt_for_use_case("comprehensive", frames, video_duration=60.0)
        
        assert "comprehensive analysis" in prompt.lower()
    
    def test_unknown_use_case_fallback(self):
        """Test fallback for unknown use case."""
        prompt = get_prompt_for_use_case("unknown_use_case")
        
        assert prompt == SIMPLE_ANALYSIS_PROMPT


class TestPredefinedPrompts:
    """Tests for predefined prompt constants."""
    
    def test_timestamp_extraction_prompt(self):
        """Test timestamp extraction prompt content."""
        assert "segments" in TIMESTAMP_EXTRACTION_PROMPT.lower()
        assert "JSON" in TIMESTAMP_EXTRACTION_PROMPT
        assert "tag_name" in TIMESTAMP_EXTRACTION_PROMPT
        assert "start_time" in TIMESTAMP_EXTRACTION_PROMPT
    
    def test_tag_extraction_prompt(self):
        """Test tag extraction prompt content."""
        assert "tags" in TAG_EXTRACTION_PROMPT.lower()
        assert "JSON" in TAG_EXTRACTION_PROMPT
        assert "confidence" in TAG_EXTRACTION_PROMPT
    
    def test_simple_analysis_prompt(self):
        """Test simple analysis prompt content."""
        assert "describe" in SIMPLE_ANALYSIS_PROMPT.lower()
        assert "brief" in SIMPLE_ANALYSIS_PROMPT.lower()
