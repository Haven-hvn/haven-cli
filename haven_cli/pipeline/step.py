"""Base class for pipeline steps with lifecycle hooks.

Pipeline steps are the building blocks of the processing pipeline.
Each step implements a specific processing stage (ingest, analyze, etc.)
and follows a consistent lifecycle with hooks for customization.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Type

from haven_cli.pipeline.context import PipelineContext
from haven_cli.pipeline.events import Event, EventBus, EventType, get_event_bus
from haven_cli.pipeline.results import (
    ErrorCategory,
    StepError,
    StepResult,
    StepStatus,
)


class PipelineStep(ABC):
    """Abstract base class for pipeline processing steps.
    
    Each step in the pipeline inherits from this class and implements
    the `process` method. The base class provides:
    
    - Lifecycle hooks (on_start, on_complete, on_error, on_skip)
    - Event emission for step state changes
    - Error handling and retry logic
    - Timing and metrics collection
    
    Subclasses must implement:
    - `name` property: Unique identifier for the step
    - `process` method: Core processing logic
    
    Subclasses may override:
    - `should_skip`: Condition to skip this step
    - `on_start`, `on_complete`, `on_error`, `on_skip`: Lifecycle hooks
    - `max_retries`: Number of retry attempts for transient errors
    
    Example:
        class IngestStep(PipelineStep):
            @property
            def name(self) -> str:
                return "ingest"
            
            async def process(self, context: PipelineContext) -> StepResult:
                # Process video...
                return StepResult.ok(self.name, phash="abc123")
    """
    
    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize the pipeline step.
        
        Args:
            event_bus: Event bus for publishing events (uses default if None)
            config: Step-specific configuration
        """
        self._event_bus = event_bus or get_event_bus()
        self._config = config or {}
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this step.
        
        Used for logging, metrics, and event correlation.
        """
        pass
    
    @property
    def max_retries(self) -> int:
        """Maximum number of retry attempts for transient errors.
        
        Override in subclasses to customize retry behavior.
        """
        return 3
    
    @property
    def retry_delay_seconds(self) -> float:
        """Delay between retry attempts in seconds.
        
        Override in subclasses to customize retry timing.
        """
        return 1.0
    
    async def execute(self, context: PipelineContext) -> StepResult:
        """Execute the step with full lifecycle management.
        
        This is the main entry point called by the PipelineManager.
        It handles:
        - Skip condition checking
        - Lifecycle hook invocation
        - Error handling and retries
        - Event emission
        - Timing collection
        
        Args:
            context: The pipeline context
            
        Returns:
            StepResult indicating success, failure, or skip
        """
        # Check skip condition
        if await self.should_skip(context):
            result = await self._handle_skip(context)
            return result
        
        started_at = datetime.now(timezone.utc)
        attempts = 0
        last_error: Optional[StepError] = None
        
        # Emit start event
        await self._emit_event(EventType.STEP_STARTED, context, {
            "step_name": self.name,
        })
        
        # Call on_start hook
        await self.on_start(context)
        
        # Retry loop
        while attempts < self.max_retries:
            attempts += 1
            
            try:
                # Execute the step's processing logic
                result = await self.process(context)
                
                # Add timing information
                result = result.with_timing(started_at)
                result.attempts = attempts
                
                if result.success:
                    # Call on_complete hook
                    await self.on_complete(context, result)
                    
                    # Emit complete event.
                    #
                    # ``video_id`` is REQUIRED by ``_on_stage_complete``
                    # in the TUI's StateManager (haven_tui/core/state_manager.py
                    # ~line 1169) — omitting it makes per-stage ✅ ticks never
                    # appear on the event path; the TUI only catches up on
                    # the next ~2s poll.  We also forward ``stage`` (alias of
                    # ``step_name``) because the handler keys on ``stage`` to
                    # decide which status field to flip.
                    await self._emit_event(EventType.STEP_COMPLETE, context, {
                        "video_id": context.video_id,
                        "step_name": self.name,
                        "stage": self.name,
                        "duration_ms": result.duration_ms,
                        "data": result.data,
                    })
                    
                    return result
                
                elif result.failed and result.error:
                    last_error = result.error
                    
                    # Check if error is retryable
                    if result.error.retryable and attempts < self.max_retries:
                        await self._wait_for_retry(attempts)
                        continue
                    
                    # Non-retryable or max retries reached
                    break
                
                else:
                    # Unexpected result state
                    break
                    
            except Exception as e:
                # Unexpected exception
                last_error = StepError.from_exception(
                    e,
                    code=f"{self.name.upper()}_ERROR",
                    category=ErrorCategory.UNKNOWN,
                )
                
                if attempts < self.max_retries:
                    await self._wait_for_retry(attempts)
                    continue
                break
        
        # All retries exhausted or non-retryable error
        result = StepResult.fail(self.name, last_error or StepError.permanent(
            code=f"{self.name.upper()}_FAILED",
            message="Step failed without specific error",
        ))
        result = result.with_timing(started_at)
        result.attempts = attempts
        
        # Record error in context
        context.add_error(
            self.name,
            result.error.code if result.error else "UNKNOWN",
            result.error.message if result.error else "Unknown error",
        )
        
        # Call on_error hook
        await self.on_error(context, result.error)
        
        # Emit failed event.  ``video_id`` and ``stage`` are REQUIRED by
        # the TUI's ``_on_step_failed`` → ``_on_pipeline_failed`` chain
        # (haven_tui/core/state_manager.py).  Without them the failure
        # status never reflects in the TUI until the next ~2s poll.
        await self._emit_event(EventType.STEP_FAILED, context, {
            "video_id": context.video_id,
            "step_name": self.name,
            "stage": self.name,
            "error_code": result.error.code if result.error else None,
            "error_message": result.error.message if result.error else None,
            "attempts": attempts,
        })
        
        return result
    
    @abstractmethod
    async def process(self, context: PipelineContext) -> StepResult:
        """Execute the step's core processing logic.
        
        This method must be implemented by subclasses. It should:
        - Perform the step's specific processing
        - Return StepResult.ok() on success with any output data
        - Return StepResult.fail() on failure with error details
        - NOT handle retries (handled by execute())
        
        Args:
            context: The pipeline context with input data
            
        Returns:
            StepResult indicating the outcome
        """
        pass
    
    async def should_skip(self, context: PipelineContext) -> bool:
        """Determine if this step should be skipped.
        
        Override in subclasses to implement skip conditions.
        For example, skip encryption if not enabled.
        
        Args:
            context: The pipeline context
            
        Returns:
            True if the step should be skipped
        """
        return False
    
    async def preflight_check(self, context: PipelineContext) -> Dict[str, Any]:
        """Perform preflight checks and return diagnostic information.
        
        Override in subclasses to provide diagnostic information about
        step configuration and whether it will execute or skip.
        
        Args:
            context: The pipeline context
            
        Returns:
            Dictionary with diagnostic information
        """
        return {"step": self.name, "will_execute": True}
    
    async def on_start(self, context: PipelineContext) -> None:
        """Hook called before step processing begins.
        
        Override to perform setup or logging.
        
        Args:
            context: The pipeline context
        """
        pass
    
    async def on_complete(
        self,
        context: PipelineContext,
        result: StepResult,
    ) -> None:
        """Hook called after successful step completion.
        
        Override to perform cleanup or additional processing.
        
        Args:
            context: The pipeline context
            result: The successful result
        """
        pass
    
    async def on_error(
        self,
        context: PipelineContext,
        error: Optional[StepError],
    ) -> None:
        """Hook called when step fails after all retries.
        
        Override to perform error handling or notifications.
        
        Args:
            context: The pipeline context
            error: The error that caused failure
        """
        pass
    
    async def on_skip(self, context: PipelineContext, reason: str) -> None:
        """Hook called when step is skipped.
        
        Override to perform logging or alternative processing.
        
        Args:
            context: The pipeline context
            reason: Reason for skipping
        """
        pass
    
    async def _handle_skip(self, context: PipelineContext) -> StepResult:
        """Handle step skip with proper lifecycle management."""
        reason = await self._get_skip_reason(context)
        
        # Call on_skip hook
        await self.on_skip(context, reason)
        
        # Emit skip event.  ``video_id`` is REQUIRED by
        # ``StateManager._on_step_skipped`` (haven_tui/core/state_manager.py).
        await self._emit_event(EventType.STEP_SKIPPED, context, {
            "video_id": context.video_id,
            "step_name": self.name,
            "stage": self.name,
            "reason": reason,
        })
        
        return StepResult.skip(self.name, reason)
    
    async def _get_skip_reason(self, context: PipelineContext) -> str:
        """Get the reason for skipping this step.
        
        Override in subclasses to provide specific reasons.
        """
        return "Condition not met"
    
    async def _wait_for_retry(self, attempt: int) -> None:
        """Wait before retrying with exponential backoff."""
        import asyncio
        
        delay = self.retry_delay_seconds * (2 ** (attempt - 1))
        await asyncio.sleep(delay)
    
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
            source=self.name,
        )
        await self._event_bus.publish(event)


class ConditionalStep(PipelineStep):
    """A pipeline step that can be conditionally enabled/disabled.
    
    Provides a simpler interface for steps that have a boolean
    enable/disable condition.
    """
    
    @property
    @abstractmethod
    def enabled_option(self) -> str:
        """Name of the context option that enables this step.
        
        For example: "vlm_enabled", "encrypt", "arkiv_sync_enabled"
        """
        pass
    
    @property
    def default_enabled(self) -> bool:
        """Default enabled state if option not specified."""
        return True
    
    async def should_skip(self, context: PipelineContext) -> bool:
        """Skip if the step is not enabled in context options."""
        import logging
        logger = logging.getLogger(__name__)
        
        context_value = context.options.get(self.enabled_option)
        config_value = self._config.get(self.enabled_option)
        default_value = self.default_enabled
        
        # Determine effective value and source
        if context_value is not None:
            enabled = context_value
            source = "context.options"
        elif config_value is not None:
            enabled = config_value
            source = "step config"
        else:
            enabled = default_value
            source = "default"
        
        # Only log at debug level for most steps to avoid noise
        # Cleanup step has its own detailed logging
        if self.name != "cleanup":
            logger.debug(
                f"Step '{self.name}' enabled check: {enabled} (from {source}, "
                f"context={context_value}, config={config_value}, default={default_value})"
            )
        
        return not enabled
    
    async def _get_skip_reason(self, context: PipelineContext) -> str:
        """Provide skip reason based on option name."""
        return f"{self.enabled_option} is disabled"
