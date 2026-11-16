"""Regression tests for StepResult.execute() skip handling.

A step may return ``StepResult.skip()`` directly from ``process()`` (for
example ``IngestStep`` when the file is not a supported media type). That is a
non-failure and must be preserved. Previously ``execute()`` only handled
``result.success`` and ``result.failed``; a SKIPPED result fell through to the
retry-exhausted branch and was rewritten as
``Step failed without specific error``, which also made ``haven upload file``
report a successful upload as failed.
"""

from pathlib import Path

import pytest

from haven_cli.pipeline.context import PipelineContext
from haven_cli.pipeline.results import PipelineResult, StepResult, StepStatus
from haven_cli.pipeline.step import PipelineStep


class _SkippingStep(PipelineStep):
    @property
    def name(self) -> str:
        return "skipping"

    async def process(self, context: PipelineContext) -> StepResult:
        return StepResult.skip(self.name, reason="unsupported file")


class _OkStep(PipelineStep):
    @property
    def name(self) -> str:
        return "ok"

    async def process(self, context: PipelineContext) -> StepResult:
        return StepResult.ok(self.name)


@pytest.mark.asyncio
async def test_process_skip_is_preserved():
    """A StepResult.skip() returned from process() stays SKIPPED, not FAILED."""
    step = _SkippingStep()
    context = PipelineContext(source_path=Path("/tmp/archive.zip"))

    result = await step.execute(context)

    assert result.status == StepStatus.SKIPPED
    assert result.skipped is True
    assert result.failed is False
    assert result.error is None
    assert result.metadata.get("skip_reason") == "unsupported file"


@pytest.mark.asyncio
async def test_pipeline_treats_skipped_step_as_success():
    """A skipped step does not make the overall pipeline fail."""
    context = PipelineContext(source_path=Path("/tmp/archive.zip"))

    skipped = await _SkippingStep().execute(context)
    ok = await _OkStep().execute(context)

    pipeline = PipelineResult.from_steps(
        [skipped, ok], video_path=str(context.source_path)
    )

    assert pipeline.success is True
    assert pipeline.failed_steps == []