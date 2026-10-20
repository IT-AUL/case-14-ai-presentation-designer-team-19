"""ADR-036: real TCP SSE, not TestClient's buffered response iterator."""

from __future__ import annotations

import json
import socket
import threading
import time
from uuid import uuid4

import httpx
import pytest
import uvicorn
from deckdna.api import app as api
from fastapi.testclient import TestClient


@pytest.fixture
def live_api():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(
        api.app, log_level="error", lifespan="off", access_log=False,
    ))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}/api/v1"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()


@pytest.fixture
def job():
    job_id = f"sse-{uuid4().hex}"
    api.STORE.jobs[job_id] = api.Job(
        id=job_id, kind=api.JobKind.generation, state=api.JobState.running,
        progress=0.0, created_at=api._utcnow(),
    )
    api._journal(job_id, "job.state_changed", {"state": "running"})
    yield job_id
    api.STORE.jobs.pop(job_id, None)
    api.STORE.job_events.pop(job_id, None)


def _data(lines):
    for line in lines:
        if line.startswith("data:"):
            return json.loads(line[5:])
    raise AssertionError("stream closed without event")


@pytest.mark.parametrize("state", ["completed", "failed", "canceled"])
def test_new_event_arrives_before_job_finishes_and_stream_closes(live_api, job, state):
    with httpx.Client(timeout=5) as client:
        with client.stream("GET", f"{live_api}/jobs/{job}/events") as response:
            assert response.status_code == 200
            assert response.headers["x-accel-buffering"] == "no"
            lines = response.iter_lines()
            assert _data(lines)["sequence"] == 1
            # This event did not exist when the connection opened.
            api._journal(job, "slide.ready", {"slide_id": "slide-a", "revision": 1})
            event = _data(lines)
            assert event["event"] == "slide.ready" and event["sequence"] == 2
            assert api.STORE.jobs[job].state == api.JobState.running
            api._set_job(job, state=api.JobState(state), finished_at=api._utcnow())
            api._journal(job, "job.state_changed", {"state": state})
            assert _data(lines)["state"] == state
            assert not [line for line in lines if line.startswith("data:")]


def test_reconnect_cursor_and_disconnect_do_not_cancel_job(live_api, job):
    with httpx.Client(timeout=5) as client:
        with client.stream("GET", f"{live_api}/jobs/{job}/events") as response:
            assert _data(response.iter_lines())["sequence"] == 1
        assert api.STORE.jobs[job].state == api.JobState.running
        api._journal(job, "slide.ready", {"slide_id": "slide-a"})
        with client.stream(
            "GET", f"{live_api}/jobs/{job}/events?after=1",
            headers={"Last-Event-ID": "2"},
        ) as response:
            api._journal(job, "slide.ready", {"slide_id": "slide-b"})
            event = _data(response.iter_lines())
            assert event["sequence"] == 3 and event["slide_id"] == "slide-b"
        api._set_job(job, state=api.JobState.completed)
        api._journal(job, "job.completed", {"state": "completed"})
        replay = client.get(f"{live_api}/jobs/{job}/events?after=3")
        assert _data(iter(replay.text.splitlines()))["sequence"] == 4
        assert not client.get(f"{live_api}/jobs/{job}/events?after=4").text


def test_final_state_before_final_event_does_not_drop_payload(live_api, job):
    with httpx.Client(timeout=5) as client:
        with client.stream("GET", f"{live_api}/jobs/{job}/events?after=1") as response:
            api._set_job(job, state=api.JobState.completed)
            # Wider than the subscriber polling interval: reproduce the race.
            time.sleep(0.25)
            api._journal(job, "job.completed", {"state": "completed", "result": "actual"})
            assert _data(response.iter_lines())["result"] == "actual"


@pytest.mark.parametrize("cursor", ["bad", "-1", "1.0", "9" * 30])
def test_invalid_resume_header_is_rejected(job, cursor):
    response = TestClient(api.app).get(
        f"/api/v1/jobs/{job}/events", headers={"Last-Event-ID": cursor},
    )
    assert response.status_code == 422


def test_cancelled_journal_closes_even_with_late_legacy_stage_events(live_api, job):
    api._set_job(job, state=api.JobState.canceled)
    api._journal(job, "job.state_changed", {"state": "canceled"})
    for _ in range(5):
        api._journal(job, "stage.started", {"stage": "audit"})
    with httpx.Client(timeout=5) as client:
        response = client.get(f"{live_api}/jobs/{job}/events")
    assert response.status_code == 200
    assert response.text.count("data:") == 7
