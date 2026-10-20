"""Authenticated tickets keep STORE execution in the single API coordinator."""
from __future__ import annotations

import asyncio
import secrets
from typing import Any

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from deckdna.errors import DeckDNAError
from deckdna.jobs.durable import DurableQueue
from deckdna.settings import settings

router = APIRouter(prefix='/api/v1/_internal', include_in_schema=False)
_ACTIVE: set[str] = set()


def queue():
    return DurableQueue(settings.database_url, settings.redis_url)


def checkpoint():
    from deckdna.api import app as api
    from deckdna.api.persistence import save_store

    save_store(api.STORE, api._snapshot_path(), api._JOB_LOCK)


async def accept(job_id: str):
    """No durable 202 until both the inputs and PG intent are committed."""
    from deckdna.api import app as api

    try:
        await asyncio.to_thread(checkpoint)
        ledger = queue()
        await ledger.initialize()
        await ledger.enqueue(job_id)
    except Exception as exc:
        error = api.JobError(code='internal_error', message='Durable enqueue failed.')
        job = api.STORE.jobs[job_id]
        if job.kind == api.JobKind.generation:
            api._finish_generation(api.STORE.runs[job.result_ids['generation_id']],
                                    error=error, plans={})
        else:
            api._set_job(job_id, state=api.JobState.failed, finished_at=api._utcnow(), error=error)
        api._persist()
        raise DeckDNAError('internal_error', 'Durable queue unavailable; request was not accepted.',
                           http_status=503, retryable=True) from exc


async def schedule_generation(run, gateway):
    from deckdna.api import app as api

    if not settings.durable_queue:
        api._GEN_POOL.submit(api._execute_generation, run.detail.id, gateway)
        return
    try:
        await accept(run.detail.job_id)
    finally:
        close = getattr(gateway, 'aclose', None)
        if close:
            await close()


class Ticket(BaseModel):
    owner: str = Field(min_length=1, max_length=64)


@router.post('/jobs/{job_id}/dispatch')
async def dispatch(job_id: str, body: Ticket,
                   x_deckdna_worker: str = Header(default='')) -> dict[str, Any]:
    from deckdna.api import app as api
    from deckdna.api.changes import _POOL, drain

    if not settings.durable_queue:
        raise DeckDNAError('not_found', 'Durable dispatcher is disabled')
    if (not settings.worker_token or not secrets.compare_digest(
            x_deckdna_worker.encode(), settings.worker_token.encode())):
        raise DeckDNAError('not_found', 'Unknown dispatcher')
    if not await queue().owns(job_id, body.owner):
        raise DeckDNAError('state_conflict', 'Dispatch lease expired', http_status=409)
    job = api.STORE.jobs.get(job_id)
    if job is None:
        raise DeckDNAError('state_conflict', 'Restore API state before dispatching this intent.',
                           http_status=503, retryable=True)
    if job.state in api._TERMINAL_JOB_STATES:
        await asyncio.to_thread(checkpoint)
        return {'state': job.state.value}
    with api._JOB_LOCK:
        if job_id in _ACTIVE:
            return {'state': api.STORE.jobs[job_id].state.value}
        _ACTIVE.add(job_id)
    gateway = None
    submitted = False
    try:
        if job.stage == 'slide_change':
            run = api.STORE.runs[job.result_ids['generation_id']]
            vid = job.result_ids['variant_id']
            with api._JOB_LOCK:
                for item in run.changes.get(vid, []):
                    if item['job_id'] == job_id:
                        item['durable_ready'] = True
            base = api.STORE.jobs.get(run.detail.job_id)
            if base is None or base.state in {api.JobState.failed, api.JobState.canceled}:
                from deckdna.api.changes import _settle

                for item in run.changes.get(vid, []):
                    if item['job_id'] == job_id:
                        _settle(item, 'canceled', error={'code': 'state_conflict',
                                'message': 'Base generation unavailable; resubmit edits.'})
                await asyncio.to_thread(checkpoint)
                return {'state': 'canceled'}
            if not run.base_ready.get(vid):
                return {'state': 'queued'}
            target, args, pool = drain, (run.detail.id, vid), _POOL
        elif job.kind == api.JobKind.generation:
            run = api.STORE.runs[job.result_ids['generation_id']]
            gateway = api._gateway_for(run.request, run.detail.project_id)
            target, args, pool = api._execute_generation, (run.detail.id, gateway), api._GEN_POOL
        else:
            raise DeckDNAError('invalid_input', 'This job kind is not durably dispatched')

        def work():
            try:
                if job.kind == api.JobKind.generation:
                    with api._JOB_LOCK:
                        if api.STORE.jobs[job_id].state in api._TERMINAL_JOB_STATES:
                            close = getattr(gateway, 'aclose', None)
                            if close:
                                asyncio.run(close())
                            return
                        api._set_job(job_id, state=api.JobState.running, started_at=api._utcnow())
                    checkpoint()  # no provider side effects before durable running state
                target(*args)
            except Exception:
                error = api.JobError(code='internal_error', message='Dispatched execution failed.')
                if job.kind == api.JobKind.generation:
                    api._finish_generation(run, error=error, plans={})
                    close = getattr(gateway, 'aclose', None)
                    if close:
                        asyncio.run(close())
                else:
                    api._set_job(job_id, state=api.JobState.failed,
                                 finished_at=api._utcnow(), error=error)
            finally:
                api._persist()
                with api._JOB_LOCK:
                    _ACTIVE.discard(job_id)

        pool.submit(work)
        submitted = True
        return {'state': 'running'}
    except Exception as exc:
        error = api.JobError(code='provider_unavailable',
                            message='Inputs/provider lease unavailable; retry with a new session.')
        if job.kind == api.JobKind.generation:
            api._finish_generation(api.STORE.runs[job.result_ids['generation_id']],
                                   error=error, plans={})
        else:
            api._set_job(job_id, state=api.JobState.failed, finished_at=api._utcnow(), error=error)
        api._persist()
        close = getattr(gateway, 'aclose', None)
        if close:
            await close()
        if isinstance(exc, DeckDNAError):
            return {'state': 'failed'}
        raise
    finally:
        # A submitted task owns the guard until its worker actually ends.
        if not submitted:
            with api._JOB_LOCK:
                _ACTIVE.discard(job_id)
