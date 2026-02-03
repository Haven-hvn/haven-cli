"""VLM analysis prompts for video content understanding.

This module contains prompts used to guide VLM models in analyzing
video frames and extracting structured information like timestamps
and content tags.
"""

from typing import List, Tuple
from datetime import timedelta


def build_timestamp_prompt(
    frames_with_timestamps: List[Tuple[float, "Image.Image"]],
    video_duration: float = 0.0,
    categories: List[str] | None = None,
) -> str:
    """Build a prompt for timestamp extraction.
    
    This prompt guides the VLM to identify semantic segments in the video
    and return them with start/end times and confidence scores.
    
    Args:
        frames_with_timestamps: List of (timestamp, frame) pairs
        video_duration: Total video duration in seconds
        categories: Optional list of category names to focus on
        
    Returns:
        Formatted prompt string
    """
    timestamps_str = "\n".join(
        f"Frame {i+1}: {format_timestamp(ts)}"
        for i, (ts, _) in enumerate(frames_with_timestamps)
    )
    
    default_categories = [
        "action", "dialogue", "transition", "montage",
        "introduction", "climax", "ending", "credits"
    ]
    
    categories_list = categories or default_categories
    categories_str = ", ".join(categories_list)
    
    prompt = f"""Analyze this video sequence and identify meaningful time segments.

Video Information:
- Total duration: {format_timestamp(video_duration) if video_duration else "Unknown"}
- Analyzed frames at: {len(frames_with_timestamps)} timestamps

Frame timestamps:
{timestamps_str}

Your task:
1. Identify segments with distinct content types, actions, or scenes
2. For each segment, provide:
   - A descriptive tag name (e.g., "opening_credits", "action_sequence", "dialogue_scene")
   - Start time in seconds (as a number)
   - End time in seconds (as a number, or null if unknown)
   - Confidence score (0.0 to 1.0)
   - Brief description of what's happening

Consider these content categories: {categories_str}

IMPORTANT: Respond ONLY with a valid JSON object in this exact format:
{{
  "segments": [
    {{
      "tag_name": "example_tag",
      "start_time": 0.0,
      "end_time": 15.5,
      "confidence": 0.95,
      "description": "Brief description of the segment"
    }}
  ]
}}

Guidelines:
- Use descriptive, lowercase tag names with underscores
- Times must be in seconds as floating point numbers
- Confidence should reflect how certain you are about the classification
- Segments can overlap if different categories apply
- Include at least one segment, even if the video is unclear
- If you cannot identify segments, return empty segments array
"""
    
    return prompt


def build_tag_extraction_prompt(
    frames_with_timestamps: List[Tuple[float, "Image.Image"]],
    video_duration: float = 0.0,
) -> str:
    """Build a prompt for content tag extraction.
    
    This prompt guides the VLM to classify the overall video content
    and return relevant tags with confidence scores.
    
    Args:
        frames_with_timestamps: List of (timestamp, frame) pairs
        video_duration: Total video duration in seconds
        
    Returns:
        Formatted prompt string
    """
    num_frames = len(frames_with_timestamps)
    
    prompt = f"""Analyze these {num_frames} frames from a video and provide content classification tags.

This is for overall video classification, not timestamp-specific analysis.

Your task:
1. Identify the main content type, genre, setting, mood, and subjects
2. Provide relevant tags with confidence scores

IMPORTANT: Respond ONLY with a valid JSON object in this exact format:
{{
  "tags": [
    {{
      "name": "tag_name",
      "confidence": 0.95,
      "category": "genre|setting|mood|content|subject|activity"
    }}
  ],
  "summary": "Brief summary of the video content (1-2 sentences)"
}}

Tag categories to consider:
- Genre: sports, news, entertainment, educational, documentary, comedy, drama, action
- Setting: indoor, outdoor, urban, rural, studio, natural
- Mood: energetic, calm, tense, humorous, serious, uplifting
- Content type: interview, tutorial, vlog, cinematic, animation, live_event
- Subjects: people, animals, nature, vehicles, buildings, technology
- Activities: talking, moving, dancing, working, playing, exploring

Guidelines:
- Tag names should be lowercase with underscores
- Confidence must be between 0.0 and 1.0
- Provide 5-15 relevant tags
- Include at least one tag from each applicable category
- Be specific but accurate (e.g., "basketball" not just "sports")
"""
    
    return prompt


def build_detailed_analysis_prompt(
    frames_with_timestamps: List[Tuple[float, "Image.Image"]],
    video_duration: float = 0.0,
    analysis_type: str = "comprehensive",
) -> str:
    """Build a prompt for detailed video analysis.
    
    This prompt combines timestamp and tag extraction into a single
    comprehensive analysis.
    
    Args:
        frames_with_timestamps: List of (timestamp, frame) pairs
        video_duration: Total video duration in seconds
        analysis_type: Type of analysis to perform
        
    Returns:
        Formatted prompt string
    """
    num_frames = len(frames_with_timestamps)
    
    if analysis_type == "comprehensive":
        return f"""Perform a comprehensive analysis of this video using {num_frames} sampled frames.

Your task is to extract both temporal segments AND content classification in a single response.

IMPORTANT: Respond ONLY with a valid JSON object in this exact format:
{{
  "video_info": {{
    "duration_estimate": {video_duration if video_duration else "null"},
    "frame_count_analyzed": {num_frames}
  }},
  "segments": [
    {{
      "tag_name": "segment_tag",
      "start_time": 0.0,
      "end_time": 30.0,
      "confidence": 0.95,
      "description": "Description of what happens in this segment"
    }}
  ],
  "tags": [
    {{
      "name": "content_tag",
      "confidence": 0.90,
      "category": "genre|setting|mood|subject"
    }}
  ],
  "summary": "Brief overall summary of the video"
}}

Guidelines for segments:
- Identify 3-10 distinct segments based on content changes
- Use descriptive, lowercase tag names with underscores
- Provide start and end times in seconds
- Include confidence scores (0.0-1.0)
- Segments can overlap if multiple categories apply

Guidelines for tags:
- Identify 5-15 content tags
- Include genre, setting, mood, subjects, and activities
- Confidence reflects certainty of classification
- Be specific but accurate
"""
    
    elif analysis_type == "action_detection":
        return f"""Analyze this video for action detection using {num_frames} sampled frames.

Focus specifically on identifying action sequences, movement patterns, and dynamic events.

IMPORTANT: Respond ONLY with a valid JSON object in this exact format:
{{
  "action_segments": [
    {{
      "tag_name": "action_description",
      "start_time": 10.5,
      "end_time": 25.0,
      "confidence": 0.88,
      "intensity": "low|medium|high",
      "participants": ["person", "vehicle", "animal", etc]
    }}
  ],
  "activity_tags": [
    {{
      "name": "activity_type",
      "confidence": 0.92,
      "frequency": "continuous|intermittent|rare"
    }}
  ]
}}

Guidelines:
- Focus on ACTION verbs (running, fighting, dancing, driving)
- Include intensity level for each segment
- Identify participants in the action
- Note frequency of recurring activities
"""
    
    else:
        return build_timestamp_prompt(frames_with_timestamps, video_duration)


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS or MM:SS.
    
    Args:
        seconds: Time in seconds
        
    Returns:
        Formatted timestamp string
    """
    if seconds < 0:
        return "00:00"
    
    td = timedelta(seconds=int(seconds))
    hours = td.seconds // 3600
    minutes = (td.seconds % 3600) // 60
    secs = td.seconds % 60
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes:02d}:{secs:02d}"


# Predefined prompt templates for common use cases

TIMESTAMP_EXTRACTION_PROMPT = """Analyze these video frames and identify meaningful time segments.

For each distinct scene, action, or content type you identify, provide:
- tag_name: A descriptive label (lowercase, underscores)
- start_time: When the segment starts (in seconds)
- end_time: When the segment ends (in seconds, or null)
- confidence: Your certainty (0.0 to 1.0)
- description: Brief explanation of what's happening

Respond with JSON only:
{
  "segments": [
    {
      "tag_name": "example",
      "start_time": 0.0,
      "end_time": 15.5,
      "confidence": 0.95,
      "description": "Description here"
    }
  ]
}"""

TAG_EXTRACTION_PROMPT = """Analyze these video frames and provide content classification tags.

Consider:
- Genre (sports, news, entertainment, documentary, etc.)
- Setting (indoor, outdoor, urban, natural, etc.)
- Mood (energetic, calm, serious, humorous, etc.)
- Subjects (people, animals, vehicles, nature, etc.)
- Activities (talking, moving, playing, working, etc.)

Respond with JSON only:
{
  "tags": [
    {
      "name": "tag_name",
      "confidence": 0.95,
      "category": "genre|setting|mood|subject|activity"
    }
  ],
  "summary": "Brief description of the video"
}"""

SIMPLE_ANALYSIS_PROMPT = """Look at these frames from a video and describe:
1. What is the main subject or focus?
2. What type of content is this? (genre/category)
3. What is happening in the video?

Keep your response brief and focused."""


def get_prompt_for_use_case(
    use_case: str,
    frames_with_timestamps: List[Tuple[float, "Image.Image"]] | None = None,
    **kwargs,
) -> str:
    """Get a predefined prompt for a specific use case.
    
    Args:
        use_case: Name of the use case
        frames_with_timestamps: Optional frame data for context
        **kwargs: Additional parameters for prompt building
        
    Returns:
        Prompt string
    """
    prompts = {
        "timestamps": TIMESTAMP_EXTRACTION_PROMPT,
        "tags": TAG_EXTRACTION_PROMPT,
        "simple": SIMPLE_ANALYSIS_PROMPT,
    }
    
    if use_case in prompts:
        return prompts[use_case]
    
    # Build dynamic prompt if frames are provided
    if frames_with_timestamps:
        if use_case == "detailed_timestamps":
            return build_timestamp_prompt(
                frames_with_timestamps,
                kwargs.get("video_duration", 0.0),
                kwargs.get("categories"),
            )
        elif use_case == "detailed_tags":
            return build_tag_extraction_prompt(
                frames_with_timestamps,
                kwargs.get("video_duration", 0.0),
            )
        elif use_case == "comprehensive":
            return build_detailed_analysis_prompt(
                frames_with_timestamps,
                kwargs.get("video_duration", 0.0),
                kwargs.get("analysis_type", "comprehensive"),
            )
    
    # Default to simple prompt
    return SIMPLE_ANALYSIS_PROMPT
