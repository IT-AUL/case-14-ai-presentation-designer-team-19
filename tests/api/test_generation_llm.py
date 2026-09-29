"""POST /generations use_llm: паритет с CLI --llm.

use_llm=true собирает gateway через build_gateway() и ведёт генерацию по
LLM/VLM-пути (plan_deck_llm + run_contextual_audit); ответ честно несёт
planner и contextual_issues на каждом variant. Проверяется через
TestClient + MockProvider (патчим deckdna.api.app.build_gateway — без сети).
"""

import shutil
from pathlib import Path

import pytest
from deckdna.api import app as app_module
from deckdna.api.app import app
from deckdna.planning.story_director import LLM_PLANNER_VERSION, PLANNER_VERSION
from deckdna.providers.mock import MockProvider
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEMPLATE = FIXTURES / "pptx" / "vk_tech_template.pptx"
CONTENT = FIXTURES / "content" / "poc_article.md"
PPTX_MIME = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)

client = TestClient(app)

BRIEF = {
    "purpose": "Представить решение",
    "audience": "эксперты и жюри",
    "language": "ru",
    "target_slide_count": 10,
}

needs_render = pytest.mark.skipif(
    shutil.which("soffice") is None or not TEMPLATE.exists(),
    reason="generation pipeline needs the fixture + soffice",
)


def _project_with_inputs() -> tuple[str, str, str]:
    project = client.post(
        "/api/v1/projects", json={"name": "llm gen", "target_slide_count": 10}
    ).json()
    tpl = client.post(
        f"/api/v1/projects/{project['id']}/templates",
        files={"file": (TEMPLATE.name, TEMPLATE.read_bytes(), PPTX_MIME)},
    )
    assert tpl.status_code == 201, tpl.text
    pack = client.post(
        f"/api/v1/projects/{project['id']}/content-packs",
        files={"files": (CONTENT.name, CONTENT.read_bytes(), "text/markdown")},
    )
    assert pack.status_code == 202, pack.text
    return project["id"], tpl.json()["id"], pack.json()["content_pack"]["id"]


def _generate(project_id: str, template_id: str, pack_id: str, **extra) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/generations",
        json={
            "template_id": template_id,
            "content_pack_id": pack_id,
            "brief": BRIEF,
            "variants": [{"strategy": "balanced"}],
            **extra,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def _storyline_fixture():
    """Callable-фикстура: per-batch валидный SlideOutlineBatch-ответ «LLM»,
    заземлённый на реальные evidence_ids из присланного payload."""

    def _respond(payload: dict) -> dict:
        nodes = payload["evidence_graph"]["nodes"]
        claim_ids = [n["id"] for n in nodes if n["type"] == "claim"] or [
            n["id"] for n in nodes
        ]
        ev = claim_ids[:1]
        return {
            "slides": [
                {
                    "index": hint["index"],
                    "purpose": hint["purpose"],
                    "title_intent": f"LLM: {hint['baseline_title']}",
                    "key_message": "Заключение по данным.",
                    "evidence_ids": ev,
                }
                for hint in payload["batch"]["hints"]
            ]
        }

    return _respond


def _mock_gateway() -> MockProvider:
    """MockProvider со схема-валидным storyline-ответом и одним VLM-fail на слайд."""
    return MockProvider(
        fixtures={
            "storyline": _storyline_fixture(),
            "slide_checks": {
                "verdicts": [
                    {
                        "check": "5",
                        "verdict": "fail",
                        "rationale": "VLM: слайд выглядит пустым",
                        "confidence": 0.9,
                    }
                ]
            },
        }
    )


@needs_render
def test_use_llm_calls_real_gateway_and_surfaces_results(monkeypatch, wait_generation):
    """Пин на v1 — тест проверяет storyline/exemplar_rerank-специфичный
    prompt-набор; v3 (дефолт с 27.09) имеет свои e2e-проверки ниже
    (test_use_llm_routes_through_adr016_v3_layout_fit_when_configured)."""
    monkeypatch.setattr(app_module.pipeline, "planning_pipeline_version", lambda: "v1")
    gateway = _mock_gateway()
    monkeypatch.setattr(app_module, "build_gateway", lambda config=None: gateway)
    project_id, template_id, pack_id = _project_with_inputs()

    accepted = _generate(project_id, template_id, pack_id, use_llm=True)

    run = wait_generation(client, accepted["generation_id"])
    assert run["state"] == "completed"
    variant = run["variants"][0]
    assert variant["planner"] == LLM_PLANNER_VERSION
    assert variant["contextual_issues"] == BRIEF["target_slide_count"]

    # gateway реально вызван — не «флаг принят и проигнорирован»
    methods = {c["method"] for c in gateway.calls}
    assert {"text_json", "vision_json"} <= methods
    assert {c["prompt"] for c in gateway.calls} == {"storyline", "slide_checks", "exemplar_rerank"}

    # recorded plan — LLM-атрибуция
    assert run["deck_plan"]["provenance"]["planner"] == LLM_PLANNER_VERSION


@needs_render
def test_use_llm_styles_content_units_via_plan_all_strategies(monkeypatch, wait_generation):
    """content_style запускается внутри _plan_all_strategies (не generate()'s
    own deck_plan-is-None branch, потому что API всегда передаёт
    предвычисленный deck_plan в generate() для compose) — см. докстринг
    _plan_and_style в app.py. Без fixture'а стадия молчит (проверено
    остальными тестами этого файла); с fixture'ом переписанный текст
    должен дойти до записанного deck_plan и до gateway.calls.

    Пин на v1 — content_style deliberately пропускается на v3 (дефолт
    с 27.09, см. ADR-016)."""
    monkeypatch.setattr(app_module.pipeline, "planning_pipeline_version", lambda: "v1")
    gateway = _mock_gateway()
    gateway.fixtures["content_style"] = lambda payload: {
        "items": [
            {"index": it["index"], "text": f"Стилизовано: {it['text'][:30]}"}
            for it in payload["items"]
        ]
    }
    monkeypatch.setattr(app_module, "build_gateway", lambda config=None: gateway)
    project_id, template_id, pack_id = _project_with_inputs()

    accepted = _generate(project_id, template_id, pack_id, use_llm=True)
    run = wait_generation(client, accepted["generation_id"])
    assert run["state"] == "completed"

    assert {c["prompt"] for c in gateway.calls} == {
        "storyline",
        "slide_checks",
        "exemplar_rerank",
        "content_style",
    }
    bullets = [
        u["text"]
        for slide in run["deck_plan"]["slides"]
        for u in slide["content_units"]
        if u["kind"] == "bullet"
    ]
    assert bullets, "fixture content should have produced at least one bullet unit"
    assert all(t.startswith("Стилизовано: ") for t in bullets)


@needs_render
def test_use_llm_routes_through_adr016_v2_when_configured(monkeypatch, wait_generation):
    """planning.pipeline_version="v2" (ADR-016, config-gated in
    configs/generation.default.yaml, default stays "v1") — _plan_and_style
    in app.py picks plan_deck_llm_v2 over plan_deck_llm; deck_structure/
    content_writer prompts fire, not storyline, and the recorded deck_plan
    carries the synthesized (not verbatim) content."""

    def _structure_fixture(payload: dict) -> dict:
        claims = [n["id"] for n in payload["evidence_graph"]["nodes"] if n["type"] == "claim"]
        return {
            "slides": [
                {
                    "index": h["index"],
                    "purpose": h["purpose"],
                    "slide_brief": f"job for slide {h['index']}",
                    "evidence_ids": claims[:1],
                }
                for h in payload["batch"]["hints"]
            ]
        }

    def _writer_fixture(payload: dict) -> dict:
        return {
            "title_intent": f"ADR016: {payload['slide_brief']}",
            "key_message": "Синтезированный вывод.",
            "bullets": ["Синтезированный тезис из evidence-пула."],
        }

    gateway = MockProvider(
        fixtures={
            "deck_structure": _structure_fixture,
            "content_writer": _writer_fixture,
            "slide_checks": {"verdicts": []},
        }
    )
    monkeypatch.setattr(app_module, "build_gateway", lambda config=None: gateway)
    monkeypatch.setattr(app_module.pipeline, "planning_pipeline_version", lambda: "v2")
    project_id, template_id, pack_id = _project_with_inputs()

    accepted = _generate(project_id, template_id, pack_id, use_llm=True)
    run = wait_generation(client, accepted["generation_id"])
    assert run["state"] == "completed"

    called_prompts = {c["prompt"] for c in gateway.calls}
    assert "storyline" not in called_prompts
    assert {"deck_structure", "content_writer"} <= called_prompts

    bullets = [
        u["text"]
        for slide in run["deck_plan"]["slides"]
        for u in slide["content_units"]
        if u["kind"] == "bullet"
    ]
    assert bullets
    assert all("Синтезированный" in t for t in bullets)


@needs_render
def test_use_llm_routes_through_adr016_v3_layout_fit_when_configured(monkeypatch, wait_generation):
    """ADR-016 Stage 3 через живой API-путь: planning.pipeline_version="v3"
    -- _plan_and_style picks the Stage 1+2 planner AND skips content_style
    (layout_fit adapts the final text itself, in generate()'s compose
    stage); layout_fit prompt fires, exemplar_rerank/content_style/
    storyline do not. The recorded deck_plan reflects Stage 2's output
    (layout_fit's own adaptation happens later, inside generate()'s
    compose stage, and isn't written back to the stored plan -- same
    known provenance gap as content_style's, ADR-014) -- what matters
    here is that the RIGHT prompts fired and nothing crashed end to end."""

    def _structure_fixture(payload: dict) -> dict:
        claims = [n["id"] for n in payload["evidence_graph"]["nodes"] if n["type"] == "claim"]
        return {
            "slides": [
                {
                    "index": h["index"],
                    "purpose": h["purpose"],
                    "slide_brief": f"job for slide {h['index']}",
                    "evidence_ids": claims[:1],
                }
                for h in payload["batch"]["hints"]
            ]
        }

    def _writer_fixture(payload: dict) -> dict:
        return {
            "title_intent": f"W: {payload['slide_brief']}",
            "key_message": "Написанный вывод.",
            "bullets": ["Написанный тезис про важное дело подробно и по делу."],
        }

    def _fit_fixture(payload: dict) -> dict:
        return {
            "candidate_index": 0,
            "title": f"FIT: {payload['title'][:30]}",
            "bullets": [b[:60] for b in payload["bullets"]],
        }

    gateway = MockProvider(
        fixtures={
            "deck_structure": _structure_fixture,
            "content_writer": _writer_fixture,
            "layout_fit": _fit_fixture,
            "slide_checks": {"verdicts": []},
        }
    )
    monkeypatch.setattr(app_module, "build_gateway", lambda config=None: gateway)
    monkeypatch.setattr(app_module.pipeline, "planning_pipeline_version", lambda: "v3")
    project_id, template_id, pack_id = _project_with_inputs()

    accepted = _generate(project_id, template_id, pack_id, use_llm=True)
    run = wait_generation(client, accepted["generation_id"])
    assert run["state"] == "completed"

    called_prompts = {c["prompt"] for c in gateway.calls}
    assert called_prompts == {"deck_structure", "content_writer", "layout_fit", "slide_checks"}


@needs_render
def test_use_llm_false_keeps_deterministic(monkeypatch, wait_generation):
    gateway = _mock_gateway()
    monkeypatch.setattr(app_module, "build_gateway", lambda config=None: gateway)
    project_id, template_id, pack_id = _project_with_inputs()

    accepted = _generate(project_id, template_id, pack_id)  # default: use_llm=false

    run = wait_generation(client, accepted["generation_id"])
    assert run["state"] == "completed"
    variant = run["variants"][0]
    assert variant["planner"] == PLANNER_VERSION
    assert variant["contextual_issues"] is None
    assert gateway.calls == []  # gateway не строился/не вызывался
    assert run["deck_plan"]["provenance"]["planner"] == PLANNER_VERSION
