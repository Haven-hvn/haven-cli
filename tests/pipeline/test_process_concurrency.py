"""process() must honor max_concurrent for detached daemon tasks."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from haven_cli.pipeline.context import PipelineContext
from haven_cli.pipeline.manager import PipelineManager
from haven_cli.pipeline.results import StepResult


class BlockingStep:
    def __init__(self):
        self.name = "blocker"
        self.active = 0
        self.max_active = 0

    async def execute(self, context):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.05)
        self.active -= 1
        return StepResult.ok(self.name)


async def _run_one(manager: PipelineManager, index: int):
    ctx = PipelineContext(source_path=Path(f"/tmp/v{index}.mp4"), options={})
    return await manager.process(ctx)


def test_process_respects_max_concurrent() -> None:
    async def main():
        manager = PipelineManager(max_concurrent=1)
        step = BlockingStep()
        manager.register_step(step)
        await asyncio.gather(*[_run_one(manager, i) for i in range(5)])
        assert step.max_active == 1

    asyncio.run(main())
