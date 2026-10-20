"""ADR-034 §8: intake → one autopilot job → deck, resume and human control."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from deckdna.api import app as api
from deckdna.api.persistence import load_store, save_store
from deckdna.contracts.dataset import InsightKind
from deckdna.providers.mock import MockProvider
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
A = "/api/v1"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "STORE", api._Store())
    monkeypatch.setattr(api.settings, "state_snapshot", False)
    monkeypatch.setattr(api, "build_gateway", lambda *args, **kwargs: MockProvider())
    return TestClient(api.app)


def _intake(client, data=False):
    pid = client.post(f"{A}/projects", json={"name": "autopilot"}).json()["id"]
    assert client.get(f"{A}/projects/{pid}/session").json()["step"] == 1
    template = ROOT / "tests/fixtures/pptx/vk_tech_template.pptx"
    files = [("files", ("template.pptx", template.read_bytes(), "application/octet-stream"))]
    if data:
        path = ROOT / "examples/stage2/purchases.csv"
        files.append(("files", (path.name, path.read_bytes(), "text/csv")))
    result = client.post(f"{A}/projects/{pid}/intake", files=files,
                         data={"idea": "Итоги продаж для совета директоров"})
    assert result.status_code == 202, result.text
    session = client.get(f"{A}/projects/{pid}/session").json()
    assert session["step"] == 2 and session["completed_steps"] == [1]
    return pid, session["artifacts"]


def _wait(client, job_id):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        job = client.get(f"{A}/jobs/{job_id}").json()
        if job["state"] in ("completed", "failed", "canceled"):
            events = api.STORE.job_events.get(job_id, [])
            if events and (events[-1]["event"] in ("job.completed", "job.failed")
                           or events[-1].get("state") == "canceled"):
                return job
        time.sleep(0.1)
    raise AssertionError(f"autopilot timed out: {job}")


def test_autopilot_from_step_one_is_one_job_and_never_asks(client, tmp_path, monkeypatch):
    pid, artifacts = _intake(client)
    jobs_before = set(api.STORE.jobs)
    accepted = client.post(f"{A}/projects/{pid}/session/autopilot",
                           json={"from_step": 1, "use_llm": True,
                                 "progressive_previews": True})
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job_id"]
    # A second click while running subscribes to the same work.
    again = client.post(f"{A}/projects/{pid}/session/autopilot", json={"from_step": 1})
    assert again.json()["job_id"] == job_id
    job = _wait(client, job_id)
    assert job["state"] == "completed", job
    assert set(api.STORE.jobs) - jobs_before == {job_id}
    assert job["kind"] == "autopilot"
    assert {"plan_id", "generation_id", "deck_plan_id"} <= set(job["result_ids"])
    assert "preview_id" not in job["result_ids"]
    run = client.get(f"{A}/generations/{job['result_ids']['generation_id']}").json()
    assert run["job_id"] == job_id and run["state"] == "completed"
    assert len(run["variants"]) == 1
    # аудитория не выдумана из чипов анкеты — дословно из идеи автора
    assert run["deck_plan"]["brief"]["audience"] == "совета директоров"
    assert not run["deck_plan"]["brief"].get("call_to_action")
    assert any(
        "продаж" in (unit.get("text") or "").casefold()
        for slide in run["deck_plan"]["slides"] if slide["purpose"] not in ("title", "thank_you")
        for unit in slide["content_units"] if unit["kind"] != "title"
    ), "The supplied idea must appear in the deck body, not only on the cover"
    assert run["variants"][0]["deck_artifact_id"]
    assert client.get(f"{A}/projects/{pid}/questions").json()["questions"] == []
    events = api.STORE.job_events[job_id]
    assert any(e["event"] == "slide.ready" and e.get("status") == "draft" for e in events)
    assert api.STORE.runs[run["id"]].request.progressive_previews
    automatic = next(e for e in events if e["event"] == "questions.answered")
    assert automatic["answers"] == {}  # предложения не приписываются автору
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert {"understanding.ready", "questions.answered", "plan.slide",
            "style.selected", "slide.ready", "job.completed"} <= {e["event"] for e in events}
    assert all(e["job_id"] == job_id for e in events)
    assert sum(e["event"] == "job.completed" for e in events) == 1
    assert not any(e["event"] == "preview.ready" for e in events)
    assert next(e for e in events if e["event"] == "style.selected")["previews_skipped"]
    source_pack = api.STORE.content_packs[artifacts["content_pack_id"]]
    assert not any(i.confirmed for i in source_pack.insights if i.implication)
    session = client.get(f"{A}/projects/{pid}/session").json()
    assert session["step"] == 7 and session["artifacts"]["generation_id"] == run["id"]
    assert session["skipped_steps"] == [2, 3, 4, 5]
    # ADR-031 snapshot contains the full session and its journal.
    path = tmp_path / "state/store.pkl"
    save_store(api.STORE, path)
    fresh = api._Store()
    assert load_store(fresh, path)
    monkeypatch.setattr(api, "STORE", fresh)
    assert client.get(f"{A}/projects/{pid}/session").json() == session
    assert fresh.job_events[job_id] == events
    # Replacing source material invalidates old plan, preview and run selection.
    upload = client.post(f"{A}/projects/{pid}/content-packs",
                         files={"files": ("new.md", b"# New\n\nDifferent content.",
                                           "text/markdown")})
    assert upload.status_code == 202
    changed = client.get(f"{A}/projects/{pid}/session").json()
    assert changed["step"] == 2 and changed["artifacts"]["generation_id"] is None
    assert changed["artifacts"]["plan_id"] is None and not changed["skipped_steps"]


def test_autopilot_confirms_only_strong_facts_without_implications(client, monkeypatch):
    pid, artifacts = _intake(client, data=True)
    pack_id = artifacts["content_pack_id"]
    pack = api.STORE.content_packs[pack_id]
    strong, recommendation, implication, weak, *rest = pack.insights
    strong = strong.model_copy(update={"strength": 1.0, "implication": None,
                                       "confirmed": False, "needs_confirmation": False})
    recommendation = recommendation.model_copy(update={
        "kind": InsightKind.recommendation, "strength": 1.0, "confirmed": False,
        "implication": None, "needs_confirmation": True,
    })
    implication = implication.model_copy(update={
        "strength": 1.0, "implication": "Стоит поощрить команду продаж.",
        "confirmed": False, "needs_confirmation": True,
    })
    weak = weak.model_copy(update={"strength": 0.1, "confirmed": False})
    api.STORE.content_packs[pack_id] = pack.model_copy(update={
        "insights": [strong, recommendation, implication, weak, *rest],
    })
    # On a later model pass, new implications must not inherit approval
    # from the automatically confirmed bare facts.
    from deckdna.analysis.interpret import INTERPRET_PROMPT

    calls = []

    def interpret(payload):
        calls.append(payload)
        if len(calls) == 1:
            return {"items": []}
        return {"items": [{"id": i["id"], "meaning": "Результат стоит обсудить.",
                           "implication": "Предлагаю обсудить результат с командой."}
                          for i in payload["items"]]}

    monkeypatch.setattr(api, "build_gateway", lambda *a, **kw: MockProvider(
        fixtures={INTERPRET_PROMPT: interpret},
    ))
    accepted = client.post(f"{A}/projects/{pid}/session/autopilot",
                           json={"from_step": 1, "use_llm": True}).json()
    job = _wait(client, accepted["job_id"])
    assert job["state"] == "completed", job
    insights = {i.id: i for i in api.STORE.content_packs[pack_id].insights}
    assert insights[strong.id].confirmed
    assert not any(insights[i.id].confirmed for i in (recommendation, implication, weak))
    assert insights[implication.id].needs_confirmation
    run = api.STORE.runs[job["result_ids"]["generation_id"]]
    assert all(s.title_intent != "Что предлагаем" for s in run.detail.deck_plan.slides)
    result_pack = api.STORE.content_packs[run.detail.content_pack_id]
    assert not any(i.confirmed and i.implication for i in result_pack.insights)


def test_session_questions_and_autopilot_preserve_the_saved_plan(client):
    pid, artifacts = _intake(client)
    pack_id = artifacts["content_pack_id"]
    client.get(f"{A}/content-packs/{pack_id}/understanding")
    # аудитория названа в идее, но пак из одной фразы худой — остаётся
    # content_short, шаг 3 не пропускается
    assert client.get(f"{A}/projects/{pid}/session").json()["step"] == 3
    questions = client.get(f"{A}/projects/{pid}/questions").json()["questions"]
    assert any(q["id"] == "content_short" for q in questions)
    answers = {q["id"]: next(o["value"] for o in q["options"] if o.get("recommended"))
               for q in questions}
    brief = client.post(f"{A}/projects/{pid}/answers", json={"answers": answers}).json()["brief"]
    assert client.get(f"{A}/projects/{pid}/session").json()["step"] == 4
    planned = client.post(f"{A}/projects/{pid}/plans", json={
        "content_pack_id": pack_id, "template_id": artifacts["template_id"],
        "brief": brief, "strategies": ["balanced"], "use_llm": False,
    }).json()
    plan_id = planned["plan_id"]
    assert client.get(f"{A}/projects/{pid}/session").json()["step"] == 5
    entry = api.STORE.plans[plan_id].plans[0]
    entry.deck_plan.slides[0].title_intent = "Заголовок, согласованный автором"
    job_id = client.post(f"{A}/projects/{pid}/session/autopilot",
                         json={"from_step": 5, "use_llm": False}).json()["job_id"]
    job = _wait(client, job_id)
    assert job["state"] == "completed", job
    assert job["result_ids"]["plan_id"] == plan_id and len(api.STORE.plans) == 1
    run = api.STORE.runs[job["result_ids"]["generation_id"]]
    assert run.detail.deck_plan.slides[0].title_intent == entry.deck_plan.slides[0].title_intent
    stored = api.STORE.plans[plan_id].plans[0].deck_plan
    edited = client.post(f"{A}/plans/{plan_id}/slides/{stored.slides[1].id}/actions",
                         json={"action": "shorten"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["updated_at"]
    session = client.get(f"{A}/projects/{pid}/session").json()
    assert session["step"] == 5 and session["artifacts"]["generation_id"] is None
    assert session["artifacts"]["preview_id"] is None


def test_autopilot_validation_and_failure_settle_the_job(client, monkeypatch):
    pid = client.post(f"{A}/projects", json={"name": "empty"}).json()["id"]
    invalid = client.post(f"{A}/projects/{pid}/session/autopilot", json={"from_step": 1})
    assert invalid.status_code == 422
    assert client.get(f"{A}/projects/missing/session").status_code == 404
    pid, _ = _intake(client)
    invalid = client.post(f"{A}/projects/{pid}/session/autopilot", json={"from_step": 0})
    assert invalid.status_code == 422

    def fail(*args, **kwargs):
        raise RuntimeError("generation failure fixture")

    monkeypatch.setattr(api, "_create_generation", fail)
    accepted = client.post(f"{A}/projects/{pid}/session/autopilot", json={"from_step": 1})
    job_id = accepted.json()["job_id"]
    job = _wait(client, job_id)
    assert job["state"] == "failed" and job["error"]["stage"] == "generation"
    assert "generation failure fixture" in job["error"]["message"]
    assert api.STORE.job_events[job_id][-1]["event"] == "job.failed"


def test_interrupted_autopilot_is_retryable_after_snapshot(client, tmp_path, monkeypatch):
    pid, _ = _intake(client)
    job = api.Job(id="job_interrupted", kind=api.JobKind.autopilot, state=api.JobState.running,
                  project_id=pid, created_at=api._utcnow(), stage="plan", progress=0.2)
    api.STORE.jobs[job.id] = job
    project = api.STORE.projects[pid]
    run = api._create_generation(project, api.GenerationCreate(
        template_id=project.template_id, content_pack_id=project.content_pack_id,
        brief=api.Brief(purpose="Итоги", audience="команда", language="ru", target_slide_count=5),
        variants=[api.VariantRequest(strategy="balanced")],
    ), parent_run_id=None, job_id=job.id)
    api._journal(job.id, "stage.started", {"stage": "plan"})
    path = tmp_path / "state/store.pkl"
    save_store(api.STORE, path)
    fresh = api._Store()
    assert load_store(fresh, path)
    monkeypatch.setattr(api, "STORE", fresh)
    restored = client.get(f"{A}/jobs/{job.id}").json()
    assert restored["state"] == "failed" and restored["error"]["code"] == "execution_interrupted"
    assert fresh.job_events[job.id][-1]["event"] == "job.failed"
    assert client.get(f"{A}/projects/{pid}/session").json()["step"] == 6
    restored_run = fresh.runs[run.detail.id]
    assert restored_run.detail.state == api.JobState.failed
    assert all(v.status == api.JobState.failed for v in restored_run.detail.variants)
