"""ADR-035: слушать один раз, спрашивать по влиянию, не выдумывать ответы."""

from pathlib import Path

import pytest
from deckdna.api import app as api
from deckdna.assistant.context import CONTEXT_PROMPT
from deckdna.assistant.questions import build_questions
from deckdna.ingestion.content_parsers import parse_file
from deckdna.providers.mock import MockProvider
from fastapi.testclient import TestClient

A = "/api/v1"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "STORE", api._Store())
    monkeypatch.setattr(api.settings, "state_snapshot", False)
    return TestClient(api.app)


def intake(client, idea):
    pid = client.post(f"{A}/projects", json={"name": "listen"}).json()["id"]
    response = client.post(f"{A}/projects/{pid}/intake", data={"idea": idea})
    assert response.status_code == 202, response.text
    return pid, response.json()["content_pack_id"]


def test_understanding_extracts_explicit_context_once_and_questions_reuse_it(client, monkeypatch):
    pid, pack_id = intake(client, "Итоги продаж для совета директоров, 5 слайдов")
    gateway = MockProvider(fixtures={CONTEXT_PROMPT: {"fields": [
        {"field": "audience", "value": "совета директоров", "source_id": "brief:purpose",
         "quote": "для совета директоров"},
        {"field": "target_slide_count", "value": "5", "source_id": "brief:purpose",
         "quote": "5 слайдов"},
    ]}})
    monkeypatch.setattr(api, "build_gateway", lambda: gateway)
    first = client.get(f"{A}/content-packs/{pack_id}/understanding?use_llm=true")
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["brief"]["audience"] == "совета директоров"
    assert result["brief"]["target_slide_count"] == 5
    assert len(result["context"]["fields"]) == 2
    second = client.get(f"{A}/content-packs/{pack_id}/understanding?use_llm=true").json()
    assert second["context"] == result["context"]
    assert len(gateway.calls) == 1
    questions = client.get(f"{A}/projects/{pid}/questions").json()["questions"]
    assert all(q["id"] not in {"audience", "length"} for q in questions)


def test_full_brief_and_question_refresh_do_not_call_a_model(client, monkeypatch):
    pid, pack_id = intake(client, "Итоги продаж")
    api.STORE.project_briefs[pid].update(audience="команда", target_slide_count=5)

    def forbidden(*args, **kwargs):
        raise AssertionError("complete context/question refresh must not build a gateway")

    monkeypatch.setattr(api, "build_gateway", forbidden)
    result = client.get(f"{A}/content-packs/{pack_id}/understanding?use_llm=true").json()
    assert result["context"]["status"] == "known"
    for _ in range(2):
        assert client.get(f"{A}/projects/{pid}/questions").status_code == 200


def test_known_audience_does_not_hide_explicit_length_in_author_request(client, monkeypatch):
    pid, pack_id = intake(client, "Обзор продаж, 5 слайдов")
    api.STORE.project_briefs[pid]["audience"] = "команда"
    gateway = MockProvider(fixtures={CONTEXT_PROMPT: {"fields": [
        {"field": "target_slide_count", "value": "5", "source_id": "brief:purpose",
         "quote": "5 слайдов"},
    ]}})
    monkeypatch.setattr(api, "build_gateway", lambda: gateway)
    result = client.get(f"{A}/content-packs/{pack_id}/understanding?use_llm=true").json()
    assert result["brief"]["target_slide_count"] == 5
    assert result["brief"]["audience"] == "команда"


def test_unsupported_mock_does_not_synthesize_preferences(client, monkeypatch):
    pid, pack_id = intake(client, "Итоги продаж")
    gateway = MockProvider()
    monkeypatch.setattr(api, "build_gateway", lambda: gateway)
    result = client.get(f"{A}/content-packs/{pack_id}/understanding?use_llm=true").json()
    assert result["context"] == {"status": "unavailable", "fields": []}
    assert "audience" not in result["brief"]
    assert gateway.calls == []
    assert "audience" in [q["id"] for q in
                          client.get(f"{A}/projects/{pid}/questions").json()["questions"]]


def test_explicit_answers_win_over_model_and_unknown_citations_are_rejected(client, monkeypatch):
    pid, pack_id = intake(client, "Итоги продаж за 2025 год для команды")
    api.STORE.project_briefs[pid]["audience"] = "клиенты"
    # Оставляем purpose пустым, чтобы разбор ещё был нужен.
    api.STORE.project_briefs[pid]["purpose"] = ""
    pack = api.STORE.content_packs[pack_id]
    source = next(f"b:{s.id}:{i}" for s in pack.sections for i, b in enumerate(s.blocks)
                  if b.text and "2025" in b.text)
    gateway = MockProvider(fixtures={CONTEXT_PROMPT: {"fields": [
        {"field": "audience", "value": "команды", "source_id": source,
         "quote": "для команды"},
        {"field": "target_slide_count", "value": "5", "source_id": source,
         "quote": "2025 год"},
        {"field": "key_message", "value": "Мы удвоились", "source_id": source,
         "quote": "Итоги продаж"},
        {"field": "tone", "value": "деловой", "source_id": "invented",
         "quote": "деловой"},
    ]}})
    monkeypatch.setattr(api, "build_gateway", lambda: gateway)
    result = client.get(f"{A}/content-packs/{pack_id}/understanding?use_llm=true").json()
    assert result["brief"]["audience"] == "клиенты"
    assert result["context"]["fields"] == []
    assert "target_slide_count" not in result["brief"]


def test_question_budget_and_reversible_defaults():
    pack = parse_file(Path("tests/fixtures/content/poc_article.md"))
    result = build_questions(pack, {"target_slide_count": 40})
    assert len(result["questions"]) == 2
    assert result["questions"][0]["id"] == "content_short"
    assert "key_message" in result["deferred_questions"]
    compact = build_questions(pack, {})
    assert all(q["id"] not in {"length", "content_short", "call_to_action"}
               for q in compact["questions"])
    assert compact["suggested_brief"]["audience"] == ""
    assert "call_to_action" not in compact["suggested_brief"]
    assert 3 <= compact["suggested_brief"]["target_slide_count"] <= 12


def test_skip_moves_to_draft_without_making_up_audience_or_action(client):
    pid, _pack_id = intake(client, "Итоги продаж")
    result = client.post(f"{A}/projects/{pid}/answers", json={"answers": {}})
    assert result.status_code == 200, result.text
    brief = result.json()["brief"]
    assert brief["audience"] == "" and not brief.get("call_to_action")
    assert brief["target_slide_count"] == 3
    assert client.get(f"{A}/projects/{pid}/questions").json()["questions"] == []


def test_partial_answer_keeps_the_unresolved_essential_question(client):
    pid, _pack_id = intake(client, "Итоги продаж")
    api.STORE.project_briefs[pid]["target_slide_count"] = 12
    result = client.post(f"{A}/projects/{pid}/answers", json={
        "answers": {"audience": "команда"},
    })
    assert result.status_code == 200
    questions = client.get(f"{A}/projects/{pid}/questions").json()["questions"]
    assert all(q["id"] != "audience" for q in questions)
    assert any(q["id"] == "content_short" for q in questions)


def test_null_form_fields_are_not_treated_as_answers_or_delegation(client):
    pid, _pack_id = intake(client, "Итоги продаж")
    api.STORE.project_briefs[pid]["target_slide_count"] = 12
    response = client.post(f"{A}/projects/{pid}/answers", json={"answers": {
        "audience": None, "content_short": None,
    }})
    assert response.status_code == 200, response.text
    assert response.json()["brief"]["content_fill"] == "ask"
    assert client.get(f"{A}/projects/{pid}/questions").json()["questions"]


def test_stale_pack_cannot_rewrite_current_project_context(client, monkeypatch):
    pid, old = intake(client, "Итоги продаж")
    response = client.post(f"{A}/projects/{pid}/intake", data={"idea": "Обучение команды"})
    assert response.json()["content_pack_id"] != old
    gateway = MockProvider()
    monkeypatch.setattr(api, "build_gateway", lambda: gateway)
    result = client.get(f"{A}/content-packs/{old}/understanding?use_llm=true").json()
    assert result["context"]["status"] == "not_current"
    assert gateway.calls == []


def test_data_interpretation_waits_for_essential_answers(client, monkeypatch):
    from deckdna.analysis.interpret import INTERPRET_PROMPT

    pid = client.post(f"{A}/projects", json={"name": "data"}).json()["id"]
    data = Path("examples/stage2/purchases.csv")
    response = client.post(f"{A}/projects/{pid}/content-packs", files=[
        ("files", (data.name, data.read_bytes(), "text/csv")),
    ])
    pack_id = response.json()["content_pack"]["id"]
    gateway = MockProvider(fixtures={INTERPRET_PROMPT: {"items": []}})
    monkeypatch.setattr(api, "build_gateway", lambda: gateway)
    result = client.get(f"{A}/content-packs/{pack_id}/understanding?use_llm=true").json()
    assert result["datasets"]  # факты и мини-график доступны сразу
    assert result["interpretation"]["status"] == "deferred"
    assert gateway.calls == []
    client.post(f"{A}/projects/{pid}/answers", json={"answers": {}})
    result = client.get(f"{A}/content-packs/{pack_id}/understanding?use_llm=true").json()
    assert result["interpretation"]["status"] == "processed"
    assert [c["prompt"] for c in gateway.calls] == [INTERPRET_PROMPT]
    refreshed = client.get(f"{A}/content-packs/{pack_id}/understanding?use_llm=true").json()
    assert refreshed["interpretation"]["status"] == "cached"
    assert [c["prompt"] for c in gateway.calls] == [INTERPRET_PROMPT]


@pytest.mark.asyncio
async def test_concurrent_refresh_single_flights_context(client, monkeypatch):
    import asyncio

    from deckdna.assistant.context import ContextAnswer

    pid, pack_id = intake(client, "Итоги продаж")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    class SlowGateway:
        async def text_json(self, prompt, payload, schema):
            calls.append(prompt)
            entered.set()
            await release.wait()
            return ContextAnswer(fields=[])

    monkeypatch.setattr(api, "build_gateway", lambda: SlowGateway())
    pack = api.STORE.content_packs[pack_id]
    options = api._UnderstandingOptions(use_llm=True)
    first = asyncio.create_task(api._listen_context(pid, pack, options))
    await entered.wait()
    second = await api._listen_context(pid, pack, options)
    assert second["status"] == "pending"
    release.set()
    await first
    assert calls == [CONTEXT_PROMPT]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["insight", "brief"])
async def test_human_edit_wins_over_inflight_interpretation(client, monkeypatch, change):
    import asyncio

    from deckdna.analysis import interpret

    pid = client.post(f"{A}/projects", json={"name": "concurrent"}).json()["id"]
    data = Path("examples/stage2/purchases.csv")
    contents = await asyncio.to_thread(data.read_bytes)
    response = client.post(f"{A}/projects/{pid}/content-packs", files=[
        ("files", (data.name, contents, "text/csv")),
    ])
    pack_id = response.json()["content_pack"]["id"]
    api.STORE.project_briefs[pid] = {"purpose": "Итоги", "audience": "команда"}
    pack = api.STORE.content_packs[pack_id]
    before = pack.insights[0]
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(pack, brief, gateway):
        entered.set()
        await release.wait()
        return pack.model_copy(update={"insights": [
            before.model_copy(update={"meaning": "Предложение модели"}), *pack.insights[1:],
        ]}), interpret.InterpretReport(interpreted=1)

    monkeypatch.setattr(interpret, "interpret_pack", slow)
    task = asyncio.create_task(api._interpret_understanding(
        pack_id, pack, api._brief_model(pid, pack), api._UnderstandingOptions(use_llm=False),
    ))
    await entered.wait()
    if change == "insight":
        await api.patch_insight(pack_id, before.id, api.InsightPatch(
            meaning="Мой вывод", confirmed=True,
        ))
    else:
        api.STORE.project_briefs[pid]["audience"] = "клиенты"
    release.set()
    result, metadata = await task
    if change == "insight":
        assert result.insights[0].meaning == "Мой вывод"
        assert result.insights[0].confirmed
    else:
        assert metadata["status"] == "superseded"
        assert result.insights[0].meaning != "Предложение модели"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Итоги квартала по регионам для команды продукта", "команды продукта"),
        ("доклад для совета директоров", "совета директоров"),
        ("Презентация перед инвесторами", "инвесторами"),
        ("Results for the board of directors", "the board of directors"),
    ],
)
def test_find_audience_reads_explicit_phrases(text, expected):
    from deckdna.assistant.context import find_audience

    found = find_audience(text)
    assert found is not None
    value, quote = found
    assert value == expected
    assert quote in text and value in quote


@pytest.mark.parametrize(
    "text",
    ["отчёт для печати", "план для анализа продаж", "Итоги продаж", ""],
)
def test_find_audience_ignores_non_audience_phrases(text):
    from deckdna.assistant.context import find_audience

    assert find_audience(text) is None


def test_explicit_audience_in_idea_is_read_without_a_model(client, monkeypatch):
    """BE3: «для команды продукта» в идее читается правилами — вопрос
    об аудитории не задаётся и модель не вызывается."""
    pid, pack_id = intake(client, "Итоги квартала по регионам для команды продукта")
    gateway = MockProvider()
    monkeypatch.setattr(api, "build_gateway", lambda: gateway)
    result = client.get(f"{A}/content-packs/{pack_id}/understanding?use_llm=true").json()
    assert result["brief"]["audience"] == "команды продукта"
    [audience] = [f for f in result["context"]["fields"] if f["field"] == "audience"]
    assert audience["quote"] == "для команды продукта"
    assert audience["source_id"] == "brief:purpose"
    assert gateway.calls == []  # правило отработало до модели
    questions = client.get(f"{A}/projects/{pid}/questions").json()["questions"]
    assert all(q["id"] != "audience" for q in questions)


def test_rule_audience_merges_with_model_fields(client, monkeypatch):
    """Правило снимает аудиторию, модель дочитывает остальное — за один
    вызов, без повторного вопроса об аудитории."""
    pid, pack_id = intake(client, "Итоги продаж для совета директоров, 5 слайдов")
    gateway = MockProvider(fixtures={CONTEXT_PROMPT: {"fields": [
        {"field": "target_slide_count", "value": "5", "source_id": "brief:purpose",
         "quote": "5 слайдов"},
    ]}})
    monkeypatch.setattr(api, "build_gateway", lambda: gateway)
    result = client.get(f"{A}/content-packs/{pack_id}/understanding?use_llm=true").json()
    assert result["brief"]["audience"] == "совета директоров"
    assert result["brief"]["target_slide_count"] == 5
    fields = {f["field"] for f in result["context"]["fields"]}
    assert fields == {"audience", "target_slide_count"}
    questions = client.get(f"{A}/projects/{pid}/questions").json()["questions"]
    assert all(q["id"] != "audience" for q in questions)


def test_saved_audience_is_not_overwritten_by_rules(client, monkeypatch):
    """Явный ответ пользователя важнее извлечённого правилом."""
    pid, pack_id = intake(client, "Итоги продаж для совета директоров")
    api.STORE.project_briefs[pid]["audience"] = "клиенты"
    result = client.get(f"{A}/content-packs/{pack_id}/understanding?use_llm=false").json()
    assert result["brief"]["audience"] == "клиенты"
    assert all(f["field"] != "audience" for f in result["context"].get("fields") or [])


@pytest.mark.asyncio
async def test_context_call_bounds_sources_and_known_text(client):
    from deckdna.assistant.context import context_sources, extract_context

    _pid, pack_id = intake(client, "Обзор результатов")
    pack = api.STORE.content_packs[pack_id]
    block = pack.sections[0].blocks[0]
    section = pack.sections[0].model_copy(update={"blocks": [
        block.model_copy(update={"text": "x"}) for _ in range(1000)
    ]})
    pack = pack.model_copy(update={"sections": [section]})
    known = {"purpose": "Большой запрос " * 10000}
    sources = context_sources(pack, known)
    seen = []
    gateway = MockProvider(fixtures={CONTEXT_PROMPT: lambda payload: (
        seen.append(payload) or {"fields": []}
    )})
    await extract_context(sources, known, gateway)
    assert len(sources) <= 24 and sum(map(len, sources.values())) <= 8000
    assert len(seen[0]["known"]["purpose"]) == 500
