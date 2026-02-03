"""Tests for VLM response parsing utilities."""

import json

import pytest

from haven_cli.vlm.parsing import (
    ResponseValidator,
    extract_json_from_text,
    filter_segments_by_confidence,
    filter_tags_by_confidence,
    merge_overlapping_segments,
    parse_content_tags,
    parse_timestamp_segments,
    parse_vlm_response,
    _normalize_tag_name,
    _attempt_json_repair,
)


class TestExtractJsonFromText:
    """Tests for JSON extraction from text."""
    
    def test_direct_json(self):
        """Test extracting direct JSON."""
        text = '{"key": "value", "number": 123}'
        result = extract_json_from_text(text)
        
        assert result == {"key": "value", "number": 123}
    
    def test_markdown_code_block(self):
        """Test extracting JSON from markdown code block."""
        text = '''```json
{"key": "value"}
```'''
        result = extract_json_from_text(text)
        
        assert result == {"key": "value"}
    
    def test_plain_code_block(self):
        """Test extracting JSON from plain code block."""
        text = '''```
{"key": "value"}
```'''
        result = extract_json_from_text(text)
        
        assert result == {"key": "value"}
    
    def test_json_in_text(self):
        """Test extracting JSON embedded in text."""
        text = 'Here is the result: {"key": "value"} That was it.'
        result = extract_json_from_text(text)
        
        assert result == {"key": "value"}
    
    def test_invalid_json(self):
        """Test handling invalid JSON."""
        text = "This is not JSON {invalid}"
        result = extract_json_from_text(text)
        
        assert result is None
    
    def test_empty_text(self):
        """Test handling empty text."""
        assert extract_json_from_text("") is None
        assert extract_json_from_text(None) is None


class TestAttemptJsonRepair:
    """Tests for JSON repair functionality."""
    
    def test_remove_trailing_commas(self):
        """Test removing trailing commas."""
        text = '{"key": "value",}'
        result = _attempt_json_repair(text)
        
        assert result is not None
        parsed = json.loads(result)
        assert parsed == {"key": "value"}
    
    def test_extract_json_object(self):
        """Test extracting JSON object from text."""
        text = 'prefix{"key": "value"}suffix'
        result = _attempt_json_repair(text)
        
        assert result is not None
        parsed = json.loads(result)
        assert parsed == {"key": "value"}


class TestParseTimestampSegments:
    """Tests for timestamp segment parsing."""
    
    def test_parse_segments_key(self):
        """Test parsing segments from 'segments' key."""
        data = {
            "segments": [
                {
                    "tag_name": "intro",
                    "start_time": 0.0,
                    "end_time": 10.0,
                    "confidence": 0.9,
                }
            ]
        }
        
        result = parse_timestamp_segments(data, 60.0)
        
        assert len(result) == 1
        assert result[0]["tag_name"] == "intro"
        assert result[0]["start_time"] == 0.0
        assert result[0]["end_time"] == 10.0
        assert result[0]["confidence"] == 0.9
    
    def test_parse_timestamps_key(self):
        """Test parsing from 'timestamps' key."""
        data = {
            "timestamps": [
                {"tag_name": "scene1", "start_time": 5.0, "confidence": 0.8}
            ]
        }
        
        result = parse_timestamp_segments(data, 60.0)
        
        assert len(result) == 1
        assert result[0]["tag_name"] == "scene1"
    
    def test_parse_tag_timespans_nested(self):
        """Test parsing nested tag_timespans structure."""
        data = {
            "video_tag_info": {
                "tag_timespans": {
                    "category1": {
                        "tag1": [
                            {"start": 0.0, "end": 10.0, "totalConfidence": 0.85}
                        ]
                    }
                }
            }
        }
        
        result = parse_timestamp_segments(data, 60.0)
        
        assert len(result) == 1
        assert result[0]["tag_name"] == "tag1"
        assert result[0]["confidence"] == 0.85
    
    def test_parse_alternative_keys(self):
        """Test parsing with alternative key names."""
        data = {
            "segments": [
                {
                    "tag": "intro",  # alternative to tag_name
                    "start": 0.0,     # alternative to start_time
                    "end": 10.0,      # alternative to end_time
                    "score": 0.9,     # alternative to confidence
                }
            ]
        }
        
        result = parse_timestamp_segments(data, 60.0)
        
        assert len(result) == 1
        assert result[0]["tag_name"] == "intro"
        assert result[0]["start_time"] == 0.0
        assert result[0]["end_time"] == 10.0
        assert result[0]["confidence"] == 0.9
    
    def test_parse_missing_required_fields(self):
        """Test parsing segment with missing required fields."""
        data = {
            "segments": [
                {"start_time": 0.0},  # Missing tag_name
                {"tag_name": "valid", "start_time": 5.0},  # Valid
            ]
        }
        
        result = parse_timestamp_segments(data, 60.0)
        
        assert len(result) == 1
        assert result[0]["tag_name"] == "valid"
    
    def test_parse_confidence_clamping(self):
        """Test that confidence is clamped to 0-1 range."""
        data = {
            "segments": [
                {"tag_name": "low", "start_time": 0.0, "confidence": -0.5},
                {"tag_name": "high", "start_time": 1.0, "confidence": 1.5},
                {"tag_name": "normal", "start_time": 2.0, "confidence": 0.5},
            ]
        }
        
        result = parse_timestamp_segments(data, 60.0)
        
        assert result[0]["confidence"] == 0.0  # Clamped to 0
        assert result[1]["confidence"] == 1.0  # Clamped to 1
        assert result[2]["confidence"] == 0.5  # Unchanged
    
    def test_time_validation(self):
        """Test that times are validated against video duration."""
        data = {
            "segments": [
                {"tag_name": "valid", "start_time": 0.0, "end_time": 50.0},
                {"tag_name": "overflow", "start_time": 70.0, "end_time": 100.0},
            ]
        }
        
        result = parse_timestamp_segments(data, 60.0)
        
        assert result[0]["start_time"] == 0.0
        assert result[0]["end_time"] == 50.0
        # Overflow times should be clamped to video duration
        # start_time (70) clamped to 60, end_time (100) clamped to 60
        # then end_time extended by 1 second since it equals start_time
        assert result[1]["start_time"] == 60.0
        assert result[1]["end_time"] == 61.0  # Extended to be > start_time


class TestParseContentTags:
    """Tests for content tag parsing."""
    
    def test_parse_tags_list(self):
        """Test parsing tags from list format."""
        data = {
            "tags": [
                {"name": "sports", "confidence": 0.95},
                {"name": "action", "confidence": 0.88},
            ]
        }
        
        result = parse_content_tags(data)
        
        assert result == {"sports": 0.95, "action": 0.88}
    
    def test_parse_tags_dict(self):
        """Test parsing tags from dictionary format."""
        data = {
            "tags": {
                "sports": 0.95,
                "action": 0.88,
            }
        }
        
        result = parse_content_tags(data)
        
        assert result == {"sports": 0.95, "action": 0.88}
    
    def test_parse_string_tags(self):
        """Test parsing string-only tags with default confidence."""
        data = {
            "tags": ["sports", "action", "entertainment"]
        }
        
        result = parse_content_tags(data)
        
        assert result["sports"] == 0.8
        assert result["action"] == 0.8
        assert result["entertainment"] == 0.8
    
    def test_parse_alternative_keys(self):
        """Test parsing with alternative key names."""
        data = {
            "categories": [
                {"label": "sports", "score": 0.95},
            ]
        }
        
        result = parse_content_tags(data)
        
        assert result["sports"] == 0.95


class TestFilterSegmentsByConfidence:
    """Tests for segment confidence filtering."""
    
    def test_filter_by_threshold(self):
        """Test filtering segments by confidence threshold."""
        segments = [
            {"tag_name": "high", "confidence": 0.9},
            {"tag_name": "medium", "confidence": 0.6},
            {"tag_name": "low", "confidence": 0.3},
        ]
        
        result = filter_segments_by_confidence(segments, threshold=0.5)
        
        assert len(result) == 2
        assert result[0]["tag_name"] == "high"
        assert result[1]["tag_name"] == "medium"
    
    def test_empty_segments(self):
        """Test filtering empty segment list."""
        result = filter_segments_by_confidence([], threshold=0.5)
        
        assert result == []


class TestFilterTagsByConfidence:
    """Tests for tag confidence filtering."""
    
    def test_filter_by_threshold(self):
        """Test filtering tags by confidence threshold."""
        tags = {
            "high": 0.9,
            "medium": 0.6,
            "low": 0.3,
        }
        
        result = filter_tags_by_confidence(tags, threshold=0.5)
        
        assert "high" in result
        assert "medium" in result
        assert "low" not in result
    
    def test_max_tags_limit(self):
        """Test limiting number of tags."""
        tags = {f"tag_{i}": 0.9 - (i * 0.05) for i in range(30)}
        
        result = filter_tags_by_confidence(tags, threshold=0.0, max_tags=10)
        
        assert len(result) == 10
        # Should keep highest confidence tags
        assert "tag_0" in result
        assert "tag_9" in result
        assert "tag_10" not in result


class TestMergeOverlappingSegments:
    """Tests for merging overlapping segments."""
    
    def test_merge_same_tag(self):
        """Test merging segments with same tag."""
        segments = [
            {"tag_name": "intro", "start_time": 0.0, "end_time": 10.0, "confidence": 0.9},
            {"tag_name": "intro", "start_time": 10.5, "end_time": 20.0, "confidence": 0.85},
        ]
        
        result = merge_overlapping_segments(segments, max_gap_seconds=1.0)
        
        assert len(result) == 1
        assert result[0]["start_time"] == 0.0
        assert result[0]["end_time"] == 20.0
        # Confidence should be averaged
        assert result[0]["confidence"] == 0.875
    
    def test_no_merge_different_tags(self):
        """Test that different tags are not merged."""
        segments = [
            {"tag_name": "intro", "start_time": 0.0, "end_time": 10.0, "confidence": 0.9},
            {"tag_name": "main", "start_time": 10.0, "end_time": 20.0, "confidence": 0.85},
        ]
        
        result = merge_overlapping_segments(segments, max_gap_seconds=1.0)
        
        assert len(result) == 2
    
    def test_no_merge_large_gap(self):
        """Test that segments with large gaps are not merged."""
        segments = [
            {"tag_name": "intro", "start_time": 0.0, "end_time": 10.0, "confidence": 0.9},
            {"tag_name": "intro", "start_time": 20.0, "end_time": 30.0, "confidence": 0.85},
        ]
        
        result = merge_overlapping_segments(segments, max_gap_seconds=1.0)
        
        assert len(result) == 2


class TestParseVlmResponse:
    """Tests for complete VLM response parsing."""
    
    def test_parse_complete_response(self):
        """Test parsing complete VLM response."""
        response_text = json.dumps({
            "segments": [
                {"tag_name": "intro", "start_time": 0.0, "end_time": 10.0, "confidence": 0.9}
            ],
            "tags": [
                {"name": "sports", "confidence": 0.95}
            ]
        })
        
        timestamps, tags = parse_vlm_response(response_text, 60.0)
        
        assert len(timestamps) == 1
        assert timestamps[0]["tag_name"] == "intro"
        assert tags["sports"] == 0.95
    
    def test_parse_invalid_json(self):
        """Test parsing invalid JSON response."""
        timestamps, tags = parse_vlm_response("not json", 60.0)
        
        assert timestamps == []
        assert tags == {}


class TestNormalizeTagName:
    """Tests for tag name normalization."""
    
    def test_lowercase(self):
        """Test conversion to lowercase."""
        assert _normalize_tag_name("SPORTS") == "sports"
    
    def test_underscore_replacement(self):
        """Test replacing spaces with underscores."""
        assert _normalize_tag_name("action scene") == "action_scene"
    
    def test_special_characters(self):
        """Test removing special characters."""
        assert _normalize_tag_name("sports!!!") == "sports"
        assert _normalize_tag_name("action@scene") == "actionscene"
    
    def test_empty_name(self):
        """Test handling empty names."""
        assert _normalize_tag_name("") == "unknown"
        assert _normalize_tag_name(None) == "unknown"


class TestResponseValidator:
    """Tests for ResponseValidator."""
    
    def test_validate_valid_segment(self):
        """Test validating a valid segment."""
        segment = {
            "tag_name": "intro",
            "start_time": 0.0,
            "end_time": 10.0,
            "confidence": 0.9,
        }
        
        is_valid, errors = ResponseValidator.validate_timestamp_segment(segment)
        
        assert is_valid
        assert errors == []
    
    def test_validate_missing_tag_name(self):
        """Test validating segment without tag_name."""
        segment = {"start_time": 0.0}
        
        is_valid, errors = ResponseValidator.validate_timestamp_segment(segment)
        
        assert not is_valid
        assert any("tag_name" in e.lower() for e in errors)
    
    def test_validate_invalid_confidence(self):
        """Test validating segment with invalid confidence."""
        segment = {
            "tag_name": "test",
            "start_time": 0.0,
            "confidence": 1.5,  # Out of range
        }
        
        is_valid, errors = ResponseValidator.validate_timestamp_segment(segment)
        
        assert not is_valid
        assert any("confidence" in e.lower() for e in errors)
    
    def test_validate_valid_tag(self):
        """Test validating a valid tag."""
        is_valid, errors = ResponseValidator.validate_tag("sports", 0.9)
        
        assert is_valid
        assert errors == []
    
    def test_validate_invalid_tag_confidence(self):
        """Test validating tag with invalid confidence."""
        is_valid, errors = ResponseValidator.validate_tag("sports", -0.5)
        
        assert not is_valid
        assert any("confidence" in e.lower() for e in errors)
