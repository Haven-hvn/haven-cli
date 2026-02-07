"""Job executor for running scheduled jobs.

The JobExecutor handles the actual execution of a scheduled job,
including plugin discovery, archiving, and pipeline processing.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from uuid import UUID

from haven_cli.plugins.base import ArchiverPlugin, ArchiveResult as PluginArchiveResult
from haven_cli.plugins.base import MediaSource as PluginMediaSource
from haven_cli.scheduler.job_scheduler import (
    JobExecutionResult,
    OnSuccessAction,
    RecurringJob,
)
from haven_cli.scheduler.source_tracker import SourceTracker

logger = logging.getLogger(__name__)


@dataclass
class MediaSource:
    """A media source discovered by a plugin.
    
    Aligned with MediaSource type from HavenPlayer.p specification.
    
    Attributes:
        source_id: Unique identifier for the source
        media_type: Type of media (youtube, bittorrent, webrtc, etc.)
        uri: URI to the media source
        priority: Priority level (high, medium, low)
        metadata: Additional source metadata
    """
    
    source_id: str
    media_type: str
    uri: str
    priority: str = "medium"
    metadata: Dict[str, Any] = None
    
    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


@dataclass
class ArchiveResult:
    """Result of archiving a media source.
    
    Aligned with ArchiveResult type from HavenPlayer.p specification.
    """
    
    success: bool
    output_path: str = ""
    file_size: int = 0
    duration: int = 0
    error: str = ""
    metadata: Dict[str, Any] = None
    
    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


class JobExecutor:
    """Executes scheduled jobs by coordinating plugins and pipeline.
    
    The JobExecutor is responsible for:
    1. Calling plugin.discover_sources() to find new media
    2. Filtering sources based on on_success action
    3. Calling plugin.archive() for each source
    4. Enqueuing archived content to the pipeline
    
    Example:
        executor = JobExecutor(pipeline_manager, config)
        result = await executor.execute(job)
    """
    
    def __init__(
        self,
        pipeline_manager: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize the job executor.
        
        Args:
            pipeline_manager: PipelineManager for processing archived content
            config: Executor configuration
        """
        self._pipeline_manager = pipeline_manager
        self._config = config or {}
        
        # Initialize source tracker for persistent known source storage
        data_dir = Path(self._config.get("data_dir", Path.home() / ".haven" / "scheduler"))
        self._source_tracker = SourceTracker(data_dir)
        
        # In-memory cache for backwards compatibility
        self._known_sources: Dict[str, Set[str]] = {}
        
        # Concurrency control for archiving
        self._max_concurrent_archives = self._config.get("max_concurrent_archives", 3)
        self._archive_semaphore = asyncio.Semaphore(self._max_concurrent_archives)
    
    async def execute(self, job: RecurringJob) -> JobExecutionResult:
        """Execute a scheduled job.
        
        Args:
            job: The job to execute
            
        Returns:
            Execution result with statistics
        """
        started_at = datetime.utcnow()
        sources_found = 0
        sources_archived = 0
        
        try:
            # Get plugin
            plugin = await self._get_plugin(job.plugin_name)
            if not plugin:
                return JobExecutionResult(
                    job_id=job.job_id,
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    success=False,
                    error=f"Plugin not found: {job.plugin_name}",
                )
            
            # Discover sources
            logger.info(f"Discovering sources with {job.plugin_name}")
            sources = await self._discover_sources(plugin, job.plugin_name)
            sources_found = len(sources)
            
            logger.info(f"Found {sources_found} sources")
            
            if not sources:
                return JobExecutionResult(
                    job_id=job.job_id,
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    success=True,
                    sources_found=0,
                    sources_archived=0,
                )
            
            # Filter based on on_success action
            sources_to_archive = self._filter_sources(
                sources, job.plugin_name, job.on_success
            )
            
            logger.info(f"Archiving {len(sources_to_archive)} sources")
            
            # Archive sources
            if job.on_success != OnSuccessAction.LOG_ONLY:
                for source in sources_to_archive:
                    result = await self._archive_source(plugin, source)
                    
                    if result.success:
                        sources_archived += 1
                        self._mark_source_known(job.plugin_name, source.source_id)
                        await self._enqueue_to_pipeline(
                            result.output_path, job, source
                        )
                    else:
                        logger.warning(
                            f"Failed to archive {source.source_id}: {result.error}"
                        )
            
            # Build execution result
            execution_result = JobExecutionResult(
                job_id=job.job_id,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                success=True,
                sources_found=sources_found,
                sources_archived=sources_archived,
            )
            
            # Save execution to database
            await self._save_execution(execution_result, job.plugin_name)
            
            return execution_result
            
        except Exception as e:
            logger.error(f"Job execution failed: {e}")
            return JobExecutionResult(
                job_id=job.job_id,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                success=False,
                sources_found=sources_found,
                sources_archived=sources_archived,
                error=str(e),
            )
    
    async def _get_plugin(self, plugin_name: str) -> Optional[ArchiverPlugin]:
        """Get a plugin instance by name.
        
        First tries to get from the plugin manager. If not found,
        attempts to load from the registry and register it.
        
        Args:
            plugin_name: Name of the plugin to get
            
        Returns:
            Plugin instance or None if not found
        """
        from haven_cli.plugins.manager import PluginManager
        from haven_cli.config import get_config
        
        # Get plugin-specific configuration from Haven config
        config = get_config()
        plugin_settings = config.plugins.plugin_settings.get(plugin_name, {})
        
        # Try to get from plugin manager
        manager = PluginManager()
        plugin = manager.get_plugin(plugin_name)
        
        if not plugin:
            # Try to load from registry
            from haven_cli.plugins.registry import get_registry
            registry = get_registry()
            plugin_class = registry.load(plugin_name)
            if plugin_class:
                # Register with plugin-specific configuration
                manager.register(plugin_class, config=plugin_settings)
                plugin = manager.get_plugin(plugin_name)
        elif plugin_settings:
            # Update existing plugin's configuration
            plugin.configure(plugin_settings)
        
        if plugin and not plugin._initialized:
            try:
                await plugin.initialize()
            except Exception as e:
                logger.error(f"Failed to initialize plugin {plugin_name}: {e}")
                return None
        
        return plugin
    
    async def _discover_sources(
        self,
        plugin: ArchiverPlugin,
        plugin_name: str,
    ) -> List[MediaSource]:
        """Call plugin's discover_sources method.
        
        Args:
            plugin: The plugin instance to use
            plugin_name: Name of the plugin (for logging)
            
        Returns:
            List of discovered media sources
            
        Raises:
            RuntimeError: If plugin health check fails
        """
        # Check plugin health first
        try:
            if not await plugin.health_check():
                raise RuntimeError(f"Plugin {plugin_name} health check failed")
        except Exception as e:
            raise RuntimeError(f"Plugin {plugin_name} health check failed: {e}")
        
        # Discover sources
        plugin_sources = await plugin.discover_sources()
        
        # Convert to our MediaSource type
        sources: List[MediaSource] = []
        for s in plugin_sources:
            sources.append(MediaSource(
                source_id=s.source_id,
                media_type=s.media_type,
                uri=s.uri,
                priority=s.priority,
                metadata=s.metadata,
            ))
        
        return sources
    
    def _filter_sources(
        self,
        sources: List[MediaSource],
        plugin_name: str,
        action: OnSuccessAction,
    ) -> List[MediaSource]:
        """Filter sources based on on_success action.
        
        Args:
            sources: All discovered sources
            plugin_name: Name of the plugin
            action: Action determining which sources to archive
            
        Returns:
            Filtered list of sources to archive
        """
        if action == OnSuccessAction.LOG_ONLY:
            return []
        
        if action == OnSuccessAction.ARCHIVE_ALL:
            return sources
        
        if action == OnSuccessAction.ARCHIVE_NEW:
            # Filter to only new sources using the persistent tracker
            return [
                s for s in sources
                if not self._source_tracker.is_known(plugin_name, s.source_id)
            ]
        
        return sources
    
    def _mark_source_known(self, plugin_name: str, source_id: str) -> None:
        """Mark a source as known (already archived).
        
        Updates both the in-memory cache and the persistent tracker.
        
        Args:
            plugin_name: Name of the plugin
            source_id: ID of the source to mark as known
        """
        # Update in-memory cache (backwards compatibility)
        if plugin_name not in self._known_sources:
            self._known_sources[plugin_name] = set()
        self._known_sources[plugin_name].add(source_id)
        
        # Update persistent tracker
        self._source_tracker.add(plugin_name, source_id)
    
    async def _archive_source(
        self,
        plugin: ArchiverPlugin,
        source: MediaSource,
    ) -> ArchiveResult:
        """Archive a media source using the plugin.
        
        Args:
            plugin: The plugin to use for archiving
            source: The media source to archive
            
        Returns:
            Archive result with success status and output details
        """
        try:
            # Convert our MediaSource to plugin's MediaSource
            plugin_source = PluginMediaSource(
                source_id=source.source_id,
                media_type=source.media_type,
                uri=source.uri,
                metadata=source.metadata,
                priority=source.priority,
            )
            
            result = await plugin.archive(plugin_source)
            
            return ArchiveResult(
                success=result.success,
                output_path=result.output_path,
                file_size=result.file_size,
                duration=result.duration,
                error=result.error,
                metadata=result.metadata,
            )
            
        except Exception as e:
            logger.error(f"Failed to archive source {source.source_id}: {e}")
            return ArchiveResult(
                success=False,
                error=str(e),
            )
    
    async def _enqueue_to_pipeline(
        self,
        output_path: str,
        job: RecurringJob,
        source: MediaSource,
    ) -> None:
        """Enqueue archived content to the pipeline for processing.
        
        Args:
            output_path: Path to the archived file
            job: The job that triggered the archive
            source: The media source that was archived
        """
        if not self._pipeline_manager:
            logger.warning("No pipeline manager configured")
            return
        
        from haven_cli.pipeline.context import PipelineContext
        
        # Create pipeline context with source metadata
        context = PipelineContext(
            source_path=Path(output_path),
            options={
                "job_id": str(job.job_id),
                "plugin_name": job.plugin_name,
                "source_id": source.source_id,
                "source_uri": source.uri,
                **source.metadata,
                **job.metadata,
            },
        )
        
        # Process async - don't wait for completion
        asyncio.create_task(
            self._process_with_logging(context, job.job_id)
        )
    
    async def _process_with_logging(
        self,
        context: Any,  # PipelineContext
        job_id: UUID,
    ) -> None:
        """Process pipeline with error logging.
        
        Args:
            context: The pipeline context to process
            job_id: ID of the job that triggered this processing
        """
        try:
            result = await self._pipeline_manager.process(context)
            if result.success:
                logger.info(f"Pipeline completed for {context.source_path}")
            else:
                logger.error(f"Pipeline failed: {result.error}")
        except Exception as e:
            logger.error(f"Pipeline error for job {job_id}: {e}")
    
    async def _save_execution(
        self,
        result: JobExecutionResult,
        plugin_name: str,
    ) -> None:
        """Save execution result to database.
        
        Args:
            result: The execution result to save
            plugin_name: Name of the plugin that was executed
        """
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.models import JobExecution
            
            with get_db_session() as session:
                execution = JobExecution(
                    job_id=str(result.job_id),  # Convert UUID to string for SQLite
                    plugin_name=plugin_name,
                    started_at=result.started_at,
                    completed_at=result.completed_at,
                    success=result.success,
                    sources_found=result.sources_found,
                    sources_archived=result.sources_archived,
                    error=result.error,
                    execution_metadata={
                        "executor_version": "1.0.0",
                    },
                )
                session.add(execution)
                session.commit()
                logger.debug(f"Saved execution result for job {result.job_id}")
        except Exception as e:
            logger.error(f"Failed to save execution result: {e}")


class BatchJobExecutor:
    """Executes multiple jobs in parallel with concurrency control.
    
    Useful for running multiple jobs simultaneously while
    respecting resource limits.
    """
    
    def __init__(
        self,
        pipeline_manager: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
        max_concurrent: int = 4,
    ) -> None:
        """Initialize the batch executor.
        
        Args:
            pipeline_manager: PipelineManager for processing
            config: Executor configuration
            max_concurrent: Maximum concurrent job executions
        """
        self._pipeline_manager = pipeline_manager
        self._config = config or {}
        self._max_concurrent = max_concurrent
    
    async def execute_batch(
        self,
        jobs: List[RecurringJob],
    ) -> List[JobExecutionResult]:
        """Execute multiple jobs with concurrency control.
        
        Args:
            jobs: List of jobs to execute
            
        Returns:
            List of execution results
        """
        semaphore = asyncio.Semaphore(self._max_concurrent)
        
        async def execute_with_semaphore(job: RecurringJob) -> JobExecutionResult:
            async with semaphore:
                executor = JobExecutor(
                    pipeline_manager=self._pipeline_manager,
                    config=self._config,
                )
                return await executor.execute(job)
        
        results = await asyncio.gather(
            *[execute_with_semaphore(job) for job in jobs],
            return_exceptions=True,
        )
        
        # Convert exceptions to failed results
        processed_results: List[JobExecutionResult] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results.append(JobExecutionResult(
                    job_id=jobs[i].job_id,
                    started_at=datetime.utcnow(),
                    completed_at=datetime.utcnow(),
                    success=False,
                    error=str(result),
                ))
            else:
                processed_results.append(result)
        
        return processed_results
