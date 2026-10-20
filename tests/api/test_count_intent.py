"""Author count qualifiers survive understanding, questions and commands."""
from pathlib import Path

import pytest
from deckdna.assistant.commands import parse_command
from deckdna.assistant.context import extract_context, extract_context_rules
from deckdna.assistant.questions import build_questions
from deckdna.ingestion.content_parsers import parse_file
from deckdna.providers.mock import MockProvider


@pytest.mark.parametrize("text,count,policy", [
    ("Сделай ровно 6 слайдов", 6, "exact"),
    ("Обзор для команды, не более 7 слайдов", 7, "maximum"),
    ("До 8 слайдов о продажах", 8, "maximum"),
    ("Примерно 5 слайдов", 5, "preferred"),
    ("Около 9 слайдов о рисках", 9, "preferred"),
    ("Exactly 6 slides for the board", 6, "exact"),
    ("At most 7 slides", 7, "maximum"),
    ("Around 5 slides", 5, "preferred"),
])
def test_qualified_author_count_is_read_locally(text, count, policy):
    evidence = extract_context_rules({"brief:purpose": text}, {})
    values = {e["field"]: e["value"] for e in evidence}
    assert values["target_slide_count"] == count
    assert values["slide_count_policy"] == policy
    if policy != "preferred":
        assert values["requested_slide_count"] == count
    assert all(e["quote"] in text for e in evidence)


@pytest.mark.parametrize("text", [
    "Не ровно 6 слайдов", "От 5 до 7 слайдов", "Ровно 5 или 7 слайдов",
    "Ровно 6 слайдов, а лучше до 8 слайдов", "Ровно 2 слайда",
    "В шаблоне ровно 6 слайдов", "В документе примерно 9 слайдов",
    "Доклад называется «Ровно 6 слайдов»", "В 2026 году", "5 слайдов",
    "Ровно 6 слайдов не нужно", "Ровно 6 слайдов, а лучше 7",
])
def test_uncertain_or_non_request_count_never_becomes_constraint(text):
    evidence = extract_context_rules({"brief:purpose": text}, {})
    assert all(e["field"] not in {"target_slide_count", "slide_count_policy",
                                  "requested_slide_count"} for e in evidence)


def test_document_and_saved_preferences_cannot_be_reinterpreted_as_request():
    assert extract_context_rules({"b:sec:0": "Сделай ровно 6 слайдов"}, {}) == []
    known = {"target_slide_count": 8, "slide_count_policy": "maximum",
             "requested_slide_count": 8}
    evidence = extract_context_rules({"brief:purpose": "Сделай ровно 6 слайдов"}, known)
    assert evidence == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message,policy", [
    ("Сделай ровно 6 слайдов", "exact"),
    ("Не более 6 слайдов", "maximum"),
    ("Сделай примерно 6 слайдов", "preferred"),
])
async def test_pure_count_command_needs_no_provider(message, policy):
    parsed, source = await parse_command(message, {}, None)
    assert source == "local" and parsed.intent == "update_brief"
    values = {e.field: e.value for e in parsed.brief}
    assert values["target_slide_count"] == "6"
    assert values["slide_count_policy"] == policy


@pytest.mark.asyncio
async def test_mixed_command_keeps_other_edits_and_adds_verified_policy():
    gateway = MockProvider(fixtures={"assistant_command": {
        "intent": "update_brief", "brief": [
            {"field": "audience", "value": "клиентов", "quote": "для клиентов"},
            {"field": "target_slide_count", "value": "6", "quote": "6 слайдов"},
        ],
    }})
    parsed, source = await parse_command("Теперь для клиентов, ровно 6 слайдов", {}, gateway)
    assert source == "model" and parsed.intent == "update_brief"
    assert {e.field: e.value for e in parsed.brief} == {
        "audience": "клиентов", "target_slide_count": "6", "slide_count_policy": "exact",
    }
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Не ровно 6 слайдов", "От 5 до 7 слайдов",
                                         "Ровно 5 или 7 слайдов"])
async def test_uncertain_command_clarifies_locally(message):
    parsed, source = await parse_command(message, {}, None)
    assert parsed.intent == "clarify" and source == "local"


@pytest.mark.asyncio
async def test_model_cannot_turn_negation_or_document_count_into_author_count():
    for source, text in [("brief:purpose", "Не ровно 6 слайдов"),
                         ("b:doc:0", "В документе 6 слайдов")]:
        gateway = MockProvider(fixtures={"assistant_context": {"fields": [
            {"field": "target_slide_count", "value": "6", "source_id": source,
             "quote": "6 слайдов"},
        ]}})
        values, evidence, _ = await extract_context({source: text}, {}, gateway)
        assert "target_slide_count" not in values and evidence == []


def test_maximum_is_not_a_short_content_conflict():
    pack = parse_file(Path("tests/fixtures/content/poc_article.md"))
    result = build_questions(pack, {"target_slide_count": 40, "slide_count_policy": "maximum",
                                    "requested_slide_count": 40, "audience": "команда"})
    assert all(q["id"] != "content_short" for q in result["questions"])
    assert result["suggested_brief"]["content_fill"] != "ask"


def test_exact_shortage_never_recommends_silently_shrinking():
    pack = parse_file(Path("tests/fixtures/content/poc_article.md"))
    result = build_questions(pack, {"target_slide_count": 40, "slide_count_policy": "exact",
                                    "requested_slide_count": 40, "audience": "команда"})
    question = next(q for q in result["questions"] if q["id"] == "content_short")
    assert all(o["value"] != "shrink" for o in question["options"])
    assert any(o.get("recommended") for o in question["options"])
    assert result["suggested_brief"]["requested_slide_count"] == 40


@pytest.fixture
def client(monkeypatch):
    from deckdna.api import app as api
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api, "STORE", api._Store())
    monkeypatch.setattr(api.settings, "state_snapshot", False)
    return TestClient(api.app)


@pytest.mark.parametrize("entry", ["understanding", "questions", "answers"])
def test_headless_and_question_paths_preserve_qualified_count(client, monkeypatch, entry):
    from deckdna.api import app as api

    def forbidden(*args, **kwargs):
        raise AssertionError("local count must not call provider")

    monkeypatch.setattr(api, "build_gateway", forbidden)
    root = "/api/v1"
    pid = client.post(f"{root}/projects", json={"name": "count"}).json()["id"]
    intake = client.post(f"{root}/projects/{pid}/intake", data={
        "idea": "Обзор продаж для команды, ровно 6 слайдов",
    }).json()
    if entry == "understanding":
        response = client.get(f"{root}/content-packs/{intake['content_pack_id']}"
                              "/understanding?use_llm=false")
    elif entry == "questions":
        response = client.get(f"{root}/projects/{pid}/questions")
    else:
        response = client.post(f"{root}/projects/{pid}/answers", json={"answers": {}})
    assert response.status_code == 200, response.text
    brief = client.get(f"{root}/projects/{pid}/session").json()["brief"]
    assert brief["slide_count_policy"] == "exact"
    assert brief["requested_slide_count"] == brief["target_slide_count"] == 6


def test_command_rebinds_hard_limit_and_explicit_softening_is_retained(client):
    root = "/api/v1"
    pid = client.post(f"{root}/projects", json={"name": "count"}).json()["id"]
    for message, count, policy in [("Ровно 6 слайдов", 6, "exact"),
                                    ("Не более 8 слайдов", 8, "maximum"),
                                    ("Примерно 5 слайдов", 5, "preferred")]:
        response = client.post(f"{root}/projects/{pid}/commands", json={"text": message})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "applied"
        brief = response.json()["brief"]
        assert brief["target_slide_count"] == count
        assert brief["slide_count_policy"] == policy
        if policy != "preferred":
            assert brief["requested_slide_count"] == count


def test_ambiguous_intake_asks_length_without_assigning_hard_limit(client):
    root = "/api/v1"
    pid = client.post(f"{root}/projects", json={"name": "count"}).json()["id"]
    client.post(f"{root}/projects/{pid}/intake", data={"idea": "Обзор на 5 или 7 слайдов"})
    result = client.get(f"{root}/projects/{pid}/questions").json()
    assert result["questions"][0]["id"] == "length"
    assert not result["suggested_brief"].get("slide_count_policy")


@pytest.mark.asyncio
async def test_model_cannot_invent_policy_even_with_a_grounded_number():
    gateway = MockProvider(fixtures={"assistant_command": {
        "intent": "update_brief", "brief": [
            {"field": "target_slide_count", "value": "6", "quote": "6 слайдов"},
            {"field": "slide_count_policy", "value": "exact", "quote": "6 слайдов"},
        ],
    }})
    parsed, source = await parse_command("Для команды, примерно 6 слайдов", {}, gateway)
    assert parsed.intent == "clarify" and source == "rejected"


def test_stale_answers_do_not_apply_even_local_inference(client):
    from deckdna.api import app as api

    root = "/api/v1"
    pid = client.post(f"{root}/projects", json={"name": "count"}).json()["id"]
    client.post(f"{root}/projects/{pid}/intake", data={"idea": "Ровно 6 слайдов"})
    before = dict(api.STORE.project_briefs[pid])
    response = client.post(f"{root}/projects/{pid}/answers", json={
        "expected_context_revision": 999, "answers": {"audience": "команда"},
    })
    assert response.status_code == 409
    assert api.STORE.project_briefs[pid] == before


def test_excessive_digit_count_clarifies_without_integer_conversion_failure():
    from deckdna.assistant.commands import quick_parse

    result = quick_parse("Ровно " + "9" * 5000 + " слайдов")
    assert result.intent == "clarify"
