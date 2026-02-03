"""VLM video processing service.

This module provides high-level video processing functionality using VLM engines,
adapted from the backend VLM processor for CLI use.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from haven_cli.vlm.engine import VLMEngine, create_vlm_engine, AnalysisConfig
from haven_cli.vlm.prompts import build_timestamp_prompt, build_tag_extraction_prompt
from haven_cli.vlm.parsing import (
    parse_vlm_response,
    filter_segments_by_confidence,
    filter_tags_by_confidence,
    merge_overlapping_segments,
)
from haven_cli.vlm.config import (
    load_vlm_config,
    get_engine_config,
    create_analysis_config,
    VLMConfig,
)

logger = logging.getLogger(__name__)


class VLMProcessor:
    """Video processing service using VLM engines.
    
    This class handles the complete VLM processing pipeline:
    1. Frame sampling from video
    2. VLM analysis with prompts
    3. Response parsing and validation
    4. Result formatting and saving
    
    Example:
        >>> processor = VLMProcessor()
        >>> results = await processor.process_video(Path("video.mp4"))
        >>> print(results["tags"])
        {'sports': 0.95, 'action': 0.88}
    """
    
    def __init__(
        self,
        engine: Optional[VLMEngine] = None,
        config: Optional[VLMConfig] = None,
    ):
        """Initialize the VLM processor.
        
        Args:
            engine: Optional pre-configured VLM engine
            config: Optional VLM configuration (loads from global if not provided)
        """
        self.config = config or load_vlm_config()
        self.engine = engine
        self._initialized = False
    
    async def initialize(self) -> None:
        """Initialize the processor and engine."""
        if self._initialized:
            return
        
        # Create engine if not provided
        if self.engine is None:
            engine_config = get_engine_config(self.config)
            analysis_config = create_analysis_config(self.config)
            
            self.engine = create_vlm_engine(
                model=engine_config.model_name,
                api_key=engine_config.api_key,
                base_url=engine_config.base_url,
                config=analysis_config,
            )
        
        # Initialize the engine
        if self.engine:
            await self.engine.initialize()
        
        self._initialized = True
        logger.info("VLM processor initialized")
    
    async def close(self) -> None:
        """Close the processor and release resources."""
        if self.engine:
            if hasattr(self.engine, 'close'):
                await self.engine.close()
        
        self._initialized = False
    
    async def process_video(
        self,
        video_path: Path | str,
        progress_callback: Optional[Callable[[int], None]] = None,
        frame_count: Optional[int] = None,
        threshold: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Process a video with VLM analysis.
        
        Args:
            video_path: Path to the video file
            progress_callback: Optional callback(progress_percent) for progress updates
            frame_count: Override frame count from config
            threshold: Override confidence threshold from config
            
        Returns:
            Dictionary containing analysis results:
            {
                "video_path": str,
                "timestamps": List[Dict],
                "tags": Dict[str, float],
                "confidence": float,
                "summary": str,
            }
            
        Raises:
            FileNotFoundError: If video file doesn't exist
            RuntimeError: If VLM analysis fails
        """
        video_path = Path(video_path)
        
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")
        
        if not self._initialized:
            await self.initialize()
        
        if not self.engine:
            raise RuntimeError("VLM engine not available")
        
        # Use config values for overrides
        frame_count = frame_count or self.config.processing.frame_count
        threshold = threshold or self.config.processing.threshold
        
        logger.info(f"Starting VLM processing for: {video_path}")
        
        if progress_callback:
            progress_callback(10)
        
        # Sample frames from video
        try:
            frames_with_ts = await self.engine.sample_frames(
                video_path,
                strategy="uniform",
                count=frame_count,
            )
        except Exception as e:
            logger.error(f"Failed to sample frames from {video_path}: {e}")
            raise RuntimeError(f"Frame sampling failed: {e}") from e
        
        if not frames_with_ts:
            logger.warning(f"No frames could be extracted from {video_path}")
            return {
                "video_path": str(video_path),
                "timestamps": [],
                "tags": {},
                "confidence": 0.0,
                "summary": "No frames could be extracted",
            }
        
        logger.info(f"Sampled {len(frames_with_ts)} frames for analysis")
        
        if progress_callback:
            progress_callback(30)
        
        # Get video duration for context
        from haven_cli.media.metadata import extract_video_duration
        video_duration = await extract_video_duration(video_path)
        
        # Extract timestamps
        timestamps: List[Dict[str, Any]] = []
        tags: Dict[str, float] = {}
        summary = ""
        
        try:
            timestamps = await self._extract_timestamps(
                frames_with_ts,
                video_duration,
                threshold,
            )
            
            if progress_callback:
                progress_callback(60)
            
            # Extract tags
            tags = await self._extract_tags(
                frames_with_ts,
                video_duration,
                threshold,
            )
            
            if progress_callback:
                progress_callback(80)
            
        except Exception as e:
            logger.error(f"VLM analysis failed for {video_path}: {e}")
            raise RuntimeError(f"VLM analysis failed: {e}") from e
        
        # Calculate overall confidence
        confidence = self._calculate_confidence(timestamps, tags)
        
        # Generate summary if we have results
        if timestamps or tags:
            summary = self._generate_summary(timestamps, tags, video_duration)
        
        # Build results
        results = {
            "video_path": str(video_path),
            "timestamps": timestamps,
            "tags": tags,
            "confidence": confidence,
            "summary": summary,
            "video_duration": video_duration,
            "frame_count": len(frames_with_ts),
        }
        
        # Save to .AI.json file if enabled
        if self.config.processing.save_to_file:
            await self._save_results_to_file(video_path, results)
        
        if progress_callback:
            progress_callback(100)
        
        logger.info(
            f"Completed VLM processing for {video_path}: "
            f"{len(timestamps)} timestamps, {len(tags)} tags, "
            f"confidence={confidence:.2f}"
        )
        
        return results
    
    async def _extract_timestamps(
        self,
        frames_with_ts: List[Tuple[float, Any]],
        video_duration: float,
        threshold: float,
    ) -> List[Dict[str, Any]]:
        """Extract timestamps from video frames.
        
        Args:
            frames_with_ts: List of (timestamp, frame) pairs
            video_duration: Total video duration
            threshold: Confidence threshold
            
        Returns:
            List of timestamp dictionaries
        """
        if not self.config.processing.return_timestamps:
            return []
        
        if not self.engine:
            return []
        
        # Build prompt
        prompt = build_timestamp_prompt(frames_with_ts, video_duration)
        
        # Extract frames
        frames = [frame for _, frame in frames_with_ts]
        
        # Call VLM
        response = await self.engine.analyze_frames(frames, prompt)
        
        # Parse response
        timestamps, _ = parse_vlm_response(
            json.dumps(response.parsed_result),
            video_duration,
        )
        
        # Filter by confidence
        timestamps = filter_segments_by_confidence(timestamps, threshold)
        
        # Merge overlapping segments with same tag
        timestamps = merge_overlapping_segments(timestamps, max_gap_seconds=1.0)
        
        return timestamps
    
    async def _extract_tags(
        self,
        frames_with_ts: List[Tuple[float, Any]],
        video_duration: float,
        threshold: float,
    ) -> Dict[str, float]:
        """Extract content tags from video frames.
        
        Args:
            frames_with_ts: List of (timestamp, frame) pairs
            video_duration: Total video duration
            threshold: Confidence threshold
            
        Returns:
            Dictionary mapping tag names to confidence scores
        """
        if not self.config.processing.return_confidence:
            return {}
        
        if not self.engine:
            return {}
        
        # Use a subset of frames for tag extraction (fewer frames = faster/cheaper)
        # Take first, middle, and last frames
        if len(frames_with_ts) > 5:
            indices = [0, len(frames_with_ts) // 4, len(frames_with_ts) // 2, 
                      3 * len(frames_with_ts) // 4, len(frames_with_ts) - 1]
            tag_frames = [frames_with_ts[i] for i in indices]
        else:
            tag_frames = frames_with_ts
        
        # Build prompt
        prompt = build_tag_extraction_prompt(tag_frames, video_duration)
        
        # Extract frames
        frames = [frame for _, frame in tag_frames]
        
        # Call VLM
        response = await self.engine.analyze_frames(frames, prompt)
        
        # Parse response
        _, tags = parse_vlm_response(
            json.dumps(response.parsed_result),
            video_duration,
        )
        
        # Filter by confidence
        tags = filter_tags_by_confidence(tags, threshold, max_tags=20)
        
        return tags
    
    def _calculate_confidence(
        self,
        timestamps: List[Dict[str, Any]],
        tags: Dict[str, float],
    ) -> float:
        """Calculate overall analysis confidence.
        
        Args:
            timestamps: List of timestamp dictionaries
            tags: Dictionary of tags with confidence scores
            
        Returns:
            Overall confidence score (0-1)
        """
        confidences: List[float] = []
        
        # Collect timestamp confidences
        for ts in timestamps:
            if "confidence" in ts:
                confidences.append(ts["confidence"])
        
        # Collect tag confidences
        confidences.extend(tags.values())
        
        if not confidences:
            return 0.0
        
        return sum(confidences) / len(confidences)
    
    def _generate_summary(
        self,
        timestamps: List[Dict[str, Any]],
        tags: Dict[str, float],
        video_duration: float,
    ) -> str:
        """Generate a brief summary of the analysis.
        
        Args:
            timestamps: List of timestamp dictionaries
            tags: Dictionary of tags with confidence scores
            video_duration: Video duration in seconds
            
        Returns:
            Summary string
        """
        parts: List[str] = []
        
        # Add tag information
        if tags:
            top_tags = sorted(tags.items(), key=lambda x: x[1], reverse=True)[:3]
            tag_str = ", ".join(name.replace("_", " ") for name, _ in top_tags)
            parts.append(f"Content: {tag_str}")
        
        # Add segment count
        if timestamps:
            parts.append(f"{len(timestamps)} segments identified")
        
        # Add duration info
        if video_duration > 0:
            minutes = int(video_duration // 60)
            seconds = int(video_duration % 60)
            parts.append(f"Duration: {minutes}:{seconds:02d}")
        
        return " | ".join(parts) if parts else "Analysis complete"
    
    async def _save_results_to_file(
        self,
        video_path: Path,
        results: Dict[str, Any],
    ) -> None:
        """Save results to .AI.json file.
        
        Args:
            video_path: Path to the video file
            results: Analysis results dictionary
        """
        try:
            output_path = video_path.with_suffix(video_path.suffix + ".AI.json")
            
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Saved VLM results to: {output_path}")
            
        except Exception as e:
            logger.warning(f"Failed to save results to file: {e}")
    
    async def process_video_with_fallback(
        self,
        video_path: Path | str,
        fallback_enabled: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        """Process video with fallback to empty results on error.
        
        Args:
            video_path: Path to video file
            fallback_enabled: Whether to return empty results on error
            **kwargs: Additional arguments for process_video
            
        Returns:
            Analysis results or empty fallback results
        """
        try:
            return await self.process_video(video_path, **kwargs)
        except Exception as e:
            logger.error(f"VLM processing failed: {e}")
            
            if not fallback_enabled:
                raise
            
            # Return empty fallback results
            return {
                "video_path": str(video_path),
                "timestamps": [],
                "tags": {},
                "confidence": 0.0,
                "summary": f"Analysis failed: {str(e)}",
                "error": str(e),
            }


async def process_video(
    video_path: Path | str,
    progress_callback: Optional[Callable[[int], None]] = None,
    config: Optional[VLMConfig] = None,
) -> Dict[str, Any]:
    """Convenience function to process a video with VLM.
    
    Args:
        video_path: Path to video file
        progress_callback: Optional progress callback
        config: Optional VLM configuration
        
    Returns:
        Analysis results dictionary
        
    Example:
        >>> results = await process_video("video.mp4")
        >>> print(results["tags"])
    """
    processor = VLMProcessor(config=config)
    
    try:
        return await processor.process_video(video_path, progress_callback)
    finally:
        await processor.close()


def save_results_to_db(
    video_path: Path | str,
    results: Dict[str, Any],
    video_id: Optional[int] = None,
) -> int:
    """Save VLM results to database.
    
    Args:
        video_path: Path to video file
        results: Analysis results from VLM processing
        video_id: Optional video ID (looks up by path if not provided)
        
    Returns:
        Number of timestamps saved
        
    Raises:
        ValueError: If video not found in database
    """
    from haven_cli.database.connection import get_db_session
    from haven_cli.database.models import Timestamp
    from haven_cli.database.repositories import VideoRepository
    
    video_path = Path(video_path)
    timestamp_count = 0
    
    with get_db_session() as session:
        # Get video
        if video_id is None:
            video_repo = VideoRepository(session)
            video = video_repo.get_by_source_path(str(video_path))
            if not video:
                raise ValueError(f"Video not found in database: {video_path}")
            video_id = video.id
        
        # Clear existing timestamps for this video
        session.query(Timestamp).filter(Timestamp.video_id == video_id).delete()
        
        # Add new timestamps
        timestamps = results.get("timestamps", [])
        for ts_data in timestamps:
            timestamp = Timestamp(
                video_id=video_id,
                tag_name=ts_data.get("tag_name", "unknown"),
                start_time=ts_data.get("start_time", 0.0),
                end_time=ts_data.get("end_time"),
                confidence=ts_data.get("confidence", 0.5),
            )
            session.add(timestamp)
            timestamp_count += 1
        
        # Update video has_ai_data flag
        if video := session.query(VideoRepository).filter_by(id=video_id).first():
            video.has_ai_data = True  # type: ignore
        
        session.commit()
        
        logger.info(
            f"Saved {timestamp_count} timestamps to database for video {video_id}"
        )
    
    return timestamp_count


# Import Video at the end to avoid circular imports
from haven_cli.database.models import Video  # noqa: E402
