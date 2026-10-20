"""Durable acceptance stores inputs; tickets cannot bypass a lease or cancellation."""
from concurrent.futures import Future

import pytest
from deckdna.api import app as api
from deckdna.api import dispatch
from deckdna.api.persistence import load_store
from fastapi.testclient import TestClient
from test_previews_regenerate import _gen_base, _project_with_plan

A = '/api/v1'
client = TestClient(api.app)


class Ledger:
    def __init__(self):
        self.accepted = []
        self.owned = True

    async def initialize(self):
        pass

    async def enqueue(self, job_id):
        self.accepted.append(job_id)

    async def owns(self, job_id, owner):
        return self.owned


@pytest.fixture
def durable(monkeypatch, tmp_path):
    monkeypatch.setattr(api.settings, 'durable_queue', True)
    monkeypatch.setattr(api.settings, 'state_snapshot', True)
    monkeypatch.setattr(api.settings, 'worker_token', 'unit-dispatch-token')
    monkeypatch.setattr(api.settings, 'artifact_root', str(tmp_path))
    # Explicit checkpoints are tested; do not create persistent background test threads.
    monkeypatch.setattr(api, '_ensure_snapshotter', lambda: None)
    ledger = Ledger()
    monkeypatch.setattr(dispatch, 'queue', lambda: ledger)
    return ledger


def enqueue():
    pid, tid, plans = _project_with_plan()
    response = client.post(f'{A}/projects/{pid}/generations', json={
        'template_id': tid, 'deck_plan_id': plans['plan_id'], **_gen_base(plans),
        'variants': [{'strategy': 'balanced'}], 'use_llm': False,
    })
    return response


def ticket(job_id, token='unit-dispatch-token'):  # noqa: S107 — fixture credential
    return client.post(f'{A}/_internal/jobs/{job_id}/dispatch',
                       json={'owner': 'dispatcher'}, headers={'X-DeckDNA-Worker': token})


def test_accepted_inputs_survive_restart_and_queued_is_preserved(durable):
    result = enqueue()
    assert result.status_code == 202, result.text
    job_id = result.json()['job_id']
    assert durable.accepted == [job_id]
    fresh = api._Store()
    assert load_store(fresh, api._snapshot_path(), preserve_queued=True)
    assert fresh.jobs[job_id].state == api.JobState.queued
    assert fresh.runs[result.json()['generation_id']].request.use_llm is False
    assert not fresh.provider_secrets
    legacy = api._Store()
    assert load_store(legacy, api._snapshot_path())
    assert legacy.jobs[job_id].state == api.JobState.failed


def test_dispatch_requires_token_and_current_lease(durable):
    job_id = enqueue().json()['job_id']
    assert ticket(job_id, 'wrong-token').status_code == 404
    durable.owned = False
    assert ticket(job_id).status_code == 409
    assert api.STORE.jobs[job_id].state == api.JobState.queued
    durable.owned = True
    api._set_job(job_id, state=api.JobState.canceled)
    assert ticket(job_id).json()['state'] == 'canceled'


def test_missing_api_metadata_does_not_acknowledge_a_durable_intent(durable):
    response = ticket('job-with-missing-snapshot')
    assert response.status_code == 503
    assert 'Restore API state' in response.json()['error']['message']


def test_dispatch_persists_running_before_work_and_deduplicates(durable, monkeypatch):
    job_id = enqueue().json()['job_id']
    calls = []

    def execute(run_id, gateway):
        fresh = api._Store()
        load_store(fresh, api._snapshot_path(), preserve_queued=True)
        # Recovery proves running was recorded before executing any work.
        assert fresh.jobs[job_id].state == api.JobState.failed
        assert fresh.jobs[job_id].error.code == 'execution_interrupted'
        calls.append(run_id)
        api._set_job(job_id, state=api.JobState.completed)

    class InlinePool:
        def submit(self, function):
            result = Future()
            function()
            result.set_result(None)
            return result

    monkeypatch.setattr(api, '_execute_generation', execute)
    monkeypatch.setattr(api, '_GEN_POOL', InlinePool())
    assert ticket(job_id).status_code == 200
    assert ticket(job_id).json()['state'] == 'completed'
    assert len(calls) == 1 and job_id not in dispatch._ACTIVE


def test_queue_failure_is_not_an_accepted_generation(durable, monkeypatch):
    async def unavailable(job_id):
        raise ConnectionError('queue offline')

    monkeypatch.setattr(durable, 'enqueue', unavailable)
    response = enqueue()
    assert response.status_code == 503
    assert 'not accepted' in response.json()['error']['message']


def test_duplicate_ticket_and_cancel_before_thread_start(durable, monkeypatch):
    accepted = enqueue().json()
    job_id = accepted['job_id']
    work = []

    class DelayedPool:
        def submit(self, function):
            work.append(function)

    monkeypatch.setattr(api, '_GEN_POOL', DelayedPool())
    calls = []
    monkeypatch.setattr(api, '_execute_generation', lambda *a: calls.append(a))
    assert ticket(job_id).status_code == 200
    assert ticket(job_id).status_code == 200
    assert len(work) == 1
    canceled = client.post(f"{A}/generations/{accepted['generation_id']}/cancel")
    assert canceled.status_code == 200
    work[0]()
    assert not calls and api.STORE.jobs[job_id].state == api.JobState.canceled
    assert job_id not in dispatch._ACTIVE
