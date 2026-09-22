from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from .workflows import GrowthPlanningWorkflow, calculate_growth_plan, complete_growth_plan, mark_workflow_started


async def run() -> None:
    client = await Client.connect(os.getenv("TEMPORAL_ADDRESS", "127.0.0.1:7233"))
    worker = Worker(
        client,
        task_queue=os.getenv("GROWTH_TASK_QUEUE", "growth-intelligence"),
        workflows=[GrowthPlanningWorkflow],
        activities=[mark_workflow_started, calculate_growth_plan, complete_growth_plan],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(run())
