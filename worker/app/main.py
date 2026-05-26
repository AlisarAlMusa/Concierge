"""
Background worker — Person B wires embedding jobs, CMS re-indexing, and tenant erasure here.
Runs as a long-lived process so the container stays healthy.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Worker starting — waiting for tasks")
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
