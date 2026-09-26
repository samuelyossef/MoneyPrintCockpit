"""Dedicated worker for local channel operators and scheduled publications."""
from __future__ import annotations

import asyncio
import logging

from gateway import main


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main.agent_loop())


if __name__ == "__main__":
    run()
