"""Ingest step - Video ingestion, pHash calculation, and database entry.

This step is the entry point for videos into the pipeline. It:
1. Validates the video file exists and is readable
2. Extracts video metadata (duration, size, mime type, technical metadata)
3. Calculates perceptual hash (pHash) for deduplication
4. Creates a database entry for the video
5. Checks for duplicates based on pHash
"""

from pathlib import Path
from typing import Any

from haven_cli.database.connection import get_db_session
from haven_cli.database.repositories import VideoRepository
from haven_cli.media import detect_mime_type, extract_video_metadata
from haven_cli.media.exceptions import VideoMetadataError
from haven_cli.media.phash import calculate_video_phash, VideoHashError
from haven_cli.pipeline.context import PipelineContext, VideoMetadata
from haven_cli.pipeline.events import Event, EventType
from haven_cli.pipeline.results import StepError, StepResult
from haven_cli.pipeline.step import PipelineStep


class IngestStep(PipelineStep):
    """Pipeline step for video ingestion and metadata extraction.
    
    This step is always executed (cannot be skipped) as it's the
    foundation for all subsequent processing.
    
    Emits:
        - VIDEO_INGESTED event on successful ingestion
    
    Output data:
        - phash: Perceptual hash of the video
        - file_size: Size in bytes
        - duration: Duration in seconds
        - mime_type: MIME type of the video
        - is_duplicate: Whether this video is a duplicate
        - technical_metadata: Full technical metadata (width, height, codec, etc.)
    """
    
    @property
    def name(self) -> str:
        """Step identifier."""
        return "ingest"
    
    async def process(self, context: PipelineContext) -> StepResult:
        """Process video ingestion.
        
        Args:
            context: Pipeline context with source_path
            
        Returns:
            StepResult with video metadata
        """
        video_path = context.source_path
        
        # Validate file exists
        if not video_path.exists():
            return StepResult.fail(
                self.name,
                StepError.permanent(
                    code="FILE_NOT_FOUND",
                    message=f"Video file not found: {video_path}",
                    path=str(video_path),
                ),
            )
        
        # Validate file is readable
        if not video_path.is_file():
            return StepResult.fail(
                self.name,
                StepError.permanent(
                    code="NOT_A_FILE",
                    message=f"Path is not a file: {video_path}",
                    path=str(video_path),
                ),
            )
        
        try:
            # Extract basic file metadata
            file_size = video_path.stat().st_size
            mime_type = detect_mime_type(video_path)
            
            # Calculate perceptual hash
            # TODO: Implement actual pHash calculation
            phash = await self._calculate_phash(video_path)
            
            # Extract video technical metadata using ffprobe
            try:
                tech_metadata = await extract_video_metadata(video_path)
                duration = tech_metadata.duration
            except VideoMetadataError as e:
                # Log warning but continue with default values
                print(f"Warning: Failed to extract full metadata: {e}")
                duration = 0.0
                tech_metadata = None
            
            # Check for duplicates
            is_duplicate = await self._check_duplicate(phash)
            
            if is_duplicate:
                # Store duplicate status in context
                context.set_step_data(self.name, "is_duplicate", True)
                
                # Handle duplicate based on configuration
                duplicate_action = context.options.get("duplicate_action", "continue")
                if duplicate_action == "error":
                    return StepResult.fail(
                        self.name,
                        StepError.permanent(
                            code="DUPLICATE_VIDEO",
                            message=f"Video with similar pHash already exists: {phash}",
                            path=str(video_path),
                        ),
                    )
                # else: "continue" (default) - proceed with ingestion
                # or "skip" - skip remaining processing (not implemented yet)
            
            # Create video metadata
            video_metadata = VideoMetadata(
                path=str(video_path),
                title=video_path.stem,
                duration=duration,
                file_size=file_size,
                mime_type=mime_type,
                phash=phash,
                width=tech_metadata.width if tech_metadata else 0,
                height=tech_metadata.height if tech_metadata else 0,
                fps=tech_metadata.fps if tech_metadata else 0.0,
                codec=tech_metadata.codec if tech_metadata else "",
                bitrate=tech_metadata.bitrate if tech_metadata else 0,
                audio_codec=tech_metadata.audio_codec if tech_metadata else "",
                audio_channels=tech_metadata.audio_channels if tech_metadata else 0,
                container=tech_metadata.container if tech_metadata else "",
                has_audio=tech_metadata.has_audio if tech_metadata else False,
            )
            
            # Store in context
            context.video_metadata = video_metadata
            
            # Emit video ingested event
            await self._emit_event(EventType.VIDEO_INGESTED, context, {
                "path": str(video_path),
                "phash": phash,
                "file_size": file_size,
                "duration": duration,
                "is_duplicate": is_duplicate,
                "mime_type": mime_type,
                "container": video_metadata.container,
                "resolution": f"{video_metadata.width}x{video_metadata.height}",
                "codec": video_metadata.codec,
            })
            
            # Save to database and get video ID
            video_id = await self._save_to_database(video_metadata)
            
            # Store video ID in context for later steps
            if video_id > 0:
                context.video_id = video_id
                context.set_step_data(self.name, "video_id", video_id)
            
            return StepResult.ok(
                self.name,
                video_id=video_id if video_id > 0 else None,
                phash=phash,
                file_size=file_size,
                duration=duration,
                mime_type=mime_type,
                is_duplicate=is_duplicate,
                technical_metadata=tech_metadata,
            )
            
        except Exception as e:
            return StepResult.fail(
                self.name,
                StepError.from_exception(e, code="INGEST_ERROR"),
            )
    
    async def _calculate_phash(self, path: Path) -> str:
        """Calculate perceptual hash for the video.
        
        Uses frame-based DCT pHash calculation for content-based
        deduplication regardless of encoding differences.
        
        Args:
            path: Path to video file
            
        Returns:
            Hexadecimal pHash string
            
        Raises:
            VideoHashError: If pHash calculation fails
        """
        try:
            phash = await calculate_video_phash(path)
            return phash
        except VideoHashError:
            # Re-raise specific errors
            raise
        except Exception as e:
            raise VideoHashError(
                f"Failed to calculate pHash: {e}",
                path=str(path),
            ) from e
    
    async def _check_duplicate(self, phash: str) -> bool:
        """Check if a video with this pHash already exists.
        
        Uses Hamming distance comparison to detect similar videos
        even with minor encoding differences.
        
        Args:
            phash: Perceptual hash to check
            
        Returns:
            True if a similar video exists in the database
        """
        try:
            with get_db_session() as session:
                repo = VideoRepository(session)
                return repo.is_duplicate(phash)
        except Exception:
            # If database check fails, don't block ingestion
            # Log error but return False to allow processing
            return False
    
    async def _save_to_database(self, metadata: VideoMetadata) -> int:
        """Save video metadata to database.
        
        Args:
            metadata: Video metadata to save
            
        Returns:
            Database ID of the created/updated video record
        """
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            with get_db_session() as session:
                repo = VideoRepository(session)
                
                # Check if video already exists by path
                existing = repo.get_by_source_path(metadata.path)
                if existing:
                    # Update existing record
                    repo.update(
                        existing,
                        title=metadata.title,
                        duration=metadata.duration,
                        file_size=metadata.file_size,
                        mime_type=metadata.mime_type,
                        phash=metadata.phash,
                    )
                    return existing.id
                else:
                    # Create new record
                    video = repo.create(
                        source_path=metadata.path,
                        title=metadata.title,
                        duration=metadata.duration,
                        file_size=metadata.file_size,
                        mime_type=metadata.mime_type,
                        phash=metadata.phash,
                    )
                    return video.id
        except Exception as e:
            # If database save fails, don't block ingestion
            # Log error but allow pipeline to continue
            # Return -1 to indicate error
            logger.error(f"Failed to save video to database: {e}")
            return -1
    
    async def on_complete(
        self,
        context: PipelineContext,
        result: StepResult,
    ) -> None:
        """Log successful ingestion."""
        # Could add logging here
        pass
