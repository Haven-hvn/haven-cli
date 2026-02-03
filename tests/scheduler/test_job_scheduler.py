"""Tests for the job scheduler."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.triggers.cron import CronTrigger

from haven_cli.scheduler.job_scheduler import (
    JobExecutionResult,
    JobScheduler,
    JobStatus,
    OnSuccessAction,
    RecurringJob,
    _execute_job_wrapper,
    _global_scheduler,
    get_scheduler,
    _scheduler_instance,
    logger as scheduler_logger,
)


@pytest.fixture
def scheduler() -> JobScheduler:
    """Create a fresh scheduler instance."""
    return JobScheduler()


@pytest.fixture
def sample_job() -> RecurringJob:
    """Create a sample recurring job."""
    return RecurringJob(
        job_id=uuid4(),
        name="Test Job",
        plugin_name="TestPlugin",
        schedule="0 * * * *",  # Every hour
        on_success=OnSuccessAction.ARCHIVE_NEW,
        enabled=True,
    )


class TestRecurringJob:
    """Tests for the RecurringJob dataclass."""

    def test_job_creation(self) -> None:
        """Test creating a recurring job."""
        job = RecurringJob(
            name="Test Job",
            plugin_name="TestPlugin",
            schedule="0 0 * * *",
        )

        assert job.name == "Test Job"
        assert job.plugin_name == "TestPlugin"
        assert job.schedule == "0 0 * * *"
        assert job.enabled is True
        assert job.job_id is not None
        assert isinstance(job.job_id, UUID)

    def test_job_status_active(self) -> None:
        """Test that enabled jobs have ACTIVE status."""
        job = RecurringJob(enabled=True)
        assert job.status == JobStatus.ACTIVE

    def test_job_status_disabled(self) -> None:
        """Test that disabled jobs have DISABLED status."""
        job = RecurringJob(enabled=False)
        assert job.status == JobStatus.DISABLED

    def test_default_schedule(self) -> None:
        """Test default schedule is hourly."""
        job = RecurringJob()
        assert job.schedule == "0 * * * *"

    def test_default_on_success(self) -> None:
        """Test default on_success action."""
        job = RecurringJob()
        assert job.on_success == OnSuccessAction.ARCHIVE_NEW


class TestJobSchedulerBasics:
    """Tests for basic JobScheduler functionality."""

    def test_scheduler_creation(self, scheduler: JobScheduler) -> None:
        """Test creating a scheduler."""
        assert scheduler.is_running is False
        assert scheduler.jobs == []
        assert scheduler.active_jobs == []

    def test_add_job(self, scheduler: JobScheduler, sample_job: RecurringJob) -> None:
        """Test adding a job."""
        result = scheduler.add_job(sample_job)

        assert result.job_id == sample_job.job_id
        assert result.next_run is not None
        assert sample_job.job_id in scheduler._jobs

    def test_get_job(self, scheduler: JobScheduler, sample_job: RecurringJob) -> None:
        """Test getting a job by ID."""
        scheduler.add_job(sample_job)

        found = scheduler.get_job(sample_job.job_id)
        assert found is not None
        assert found.job_id == sample_job.job_id

    def test_get_job_not_found(self, scheduler: JobScheduler) -> None:
        """Test getting a non-existent job."""
        found = scheduler.get_job(uuid4())
        assert found is None

    def test_remove_job(self, scheduler: JobScheduler, sample_job: RecurringJob) -> None:
        """Test removing a job."""
        scheduler.add_job(sample_job)
        result = scheduler.remove_job(sample_job.job_id)

        assert result is True
        assert sample_job.job_id not in scheduler._jobs

    def test_remove_job_not_found(self, scheduler: JobScheduler) -> None:
        """Test removing a non-existent job."""
        result = scheduler.remove_job(uuid4())
        assert result is False

    def test_pause_job(self, scheduler: JobScheduler, sample_job: RecurringJob) -> None:
        """Test pausing a job."""
        scheduler.add_job(sample_job)
        result = scheduler.pause_job(sample_job.job_id)

        assert result is True
        assert sample_job.enabled is False
        assert sample_job.status == JobStatus.DISABLED

    def test_pause_job_not_found(self, scheduler: JobScheduler) -> None:
        """Test pausing a non-existent job."""
        result = scheduler.pause_job(uuid4())
        assert result is False

    def test_resume_job(self, scheduler: JobScheduler, sample_job: RecurringJob) -> None:
        """Test resuming a paused job."""
        scheduler.add_job(sample_job)
        scheduler.pause_job(sample_job.job_id)

        result = scheduler.resume_job(sample_job.job_id)

        assert result is True
        assert sample_job.enabled is True
        assert sample_job.next_run is not None

    def test_resume_job_not_found(self, scheduler: JobScheduler) -> None:
        """Test resuming a non-existent job."""
        result = scheduler.resume_job(uuid4())
        assert result is False

    def test_active_jobs_filter(self, scheduler: JobScheduler) -> None:
        """Test that active_jobs only returns enabled jobs."""
        enabled_job = RecurringJob(name="Enabled", enabled=True)
        disabled_job = RecurringJob(name="Disabled", enabled=False)

        scheduler.add_job(enabled_job)
        scheduler.add_job(disabled_job)

        active = scheduler.active_jobs
        assert len(active) == 1
        assert active[0].name == "Enabled"


class TestJobSchedulerNextRunCalculation:
    """Tests for next run time calculation."""

    def test_calculate_next_run_hourly(self, scheduler: JobScheduler) -> None:
        """Test calculating next run for hourly schedule."""
        schedule = "0 * * * *"  # Every hour
        next_run = scheduler._calculate_next_run(schedule)

        assert next_run > datetime.utcnow()
        # Should be within the next hour
        assert next_run < datetime.utcnow() + timedelta(hours=2)

    def test_calculate_next_run_daily(self, scheduler: JobScheduler) -> None:
        """Test calculating next run for daily schedule."""
        schedule = "0 0 * * *"  # Daily at midnight
        next_run = scheduler._calculate_next_run(schedule)

        assert next_run > datetime.utcnow()
        # Should be within the next day
        assert next_run < datetime.utcnow() + timedelta(days=2)

    def test_calculate_next_run_with_seconds(self, scheduler: JobScheduler) -> None:
        """Test calculating next run for schedule with seconds."""
        schedule = "0 0 * * * *"  # Every minute with seconds
        next_run = scheduler._calculate_next_run(schedule)

        assert next_run > datetime.utcnow()

    def test_calculate_next_run_invalid_schedule(self, scheduler: JobScheduler) -> None:
        """Test that invalid schedules fall back gracefully."""
        schedule = "invalid"
        next_run = scheduler._calculate_next_run(schedule)

        # Should return a fallback time (1 hour from now)
        assert next_run > datetime.utcnow()
        assert next_run <= datetime.utcnow() + timedelta(hours=2)


class TestCronTriggerParsing:
    """Tests for cron trigger parsing."""

    def test_parse_5_part_cron(self, scheduler: JobScheduler) -> None:
        """Test parsing 5-part cron expression."""
        schedule = "0 12 * * 1"  # Monday at noon
        trigger = scheduler._parse_cron_trigger(schedule)

        assert isinstance(trigger, CronTrigger)

    def test_parse_6_part_cron(self, scheduler: JobScheduler) -> None:
        """Test parsing 6-part cron expression."""
        schedule = "0 0 12 * * 1"  # Monday at noon with seconds
        trigger = scheduler._parse_cron_trigger(schedule)

        assert isinstance(trigger, CronTrigger)

    def test_parse_invalid_cron(self, scheduler: JobScheduler) -> None:
        """Test that invalid cron raises ValueError."""
        schedule = "invalid cron"

        with pytest.raises(ValueError) as exc_info:
            scheduler._parse_cron_trigger(schedule)

        assert "Invalid cron schedule" in str(exc_info.value)

    def test_parse_empty_cron(self, scheduler: JobScheduler) -> None:
        """Test that empty cron raises ValueError."""
        schedule = ""

        with pytest.raises(ValueError) as exc_info:
            scheduler._parse_cron_trigger(schedule)

        assert "Invalid cron schedule" in str(exc_info.value)


class TestJobSchedulerLifecycle:
    """Tests for scheduler start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_scheduler(self, scheduler: JobScheduler) -> None:
        """Test starting the scheduler."""
        await scheduler.start()

        assert scheduler.is_running is True
        assert scheduler._scheduler is not None

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_stop_scheduler(self, scheduler: JobScheduler) -> None:
        """Test stopping the scheduler."""
        await scheduler.start()
        await scheduler.stop()

        assert scheduler.is_running is False
        assert scheduler._scheduler is None

    @pytest.mark.asyncio
    async def test_start_already_running(self, scheduler: JobScheduler, caplog: Any) -> None:
        """Test starting an already running scheduler."""
        await scheduler.start()

        with caplog.at_level(logging.WARNING):
            await scheduler.start()

        assert "already running" in caplog.text
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_stop_not_running(self, scheduler: JobScheduler) -> None:
        """Test stopping a scheduler that isn't running."""
        # Should not raise any errors
        await scheduler.stop()
        assert scheduler.is_running is False

    @pytest.mark.asyncio
    async def test_global_scheduler_reference(self, scheduler: JobScheduler) -> None:
        """Test that global scheduler reference is set."""
        from haven_cli.scheduler import job_scheduler

        await scheduler.start()

        assert job_scheduler._global_scheduler is scheduler

        await scheduler.stop()
        assert job_scheduler._global_scheduler is None


class TestAPSchedulerIntegration:
    """Tests for APScheduler integration."""

    @pytest.mark.asyncio
    async def test_add_job_to_apscheduler(
        self, scheduler: JobScheduler, sample_job: RecurringJob
    ) -> None:
        """Test adding a job to APScheduler."""
        await scheduler.start()
        scheduler.add_job(sample_job)

        # Check that job was added to APScheduler
        aps_job = scheduler._scheduler.get_job(str(sample_job.job_id))
        assert aps_job is not None
        assert aps_job.name == sample_job.name

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_remove_job_from_apscheduler(
        self, scheduler: JobScheduler, sample_job: RecurringJob
    ) -> None:
        """Test removing a job from APScheduler."""
        await scheduler.start()
        scheduler.add_job(sample_job)

        # Remove job
        scheduler.remove_job(sample_job.job_id)

        # Check that job was removed from APScheduler
        aps_job = scheduler._scheduler.get_job(str(sample_job.job_id))
        assert aps_job is None

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_pause_job_in_apscheduler(
        self, scheduler: JobScheduler, sample_job: RecurringJob
    ) -> None:
        """Test pausing a job in APScheduler."""
        await scheduler.start()
        scheduler.add_job(sample_job)

        # Pause job
        scheduler.pause_job(sample_job.job_id)

        # Check that job was paused
        aps_job = scheduler._scheduler.get_job(str(sample_job.job_id))
        assert aps_job is not None

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_resume_job_in_apscheduler(
        self, scheduler: JobScheduler, sample_job: RecurringJob
    ) -> None:
        """Test resuming a job in APScheduler."""
        await scheduler.start()
        scheduler.add_job(sample_job)
        scheduler.pause_job(sample_job.job_id)

        # Resume job
        scheduler.resume_job(sample_job.job_id)

        # Check that job exists
        aps_job = scheduler._scheduler.get_job(str(sample_job.job_id))
        assert aps_job is not None

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_job_added_before_start(
        self, scheduler: JobScheduler, sample_job: RecurringJob
    ) -> None:
        """Test that jobs added before start are scheduled on start."""
        scheduler.add_job(sample_job)

        await scheduler.start()

        # Check that job was added to APScheduler on start
        aps_job = scheduler._scheduler.get_job(str(sample_job.job_id))
        assert aps_job is not None

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_disabled_job_not_added_to_apscheduler(
        self, scheduler: JobScheduler
    ) -> None:
        """Test that disabled jobs are not added to APScheduler."""
        disabled_job = RecurringJob(name="Disabled", enabled=False)
        scheduler.add_job(disabled_job)

        await scheduler.start()

        # Check that job was NOT added to APScheduler
        aps_job = scheduler._scheduler.get_job(str(disabled_job.job_id))
        assert aps_job is None

        await scheduler.stop()


class TestJobExecution:
    """Tests for job execution."""

    @pytest.mark.asyncio
    async def test_run_job_now_not_found(self, scheduler: JobScheduler) -> None:
        """Test running a non-existent job."""
        result = await scheduler.run_job_now(uuid4())

        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_job_callback_job_not_found(self, scheduler: JobScheduler) -> None:
        """Test job callback with non-existent job."""
        await scheduler.start()

        # Should not raise error
        await scheduler._job_callback(uuid4())

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_job_callback_disabled_job(
        self, scheduler: JobScheduler, sample_job: RecurringJob
    ) -> None:
        """Test job callback with disabled job."""
        await scheduler.start()
        scheduler.add_job(sample_job)
        scheduler.pause_job(sample_job.job_id)

        # Should not execute the disabled job
        await scheduler._job_callback(sample_job.job_id)

        await scheduler.stop()


class TestExecutionHistory:
    """Tests for execution history tracking."""

    def test_record_execution(self, scheduler: JobScheduler) -> None:
        """Test recording execution results."""
        result = JobExecutionResult(
            job_id=uuid4(),
            started_at=datetime.utcnow(),
            success=True,
            sources_found=5,
        )

        scheduler._record_execution(result)

        assert len(scheduler._execution_history) == 1
        assert scheduler._execution_history[0].success is True

    def test_get_history(self, scheduler: JobScheduler) -> None:
        """Test getting execution history."""
        job_id = uuid4()

        # Add some results
        for i in range(5):
            result = JobExecutionResult(
                job_id=job_id,
                started_at=datetime.utcnow(),
                success=True,
            )
            scheduler._record_execution(result)

        history = scheduler.get_history(job_id=job_id, limit=3)

        assert len(history) == 3

    def test_history_limit(self, scheduler: JobScheduler) -> None:
        """Test that history is trimmed to max size."""
        scheduler._max_history = 5

        # Add more results than max
        for i in range(10):
            result = JobExecutionResult(
                job_id=uuid4(),
                started_at=datetime.utcnow(),
                success=True,
            )
            scheduler._record_execution(result)

        assert len(scheduler._execution_history) == 5

    def test_get_history_no_filter(self, scheduler: JobScheduler) -> None:
        """Test getting all history without filter."""
        # Add results for different jobs
        for i in range(5):
            result = JobExecutionResult(
                job_id=uuid4(),
                started_at=datetime.utcnow(),
                success=True,
            )
            scheduler._record_execution(result)

        history = scheduler.get_history()

        assert len(history) == 5


class TestSchedulerStatus:
    """Tests for scheduler status."""

    def test_get_status_not_running(self, scheduler: JobScheduler) -> None:
        """Test getting status when not running."""
        status = scheduler.get_status()

        assert status["running"] is False
        assert status["total_jobs"] == 0
        assert status["active_jobs"] == 0

    @pytest.mark.asyncio
    async def test_get_status_running(
        self, scheduler: JobScheduler, sample_job: RecurringJob
    ) -> None:
        """Test getting status when running."""
        scheduler.add_job(sample_job)
        await scheduler.start()

        status = scheduler.get_status()

        assert status["running"] is True
        assert status["total_jobs"] == 1
        assert status["active_jobs"] == 1
        assert status["aps_scheduler_jobs"] == 1
        assert len(status["jobs"]) == 1

        await scheduler.stop()


class TestExecuteJobWrapper:
    """Tests for the _execute_job_wrapper function."""

    def test_wrapper_no_global_scheduler(self, caplog: Any) -> None:
        """Test wrapper when global scheduler is not set."""
        with caplog.at_level(logging.ERROR):
            _execute_job_wrapper(str(uuid4()))

        assert "not initialized" in caplog.text

    @pytest.mark.asyncio
    async def test_wrapper_with_scheduler(
        self, scheduler: JobScheduler, sample_job: RecurringJob, caplog: Any
    ) -> None:
        """Test wrapper with valid scheduler."""
        await scheduler.start()
        scheduler.add_job(sample_job)

        # The wrapper should handle the job execution
        # Note: We can't easily test the full execution without complex mocking,
        # but we can verify it doesn't crash
        with caplog.at_level(logging.WARNING):
            _execute_job_wrapper(str(sample_job.job_id))
            # Give it a moment to process
            await asyncio.sleep(0.1)

        await scheduler.stop()


class TestEventListeners:
    """Tests for APScheduler event listeners."""

    @pytest.mark.asyncio
    async def test_listeners_setup(self, scheduler: JobScheduler) -> None:
        """Test that event listeners are set up."""
        await scheduler.start()

        # Check that listeners are registered by checking internal state
        # APScheduler doesn't expose listeners directly, but we can verify
        # the setup doesn't crash
        assert scheduler._scheduler is not None

        await scheduler.stop()


class TestOnSuccessAction:
    """Tests for OnSuccessAction enum."""

    def test_enum_values(self) -> None:
        """Test enum values."""
        assert OnSuccessAction.ARCHIVE_ALL.value == "archive_all"
        assert OnSuccessAction.ARCHIVE_NEW.value == "archive_new"
        assert OnSuccessAction.LOG_ONLY.value == "log_only"


class TestEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.asyncio
    async def test_add_job_while_running(
        self, scheduler: JobScheduler, sample_job: RecurringJob
    ) -> None:
        """Test adding a job while scheduler is running."""
        await scheduler.start()

        # Add job after start
        scheduler.add_job(sample_job)

        # Job should be immediately added to APScheduler
        aps_job = scheduler._scheduler.get_job(str(sample_job.job_id))
        assert aps_job is not None

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_remove_job_while_not_running(
        self, scheduler: JobScheduler, sample_job: RecurringJob
    ) -> None:
        """Test removing a job when scheduler is not running."""
        scheduler.add_job(sample_job)

        # Remove job before start
        result = scheduler.remove_job(sample_job.job_id)

        assert result is True
        assert sample_job.job_id not in scheduler._jobs

    def test_job_metadata_defaults(self) -> None:
        """Test job metadata defaults to empty dict."""
        job = RecurringJob()
        assert job.metadata == {}

    def test_job_stats_initial(self) -> None:
        """Test job stats are initialized correctly."""
        job = RecurringJob()
        assert job.run_count == 0
        assert job.error_count == 0
        assert job.last_run is None
        assert job.next_run is None


class TestGetSchedulerSingleton:
    """Tests for the get_scheduler() singleton function."""

    def test_get_scheduler_returns_instance(self) -> None:
        """Test that get_scheduler() returns a JobScheduler instance."""
        scheduler = get_scheduler()
        assert isinstance(scheduler, JobScheduler)

    def test_get_scheduler_returns_same_instance(self) -> None:
        """Test that get_scheduler() returns the same instance on multiple calls."""
        scheduler1 = get_scheduler()
        scheduler2 = get_scheduler()
        assert scheduler1 is scheduler2

    def test_get_scheduler_creates_new_scheduler(self) -> None:
        """Test that get_scheduler() creates a new scheduler if none exists."""
        # Reset the global instance
        import haven_cli.scheduler.job_scheduler as js_module
        original = js_module._scheduler_instance
        js_module._scheduler_instance = None
        
        try:
            scheduler = get_scheduler()
            assert scheduler is not None
            assert isinstance(scheduler, JobScheduler)
        finally:
            # Restore original
            js_module._scheduler_instance = original
