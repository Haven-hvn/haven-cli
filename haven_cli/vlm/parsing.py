"""Response parsing utilities for VLM analysis.

This module provides functions to parse and validate VLM responses,
converting them into standardized formats for timestamps and tags.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Extract JSON object from text that may contain markdown or other content.
    
    Args:
        text: Raw text potentially containing JSON
        
    Returns:
        Parsed JSON dictionary or None if extraction fails
    """
    if not text or not isinstance(text, str):
        return None
    
    text = text.strip()
    
    # Try direct JSON parsing first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # Try to extract JSON from markdown code blocks
    patterns = [
        r"```(?:json)?\s*([\s\S]*?)```",  # Markdown code blocks
        r"```\s*([\s\S]*?)```",  # Any code block
        r"\{[\s\S]*\}",  # JSON-like structure
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            try:
                # If match is from code block, it might be the JSON directly
                if pattern.startswith("```"):
                    result = json.loads(match.strip())
                else:
                    # For raw JSON pattern, use the match as-is
                    result = json.loads(match)
                return result
            except json.JSONDecodeError:
                continue
    
    # Try to find and fix common JSON issues
    fixed_text = _attempt_json_repair(text)
    if fixed_text:
        try:
            return json.loads(fixed_text)
        except json.JSONDecodeError:
            pass
    
    return None


def _attempt_json_repair(text: str) -> Optional[str]:
    """Attempt to repair common JSON formatting issues.
    
    Args:
        text: Potentially malformed JSON string
        
    Returns:
        Repaired JSON string or None if repair fails
    """
    # Remove leading/trailing whitespace
    text = text.strip()
    
    # Find the first { and last }
    start = text.find("{")
    end = text.rfind("}")
    
    if start == -1 or end == -1 or start >= end:
        return None
    
    # Extract just the JSON object
    json_text = text[start:end+1]
    
    # Fix single quotes to double quotes (common mistake)
    # But be careful not to change quotes inside strings
    # This is a simplified fix - may not work for all cases
    
    # Fix trailing commas (not allowed in JSON)
    json_text = re.sub(r",(\s*[}\]])", r"\1", json_text)
    
    return json_text


def parse_timestamp_segments(
    data: Dict[str, Any],
    video_duration: float = 0.0,
) -> List[Dict[str, Any]]:
    """Parse timestamp segments from VLM response.
    
    Args:
        data: Parsed JSON response
        video_duration: Total video duration for validation
        
    Returns:
        List of timestamp dictionaries
    """
    segments: List[Dict[str, Any]] = []
    
    # Try different possible keys for segments at root level
    segment_keys = ["segments", "timestamps", "time_segments", "scenes", "tag_timespans"]
    raw_segments: List[Any] = []
    
    for key in segment_keys:
        if key in data:
            value = data[key]
            if isinstance(value, list):
                raw_segments = value
                break
            elif isinstance(value, dict):
                # Handle nested structure like tag_timespans
                for category, tags in value.items():
                    if isinstance(tags, dict):
                        for tag_name, timespans in tags.items():
                            if isinstance(timespans, list):
                                for ts in timespans:
                                    if isinstance(ts, dict):
                                        raw_segments.append({
                                            **ts,
                                            "tag_name": tag_name,
                                            "category": category,
                                        })
                break
    
    # Also check for nested structures like video_tag_info.tag_timespans
    if not raw_segments and "video_tag_info" in data:
        video_tag_info = data["video_tag_info"]
        if isinstance(video_tag_info, dict) and "tag_timespans" in video_tag_info:
            tag_timespans = video_tag_info["tag_timespans"]
            if isinstance(tag_timespans, dict):
                for category, tags in tag_timespans.items():
                    if isinstance(tags, dict):
                        for tag_name, timespans in tags.items():
                            if isinstance(timespans, list):
                                for ts in timespans:
                                    if isinstance(ts, dict):
                                        raw_segments.append({
                                            **ts,
                                            "tag_name": tag_name,
                                            "category": category,
                                        })
    
    for segment in raw_segments:
        if not isinstance(segment, dict):
            continue
        
        parsed = _parse_single_segment(segment, video_duration)
        if parsed:
            segments.append(parsed)
    
    # Sort by start time
    segments.sort(key=lambda x: x.get("start_time", 0))
    
    return segments


def _parse_single_segment(
    segment: Dict[str, Any],
    video_duration: float,
) -> Optional[Dict[str, Any]]:
    """Parse a single segment dictionary.
    
    Args:
        segment: Raw segment data
        video_duration: Video duration for validation
        
    Returns:
        Parsed segment or None if invalid
    """
    # Extract tag name (try multiple possible keys)
    tag_name = _extract_string_field(segment, ["tag_name", "tag", "label", "name", "type"])
    if not tag_name:
        return None
    
    # Normalize tag name
    tag_name = _normalize_tag_name(tag_name)
    
    # Extract start time
    start_time = _extract_number_field(segment, ["start_time", "start", "begin", "timestamp"])
    if start_time is None:
        start_time = 0.0
    
    # Extract end time
    end_time = _extract_number_field(segment, ["end_time", "end", "finish"])
    
    # Extract confidence
    confidence = _extract_number_field(segment, ["confidence", "score", "certainty", "totalConfidence", "total_confidence"])
    if confidence is None:
        confidence = 0.5
    
    # Clamp confidence to 0-1 range
    confidence = max(0.0, min(1.0, confidence))
    
    # Extract description
    description = _extract_string_field(segment, ["description", "desc", "details", "note"])
    
    # Validate times
    if video_duration > 0:
        if start_time > video_duration:
            start_time = video_duration
        if end_time is not None and end_time > video_duration:
            end_time = video_duration
    
    # Ensure end_time > start_time
    if end_time is not None and end_time <= start_time:
        end_time = start_time + 1.0  # Default 1 second duration
    
    result: Dict[str, Any] = {
        "tag_name": tag_name,
        "start_time": float(start_time),
        "confidence": float(confidence),
    }
    
    if end_time is not None:
        result["end_time"] = float(end_time)
    
    if description:
        result["description"] = description
    
    return result


def parse_content_tags(data: Dict[str, Any]) -> Dict[str, float]:
    """Parse content tags from VLM response.
    
    Args:
        data: Parsed JSON response
        
    Returns:
        Dictionary mapping tag names to confidence scores
    """
    tags: Dict[str, float] = {}
    
    # Try different possible keys for tags
    tag_keys = ["tags", "categories", "labels", "classifications"]
    raw_tags: List[Any] = []
    
    for key in tag_keys:
        if key in data:
            value = data[key]
            if isinstance(value, list):
                raw_tags = value
                break
            elif isinstance(value, dict):
                # Handle dictionary format {tag: confidence}
                for tag_name, confidence in value.items():
                    if isinstance(confidence, (int, float)):
                        tags[_normalize_tag_name(tag_name)] = float(confidence)
                return tags
    
    for tag in raw_tags:
        if isinstance(tag, str):
            # Simple string tag, default confidence
            tags[_normalize_tag_name(tag)] = 0.8
        elif isinstance(tag, dict):
            # Structured tag object
            tag_name = _extract_string_field(tag, ["name", "tag", "label"])
            confidence = _extract_number_field(tag, ["confidence", "score", "certainty"])
            
            if tag_name:
                confidence = confidence if confidence is not None else 0.8
                confidence = max(0.0, min(1.0, confidence))
                tags[_normalize_tag_name(tag_name)] = float(confidence)
    
    return tags


def _extract_string_field(data: Dict[str, Any], keys: List[str]) -> Optional[str]:
    """Extract a string value from a dictionary using multiple possible keys.
    
    Args:
        data: Dictionary to search
        keys: List of possible keys
        
    Returns:
        String value or None
    """
    for key in keys:
        if key in data and data[key] is not None:
            value = data[key]
            if isinstance(value, str):
                return value.strip()
    return None


def _extract_number_field(data: Dict[str, Any], keys: List[str]) -> Optional[float]:
    """Extract a numeric value from a dictionary using multiple possible keys.
    
    Args:
        data: Dictionary to search
        keys: List of possible keys
        
    Returns:
        Numeric value or None
    """
    for key in keys:
        if key in data and data[key] is not None:
            value = data[key]
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value)
                except ValueError:
                    pass
    return None


def _normalize_tag_name(name: str) -> str:
    """Normalize a tag name for consistent storage.
    
    Args:
        name: Raw tag name
        
    Returns:
        Normalized tag name
    """
    if not name:
        return "unknown"
    
    # Convert to lowercase
    name = name.lower().strip()
    
    # Replace spaces with underscores
    name = name.replace(" ", "_")
    
    # Remove special characters except underscores and hyphens
    name = re.sub(r"[^a-z0-9_\-]", "", name)
    
    # Remove leading/trailing underscores
    name = name.strip("_")
    
    return name or "unknown"


def parse_vlm_response(
    response_text: str,
    video_duration: float = 0.0,
) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
    """Parse a complete VLM response into timestamps and tags.
    
    Args:
        response_text: Raw VLM response text
        video_duration: Video duration for validation
        
    Returns:
        Tuple of (timestamps list, tags dictionary)
    """
    # Extract JSON from response
    data = extract_json_from_text(response_text)
    
    if data is None:
        logger.warning("Could not extract JSON from VLM response")
        return [], {}
    
    # Parse timestamps
    timestamps = parse_timestamp_segments(data, video_duration)
    
    # Parse tags
    tags = parse_content_tags(data)
    
    return timestamps, tags


def merge_overlapping_segments(
    segments: List[Dict[str, Any]],
    max_gap_seconds: float = 1.0,
) -> List[Dict[str, Any]]:
    """Merge overlapping or closely adjacent segments with the same tag.
    
    Args:
        segments: List of segment dictionaries
        max_gap_seconds: Maximum gap to consider for merging
        
    Returns:
        Merged segments list
    """
    if not segments:
        return []
    
    # Sort by start time
    sorted_segments = sorted(segments, key=lambda x: (x.get("tag_name", ""), x.get("start_time", 0)))
    
    merged: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    
    for segment in sorted_segments:
        if current is None:
            current = segment.copy()
            continue
        
        same_tag = current.get("tag_name") == segment.get("tag_name")
        current_end = current.get("end_time") or current.get("start_time", 0)
        next_start = segment.get("start_time", 0)
        gap = next_start - current_end
        
        if same_tag and gap <= max_gap_seconds:
            # Merge segments
            current["end_time"] = segment.get("end_time") or segment.get("start_time", 0)
            # Average confidence
            current_conf = current.get("confidence", 0.5)
            next_conf = segment.get("confidence", 0.5)
            current["confidence"] = (current_conf + next_conf) / 2
        else:
            merged.append(current)
            current = segment.copy()
    
    if current:
        merged.append(current)
    
    return merged


def filter_segments_by_confidence(
    segments: List[Dict[str, Any]],
    threshold: float = 0.5,
) -> List[Dict[str, Any]]:
    """Filter segments by confidence threshold.
    
    Args:
        segments: List of segment dictionaries
        threshold: Minimum confidence (0-1)
        
    Returns:
        Filtered segments list
    """
    return [
        seg for seg in segments
        if seg.get("confidence", 0) >= threshold
    ]


def filter_tags_by_confidence(
    tags: Dict[str, float],
    threshold: float = 0.3,
    max_tags: int = 20,
) -> Dict[str, float]:
    """Filter tags by confidence and limit count.
    
    Args:
        tags: Dictionary of tag names to confidence scores
        threshold: Minimum confidence
        max_tags: Maximum number of tags to return
        
    Returns:
        Filtered tags dictionary
    """
    # Filter by threshold
    filtered = {name: conf for name, conf in tags.items() if conf >= threshold}
    
    # Sort by confidence and limit
    sorted_tags = sorted(filtered.items(), key=lambda x: x[1], reverse=True)
    
    return dict(sorted_tags[:max_tags])


class ResponseValidator:
    """Validator for VLM responses."""
    
    @staticmethod
    def validate_timestamp_segment(segment: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate a timestamp segment.
        
        Args:
            segment: Segment dictionary to validate
            
        Returns:
            Tuple of (is_valid, list of error messages)
        """
        errors: List[str] = []
        
        # Check required fields
        if "tag_name" not in segment or not segment["tag_name"]:
            errors.append("Missing or empty tag_name")
        
        if "start_time" not in segment:
            errors.append("Missing start_time")
        elif not isinstance(segment["start_time"], (int, float)):
            errors.append("start_time must be a number")
        elif segment["start_time"] < 0:
            errors.append("start_time cannot be negative")
        
        # Check optional fields
        if "end_time" in segment:
            end_time = segment["end_time"]
            if not isinstance(end_time, (int, float)):
                errors.append("end_time must be a number")
            elif "start_time" in segment and end_time <= segment["start_time"]:
                errors.append("end_time must be greater than start_time")
        
        if "confidence" in segment:
            conf = segment["confidence"]
            if not isinstance(conf, (int, float)):
                errors.append("confidence must be a number")
            elif not 0 <= conf <= 1:
                errors.append("confidence must be between 0 and 1")
        
        return len(errors) == 0, errors
    
    @staticmethod
    def validate_tag(name: str, confidence: float) -> Tuple[bool, List[str]]:
        """Validate a content tag.
        
        Args:
            name: Tag name
            confidence: Confidence score
            
        Returns:
            Tuple of (is_valid, list of error messages)
        """
        errors: List[str] = []
        
        if not name or not isinstance(name, str):
            errors.append("Tag name must be a non-empty string")
        
        if not isinstance(confidence, (int, float)):
            errors.append("Confidence must be a number")
        elif not 0 <= confidence <= 1:
            errors.append("Confidence must be between 0 and 1")
        
        return len(errors) == 0, errors
