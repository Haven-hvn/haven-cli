"""Step result types and status tracking for pipeline execution.

This module defines the standardized output format for pipeline steps,
including success/failure states, error information, and retry handling.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, List, Optional


class StepStatus(Enum):
    """Status of a pipeline step execution.
    
    States follow the lifecycle:
    PENDING → RUNNING → (SUCCESS | FAILED | SKIPPED)
    """
    
    PENDING = auto()      # Step queued but not started
    RUNNING = auto()      # Step currently executing
    SUCCESS = auto()      # Step completed successfully
    FAILED = auto()       # Step failed (may be retryable)
    SKIPPED = auto()      # Step was skipped (condition not met)
    CANCELLED = auto()    # Step was cancelled before completion


class ErrorCategory(Enum):
    """Categories of errors for handling decisions.
    
    Used to determine retry behavior and error escalation.
    """
    
    TRANSIENT = auto()    # Temporary error (network, rate limit) - retry
    PERMANENT = auto()    # Unrecoverable error (invalid data) - skip/fail
    FATAL = auto()        # Critical error (config missing) - stop pipeline
    UNKNOWN = auto()      # Uncategorized error


@dataclass
class StepError:
    """Detailed error information from a failed step.
    
    Attributes:
        code: Error code for programmatic handling
        message: Human-readable error description
        category: Error category for retry decisions
        details: Additional context about the error
        exception_type: Name of the exception class if applicable
        stack_trace: Stack trace for debugging (optional)
        retryable: Whether this error is retryable
    """
    
    code: str
    message: str
    category: ErrorCategory = ErrorCategory.UNKNOWN
    details: Dict[str, Any] = field(default_factory=dict)
    exception_type: Optional[str] = None
    stack_trace: Optional[str] = None
    retryable: bool = False
    
    @classmethod
    def from_exception(
        cls,
        exception: Exception,
        code: str = "EXCEPTION",
        category: ErrorCategory = ErrorCategory.UNKNOWN,
    ) -> "StepError":
        """Create a StepError from an exception.
        
        Args:
            exception: The caught exception
            code: Error code to use
            category: Error category
            
        Returns:
            StepError instance populated from the exception
        """
        import traceback
        
        return cls(
            code=code,
            message=str(exception),
            category=category,
            exception_type=type(exception).__name__,
            stack_trace=traceback.format_exc(),
            retryable=category == ErrorCategory.TRANSIENT,
        )
    
    @classmethod
    def transient(cls, code: str, message: str, **details: Any) -> "StepError":
        """Create a transient (retryable) error."""
        return cls(
            code=code,
            message=message,
            category=ErrorCategory.TRANSIENT,
            details=details,
            retryable=True,
        )
    
    @classmethod
    def permanent(cls, code: str, message: str, **details: Any) -> "StepError":
        """Create a permanent (non-retryable) error."""
        return cls(
            code=code,
            message=message,
            category=ErrorCategory.PERMANENT,
            details=details,
            retryable=False,
        )
    
    @classmethod
    def fatal(cls, code: str, message: str, **details: Any) -> "StepError":
        """Create a fatal error that stops the pipeline."""
        return cls(
            code=code,
            message=message,
            category=ErrorCategory.FATAL,
            details=details,
            retryable=False,
        )


@dataclass
class StepResult:
    """Result of a pipeline step execution.
    
    Standardized output format that captures the outcome of step execution,
    including any data produced, timing information, and error details.
    
    Attributes:
        status: The execution status
        step_name: Name of the step that produced this result
        data: Output data from the step (step-specific)
        error: Error details if status is FAILED
        started_at: When step execution began
        completed_at: When step execution finished
        duration_ms: Execution duration in milliseconds
        attempts: Number of execution attempts (for retries)
        metadata: Additional step-specific metadata
    """
    
    status: StepStatus
    step_name: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[StepError] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    attempts: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def success(self) -> bool:
        """Check if the step completed successfully."""
        return self.status == StepStatus.SUCCESS
    
    @property
    def failed(self) -> bool:
        """Check if the step failed."""
        return self.status == StepStatus.FAILED
    
    @property
    def skipped(self) -> bool:
        """Check if the step was skipped."""
        return self.status == StepStatus.SKIPPED
    
    @property
    def cid(self) -> Optional[str]:
        """Get CID from result data if present (for upload results)."""
        return self.data.get("cid") or self.data.get("root_cid")
    
    @classmethod
    def pending(cls, step_name: str) -> "StepResult":
        """Create a pending result."""
        return cls(status=StepStatus.PENDING, step_name=step_name)
    
    @classmethod
    def running(cls, step_name: str) -> "StepResult":
        """Create a running result."""
        return cls(
            status=StepStatus.RUNNING,
            step_name=step_name,
            started_at=datetime.utcnow(),
        )
    
    @classmethod
    def ok(cls, step_name: str, **data: Any) -> "StepResult":
        """Create a successful result with optional data."""
        return cls(
            status=StepStatus.SUCCESS,
            step_name=step_name,
            data=data,
            completed_at=datetime.utcnow(),
        )
    
    @classmethod
    def fail(cls, step_name: str, error: StepError) -> "StepResult":
        """Create a failed result with error information."""
        return cls(
            status=StepStatus.FAILED,
            step_name=step_name,
            error=error,
            completed_at=datetime.utcnow(),
        )
    
    @classmethod
    def skip(cls, step_name: str, reason: str = "") -> "StepResult":
        """Create a skipped result with optional reason."""
        return cls(
            status=StepStatus.SKIPPED,
            step_name=step_name,
            metadata={"skip_reason": reason} if reason else {},
            completed_at=datetime.utcnow(),
        )
    
    def with_timing(self, started_at: datetime) -> "StepResult":
        """Add timing information to this result."""
        now = datetime.utcnow()
        duration = int((now - started_at).total_seconds() * 1000)
        
        self.started_at = started_at
        self.completed_at = now
        self.duration_ms = duration
        return self


@dataclass
class PipelineResult:
    """Aggregate result of a complete pipeline execution.
    
    Collects results from all steps and provides overall status.
    
    Attributes:
        success: Whether the pipeline completed successfully
        step_results: Results from each step in execution order
        total_duration_ms: Total pipeline execution time
        started_at: When pipeline execution began
        completed_at: When pipeline execution finished
        video_path: Path to the processed video
        final_cid: CID of the uploaded file (if uploaded)
        metadata: Additional pipeline-level metadata
    """
    
    success: bool
    step_results: List[StepResult] = field(default_factory=list)
    total_duration_ms: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    video_path: Optional[str] = None
    final_cid: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def cid(self) -> Optional[str]:
        """Alias for final_cid for backward compatibility."""
        return self.final_cid
    
    def get_step_result(self, step_name: str) -> Optional[StepResult]:
        """Get result for a specific step by name."""
        for result in self.step_results:
            if result.step_name == step_name:
                return result
        return None
    
    @property
    def failed_steps(self) -> List[StepResult]:
        """Get all failed step results."""
        return [r for r in self.step_results if r.failed]
    
    @property
    def successful_steps(self) -> List[StepResult]:
        """Get all successful step results."""
        return [r for r in self.step_results if r.success]
    
    @classmethod
    def from_steps(
        cls,
        step_results: List[StepResult],
        video_path: Optional[str] = None,
        started_at: Optional[datetime] = None,
    ) -> "PipelineResult":
        """Create a PipelineResult from a list of step results.
        
        Automatically determines success based on step statuses.
        """
        now = datetime.utcnow()
        
        # Pipeline succeeds if no steps failed
        all_success = all(
            r.status in (StepStatus.SUCCESS, StepStatus.SKIPPED)
            for r in step_results
        )
        
        # Find final CID from upload step
        final_cid = None
        for result in reversed(step_results):
            if result.cid:
                final_cid = result.cid
                break
        
        # Get first error message
        error = None
        for result in step_results:
            if result.error:
                error = result.error.message
                break
        
        duration = None
        if started_at:
            duration = int((now - started_at).total_seconds() * 1000)
        
        return cls(
            success=all_success,
            step_results=step_results,
            total_duration_ms=duration,
            started_at=started_at,
            completed_at=now,
            video_path=video_path,
            final_cid=final_cid,
            error=error,
        )
