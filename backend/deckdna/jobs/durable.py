"""PostgreSQL is queue authority; Redis only wakes the dispatcher."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import asyncpg

DDL = '''CREATE TABLE IF NOT EXISTS deckdna_dispatch (
    job_id text PRIMARY KEY,
    state text NOT NULL DEFAULT 'queued',
    owner text,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
)'''
CHANNEL = 'deckdna:dispatch'


class DurableQueue:
    def __init__(self, database_url: str, redis_url: str):
        self.database_url = database_url.replace('postgresql+asyncpg://', 'postgresql://', 1)
        self.redis_url = redis_url

    @asynccontextmanager
    async def connection(self):
        connection = await asyncpg.connect(self.database_url, timeout=5)
        try:
            yield connection
        finally:
            await connection.close()

    async def initialize(self):
        async with self.connection() as connection:
            await connection.execute(DDL)

    async def enqueue(self, job_id: str):
        async with self.connection() as connection:
            await connection.execute('INSERT INTO deckdna_dispatch(job_id) VALUES($1) '
                                     'ON CONFLICT DO NOTHING', job_id)
        # A Redis outage cannot erase an accepted PG intent.
        try:
            from redis.asyncio import Redis

            async with Redis.from_url(self.redis_url, socket_connect_timeout=1,
                                      socket_timeout=1) as redis:
                await redis.publish(CHANNEL, json.dumps({'job_id': job_id}))
        except Exception:  # noqa: BLE001, S110 — wakeup is only an optimization
            pass

    async def claim(self, owner: str, lease_s: int = 60):
        async with self.connection() as connection:
            row = await connection.fetchrow('''
                WITH candidate AS (
                    SELECT job_id FROM deckdna_dispatch
                    WHERE state='queued' OR (state='running' AND lease_until < now())
                    ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE deckdna_dispatch d SET state='running', owner=$1,
                    lease_until=now()+$2 * interval '1 second'
                FROM candidate c WHERE d.job_id=c.job_id RETURNING d.job_id
            ''', owner, lease_s)
        return row['job_id'] if row else None

    async def owns(self, job_id: str, owner: str) -> bool:
        async with self.connection() as connection:
            return bool(await connection.fetchval('SELECT EXISTS(SELECT 1 FROM deckdna_dispatch '
                "WHERE job_id=$1 AND owner=$2 AND state='running' AND lease_until>now())",
                job_id, owner))

    async def renew(self, job_id: str, owner: str, lease_s: int = 60) -> bool:
        async with self.connection() as connection:
            result = await connection.execute('UPDATE deckdna_dispatch SET '
                "lease_until=now()+$3 * interval '1 second' WHERE job_id=$1 AND owner=$2 "
                "AND state='running' AND lease_until>now()", job_id, owner, lease_s)
        return result == 'UPDATE 1'

    async def finish(self, job_id: str, owner: str) -> bool:
        async with self.connection() as connection:
            result = await connection.execute("UPDATE deckdna_dispatch SET state='done', "
                'finished_at=now(), lease_until=NULL WHERE job_id=$1 AND owner=$2 '
                "AND state='running' AND lease_until>now()", job_id, owner)
        return result == 'UPDATE 1'
