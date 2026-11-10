"""Regression tests: detached pipeline tasks must be retained and persisted."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from haven_cli.plugins.base import ArchiveResult as PluginArchiveResult
from haven_cli.scheduler.job_executor import JobExecutor
from haven_cli.scheduler.job_scheduler import OnSuccessAction, RecurringJob


class FakePlugin:
    def __init__(self):
        self._initialized = True

    async def health_check(self):
        return True

    async def discover_sources(self):
        source = SimpleNamespace(
            source_id="abc123",
            media_type="bittorrent",
            uri="magnet:test",
            priority="medium",
            metadata={},
            title="test",
        )
        return [source]

    async def archive(self, source):
        return PluginArchiveResult(
            success=True,
            output_path="/tmp/fake.mp4",
            file_size=100,
            metadata={"output_paths": ["/tmp/fake.mp4"]},
        )


@pytest.fixture(autouse=True)
def mock_db(request):
    session = MagicMock()
    request.node.session_used = session
    with patch("haven_cli.database.connection.get_db_session") as mock_get:
        mock_get.return_value.__enter__ = MagicMock(return_value=session)
        mock_get.return_value.__exit__ = MagicMock(return_value=False)
        yield mock_get


def _make_executor(tmp_path):
    blocker = asyncio.Event()
    pipeline_manager = MagicMock()
    processed = asyncio.Event()

    async def fake_process(context):
        processed.set()
        await blocker.wait()
        return SimpleNamespace(success=True, error=None)

    pipeline_manager.process.side_effect = fake_process
    executor = JobExecutor(
        pipeline_manager=pipeline_manager, config={"data_dir": str(tmp_path)}
    )
    return executor, pipeline_manager, blocker, processed


@pytest.mark.asyncio
async def test_detached_pipeline_task_is_retained_until_done(tmp_path, request):
    executor, pipeline_manager, blocker, processed = _make_executor(tmp_path)
    # autouse mock_db fixture stashed its session for assertion below
    globals()["session_used"] = request.node.session_used
    job = RecurringJob(
        job_id=uuid4(), name="t", plugin_name="bittorrent", schedule="hourly_random",
    )
    with patch.object(JobExecutor, "_get_plugin", return_value=FakePlugin()):
        await executor.execute(job)
        await asyncio.wait_for(processed.wait(), timeout=2)
        assert len(executor._active_tasks) == 1
        task = next(iter(executor._active_tasks))
        assert not task.done()
        blocker.set()
        await asyncio.wait_for(task, timeout=2)
    assert pipeline_manager.process.called
    await asyncio.sleep(0.05)
    assert len(executor._active_tasks) == 0
    # Durable enqueue record was written before the task started; find it
    # among all add() calls (job execution save also adds a row).
    added_types = [c[0][0].event_type for c in session_used.add.call_args_list if hasattr(c[0][0], "event_type")]
    assert "job_pipeline_enqueued" in added_types
