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
import os
import time
from datetime import datetime, timezone
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
        self._job_id: Optional[int] = None
        self._frames_total: int = 0
        self._start_time: Optional[float] = None
    
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
        self._start_time = time.time()
        
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
            
            # Create AnalysisJob record for tracking
            if context.video_id:
                self._frames_total = self._vlm_config.processing.frame_count
                self._job_id = await self._create_analysis_job(context.video_id)
                await self._update_pipeline_snapshot(context.video_id, "analyzing", 0)
            
            # Initialize VLM processor
            self._processor = VLMProcessor(config=self._vlm_config)
            await self._processor.initialize()
            
            # Process video through VLM with progress tracking
            last_progress = [0]  # Use list to allow mutation in closure
            last_speed_update = [time.time()]
            
            def progress_callback(progress: int) -> None:
                """Report progress to pipeline and update job tracking."""
                logger.debug(f"VLM analysis progress: {progress}%")
                
                # Update job progress (throttled to avoid DB spam)
                now = time.time()
                if now - last_speed_update[0] >= 1.0:  # Update every second
                    if self._job_id and context.video_id:
                        frames_processed = int(self._frames_total * progress / 100)
                        # Run async update in background
                        import asyncio
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                asyncio.create_task(
                                    self._update_job_progress(
                                        context.video_id, frames_processed, progress
                                    )
                                )
                        except RuntimeError:
                            pass
                    last_speed_update[0] = now
                last_progress[0] = progress
            
            results = await self._processor.process_video(
                video_path,
                progress_callback=progress_callback,
            )
            
            # Extract results
            timestamps = results.get("timestamps", [])
            tags = results.get("tags", {})
            confidence = results.get("confidence", 0.0)
            
            # Determine ai.json path (saved by VLM processor if save_to_file is enabled)
            ai_json_path = f"{video_path}.AI.json"
            if not os.path.exists(ai_json_path):
                ai_json_path = None  # File wasn't saved (save_to_file may be disabled)
            
            # Get the model name from VLM config
            analysis_model = self._vlm_config.engine.model_name if self._vlm_config else None
            
            # Create analysis result
            analysis_result = AIAnalysisResult(
                video_path=video_path,
                timestamps=timestamps,
                tags=tags,
                confidence=confidence,
                ai_json_path=ai_json_path,
                analysis_model=analysis_model,
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
            
            # Emit analysis complete event.
            #
            # ``video_id`` is REQUIRED by ``_on_analysis_complete`` in the
            # TUI's StateManager (haven_tui/core/state_manager.py:1106);
            # omitting it makes the analysis ✅ tick never appear on the
            # event path (the TUI only catches up on the next ~2s poll).
            await self._emit_event(EventType.ANALYSIS_COMPLETE, context, {
                "video_id": context.video_id,
                "video_path": video_path,
                "timestamp_count": len(timestamps),
                "tag_count": len(tags),
                "confidence": confidence,
            })
            
            # Mark job as completed
            if self._job_id and context.video_id:
                ai_json_path = f"{video_path}.AI.json" if os.path.exists(f"{video_path}.AI.json") else None
                await self._complete_analysis_job(self._job_id, ai_json_path)
                await self._update_pipeline_snapshot(context.video_id, "analyze", 100, status="completed")
            
            return StepResult.ok(
                self.name,
                timestamps=timestamps,
                tags=tags,
                confidence=confidence,
            )
            
        except FileNotFoundError as e:
            error_msg = f"Video file not found: {e}"
            logger.error(error_msg)
            
            # Mark job as failed
            if self._job_id and context.video_id:
                await self._fail_analysis_job(self._job_id, error_msg)
                await self._update_pipeline_snapshot(
                    context.video_id, "analyze", 0, status="failed", error=error_msg
                )
            
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
            
            # Mark job as failed
            if self._job_id and context.video_id:
                await self._fail_analysis_job(self._job_id, error_msg)
                await self._update_pipeline_snapshot(
                    context.video_id, "analyze", 0, status="failed", error=error_msg
                )
            
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
        """Handle step skip - log that VLM was skipped and create skipped record."""
        logger.info(f"VLM analysis skipped: {reason}")
        
        # Create a skipped AnalysisJob record so TUI shows correct status
        if context.video_id:
            await self._create_skipped_analysis_job(context.video_id, reason)
    
    async def _create_analysis_job(self, video_id: int) -> Optional[int]:
        """Create an AnalysisJob record for tracking.
        
        Args:
            video_id: Video ID
            
        Returns:
            Job ID or None if creation failed
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import AnalysisJobRepository
            
            with get_db_session() as session:
                repo = AnalysisJobRepository(session)
                model_name = self._vlm_config.engine.model_name if self._vlm_config else "unknown"
                job = repo.create(
                    video_id=video_id,
                    analysis_type="vlm",
                    model_name=model_name,
                    status="analyzing",
                    frames_total=self._frames_total,
                )
                logger.debug(f"Created AnalysisJob {job.id} for video {video_id}")
                return job.id
        except Exception as e:
            logger.warning(f"Failed to create AnalysisJob: {e}")
            return None
    
    async def _create_skipped_analysis_job(
        self,
        video_id: int,
        reason: str,
    ) -> Optional[int]:
        """Create an AnalysisJob record marked as skipped.
        
        This is called when VLM analysis is skipped due to configuration
        (vlm_enabled=false) so the TUI correctly shows analysis as skipped
        rather than pending.
        
        Args:
            video_id: Video ID
            reason: Reason for skipping (e.g., "vlm_enabled is disabled")
            
        Returns:
            Job ID or None if creation failed
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import AnalysisJobRepository
            
            with get_db_session() as session:
                repo = AnalysisJobRepository(session)
                job = repo.create(
                    video_id=video_id,
                    analysis_type="vlm",
                    model_name="none",
                    status="skipped",
                    frames_total=0,
                )
                # Update with skip reason
                job.error_message = reason
                session.commit()
                logger.debug(f"Created skipped AnalysisJob {job.id} for video {video_id}")
                return job.id
        except Exception as e:
            logger.warning(f"Failed to create skipped AnalysisJob: {e}")
            return None
    
    async def _update_job_progress(
        self,
        video_id: int,
        frames_processed: int,
        progress_percent: float,
    ) -> None:
        """Update AnalysisJob progress.
        
        Args:
            video_id: Video ID
            frames_processed: Number of frames processed
            progress_percent: Progress percentage (0-100)
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import AnalysisJobRepository, PipelineSnapshotRepository
            
            with get_db_session() as session:
                job_repo = AnalysisJobRepository(session)
                if self._job_id:
                    job_repo.update_progress(self._job_id, frames_processed)
                
                # Also update pipeline snapshot
                snapshot_repo = PipelineSnapshotRepository(session)
                snapshot_repo.update_stage(
                    video_id=video_id,
                    stage="analyze",
                    status="active",
                    progress_percent=progress_percent,
                )
        except Exception as e:
            logger.debug(f"Failed to update AnalysisJob progress: {e}")
    
    async def _complete_analysis_job(
        self,
        job_id: int,
        output_file: Optional[str] = None,
    ) -> None:
        """Mark AnalysisJob as completed.
        
        Args:
            job_id: Job ID
            output_file: Path to output file (AI.json)
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import AnalysisJobRepository
            
            with get_db_session() as session:
                repo = AnalysisJobRepository(session)
                repo.complete_analysis(job_id, output_file=output_file)
                logger.debug(f"Completed AnalysisJob {job_id}")
        except Exception as e:
            logger.warning(f"Failed to complete AnalysisJob: {e}")
    
    async def _fail_analysis_job(self, job_id: int, error_message: str) -> None:
        """Mark AnalysisJob as failed.
        
        Args:
            job_id: Job ID
            error_message: Error description
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.models import AnalysisJob
            
            with get_db_session() as session:
                job = session.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
                if job:
                    job.status = "failed"
                    job.error_message = error_message
                    session.commit()
                logger.debug(f"Failed AnalysisJob {job_id}: {error_message}")
        except Exception as e:
            logger.warning(f"Failed to mark AnalysisJob as failed: {e}")
    
    async def _update_pipeline_snapshot(
        self,
        video_id: int,
        stage: str,
        progress_percent: float,
        status: str = "active",
        error: Optional[str] = None,
    ) -> None:
        """Update PipelineSnapshot for TUI dashboard.
        
        Args:
            video_id: Video ID
            stage: Current stage name
            progress_percent: Stage progress (0-100)
            status: Overall status
            error: Error message if failed
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import PipelineSnapshotRepository
            
            with get_db_session() as session:
                repo = PipelineSnapshotRepository(session)
                
                if status == "failed" and error:
                    repo.mark_error(video_id, stage, error)
                elif status == "completed":
                    repo.mark_completed(video_id)
                else:
                    repo.update_stage(
                        video_id=video_id,
                        stage=stage,
                        status=status,
                        progress_percent=progress_percent,
                    )
        except Exception as e:
            logger.debug(f"Failed to update PipelineSnapshot: {e}")


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
        
        # Create analysis result with mock model
        analysis_result = AIAnalysisResult(
            video_path=video_path,
            timestamps=mock_timestamps,
            tags=mock_tags,
            confidence=confidence,
            analysis_model="mock-vlm-model",
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
