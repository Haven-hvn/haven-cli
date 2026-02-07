"""Job scheduler for cron-like recurring job execution.

The JobScheduler manages recurring jobs that trigger plugin
discover_sources() calls at scheduled intervals. It integrates
with APScheduler for cron-like scheduling.

Jobs and execution history are persisted to the database and
survive daemon restarts.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from uuid import UUID, uuid4

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.events import (
    EVENT_JOB_EXECUTED,
    EVENT_JOB_ERROR,
    EVENT_JOB_MISSED,
)
from apscheduler.job import Job as APJob

logger = logging.getLogger(__name__)

# Global reference for job execution function
# This allows APScheduler to pickle the job function without capturing the JobScheduler instance
_global_scheduler: Optional["JobScheduler"] = None

# Global singleton instance for CLI access
_scheduler_instance: Optional["JobScheduler"] = None


def get_scheduler(load_jobs: bool = True) -> "JobScheduler":
    """Get the singleton JobScheduler instance.
    
    This function provides a global access point to the scheduler
    for CLI commands and other components.
    
    Args:
        load_jobs: Whether to load persisted jobs from database (default: True)
    
    Returns:
        The singleton JobScheduler instance
    """
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = JobScheduler()
        if load_jobs:
            # Load jobs synchronously for CLI access
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            loop.run_until_complete(_scheduler_instance._load_persisted_jobs())
    return _scheduler_instance


def _execute_job_wrapper(job_id: str) -> None:
    """Module-level wrapper function for job execution.

    This function is used by APScheduler instead of a bound method to avoid
    pickling the JobScheduler instance. It uses a global reference to the
    scheduler to execute jobs.

    Args:
        job_id: ID of the recurring job to execute (as string)
    """
    global _global_scheduler
    if _global_scheduler is None:
        logger.error("Job scheduler not initialized, cannot execute job")
        return

    try:
        # Get or create event loop
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Convert string back to UUID
        job_uuid = UUID(job_id)

        # Run the async _execute_job method
        loop.run_until_complete(_global_scheduler._job_callback(job_uuid))
    except Exception as e:
        logger.error(f"Error in job execution wrapper for job {job_id}: {e}")


class JobStatus(Enum):
    """Status of a scheduled job."""

    ACTIVE = auto()  # Job is scheduled and will run
    PAUSED = auto()  # Job is paused, won't run until resumed
    DISABLED = auto()  # Job is disabled
    RUNNING = auto()  # Job is currently executing


class OnSuccessAction(Enum):
    """Action to take when job discovers sources successfully."""

    ARCHIVE_ALL = "archive_all"  # Archive all discovered sources
    ARCHIVE_NEW = "archive_new"  # Archive only new sources
    LOG_ONLY = "log_only"  # Just log, don't archive


@dataclass
class RecurringJob:
    """Definition of a recurring job.

    Attributes:
        job_id: Unique identifier for the job
        name: Human-readable job name
        plugin_name: Name of the plugin to execute
        schedule: Cron expression for scheduling
        on_success: Action to take on successful discovery
        enabled: Whether the job is enabled
        created_at: When the job was created
        last_run: When the job last ran
        next_run: When the job will next run
        run_count: Number of times the job has run
        error_count: Number of errors encountered
        metadata: Additional job metadata
    """

    job_id: UUID = field(default_factory=uuid4)
    name: str = ""
    plugin_name: str = ""
    schedule: str = "0 * * * *"  # Default: hourly
    on_success: OnSuccessAction = OnSuccessAction.ARCHIVE_NEW
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    run_count: int = 0
    error_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> JobStatus:
        """Get current job status."""
        if not self.enabled:
            return JobStatus.DISABLED
        return JobStatus.ACTIVE


@dataclass
class JobExecutionResult:
    """Result of a job execution.

    Attributes:
        job_id: ID of the job that ran
        started_at: When execution started
        completed_at: When execution completed
        success: Whether execution succeeded
        sources_found: Number of sources discovered
        sources_archived: Number of sources archived
        error: Error message if failed
    """

    job_id: UUID
    started_at: datetime
    completed_at: Optional[datetime] = None
    success: bool = False
    sources_found: int = 0
    sources_archived: int = 0
    error: Optional[str] = None


class JobScheduler:
    """Manages recurring job scheduling and execution.

    The JobScheduler coordinates with APScheduler to run jobs
    on cron-like schedules. Each job triggers a plugin's
    discover_sources() method and optionally archives the results.
    
    Jobs and execution history are persisted to the database.

    Example:
        scheduler = JobScheduler(pipeline_manager, config)

        # Add a job
        job = RecurringJob(
            name="YouTube Daily",
            plugin_name="YouTubePlugin",
            schedule="0 0 * * *",  # Daily at midnight
            on_success=OnSuccessAction.ARCHIVE_NEW,
        )
        scheduler.add_job(job)

        # Start the scheduler
        await scheduler.start()
    """

    def __init__(
        self,
        pipeline_manager: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize the job scheduler.

        Args:
            pipeline_manager: PipelineManager for processing archived content
            config: Scheduler configuration
        """
        self._pipeline_manager = pipeline_manager
        self._config = config or {}
        self._jobs: Dict[UUID, RecurringJob] = {}
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._running = False
        self._execution_history: List[JobExecutionResult] = []
        self._max_history = 1000
        
        # State file for backup/recovery
        data_dir = Path(self._config.get("data_dir", Path.home() / ".haven"))
        self._state_file = Path(self._config.get("state_file", data_dir / "scheduler_state.json"))
        
        # Ensure data directory exists
        self._state_file.parent.mkdir(parents=True, exist_ok=True)

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._running

    @property
    def jobs(self) -> List[RecurringJob]:
        """Get all registered jobs."""
        return list(self._jobs.values())

    @property
    def active_jobs(self) -> List[RecurringJob]:
        """Get all active (enabled) jobs."""
        return [j for j in self._jobs.values() if j.enabled]

    def add_job(self, job: RecurringJob) -> RecurringJob:
        """Add a new job to the scheduler.

        Args:
            job: The job to add

        Returns:
            The added job with updated next_run time
        """
        # Calculate next run time
        job.next_run = self._calculate_next_run(job.schedule)

        # Store job in memory
        self._jobs[job.job_id] = job

        # Persist to database
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import JobRepository
            
            with get_db_session() as session:
                job_repo = JobRepository(session)
                # Check if job already exists
                existing = job_repo.get_by_id(job.job_id)
                if existing:
                    # Update existing job
                    job_repo.update(
                        job.job_id,
                        name=job.name,
                        schedule=job.schedule,
                        on_success=job.on_success.value,
                        enabled=job.enabled,
                        metadata=job.metadata,
                        next_run=job.next_run,
                    )
                else:
                    # Create new job
                    job_repo.create(
                        job_id=job.job_id,
                        name=job.name,
                        plugin_name=job.plugin_name,
                        schedule=job.schedule,
                        on_success=job.on_success.value,
                        enabled=job.enabled,
                        metadata=job.metadata,
                        next_run=job.next_run,
                    )
            logger.debug(f"Persisted job {job.job_id} to database")
        except Exception as e:
            logger.error(f"Failed to persist job to database: {e}")

        # If scheduler is running, add to APScheduler
        if self._running and self._scheduler:
            self._add_to_apscheduler(job)

        logger.info(f"Added job {job.name} ({job.job_id}) with schedule '{job.schedule}'")
        return job

    def remove_job(self, job_id: UUID) -> bool:
        """Remove a job from the scheduler.

        Args:
            job_id: ID of the job to remove

        Returns:
            True if job was removed
        """
        if job_id not in self._jobs:
            return False

        # Remove from APScheduler if running
        if self._running and self._scheduler:
            self._remove_from_apscheduler(job_id)

        del self._jobs[job_id]
        
        # Delete from database
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import JobRepository
            
            with get_db_session() as session:
                job_repo = JobRepository(session)
                job_repo.delete(job_id)
            logger.debug(f"Deleted job {job_id} from database")
        except Exception as e:
            logger.error(f"Failed to delete job from database: {e}")

        logger.info(f"Removed job {job_id}")
        return True

    def get_job(self, job_id: UUID) -> Optional[RecurringJob]:
        """Get a job by ID.

        Args:
            job_id: ID of the job

        Returns:
            The job or None if not found
        """
        return self._jobs.get(job_id)

    def pause_job(self, job_id: UUID) -> bool:
        """Pause a job.

        Args:
            job_id: ID of the job to pause

        Returns:
            True if job was paused
        """
        job = self._jobs.get(job_id)
        if not job:
            return False

        job.enabled = False

        # Update database
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import JobRepository
            
            with get_db_session() as session:
                job_repo = JobRepository(session)
                job_repo.update(job_id, enabled=False)
            logger.debug(f"Updated job {job_id} enabled=False in database")
        except Exception as e:
            logger.error(f"Failed to update job in database: {e}")

        if self._running and self._scheduler:
            self._pause_in_apscheduler(job_id)

        logger.info(f"Paused job {job_id}")
        return True

    def resume_job(self, job_id: UUID) -> bool:
        """Resume a paused job.

        Args:
            job_id: ID of the job to resume

        Returns:
            True if job was resumed
        """
        job = self._jobs.get(job_id)
        if not job:
            return False

        job.enabled = True
        job.next_run = self._calculate_next_run(job.schedule)

        # Update database
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import JobRepository
            
            with get_db_session() as session:
                job_repo = JobRepository(session)
                job_repo.update(job_id, enabled=True, next_run=job.next_run)
            logger.debug(f"Updated job {job_id} enabled=True in database")
        except Exception as e:
            logger.error(f"Failed to update job in database: {e}")

        if self._running and self._scheduler:
            self._resume_in_apscheduler(job_id)

        logger.info(f"Resumed job {job_id}")
        return True

    async def run_job_now(self, job_id: UUID) -> JobExecutionResult:
        """Run a job immediately (outside of schedule).

        Args:
            job_id: ID of the job to run

        Returns:
            Execution result
        """
        job = self._jobs.get(job_id)
        if not job:
            return JobExecutionResult(
                job_id=job_id,
                started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
                success=False,
                error="Job not found",
            )

        result = await self._execute_job(job)
        
        # Record execution
        self._record_execution(result, plugin_name=job.plugin_name)
        
        return result

    async def start(self) -> None:
        """Start the scheduler.

        Initializes APScheduler, loads persisted jobs from database,
        and begins executing jobs according to their schedules.
        """
        if self._running:
            logger.warning("Scheduler already running")
            return

        logger.info("Starting job scheduler...")

        # Load jobs from database
        await self._load_persisted_jobs()

        # Set global reference for job execution wrapper
        global _global_scheduler
        _global_scheduler = self

        # Initialize APScheduler
        self._scheduler = await self._create_scheduler()

        # Set up event listeners
        self._setup_listeners()

        # Start scheduler BEFORE adding jobs (next_run_time only available after start)
        if self._scheduler:
            self._scheduler.start()

        # Add all enabled jobs
        for job in self.active_jobs:
            self._add_to_apscheduler(job)

        self._running = True
        logger.info(f"Scheduler started with {len(self.active_jobs)} jobs")

    async def _load_persisted_jobs(self) -> None:
        """Load persisted jobs from database."""
        # Skip if jobs are already loaded (e.g., from tests)
        if self._jobs:
            logger.debug("Jobs already in memory, skipping database load")
            return
            
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import JobRepository
            
            with get_db_session() as session:
                job_repo = JobRepository(session)
                db_jobs = job_repo.get_all()
                
                for db_job in db_jobs:
                    # Convert database model to RecurringJob dataclass
                    job = self._db_job_to_recurring_job(db_job)
                    self._jobs[job.job_id] = job
                    
            logger.info(f"Loaded {len(self._jobs)} jobs from database")
            
            # Also try to load from state file as backup
            self._load_state_file()
            
        except Exception as e:
            logger.error(f"Failed to load jobs from database: {e}")
            # Fall back to loading from state file
            self._load_state_file()

    def _db_job_to_recurring_job(self, db_job: Any) -> RecurringJob:
        """Convert database RecurringJob model to RecurringJob dataclass.
        
        Args:
            db_job: Database RecurringJob model instance
            
        Returns:
            RecurringJob dataclass instance
        """
        return RecurringJob(
            job_id=UUID(db_job.job_id),
            name=db_job.name,
            plugin_name=db_job.plugin_name,
            schedule=db_job.schedule,
            on_success=OnSuccessAction(db_job.on_success),
            enabled=db_job.enabled,
            created_at=db_job.created_at,
            last_run=db_job.last_run,
            next_run=db_job.next_run,
            run_count=db_job.run_count,
            error_count=db_job.error_count,
            metadata=db_job.job_metadata or {},
        )

    async def stop(self) -> None:
        """Stop the scheduler.

        Gracefully shuts down APScheduler, saves state, and stops all jobs.
        """
        if not self._running:
            return

        logger.info("Stopping job scheduler...")

        # Save state before stopping
        self.save_state()

        # Shutdown APScheduler
        if self._scheduler:
            self._scheduler.shutdown(wait=True)
            self._scheduler = None

        # Clear global reference
        global _global_scheduler
        _global_scheduler = None

        self._running = False
        logger.info("Scheduler stopped")

    async def _create_scheduler(self) -> AsyncIOScheduler:
        """Create and configure APScheduler instance."""
        jobstores = {"default": MemoryJobStore()}

        executors = {"default": AsyncIOExecutor()}

        job_defaults = {
            "coalesce": True,  # Combine missed runs
            "max_instances": 1,  # One instance per job
            "misfire_grace_time": 60 * 5,  # 5 minutes grace
        }

        scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
            timezone="UTC",
        )

        return scheduler

    def _setup_listeners(self) -> None:
        """Set up APScheduler event listeners."""
        if not self._scheduler:
            return

        def on_job_executed(event: Any) -> None:
            logger.info(f"Job {event.job_id} executed successfully")

        def on_job_error(event: Any) -> None:
            exception = getattr(event, "exception", "Unknown error")
            logger.error(f"Job {event.job_id} failed: {exception}")

        def on_job_missed(event: Any) -> None:
            logger.warning(f"Job {event.job_id} missed scheduled run")

        self._scheduler.add_listener(on_job_executed, EVENT_JOB_EXECUTED)
        self._scheduler.add_listener(on_job_error, EVENT_JOB_ERROR)
        self._scheduler.add_listener(on_job_missed, EVENT_JOB_MISSED)

    def _add_to_apscheduler(self, job: RecurringJob) -> None:
        """Add a job to APScheduler."""
        if not self._scheduler:
            return

        try:
            # Parse cron schedule (support 5 or 6 part formats)
            trigger = self._parse_cron_trigger(job.schedule)

            # Add job using module-level wrapper to avoid pickling issues
            self._scheduler.add_job(
                func=_execute_job_wrapper,
                trigger=trigger,
                id=str(job.job_id),
                name=job.name or f"Job {job.job_id}",
                args=[str(job.job_id)],
                replace_existing=True,
            )

            # Update next run time from APScheduler
            apscheduler_job = self._scheduler.get_job(str(job.job_id))
            if apscheduler_job and apscheduler_job.next_run_time:
                job.next_run = apscheduler_job.next_run_time.replace(tzinfo=None)

            logger.debug(f"Added job {job.job_id} to APScheduler")

        except Exception as e:
            logger.error(f"Failed to add job {job.job_id} to APScheduler: {e}")

    def _remove_from_apscheduler(self, job_id: UUID) -> None:
        """Remove a job from APScheduler."""
        if not self._scheduler:
            return

        try:
            self._scheduler.remove_job(str(job_id))
            logger.debug(f"Removed job {job_id} from APScheduler")
        except Exception:
            # Job may not exist in scheduler
            pass

    def _pause_in_apscheduler(self, job_id: UUID) -> None:
        """Pause a job in APScheduler."""
        if not self._scheduler:
            return

        try:
            self._scheduler.pause_job(str(job_id))
            logger.debug(f"Paused job {job_id} in APScheduler")
        except Exception:
            # Job may not exist in scheduler
            pass

    def _resume_in_apscheduler(self, job_id: UUID) -> None:
        """Resume a job in APScheduler."""
        if not self._scheduler:
            return

        try:
            # Check if job exists
            aps_job = self._scheduler.get_job(str(job_id))
            if aps_job:
                self._scheduler.resume_job(str(job_id))
            else:
                # Job doesn't exist, re-add it
                job = self._jobs.get(job_id)
                if job:
                    self._add_to_apscheduler(job)

            logger.debug(f"Resumed job {job_id} in APScheduler")
        except Exception as e:
            logger.error(f"Failed to resume job {job_id} in APScheduler: {e}")

    def _parse_cron_trigger(self, schedule: str) -> CronTrigger:
        """Parse a cron schedule string into a CronTrigger.

        Supports both 5-part (minute hour day month weekday) and
        6-part (second minute hour day month weekday) cron formats.

        Args:
            schedule: Cron schedule string

        Returns:
            CronTrigger instance
        """
        parts = schedule.split()

        if len(parts) == 6:
            second, minute, hour, day, month, weekday = parts
            return CronTrigger(
                second=second,
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=weekday,
                timezone="UTC",
            )
        elif len(parts) == 5:
            minute, hour, day, month, weekday = parts
            return CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=weekday,
                timezone="UTC",
            )
        else:
            raise ValueError(
                f"Invalid cron schedule: '{schedule}'. "
                "Expected 5 or 6 parts (minute hour day month weekday "
                "or second minute hour day month weekday)"
            )

    def _calculate_next_run(self, schedule: str) -> datetime:
        """Calculate next run time from cron expression."""
        from croniter import croniter

        try:
            cron = croniter(schedule, datetime.utcnow())
            return cron.get_next(datetime)
        except Exception as e:
            logger.error(f"Failed to calculate next run for schedule '{schedule}': {e}")
            # Fallback to 1 hour from now
            from datetime import timedelta

            return datetime.utcnow() + timedelta(hours=1)

    async def _job_callback(self, job_id: UUID) -> None:
        """Callback invoked by APScheduler when job should run."""
        job = self._jobs.get(job_id)
        if not job or not job.enabled:
            logger.warning(f"Job {job_id} not found or disabled, skipping execution")
            return

        logger.info(f"Executing scheduled job: {job.name} ({job_id})")
        result = await self._execute_job(job)
        self._record_execution(result, plugin_name=job.plugin_name)

        # Update next run time from APScheduler
        if self._scheduler:
            apscheduler_job = self._scheduler.get_job(str(job_id))
            if apscheduler_job and apscheduler_job.next_run_time:
                job.next_run = apscheduler_job.next_run_time.replace(tzinfo=None)
                # Persist updated next_run to database
                try:
                    from haven_cli.database.connection import get_db_session
                    from haven_cli.database.repositories import JobRepository
                    
                    with get_db_session() as session:
                        job_repo = JobRepository(session)
                        job_repo.update(job_id, next_run=job.next_run)
                except Exception as e:
                    logger.error(f"Failed to update next_run in database: {e}")

    async def _execute_job(self, job: RecurringJob) -> JobExecutionResult:
        """Execute a job.

        Args:
            job: The job to execute

        Returns:
            Execution result
        """
        from haven_cli.scheduler.job_executor import JobExecutor

        started_at = datetime.utcnow()

        try:
            # Create executor
            executor = JobExecutor(
                pipeline_manager=self._pipeline_manager,
                config=self._config,
            )

            # Execute job
            result = await executor.execute(job)

            # Update job stats
            job.last_run = started_at
            job.run_count += 1

            if not result.success:
                job.error_count += 1

            return result

        except Exception as e:
            job.error_count += 1

            return JobExecutionResult(
                job_id=job.job_id,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                success=False,
                error=str(e),
            )

    def _record_execution(self, result: JobExecutionResult, plugin_name: str = "") -> None:
        """Record execution result in history and database.
        
        Args:
            result: The execution result to record
            plugin_name: Name of the plugin that was executed
        """
        # Record in memory
        self._execution_history.append(result)

        # Trim history if needed
        if len(self._execution_history) > self._max_history:
            self._execution_history = self._execution_history[-self._max_history:]

        # Persist to database
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import JobExecutionRepository, JobRepository
            
            with get_db_session() as session:
                # Get the job's database ID if available
                job_repo = JobRepository(session)
                db_job = job_repo.get_by_id(result.job_id)
                scheduled_job_id = db_job.id if db_job else None
                
                # Record execution
                exec_repo = JobExecutionRepository(session)
                exec_repo.create(
                    job_id=result.job_id,
                    plugin_name=plugin_name or "unknown",
                    started_at=result.started_at,
                    completed_at=result.completed_at,
                    success=result.success,
                    sources_found=result.sources_found,
                    sources_archived=result.sources_archived,
                    error=result.error,
                    scheduled_job_id=scheduled_job_id,
                )
                
                # Update job stats
                if db_job:
                    job_repo.update_stats(
                        result.job_id,
                        last_run=result.started_at,
                        increment_run=True,
                        increment_error=not result.success,
                    )
                    
            logger.debug(f"Persisted execution result for job {result.job_id}")
        except Exception as e:
            logger.error(f"Failed to persist execution result: {e}")

        # Log result
        if result.success:
            logger.info(
                f"Job {result.job_id} completed successfully: "
                f"{result.sources_found} sources found, "
                f"{result.sources_archived} archived"
            )
        else:
            logger.error(f"Job {result.job_id} failed: {result.error}")

    def get_history(
        self,
        job_id: Optional[UUID] = None,
        limit: int = 10,
        from_database: bool = False,
    ) -> List[JobExecutionResult]:
        """Get execution history.

        Args:
            job_id: Filter by job ID (optional)
            limit: Maximum number of results
            from_database: Whether to query from database (default) or use in-memory history

        Returns:
            List of execution results
        """
        if from_database:
            try:
                from haven_cli.database.connection import get_db_session
                from haven_cli.database.repositories import JobExecutionRepository
                
                with get_db_session() as session:
                    exec_repo = JobExecutionRepository(session)
                    db_executions = exec_repo.get_history(job_id=job_id, limit=limit)
                    
                    # Convert database models to JobExecutionResult dataclasses
                    return [
                        JobExecutionResult(
                            job_id=UUID(ex.job_id),
                            started_at=ex.started_at,
                            completed_at=ex.completed_at,
                            success=ex.success,
                            sources_found=ex.sources_found,
                            sources_archived=ex.sources_archived,
                            error=ex.error,
                        )
                        for ex in db_executions
                    ]
            except Exception as e:
                logger.error(f"Failed to get history from database: {e}")
                # Fall back to in-memory history
        
        # In-memory fallback
        history = self._execution_history

        if job_id:
            history = [r for r in history if r.job_id == job_id]

        return history[-limit:]

    def save_state(self) -> None:
        """Save scheduler state to JSON file for backup/recovery.
        
        This creates a JSON backup of job definitions that can be used
        for quick recovery if the database is unavailable.
        """
        try:
            state = {
                "version": "1.0.0",
                "saved_at": datetime.utcnow().isoformat(),
                "jobs": [
                    {
                        "job_id": str(j.job_id),
                        "name": j.name,
                        "plugin_name": j.plugin_name,
                        "schedule": j.schedule,
                        "on_success": j.on_success.value,
                        "enabled": j.enabled,
                        "metadata": j.metadata,
                        "run_count": j.run_count,
                        "error_count": j.error_count,
                    }
                    for j in self._jobs.values()
                ],
            }
            
            self._state_file.write_text(json.dumps(state, indent=2))
            logger.debug(f"Saved scheduler state to {self._state_file}")
        except Exception as e:
            logger.error(f"Failed to save scheduler state: {e}")

    def _load_state_file(self) -> None:
        """Load scheduler state from JSON backup file.
        
        This is used as a fallback when database is unavailable
        or for quick recovery during startup.
        """
        if not self._state_file.exists():
            logger.debug("No state file found to load")
            return
            
        try:
            state = json.loads(self._state_file.read_text())
            
            # Only load if we don't already have jobs from database
            if self._jobs:
                logger.debug("Jobs already loaded from database, skipping state file")
                return
            
            for job_data in state.get("jobs", []):
                try:
                    job = RecurringJob(
                        job_id=UUID(job_data["job_id"]),
                        name=job_data["name"],
                        plugin_name=job_data["plugin_name"],
                        schedule=job_data["schedule"],
                        on_success=OnSuccessAction(job_data.get("on_success", "archive_new")),
                        enabled=job_data.get("enabled", True),
                        metadata=job_data.get("metadata", {}),
                        run_count=job_data.get("run_count", 0),
                        error_count=job_data.get("error_count", 0),
                    )
                    self._jobs[job.job_id] = job
                except Exception as e:
                    logger.warning(f"Failed to load job from state file: {e}")
                    
            logger.info(f"Loaded {len(self._jobs)} jobs from state file")
        except Exception as e:
            logger.error(f"Failed to load scheduler state: {e}")

    def cleanup_old_history(self, days: int = 30) -> int:
        """Clean up old execution history from database.
        
        Args:
            days: Delete executions older than this many days
            
        Returns:
            Number of executions deleted
        """
        from datetime import timedelta
        
        cutoff = datetime.utcnow() - timedelta(days=days)
        
        try:
            from haven_cli.database.connection import get_db_session
            from haven_cli.database.repositories import JobExecutionRepository
            
            with get_db_session() as session:
                exec_repo = JobExecutionRepository(session)
                deleted = exec_repo.delete_old_executions(cutoff)
                
            logger.info(f"Cleaned up {deleted} old execution records")
            return deleted
        except Exception as e:
            logger.error(f"Failed to clean up old history: {e}")
            return 0

    def get_status(self) -> Dict[str, Any]:
        """Get scheduler status.

        Returns:
            Dictionary with scheduler status information
        """
        aps_jobs: List[Dict[str, Any]] = []
        if self._scheduler:
            for job in self._scheduler.get_jobs():
                aps_jobs.append(
                    {
                        "id": job.id,
                        "name": job.name,
                        "next_run": (
                            job.next_run_time.isoformat() if job.next_run_time else None
                        ),
                    }
                )

        return {
            "running": self._running,
            "total_jobs": len(self._jobs),
            "active_jobs": len(self.active_jobs),
            "aps_scheduler_jobs": len(aps_jobs) if self._scheduler else 0,
            "jobs": aps_jobs,
        }
