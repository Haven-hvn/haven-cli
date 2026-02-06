"""Analyze step - VLM (Visual Language Model) video analysis.

This step performs AI-powered analysis of video content using
Visual Language Models to extract:
- Timestamps with semantic tags
- Content classification tags
- Confidence scores

The step is conditional and can be skipped via the vlm_enabled option.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from haven_cli.pipeline.context import AIAnalysisResult, PipelineContext
from haven_cli.pipeline.events import EventType
from haven_cli.pipeline.results import StepError, StepResult
from haven_cli.pipeline.step import ConditionalStep

# VLM imports
from haven_cli.vlm import (
    VLMProcessor,
    load_vlm_config,
    parse_vlm_response,
    save_results_to_db,
)
from haven_cli.vlm.config import VLMConfig, validate_vlm_config

logger = logging.getLogger(__name__)


class AnalyzeStep(ConditionalStep):
    """Pipeline step for VLM video analysis.
    
    This step uses Visual Language Models to analyze video content
    and extract semantic information. It can be skipped if VLM
    analysis is disabled in the pipeline options.
    
    Emits:
        - ANALYSIS_REQUESTED event when starting
        - ANALYSIS_COMPLETE event on success
        - ANALYSIS_FAILED event on failure
    
    Output data:
        - timestamps: List of tagged timestamps
        - tags: Dictionary of content tags with confidence
        - confidence: Overall analysis confidence score
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the analyze step.
        
        Args:
            config: Step configuration (passed to base class)
        """
        super().__init__(config=config)
        self._processor: Optional[VLMProcessor] = None
        self._vlm_config: Optional[VLMConfig] = None
    
    @property
    def name(self) -> str:
        """Step identifier."""
        return "analyze"
    
    @property
    def enabled_option(self) -> str:
        """Context option that enables this step."""
        return "vlm_enabled"
    
    @property
    def default_enabled(self) -> bool:
        """VLM analysis is disabled by default."""
        return False
    
    async def process(self, context: PipelineContext) -> StepResult:
        """Process VLM analysis.
        
        Args:
            context: Pipeline context with video metadata
            
        Returns:
            StepResult with analysis data
        """
        video_path = context.video_path
        
        # Emit analysis requested event
        await self._emit_event(EventType.ANALYSIS_REQUESTED, context, {
            "video_path": video_path,
        })
        
        try:
            # Load and validate VLM configuration
            self._vlm_config = load_vlm_config()
            
            # Check if VLM is properly configured
            validation_errors = validate_vlm_config(self._vlm_config)
            config_warnings = [e for e in validation_errors if "API key" in e]
            config_errors = [e for e in validation_errors if e not in config_warnings]
            
            if config_errors:
                error_msg = "; ".join(config_errors)
                logger.error(f"VLM configuration error: {error_msg}")
                return StepResult.fail(
                    self.name,
                    StepError(
                        code="VLM_CONFIG_ERROR",
                        message=error_msg,
                        details={"errors": config_errors},
                    ),
                )
            
            if config_warnings:
                for warning in config_warnings:
                    logger.warning(warning)
            
            # Check if processing is enabled
            if not self._vlm_config.processing.enabled:
                logger.info("VLM analysis disabled in configuration, skipping")
                return StepResult.ok(
                    self.name,
                    timestamps=[],
                    tags={},
                    confidence=0.0,
                    skipped=True,
                )
            
            # Initialize VLM processor
            self._processor = VLMProcessor(config=self._vlm_config)
            await self._processor.initialize()
            
            # Process video through VLM
            def progress_callback(progress: int) -> None:
                """Report progress to pipeline."""
                logger.debug(f"VLM analysis progress: {progress}%")
            
            results = await self._processor.process_video(
                video_path,
                progress_callback=progress_callback,
            )
            
            # Extract results
            timestamps = results.get("timestamps", [])
            tags = results.get("tags", {})
            confidence = results.get("confidence", 0.0)
            
            # Create analysis result
            analysis_result = AIAnalysisResult(
                video_path=video_path,
                timestamps=timestamps,
                tags=tags,
                confidence=confidence,
            )
            
            # Store in context
            context.analysis_result = analysis_result
            
            # Update video metadata
            if context.video_metadata:
                context.video_metadata.has_ai_data = True
            
            # Save timestamps to database if video_id is available
            if context.video_id:
                try:
                    await self._save_timestamps_to_db(context, results)
                except Exception as e:
                    logger.warning(f"Failed to save timestamps to database: {e}")
                    # Don't fail the step if DB save fails
            
            # Emit analysis complete event
            await self._emit_event(EventType.ANALYSIS_COMPLETE, context, {
                "video_path": video_path,
                "timestamp_count": len(timestamps),
                "tag_count": len(tags),
                "confidence": confidence,
            })
            
            return StepResult.ok(
                self.name,
                timestamps=timestamps,
                tags=tags,
                confidence=confidence,
            )
            
        except FileNotFoundError as e:
            error_msg = f"Video file not found: {e}"
            logger.error(error_msg)
            
            await self._emit_event(EventType.ANALYSIS_FAILED, context, {
                "video_path": video_path,
                "error": error_msg,
            })
            
            return StepResult.fail(
                self.name,
                StepError(
                    code="VIDEO_NOT_FOUND",
                    message=error_msg,
                    details={"path": video_path},
                ),
            )
            
        except Exception as e:
            error_msg = f"VLM analysis failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            
            # Emit analysis failed event
            await self._emit_event(EventType.ANALYSIS_FAILED, context, {
                "video_path": video_path,
                "error": error_msg,
            })
            
            return StepResult.fail(
                self.name,
                StepError.from_exception(e, code="ANALYSIS_ERROR"),
            )
        
        finally:
            # Clean up processor
            if self._processor:
                try:
                    await self._processor.close()
                except Exception as e:
                    logger.warning(f"Error closing VLM processor: {e}")
    
    async def _save_timestamps_to_db(
        self,
        context: PipelineContext,
        results: Dict[str, Any],
    ) -> None:
        """Save timestamps to database.
        
        Args:
            context: Pipeline context with video_id
            results: VLM analysis results
        """
        from haven_cli.database.connection import get_db_session
        from haven_cli.database.models import Timestamp
        
        video_id = context.video_id
        if not video_id:
            logger.warning("Cannot save timestamps: no video_id in context")
            return
        
        timestamps = results.get("timestamps", [])
        if not timestamps:
            logger.debug("No timestamps to save")
            return
        
        try:
            with get_db_session() as session:
                # Clear existing timestamps for this video
                session.query(Timestamp).filter(
                    Timestamp.video_id == video_id
                ).delete()
                
                # Add new timestamps
                for ts_data in timestamps:
                    timestamp = Timestamp(
                        video_id=video_id,
                        tag_name=ts_data.get("tag_name", "unknown"),
                        start_time=ts_data.get("start_time", 0.0),
                        end_time=ts_data.get("end_time"),
                        confidence=ts_data.get("confidence", 0.5),
                    )
                    session.add(timestamp)
                
                session.commit()
                
                logger.info(
                    f"Saved {len(timestamps)} timestamps to database "
                    f"for video {video_id}"
                )
                
        except Exception as e:
            logger.error(f"Failed to save timestamps to database: {e}")
            raise
    
    async def on_skip(self, context: PipelineContext, reason: str) -> None:
        """Handle step skip - log that VLM was skipped."""
        logger.info(f"VLM analysis skipped: {reason}")


class MockAnalyzeStep(ConditionalStep):
    """Mock analyze step for testing without VLM API calls.
    
    This step generates synthetic analysis results without making
    actual VLM API calls, useful for testing and development.
    """
    
    @property
    def name(self) -> str:
        """Step identifier."""
        return "analyze_mock"
    
    @property
    def enabled_option(self) -> str:
        """Context option that enables this step."""
        return "vlm_enabled"
    
    @property
    def default_enabled(self) -> bool:
        """Mock analysis is disabled by default."""
        return False
    
    async def process(self, context: PipelineContext) -> StepResult:
        """Generate mock analysis results.
        
        Args:
            context: Pipeline context
            
        Returns:
            StepResult with synthetic analysis data
        """
        video_path = context.video_path
        
        # Generate mock timestamps
        mock_timestamps = [
            {
                "tag_name": "introduction",
                "start_time": 0.0,
                "end_time": 10.5,
                "confidence": 0.85,
                "description": "Opening sequence",
            },
            {
                "tag_name": "main_content",
                "start_time": 10.5,
                "end_time": 60.0,
                "confidence": 0.92,
                "description": "Primary video content",
            },
        ]
        
        # Generate mock tags
        mock_tags = {
            "video": 0.95,
            "entertainment": 0.88,
            "content": 0.75,
        }
        
        # Calculate mock confidence
        confidence = 0.85
        
        # Create analysis result
        analysis_result = AIAnalysisResult(
            video_path=video_path,
            timestamps=mock_timestamps,
            tags=mock_tags,
            confidence=confidence,
        )
        
        context.analysis_result = analysis_result
        
        if context.video_metadata:
            context.video_metadata.has_ai_data = True
        
        logger.info(f"Generated mock analysis for: {video_path}")
        
        return StepResult.ok(
            self.name,
            timestamps=mock_timestamps,
            tags=mock_tags,
            confidence=confidence,
            mock=True,
        )
    
    async def on_skip(self, context: PipelineContext, reason: str) -> None:
        """Handle step skip."""
        logger.info(f"Mock VLM analysis skipped: {reason}")
