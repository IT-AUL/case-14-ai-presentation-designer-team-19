"""Commands are applied only with grounded intent and explicit target/revision."""

from __future__ import annotations

import asyncio

import pytest
from deckdna.api import app as api
from deckdna.api.dependencies import is_current, save_brief
from deckdna.assistant.commands import BriefEdit, ParsedCommand, parse_command
from fastapi.testclient import TestClient

A = "/api/v1"
client = TestClient(api.app)


def project():
    return client.post(f"{A}/projects", json={"name": "commands"}).json()["id"]


def test_local_command_without_target_clarifies_without_work():
    pid = project()
    before = (set(api.STORE.jobs), set(api.STORE.runs), set(api.STORE.style_previews))
    response = client.post(f"{A}/projects/{pid}/commands", json={"text": "Сократи"})
    assert response.status_code == 200
    assert response.json()["status"] == "clarification"
    assert response.json()["parser"] == "local"
    assert before == (set(api.STORE.jobs), set(api.STORE.runs), set(api.STORE.style_previews))


def test_explicit_audience_updates_context_once_and_rejects_old_revision():
    pid = project()
    body = {"text": "аудитория: инвесторы", "expected_context_revision": 1}
    response = client.post(f"{A}/projects/{pid}/commands", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["brief"]["audience"] == "инвесторы"
    assert response.json()["context_revision"] == 2
    stale = client.post(f"{A}/projects/{pid}/commands", json=body)
    assert stale.status_code == 409
    same = client.post(f"{A}/projects/{pid}/commands",
                       json={**body, "expected_context_revision": 2})
    assert same.json()["context_revision"] == 2 and same.json()["invalidated"] == []


@pytest.mark.asyncio
async def test_model_must_quote_author_preferences_and_is_called_once():
    class Gateway:
        calls = 0

        async def text_json(self, name, payload, schema):
            self.calls += 1
            assert name == "assistant_command"
            return ParsedCommand(intent="update_brief", brief=[
                BriefEdit(field="audience", value="инвесторов", quote="для инвесторов"),
            ])

    gateway = Gateway()
    parsed, source = await parse_command("Теперь для инвесторов", {}, gateway)
    assert parsed.intent == "update_brief" and source == "model" and gateway.calls == 1
    parsed, source = await parse_command("Покажи выручку", {}, gateway)
    assert parsed.intent == "clarify" and source == "rejected" and gateway.calls == 2
    parsed, source = await parse_command("Сократи", {}, gateway)
    assert parsed.action == "shorter" and source == "local" and gateway.calls == 2


@pytest.mark.asyncio
async def test_unavailable_free_command_does_not_claim_success():
    parsed, source = await parse_command("Пусть выглядит убедительнее", {}, None)
    assert source == "unavailable" and parsed.intent == "clarify"


def test_changed_context_cancels_old_job_and_invalidates_history():
    pid = project()
    old = api._utcnow()
    api.STORE.jobs[pid] = api.Job(id=pid, kind=api.JobKind.planning,
                                state=api.JobState.running, progress=0,
                                created_at=old, project_id=pid)
    saved = save_brief(pid, {"purpose": "Отчёт"})
    assert saved["context_revision"] == 2
    assert not is_current(pid, old)
    assert api.STORE.jobs[pid].state == api.JobState.canceled
    assert api.STORE.job_events[pid][-1]["superseded"]
    assert is_current(pid, api._utcnow())


def test_old_model_response_cannot_replace_new_brief(monkeypatch):
    import deckdna.assistant.commands as parser

    pid = project()

    async def delayed(*args):
        save_brief(pid, {"audience": "команда"})
        await asyncio.sleep(0)
        return ParsedCommand(intent="update_brief", brief=[
            BriefEdit(field="audience", value="инвесторы", quote="инвесторы"),
        ]), "model"

    monkeypatch.setattr("deckdna.api.commands.parse_command", delayed)
    response = client.post(f"{A}/projects/{pid}/commands",
                           json={"text": "инвесторы", "use_llm": False})
    assert response.status_code == 409
    assert api.STORE.project_briefs[pid]["audience"] == "команда"
    assert parser.quick_parse("заголовок: Итоги года").text == "Итоги года"


def test_context_change_resets_only_unedited_model_interpretations():
    from pathlib import Path

    pid = project()
    csv = Path(__file__).resolve().parents[2] / "examples/stage2/purchases.csv"
    upload = client.post(f"{A}/projects/{pid}/content-packs", files={
        "files": (csv.name, csv.read_bytes(), "text/csv"),
    }).json()
    pack_id = upload["content_pack"]["id"]
    pack = api.STORE.content_packs[pack_id]
    assert len(pack.insights) >= 2
    first, second = pack.insights[:2]
    model_first = first.model_copy(update={"meaning": "Модельное пояснение"})
    model_second = second.model_copy(update={"meaning": "Ещё одно пояснение"})
    api.STORE.content_packs[pack_id] = pack.model_copy(update={"insights": [
        model_first, model_second.model_copy(update={"meaning": "Авторская правка"}),
        *pack.insights[2:],
    ]})
    api.STORE.assistant_progress[pid] = {"interpretation_outputs": {pack_id: {
        first.id: {"before": first.model_dump(mode="json"),
                   "after": model_first.model_dump(mode="json")},
        second.id: {"before": second.model_dump(mode="json"),
                    "after": model_second.model_dump(mode="json")},
    }}}
    save_brief(pid, {"audience": "инвесторы"})
    current = api.STORE.content_packs[pack_id].insights
    assert current[0] == first
    assert current[1].meaning == "Авторская правка"
