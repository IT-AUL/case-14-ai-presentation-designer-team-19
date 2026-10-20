"""Queue commands during actual generation; verify final artifacts and revisions."""

from __future__ import annotations

import json
import threading
import time

import pytest
from deckdna.api import app as api
from deckdna.api import changes
from fastapi.testclient import TestClient
from test_revision_concurrency import generated as generated

A = "/api/v1"
client = TestClient(api.app)


def wait_job(job_id):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        job = client.get(f"{A}/jobs/{job_id}").json()
        if job["state"] in {"completed", "failed", "canceled"}:
            return job
        time.sleep(.05)
    raise AssertionError("change did not settle")


def test_queued_edits_revision_conflict_idempotency_and_current_exports(generated):
    run, vid = generated
    run.detail.variants[0].metrics.issues_total = 999
    slide_id = api.STORE.slides[vid][1].slide_plan_id
    body = {"slide_id": slide_id, "action": "shorter", "expected_revision": 1}
    response = client.post(f"{A}/variants/{vid}/changes", json=body,
                           headers={"Idempotency-Key": "first-change"})
    assert response.status_code == 202, response.text
    item = response.json()
    assert item["target_revision"] == 2
    repeat = client.post(f"{A}/variants/{vid}/changes", json=body,
                         headers={"Idempotency-Key": "first-change"})
    assert repeat.json() == item
    conflict = client.post(f"{A}/variants/{vid}/changes", json=body)
    assert conflict.status_code == 409
    settled = wait_job(item["job_id"])
    assert settled["state"] == "completed", settled
    state = client.get(f"{A}/variants/{vid}/changes").json()
    assert state["revision"] == state["accepted_revision"] == 2
    assert state["items"][0]["state"] == "completed"
    assert {"pptx", "pdf", "quality_passport"} <= run.variant_artifacts[vid].keys()
    assert api.STORE.audits[api._audit_id_of(vid)].deck_revision == 2
    assert run.detail.variants[0].metrics.issues_total == api.STORE.audits[
        api._audit_id_of(vid)].issue_count
    passport = json.loads(api.STORE.artifacts[run.variant_artifacts[vid]["quality_passport"]].data)
    assert passport["metrics"]["validity"]["opens_cleanly"]
    assert passport['deck_revision'] == 2
    assert any(f['feature'] == 'audit.contextual.coverage' and f['strategy'] == 'pending'
               for f in passport['fallbacks'])
    cards = client.get(f"{A}/variants/{vid}/slide-drafts").json()["slides"]
    assert all(card["revision"] == 2 for card in cards)
    events = client.get(f"{A}/jobs/{item['job_id']}/events").text
    assert events.index("slide.updated") < events.index("job.completed")


def test_accept_during_compilation_and_cancel_dependency(generated, monkeypatch, wait_generation):
    old_run, _ = generated
    entered, release = threading.Event(), threading.Event()
    original = api.pipeline.generate

    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return original(*args, **kwargs)

    monkeypatch.setattr(api.pipeline, "generate", paused)
    body = old_run.request.model_dump(mode="json")
    response = client.post(f"{A}/projects/{old_run.detail.project_id}/generations", json=body)
    accepted = response.json()
    try:
        assert entered.wait(10)
        run = api.STORE.runs[accepted["generation_id"]]
        vid = accepted["variant_ids"][0]
        sid = run.variant_plans[vid].slides[1].id
        first = client.post(f"{A}/variants/{vid}/changes", json={
            "slide_id": sid, "action": "shorter", "expected_revision": 1,
        })
        assert first.status_code == 202, first.text
        second = client.post(f"{A}/variants/{vid}/changes", json={
            "slide_id": sid, "action": "shorter", "expected_revision": 2,
        }).json()
        assert wait_queued(first.json()["job_id"])
        canceled = client.post(f"{A}/variants/{vid}/changes/{second['id']}/cancel")
        assert canceled.json()["state"] == "canceled"
    finally:
        release.set()
    result = wait_generation(client, accepted["generation_id"])
    assert result["state"] == "completed", result
    assert wait_job(first.json()["job_id"])["state"] == "completed"
    assert run.deck_revision[vid] == 2
    assert changes.accepted_revision(run, vid) == 2
    assert any(e["event"] == "slide.updated" for e in api.STORE.job_events[run.detail.job_id])


def wait_queued(job_id):
    return api.STORE.jobs[job_id].state == api.JobState.queued


def test_cancelled_generation_settles_waiting_changes(generated):
    run, vid = generated
    # No worker runs: reproduce a pending request from a now unavailable base.
    run.base_ready[vid] = False
    original_artifacts = run.variant_artifacts.pop(vid)
    item = client.post(f"{A}/variants/{vid}/changes", json={
        "slide_id": api.STORE.slides[vid][0].slide_plan_id,
        "action": "shorter", "expected_revision": 1,
    }).json()
    try:
        changes.settle_unavailable(run)
        assert wait_job(item["job_id"])["state"] == "canceled"
    finally:
        run.variant_artifacts[vid] = original_artifacts


@pytest.mark.parametrize("action", ["explode", "style:unknown"])
def test_unknown_action_is_not_accepted(generated, action):
    _, vid = generated
    response = client.post(f"{A}/variants/{vid}/changes", json={
        "slide_id": api.STORE.slides[vid][0].slide_plan_id,
        "action": action, "expected_revision": 1,
    })
    assert response.status_code == 422


def test_failed_edit_cancels_dependent_and_restores_accepted_revision(generated):
    run, vid = generated
    run.base_ready[vid] = False
    sid = api.STORE.slides[vid][0].slide_plan_id
    # other_chart on a cover is typed but semantically inapplicable.
    first = client.post(f"{A}/variants/{vid}/changes", json={
        "slide_id": sid, "action": "other_chart", "expected_revision": 1,
    }).json()
    second = client.post(f"{A}/variants/{vid}/changes", json={
        "slide_id": sid, "action": "shorter", "expected_revision": 2,
    }).json()
    run.base_ready[vid] = True
    changes.drain(run.detail.id, vid)
    assert wait_job(first["job_id"])["state"] == "failed"
    assert wait_job(second["job_id"])["state"] == "canceled"
    assert changes.accepted_revision(run, vid) == 1
    assert run.deck_revision.get(vid, 1) == 1


def test_snapshot_recovers_interrupted_queue(generated, tmp_path):
    from deckdna.api.persistence import load_store, save_store

    run, vid = generated
    run.base_ready[vid] = False
    item = client.post(f"{A}/variants/{vid}/changes", json={
        "slide_id": api.STORE.slides[vid][0].slide_plan_id,
        "action": "shorter", "expected_revision": 1,
    }).json()
    path = tmp_path / "state.pkl"
    save_store(api.STORE, path)
    fresh = api._Store()
    assert load_store(fresh, path)
    recovered = fresh.runs[run.detail.id]
    assert recovered.changes[vid][0]["state"] == "failed"
    assert fresh.jobs[item["job_id"]].error.code == "execution_interrupted"
    assert changes.accepted_revision(recovered, vid) == 1
    # Clean original pending item so later snapshot tests have no active queue.
    client.post(f"{A}/variants/{vid}/changes/{item['id']}/cancel")
