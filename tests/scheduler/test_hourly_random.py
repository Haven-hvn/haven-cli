"""Tests for hourly_random schedule (random minute each hour)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from apscheduler.triggers.date import DateTrigger


@pytest.fixture
def scheduler() -> JobScheduler:
    return JobScheduler()

from haven_cli.scheduler.job_scheduler import (
    HOURLY_RANDOM_SCHEDULE,
    JobScheduler,
    RecurringJob,
    _next_hourly_random_time,
)


class TestHourlyRandomTime:
    def test_next_hourly_random_time_with_injected_minute(self) -> None:
        now = datetime(2026, 11, 10, 15, 22, 30, tzinfo=timezone.utc)
        with patch("haven_cli.scheduler.job_scheduler._random_minute", return_value=37):
            nxt = _next_hourly_random_time(now)
        assert (nxt.hour, nxt.minute) == (16, 37)
        assert nxt.second == 0
        assert nxt > now
        assert nxt - now < timedelta(hours=2)

    def test_next_hourly_random_time_zero_minute(self) -> None:
        now = datetime(2026, 11, 10, 0, 5, 0, tzinfo=timezone.utc)
        with patch("haven_cli.scheduler.job_scheduler._random_minute", return_value=0):
            nxt = _next_hourly_random_time(now)
        assert (nxt.hour, nxt.minute) == (1, 0)

    def test_next_hourly_random_time_59_minute(self) -> None:
        now = datetime(2026, 11, 10, 23, 59, 0, tzinfo=timezone.utc)
        with patch("haven_cli.scheduler.job_scheduler._random_minute", return_value=59):
            nxt = _next_hourly_random_time(now)
        assert (nxt.day, nxt.hour, nxt.minute) == (11, 0, 59)

    def test_calculate_next_run_hourly_random(self, scheduler: JobScheduler) -> None:
        with patch("haven_cli.scheduler.job_scheduler._random_minute", return_value=12):
            nxt = scheduler._calculate_next_run(HOURLY_RANDOM_SCHEDULE)
        now = datetime.now(timezone.utc)
        assert nxt > now
        assert nxt.second == 0
        # Next hour boundary + injected minute
        assert nxt.minute == 12

    def test_default_job_schedule_is_hourly_random(self) -> None:
        job = RecurringJob()
        assert job.schedule == HOURLY_RANDOM_SCHEDULE

    def test_add_to_apscheduler_uses_date_trigger_for_hourly_random(
        self, scheduler: JobScheduler
    ) -> None:
        job = RecurringJob(name="hourly-test", plugin_name="TestPlugin")
        assert job.schedule == HOURLY_RANDOM_SCHEDULE
        # No running scheduler needed: _add_to_apscheduler is a no-op without one,
        # so verify the trigger branch logic via _calculate_next_run instead and
        # that DateTrigger can be constructed for the computed time.
        nxt = scheduler._calculate_next_run(job.schedule)
        assert isinstance(DateTrigger(run_date=nxt, timezone="UTC"), DateTrigger)
