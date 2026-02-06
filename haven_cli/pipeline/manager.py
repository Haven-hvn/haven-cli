"""Pipeline manager for orchestrating step execution.

The PipelineManager is responsible for:
- Registering and ordering pipeline steps
- Executing steps sequentially for a single video
- Managing parallel execution across multiple videos
- Coordinating with the event bus
- Collecting and aggregating results
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional, Type

from haven_cli.pipeline.context import BatchContext, PipelineContext
from haven_cli.pipeline.events import Event, EventBus, EventType, get_event_bus
from haven_cli.pipeline.results import PipelineResult, StepResult, StepStatus
from haven_cli.pipeline.step import PipelineStep


class PipelineManager:
    """Orchestrates pipeline step execution with parallel processing support.
    
    The PipelineManager coordinates the execution of pipeline steps for
    video processing. It supports:
    
    - Sequential step execution within a single video pipeline
    - Parallel execution across multiple videos
    - Configurable concurrency limits
    - Event emission for pipeline lifecycle
    - Result aggregation and error handling
    
    Example:
        manager = PipelineManager(max_concurrent=4)
        
        # Register steps in order
        manager.register_step(IngestStep())
        manager.register_step(AnalyzeStep())
        manager.register_step(EncryptStep())
        manager.register_step(UploadStep())
        manager.register_step(SyncStep())
        
        # Process a single video
        context = PipelineContext(source_path="/path/to/video.mp4")
        result = await manager.process(context)
        
        # Process multiple videos in parallel
        contexts = [PipelineContext(source_path=p) for p in video_paths]
        results = await manager.process_batch(contexts)
    """
    
    def __init__(
        self,
        max_concurrent: int = 4,
        event_bus: Optional[EventBus] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize the pipeline manager.
        
        Args:
            max_concurrent: Maximum number of concurrent pipeline executions
            event_bus: Event bus for publishing events (uses default if None)
            config: Pipeline configuration
        """
        self._steps: List[PipelineStep] = []
        self._max_concurrent = max_concurrent
        self._event_bus = event_bus or get_event_bus()
        self._config = config or {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_pipelines: Dict[str, PipelineContext] = {}
    
    @property
    def steps(self) -> List[PipelineStep]:
        """Get the registered pipeline steps in execution order."""
        return self._steps.copy()
    
    @property
    def step_names(self) -> List[str]:
        """Get the names of registered steps in execution order."""
        return [step.name for step in self._steps]
    
    def register_step(self, step: PipelineStep) -> "PipelineManager":
        """Register a pipeline step.
        
        Steps are executed in the order they are registered.
        
        Args:
            step: The pipeline step to register
            
        Returns:
            Self for method chaining
        """
        self._steps.append(step)
        return self
    
    def register_steps(self, *steps: PipelineStep) -> "PipelineManager":
        """Register multiple pipeline steps at once.
        
        Args:
            *steps: Pipeline steps to register in order
            
        Returns:
            Self for method chaining
        """
        for step in steps:
            self.register_step(step)
        return self
    
    def clear_steps(self) -> None:
        """Remove all registered steps."""
        self._steps.clear()
    
    async def process(self, context: PipelineContext) -> PipelineResult:
        """Process a single video through the pipeline.
        
        Executes all registered steps sequentially for the given context.
        Steps may be skipped based on their conditions.
        
        Args:
            context: The pipeline context with video information
            
        Returns:
            PipelineResult with aggregated step results
        """
        started_at = datetime.utcnow()
        step_results: List[StepResult] = []
        
        # Track active pipeline
        pipeline_id = str(context.context_id)
        self._active_pipelines[pipeline_id] = context
        
        try:
            # Emit pipeline started event
            await self._emit_event(EventType.PIPELINE_STARTED, context, {
                "video_path": context.video_path,
                "steps": self.step_names,
            })
            
            # Execute each step sequentially
            for step in self._steps:
                result = await step.execute(context)
                step_results.append(result)
                
                # Stop pipeline on fatal error
                if result.failed and result.error:
                    from haven_cli.pipeline.results import ErrorCategory
                    if result.error.category == ErrorCategory.FATAL:
                        break
            
            # Build pipeline result
            pipeline_result = PipelineResult.from_steps(
                step_results,
                video_path=context.video_path,
                started_at=started_at,
            )
            
            # Emit completion event
            if pipeline_result.success:
                await self._emit_event(EventType.PIPELINE_COMPLETE, context, {
                    "video_path": context.video_path,
                    "cid": pipeline_result.final_cid,
                    "duration_ms": pipeline_result.total_duration_ms,
                })
            else:
                await self._emit_event(EventType.PIPELINE_FAILED, context, {
                    "video_path": context.video_path,
                    "error": pipeline_result.error,
                    "failed_steps": [r.step_name for r in pipeline_result.failed_steps],
                })
            
            return pipeline_result
            
        finally:
            # Remove from active pipelines
            self._active_pipelines.pop(pipeline_id, None)
    
    async def process_batch(
        self,
        contexts: List[PipelineContext],
    ) -> List[PipelineResult]:
        """Process multiple videos in parallel.
        
        Executes pipelines concurrently up to the max_concurrent limit.
        Each video's pipeline runs sequentially, but multiple videos
        are processed in parallel.
        
        Args:
            contexts: List of pipeline contexts to process
            
        Returns:
            List of PipelineResults in the same order as input contexts
        """
        async def process_with_semaphore(ctx: PipelineContext) -> PipelineResult:
            async with self._semaphore:
                return await self.process(ctx)
        
        # Process all contexts concurrently with semaphore limiting
        results = await asyncio.gather(
            *[process_with_semaphore(ctx) for ctx in contexts],
            return_exceptions=True,
        )
        
        # Convert exceptions to failed results
        processed_results: List[PipelineResult] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed_results.append(PipelineResult(
                    success=False,
                    video_path=contexts[i].video_path,
                    error=str(result),
                ))
            else:
                processed_results.append(result)
        
        return processed_results
    
    async def process_batch_context(
        self,
        batch: BatchContext,
    ) -> List[PipelineResult]:
        """Process a batch context with multiple videos.
        
        Args:
            batch: BatchContext containing multiple pipeline contexts
            
        Returns:
            List of PipelineResults
        """
        return await self.process_batch(batch.contexts)
    
    @property
    def active_count(self) -> int:
        """Get the number of currently active pipelines."""
        return len(self._active_pipelines)
    
    @property
    def active_pipelines(self) -> List[PipelineContext]:
        """Get list of currently active pipeline contexts."""
        return list(self._active_pipelines.values())
    
    async def cancel(self, context_id: str) -> bool:
        """Cancel an active pipeline.
        
        Note: This is a placeholder for cancellation support.
        Full implementation requires cooperative cancellation in steps.
        
        Args:
            context_id: The context ID of the pipeline to cancel
            
        Returns:
            True if pipeline was found and cancellation initiated
        """
        if context_id in self._active_pipelines:
            context = self._active_pipelines[context_id]
            
            await self._emit_event(EventType.PIPELINE_CANCELLED, context, {
                "video_path": context.video_path,
            })
            
            # TODO: Implement cooperative cancellation
            return True
        
        return False
    
    async def _emit_event(
        self,
        event_type: EventType,
        context: PipelineContext,
        payload: Dict[str, Any],
    ) -> None:
        """Emit an event to the event bus."""
        event = Event(
            event_type=event_type,
            payload=payload,
            correlation_id=context.correlation_id,
            source="pipeline_manager",
        )
        await self._event_bus.publish(event)


class PipelineBuilder:
    """Builder for constructing pipeline configurations.
    
    Provides a fluent interface for building pipelines with
    common configurations.
    
    Example:
        pipeline = (
            PipelineBuilder()
            .with_ingest()
            .with_analysis(enabled=True)
            .with_encryption(enabled=False)
            .with_upload()
            .with_sync(enabled=True)
            .build()
        )
    """
    
    def __init__(self) -> None:
        """Initialize the pipeline builder."""
        self._steps: List[PipelineStep] = []
        self._config: Dict[str, Any] = {}
        self._max_concurrent: int = 4
    
    def with_max_concurrent(self, max_concurrent: int) -> "PipelineBuilder":
        """Set maximum concurrent pipelines.
        
        Args:
            max_concurrent: Maximum concurrent executions
            
        Returns:
            Self for method chaining
        """
        self._max_concurrent = max_concurrent
        return self
    
    def with_config(self, config: Dict[str, Any]) -> "PipelineBuilder":
        """Set pipeline configuration.
        
        Args:
            config: Configuration dictionary
            
        Returns:
            Self for method chaining
        """
        self._config.update(config)
        return self
    
    def with_step(self, step: PipelineStep) -> "PipelineBuilder":
        """Add a custom step to the pipeline.
        
        Args:
            step: The pipeline step to add
            
        Returns:
            Self for method chaining
        """
        self._steps.append(step)
        return self
    
    def with_ingest(self) -> "PipelineBuilder":
        """Add the ingest step to the pipeline.
        
        Returns:
            Self for method chaining
        """
        from haven_cli.pipeline.steps.ingest_step import IngestStep
        self._steps.append(IngestStep(config=self._config))
        return self
    
    def with_analysis(self, enabled: bool = True) -> "PipelineBuilder":
        """Add the analysis step to the pipeline.
        
        Args:
            enabled: Whether analysis is enabled by default
            
        Returns:
            Self for method chaining
        """
        from haven_cli.pipeline.steps.analyze_step import AnalyzeStep
        step_config = {**self._config, "vlm_enabled": enabled}
        self._steps.append(AnalyzeStep(config=step_config))
        return self
    
    def with_encryption(self, enabled: bool = False) -> "PipelineBuilder":
        """Add the encryption step to the pipeline.
        
        Args:
            enabled: Whether encryption is enabled by default
            
        Returns:
            Self for method chaining
        """
        from haven_cli.pipeline.steps.encrypt_step import EncryptStep
        step_config = {**self._config, "encrypt": enabled}
        self._steps.append(EncryptStep(config=step_config))
        return self
    
    def with_upload(self) -> "PipelineBuilder":
        """Add the upload step to the pipeline.
        
        Returns:
            Self for method chaining
        """
        from haven_cli.pipeline.steps.upload_step import UploadStep
        self._steps.append(UploadStep(config=self._config))
        return self
    
    def with_sync(self, enabled: bool = True) -> "PipelineBuilder":
        """Add the sync step to the pipeline.
        
        Args:
            enabled: Whether Arkiv sync is enabled by default
            
        Returns:
            Self for method chaining
        """
        from haven_cli.pipeline.steps.sync_step import SyncStep
        step_config = {**self._config, "arkiv_sync_enabled": enabled}
        self._steps.append(SyncStep(config=step_config))
        return self
    
    def with_default_steps(self) -> "PipelineBuilder":
        """Add all default pipeline steps.
        
        Adds: ingest → analyze → encrypt → upload → sync
        
        Returns:
            Self for method chaining
        """
        return (
            self
            .with_ingest()
            .with_analysis()
            .with_encryption()
            .with_upload()
            .with_sync()
        )
    
    def build(self) -> PipelineManager:
        """Build the pipeline manager with configured steps.
        
        Returns:
            Configured PipelineManager instance
        """
        manager = PipelineManager(
            max_concurrent=self._max_concurrent,
            config=self._config,
        )
        
        for step in self._steps:
            manager.register_step(step)
        
        return manager


def create_default_pipeline(
    max_concurrent: int = 4,
    config: Optional[Dict[str, Any]] = None,
) -> PipelineManager:
    """Create a pipeline manager with default steps.
    
    Convenience function for creating a standard pipeline with
    all processing steps. Steps are enabled/disabled based on config.
    
    Pipeline config keys (all in 'pipeline' section):
        - vlm_enabled: Enable VLM analysis (default: False)
        - encryption_enabled: Enable encryption (default: False)
        - upload_enabled: Enable Filecoin upload (default: True)
        - arkiv_sync_enabled: Enable Arkiv sync (default: False)
    
    Args:
        max_concurrent: Maximum concurrent pipeline executions
        config: Optional configuration dictionary
        
    Returns:
        Configured PipelineManager with default steps
    """
    builder = PipelineBuilder().with_max_concurrent(max_concurrent)
    
    if config:
        builder.with_config(config)
    
    # Always include ingest step
    builder.with_ingest()
    
    # Get pipeline config - could be nested under 'pipeline' or flat
    pipeline_config = config.get("pipeline", None) if config else None
    
    # Helper to get config value from PipelineConfig object or dict
    def get_config_value(name, default):
        if pipeline_config is None:
            return default
        return getattr(pipeline_config, name, default)
    
    # Check flags from context options (CLI flags) or config file
    # CLI flags take precedence if they're in context options
    vlm_enabled = get_config_value("vlm_enabled", False)
    encryption_enabled = get_config_value("encryption_enabled", False)
    upload_enabled = get_config_value("upload_enabled", True)
    sync_enabled = get_config_value("sync_enabled", False) or get_config_value("arkiv_sync_enabled", False)
    
    # Add steps based on configuration
    builder.with_analysis(enabled=vlm_enabled)
    builder.with_encryption(enabled=encryption_enabled)
    builder.with_upload() if upload_enabled else None  # Upload step is always added but can be skipped via context
    builder.with_sync(enabled=sync_enabled)
    
    return builder.build()
