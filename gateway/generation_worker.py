"""Claim and prepare channel-specific generation requests outside the API process."""
from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from gateway.auto_generation import process_next_item, review_next_completed_job
from gateway import main


async def run() -> None:
    owner = str(uuid4())
    while True:
        try:
            worked = await process_next_item(owner)
            with main.database() as db:
                pending = [row["job_id"] for row in db.execute(
                    "SELECT q.job_id FROM generation_quality q JOIN video_jobs j ON j.id=q.job_id "
                    "WHERE q.state='pending' AND j.state IN ('queued','running') LIMIT 10"
                )]
            for job_id in pending:
                try:
                    await main.reconcile_job(job_id)
                except Exception:
                    logging.exception("Failed to check render %s", job_id)
            worked = await review_next_completed_job() or worked
            if not worked:
                await asyncio.sleep(4)
        except Exception:
            logging.exception("Automatic generation worker failed")
            await asyncio.sleep(3)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
