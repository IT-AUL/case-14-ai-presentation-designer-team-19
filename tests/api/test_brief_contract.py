"""B01: author preferences must not silently disappear at API boundaries."""

import copy

import pytest
from deckdna.api import app as api
from deckdna.errors import DeckDNAError
from fastapi.testclient import TestClient

A = "/api/v1"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "STORE", api._Store())
    monkeypatch.setattr(api.settings, "state_snapshot", False)
    return TestClient(api.app)


def project(client, **values):
    return client.post(f"{A}/projects", json={"name": "brief", **values}).json()["id"]


def test_canonical_answers_preserve_every_brief_field_and_aliases(client):
    pid = project(client)
    answers = {"purpose": "Обзор", "language": "en", "target_slide_count": 6,
               "length": "6", "content_fill": "shrink", "content_short": "notes",
               "audience": "команда", "mandatory_sections": ["Риски"],
               "forbidden_claims": ["гарантируем"], "explain_slide": False}
    result = client.post(f"{A}/projects/{pid}/answers", json={"answers": answers})
    assert result.status_code == 200, result.text
    body = result.json()
    for key in set(answers) - {"length", "content_short"}:
        assert body["brief"][key] == answers[key]
    assert body["follow_up"] == {"notes": True}
    assert api._brief_model(pid, None).mandatory_sections == ["Риски"]
    assert body["brief_origins"]["target_slide_count"]["source"] == "user"
    revision = body["context_revision"]
    repeated = client.post(f"{A}/projects/{pid}/answers", json={"answers": answers}).json()
    assert repeated["context_revision"] == revision
    session = client.get(f"{A}/projects/{pid}/session").json()
    assert session["context_revision"] == revision
    assert session["brief_origins"] == repeated["brief_origins"]


@pytest.mark.parametrize("answers", [
    {"target_slides": 6}, {"content_fill": "invent"},
    {"length": "abc"}, {"length": 2}, {"target_slide_count": True},
    {"target_slide_count": 6, "length": 7},
    {"content_fill": "expand", "content_short": "shrink"},
    {"content_short": "invent"}, {"mandatory_sections": "Риски"},
])
def test_invalid_answers_are_typed_atomic_errors(client, answers):
    pid = project(client)
    before = copy.deepcopy(api.STORE.project_briefs.get(pid, {}))
    response = client.post(f"{A}/projects/{pid}/answers", json={"answers": answers})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_input"
    assert api.STORE.project_briefs.get(pid, {}) == before
    assert not api.STORE.jobs


def test_canonical_answer_set_finishes_same_question_round(client):
    pid = project(client)
    client.post(f"{A}/projects/{pid}/intake", data={"idea": "Обзор продукта"})
    api.STORE.project_briefs[pid]["target_slide_count"] = 12
    response = client.post(f"{A}/projects/{pid}/answers", json={"answers": {
        "audience": "команда", "content_fill": "expand",
    }})
    assert response.status_code == 200, response.text
    assert client.get(f"{A}/projects/{pid}/questions").json()["questions"] == []


def test_project_explicit_preferences_survive_intake_and_patch(client):
    pid = project(client, target_slide_count=4, default_language="en")
    client.post(f"{A}/projects/{pid}/intake", data={"idea": "A presentation assistant"})
    assert api.STORE.project_briefs[pid]["target_slide_count"] == 4
    assert api.STORE.project_briefs[pid]["language"] == "en"
    response = client.patch(f"{A}/projects/{pid}", json={"target_slide_count": 6})
    assert response.status_code == 200
    assert api.STORE.project_briefs[pid]["target_slide_count"] == 6
    # Omitted project defaults are not manufactured into author preferences.
    other = project(client)
    assert "target_slide_count" not in api.STORE.project_briefs.get(other, {})


@pytest.mark.parametrize("brief", ['[]', '{"targte_slide_count":6}'])
def test_invalid_upload_brief_does_not_leave_artifacts(client, brief):
    pid = project(client)
    response = client.post(f"{A}/projects/{pid}/content-packs", data={"brief": brief},
                           files={"files": ("idea.md", b"# Topic\nContent", "text/markdown")})
    assert response.status_code == 422, response.text
    assert not api.STORE.artifacts and not api.STORE.content_packs


def test_answers_reject_stale_revision_and_invalidate_only_changes(client):
    pid = project(client)
    body = {"answers": {"target_slide_count": 4}}
    first = client.post(f"{A}/projects/{pid}/answers", json=body).json()
    assert first["context_revision"] > 1
    when = api._utcnow()
    api.STORE.jobs["old"] = api.Job(id="old", kind=api.JobKind.planning,
                                    state="running", progress=0, project_id=pid,
                                    created_at=when)
    stale = client.post(f"{A}/projects/{pid}/answers", json={
        "answers": {"target_slide_count": 6}, "expected_context_revision": 1,
    })
    assert stale.status_code == 409
    assert api.STORE.jobs["old"].state == api.JobState.running
    new = client.post(f"{A}/projects/{pid}/answers", json={
        "answers": {"target_slide_count": 6},
        "expected_context_revision": first["context_revision"],
    }).json()
    assert new["context_revision"] == first["context_revision"] + 1
    assert api.STORE.jobs["old"].state == api.JobState.canceled
    assert "plan" in new["invalidated"]


def test_direct_plan_brief_uses_same_revision_and_invalidation(client):
    pid = project(client)
    intake = client.post(f"{A}/projects/{pid}/intake", data={"idea": "Обзор продукта"}).json()
    api.STORE.jobs["old-plan"] = api.Job(
        id="old-plan", kind="planning", state="running", progress=0,
        project_id=pid, created_at=api._utcnow(),
    )
    response = client.post(f"{A}/projects/{pid}/plans", json={
        "content_pack_id": intake["content_pack_id"], "use_llm": False,
        "brief": {"purpose": "Новая задача", "audience": "команда", "language": "ru",
                  "target_slide_count": 3, "content_fill": "shrink"},
        "strategies": ["balanced"],
    })
    assert response.status_code == 200, response.text
    assert api.STORE.jobs["old-plan"].state == api.JobState.canceled
    session = client.get(f"{A}/projects/{pid}/session").json()
    assert session["context_revision"] > 1
    assert session["brief"]["purpose"] == "Новая задача"
    assert session["brief_origins"]["purpose"]["source"] == "user"


def test_autopilot_receives_explicit_answers_without_default_overwrite(client, monkeypatch):
    from pathlib import Path

    from deckdna.api.assistant_session import AutopilotRequest, _execute_autopilot

    pid = project(client, target_slide_count=4)
    template = Path("tests/fixtures/pptx/vk_tech_template.pptx")
    client.post(f"{A}/projects/{pid}/intake", data={"idea": "Итоги команды"},
                files=[("files", (template.name, template.read_bytes(),
                                   "application/octet-stream"))])
    assert client.post(f"{A}/projects/{pid}/answers", json={"answers": {
        "target_slide_count": 6, "content_fill": "shrink", "language": "en",
        "purpose": "Review", "audience": "team",
    }}).status_code == 200

    async def capture(project_id, body, *args, **kwargs):
        assert body.brief.target_slide_count == 6
        assert body.brief.content_fill.value == "shrink"
        assert body.brief.language == "en" and body.brief.purpose == "Review"
        raise DeckDNAError("invalid_input", "captured before expensive planning")

    monkeypatch.setattr(api, "_build_plan_set", capture)
    api.STORE.jobs["capture"] = api.Job(id="capture", kind="autopilot", state="queued",
                                        progress=0, project_id=pid, created_at=api._utcnow())
    _execute_autopilot("capture", pid, AutopilotRequest(from_step=1, use_llm=False))
    error = api.STORE.jobs["capture"].error
    assert error and error.message == "captured before expensive planning", error


def test_explicit_count_change_rebinds_hard_limit_but_plan_copy_does_not(client):
    pid = project(client)
    saved = client.post(f"{A}/projects/{pid}/answers", json={"answers": {
        "target_slide_count": 4, "slide_count_policy": "exact",
    }}).json()["brief"]
    assert saved["requested_slide_count"] == 4
    changed = client.post(f"{A}/projects/{pid}/answers", json={"answers": {
        "length": 6,
    }}).json()["brief"]
    assert changed["target_slide_count"] == changed["requested_slide_count"] == 6
    b = api.Brief.model_validate(changed).model_copy(update={"target_slide_count": 3})
    assert b.requested_slide_count == 6


def test_older_client_cannot_drop_saved_hard_policy_by_omitting_new_fields(client):
    pid = project(client)
    cp = client.post(f"{A}/projects/{pid}/intake", data={"idea": "Обзор продукта"}).json()
    response = client.post(f"{A}/projects/{pid}/answers", json={"answers": {
        "target_slide_count": 6, "slide_count_policy": "maximum",
    }})
    assert response.status_code == 200, response.text
    plan = client.post(f"{A}/projects/{pid}/plans", json={
        "content_pack_id": cp["content_pack_id"], "use_llm": False,
        "brief": {"purpose": "Обзор", "audience": "команда", "language": "ru",
                  "target_slide_count": 6}, "strategies": ["balanced"],
    })
    assert plan.status_code == 200, plan.text
    brief = plan.json()["plans"][0]["deck_plan"]["brief"]
    assert brief["slide_count_policy"] == "maximum" and brief["requested_slide_count"] == 6
