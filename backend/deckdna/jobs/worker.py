"""Real durable dispatcher: PG leases, Redis wakeups, authenticated API tickets."""
from __future__ import annotations

import asyncio
import logging
import os
from uuid import uuid4

import httpx
from redis.asyncio import Redis

from deckdna.jobs.durable import CHANNEL, DurableQueue
from deckdna.settings import settings

logger = logging.getLogger('deckdna.worker')


async def main() -> None:
    if not settings.durable_queue or len(settings.worker_token) < 16:
        raise ValueError('worker requires durable_queue and a worker_token of at least 16 chars')
    ledger = DurableQueue(settings.database_url, settings.redis_url)
    await ledger.initialize()
    api_url = os.environ.get('DECKDNA_API_URL', 'http://api:8000').rstrip('/')
    owner = uuid4().hex
    tasks: set[asyncio.Task] = set()
    slots = max(1, int(os.environ.get('DECKDNA_DISPATCH_SLOTS', '4')))
    async with httpx.AsyncClient(timeout=15, headers={
        'X-DeckDNA-Worker': settings.worker_token,
    }) as client:
        async def execute(job_id):
            while await ledger.renew(job_id, owner):
                try:
                    result = await client.post(f'{api_url}/api/v1/_internal/jobs/{job_id}/dispatch',
                                               json={'owner': owner})
                    if result.status_code == 409:
                        return
                    result.raise_for_status()
                    if result.json()['state'] in {'completed', 'failed', 'canceled'}:
                        await ledger.finish(job_id, owner)
                        return
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    logger.warning('dispatch transport: %s', type(exc).__name__)
                await asyncio.sleep(2)

        redis = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=3)
        pubsub = redis.pubsub()
        subscribed = False
        try:
            while True:
                done = {task for task in tasks if task.done()}
                for task in done:
                    try:
                        task.result()
                    except Exception as exc:
                        logger.warning('dispatch attempt ended: %s', type(exc).__name__)
                tasks.difference_update(done)
                while len(tasks) < slots:
                    try:
                        job_id = await ledger.claim(owner)
                    except Exception as exc:
                        logger.warning('queue poll unavailable: %s', type(exc).__name__)
                        break
                    if job_id is None:
                        break
                    tasks.add(asyncio.create_task(execute(job_id)))
                try:
                    if not subscribed:
                        await pubsub.subscribe(CHANNEL)
                        subscribed = True
                    await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
                except Exception:  # noqa: BLE001 — PG polling works without Redis
                    subscribed = False
                    await asyncio.sleep(2)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await pubsub.aclose()
            await redis.aclose()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
