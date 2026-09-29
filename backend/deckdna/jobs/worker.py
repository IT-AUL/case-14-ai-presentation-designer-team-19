"""Worker entrypoint.

Placeholder consumer: generation currently runs synchronously inside the
API process; this stays alive as the queue-consumer slot for deployment
topologies that offload work to a worker.
"""

import asyncio
import logging

logger = logging.getLogger("deckdna.worker")


async def main() -> None:
    logger.info("deckdna worker started (stub — no queue consumer yet)")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
