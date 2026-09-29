"""Опциональный LLM top-k rerank exemplar-пула (cloning/rerank.py).

Инварианты: deterministic candidate filtering остаётся ядром — LLM только
переназначает выбор внутри того же пула; любая ошибка/невалидный ответ
честно откатывает на детерминированный baseline; бюджет — один text_json
вызов. Отличие выбора доказано на unseen-шаблоне (synthetic_unseen_43):
детерминированно все слайды берут slide2, переназначение видно напрямую.
"""

import asyncio
from pathlib import Path

from deckdna.contracts.deck_plan import Brief
from deckdna.contracts.serialize import to_schema_dict
from deckdna.contracts.variant_spec import Strategy
from deckdna.generation.pipeline import generate
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.story_director import plan_deck
from deckdna.pptx.cloning.exemplar import (
    select_exemplar_slides,
    slide_capabilities,
)
from deckdna.pptx.cloning.rerank import llm_exemplar_choices
from deckdna.pptx.opc.package import OpcPackage
from deckdna.providers.mock import MockProvider

TEMPLATE = Path("tests/fixtures/pptx/synthetic_unseen_43.pptx")
CONTENT = Path("tests/fixtures/content/poc_article.md")
BRIEF = {
    "purpose": "rerank proof",
    "audience": "qa",
    "language": "ru",
    "target_slide_count": 10,
}


def _plan():
    pack = parse_file(CONTENT)
    return plan_deck(pack, Brief.model_validate(BRIEF))


def _run(coro):
    return asyncio.run(coro)


def test_llm_rerank_changes_selection(tmp_path):
    """Валидное назначение LLM реально меняет выбор vs deterministic."""
    pkg = OpcPackage.open(TEMPLATE)
    plan = _plan()
    needs = [frozenset() for _ in plan.slides]
    baseline = select_exemplar_slides(
        pkg, count=len(plan.slides), needs=needs, strategy=Strategy.balanced
    )

    def _answer(payload):
        # Первый слайд — последний кандидат из пула (заведомо не slide2).
        return {
            "assignments": [
                {
                    "slide_index": 0,
                    "candidate_index": len(payload["candidates"]) - 1,
                    "reason": "лучше подходит по плотности",
                }
            ]
        }

    gateway = MockProvider(fixtures={"exemplar_rerank": _answer})
    choices = _run(
        llm_exemplar_choices(pkg, plan, needs, gateway, strategy=Strategy.balanced)
    )
    assert choices is not None
    # Запросы режутся на батчи по RERANK_BATCH_SIZE слайдов (10 слайдов,
    # батч 8 -> 2 вызова) — bounded бюджет на батч, не на колоду целиком.
    assert sum(1 for c in gateway.calls if c["prompt"] == "exemplar_rerank") == 2
    assert choices[0].slide_part != baseline[0].slide_part
    assert choices[1].slide_part == baseline[1].slide_part  # остальные — baseline


def test_llm_rerank_payload_carries_layout_archetype_and_preferred_hints():
    """Каждая candidate-карточка несёт свой layout_archetype (discrete
    метка, см. exemplar.py::LayoutArchetype), каждый request — свой
    preferred_archetypes (детерминированная подсказка по Purpose, см.
    _PURPOSE_ARCHETYPE_HINTS) — это и есть замена «сырых чисел» на
    подобранную под слабую модель метку (живая находка
    27.09 про planning/exemplar-подбор)."""
    from deckdna.contracts.deck_plan import Purpose
    from deckdna.pptx.cloning.exemplar import LayoutArchetype
    from deckdna.pptx.cloning.rerank import _PURPOSE_ARCHETYPE_HINTS

    pkg = OpcPackage.open(TEMPLATE)
    plan = _plan()
    needs = [frozenset() for _ in plan.slides]

    seen_payloads: list[dict] = []

    def _answer(payload):
        seen_payloads.append(payload)
        return {"assignments": []}

    gateway = MockProvider(fixtures={"exemplar_rerank": _answer})
    _run(llm_exemplar_choices(pkg, plan, needs, gateway, strategy=Strategy.balanced))

    assert seen_payloads
    for payload in seen_payloads:
        for candidate in payload["candidates"]:
            assert candidate["layout_archetype"] in {a.value for a in LayoutArchetype}
        for request in payload["requests"]:
            purpose = Purpose(request["purpose"])
            expected = [a.value for a in _PURPOSE_ARCHETYPE_HINTS.get(purpose, ())]
            assert request["preferred_archetypes"] == expected


def test_llm_rerank_invalid_assignment_falls_back():
    """Все назначения out-of-range → None: ни одна LLM-замена не принята,
    маркер в пайплайне честно станет 'fallback', а не 'llm'."""
    pkg = OpcPackage.open(TEMPLATE)
    plan = _plan()
    needs = [frozenset() for _ in plan.slides]
    gateway = MockProvider(
        fixtures={
            "exemplar_rerank": {
                "assignments": [
                    {"slide_index": 0, "candidate_index": 999},
                    {"slide_index": 999, "candidate_index": 0},
                ]
            }
        }
    )
    assert (
        _run(
            llm_exemplar_choices(
                pkg, plan, needs, gateway, strategy=Strategy.balanced
            )
        )
        is None
    )


def test_llm_rerank_respects_needs():
    """Назначение кандидата без нужной капабилити честно отклоняется."""
    pkg = OpcPackage.open(TEMPLATE)
    plan = _plan()
    needs = [frozenset()] * len(plan.slides)
    needs[0] = frozenset({"chart"})
    baseline = select_exemplar_slides(
        pkg, count=len(plan.slides), needs=needs, strategy=Strategy.balanced
    )
    # baseline обязан дать chart-способный exemplar (slide3 на этом шаблоне).
    assert "chart" in slide_capabilities(pkg, baseline[0].slide_part)

    def _bad(payload):
        # Кандидат без 'chart' в capabilities для слайда с need=chart.
        idx = next(
            i for i, c in enumerate(payload["candidates"])
            if "chart" not in c["capabilities"]
        )
        return {"assignments": [{"slide_index": 0, "candidate_index": idx}]}

    gateway = MockProvider(fixtures={"exemplar_rerank": _bad})
    assert (
        _run(
            llm_exemplar_choices(
                pkg, plan, needs, gateway, strategy=Strategy.balanced
            )
        )
        is None  # единственное назначение отклонено → честный fallback
    )


def test_llm_rerank_duplicate_candidates_fall_back():
    """Модель назначила один и тот же exemplar всем слайдам — принимается
    только первое назначение, дубли при свободных кандидатах → baseline."""
    pkg = OpcPackage.open(TEMPLATE)
    plan = _plan()
    needs = [frozenset() for _ in plan.slides]
    baseline = select_exemplar_slides(
        pkg, count=len(plan.slides), needs=needs, strategy=Strategy.balanced
    )

    def _spam(payload):
        last = len(payload["candidates"]) - 1
        return {
            "assignments": [
                {"slide_index": i, "candidate_index": last}
                for i in range(len(payload["requests"]))
            ]
        }

    gateway = MockProvider(fixtures={"exemplar_rerank": _spam})
    choices = _run(
        llm_exemplar_choices(pkg, plan, needs, gateway, strategy=Strategy.balanced)
    )
    assert choices is not None
    assert choices[0].slide_part != baseline[0].slide_part  # первое — принято
    assert all(
        c.slide_part == b.slide_part
        for c, b in zip(choices[1:], baseline[1:], strict=True)
    )  # дубли отклонены


def test_llm_rerank_assignment_for_unrequested_slide_rejected():
    """Запросы усечены max_slides → назначение для непредъявленного слайда
    не принимается, этот слайд остаётся на baseline."""
    pkg = OpcPackage.open(TEMPLATE)
    plan = _plan()
    needs = [frozenset() for _ in plan.slides]

    def _beyond(payload):
        unseen = len(payload["requests"])  # за границей предъявленных слайдов
        return {
            "assignments": [
                {
                    "slide_index": unseen,
                    "candidate_index": len(payload["candidates"]) - 1,
                }
            ]
        }

    gateway = MockProvider(fixtures={"exemplar_rerank": _beyond})
    assert (
        _run(
            llm_exemplar_choices(
                pkg, plan, needs, gateway, strategy=Strategy.balanced,
                max_slides=1,
            )
        )
        is None  # единственное назначение отклонено → ноль изменений
    )


def test_llm_rerank_agreement_with_baseline_is_fallback():
    """LLM валидно назначает ровно те exemplar'ы что и baseline → изменений
    нет → None (честный 'fallback', а не 'llm')."""
    pkg = OpcPackage.open(TEMPLATE)
    plan = _plan()
    needs = [frozenset() for _ in plan.slides]

    def _agree(payload):
        # slide2 — det-baseline на этом шаблоне; назначаем её же слайду 0.
        idx = next(
            i for i, c in enumerate(payload["candidates"])
            if c["slide_part"].endswith("slide2.xml")
        )
        return {"assignments": [{"slide_index": 0, "candidate_index": idx}]}

    gateway = MockProvider(fixtures={"exemplar_rerank": _agree})
    assert (
        _run(
            llm_exemplar_choices(
                pkg, plan, needs, gateway, strategy=Strategy.balanced
            )
        )
        is None
    )


def test_llm_rerank_provider_error_is_fallback():
    """Сбой провайдера → None → детерминированный выбор наверху."""

    def _boom(payload):
        raise RuntimeError("llm down")

    pkg = OpcPackage.open(TEMPLATE)
    plan = _plan()
    needs = [frozenset() for _ in plan.slides]
    gateway = MockProvider(fixtures={"exemplar_rerank": _boom})
    assert (
        _run(
            llm_exemplar_choices(
                pkg, plan, needs, gateway, strategy=Strategy.balanced
            )
        )
        is None
    )


def test_pipeline_llm_rerank_end_to_end(monkeypatch, tmp_path):
    """generate(gateway) + fixture-назначение → report['exemplar_rerank']
    == 'llm' и exemplar первого слайда реально другой vs deterministic.

    Пин на pipeline_version="v1": этот тест специально проверяет
    rerank.py's llm_exemplar_choices через полный generate() — с v3
    (дефолт с 27.09, ADR-016) этот путь заменён layout_fit.py, у него
    своя e2e-проверка в test_pipeline_llm.py."""
    import deckdna.generation.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "planning_pipeline_version", lambda: "v1")

    def _answer(payload):
        return {
            "assignments": [
                {
                    "slide_index": 0,
                    "candidate_index": len(payload["candidates"]) - 1,
                }
            ]
        }

    gateway = MockProvider(
        fixtures={
            "exemplar_rerank": _answer,
            # slide_checks нужен VLM-аудиту, если contextual.enabled — пустой.
            "slide_checks": {"verdicts": []},
        }
    )
    report = generate(
        TEMPLATE, CONTENT, dict(BRIEF), tmp_path / "gen", gateway=gateway
    )
    assert report["exemplar_rerank"] == "llm"
    picks = [
        s["exemplar"]["slide_part"]
        for s in report["compose_report"]["slides"]
        if not s.get("protected")
    ]
    # Детерминированный выбор на этом шаблоне — slide2 для всех; rerank
    # переназначил первый слайд на другой кандидат — доказуемое отличие.
    assert picks[0] != "ppt/slides/slide2.xml"
    assert all(p == "ppt/slides/slide2.xml" for p in picks[1:])


def test_pipeline_rerank_marker_honest_fallback(tmp_path):
    """LLM-режим + все назначения невалидны → report['exemplar_rerank']
    == 'fallback' (а не 'llm' — ни одна замена не принята)."""
    gateway = MockProvider(
        fixtures={
            "exemplar_rerank": {"assignments": [{"slide_index": 0,
                                                 "candidate_index": 999}]},
            "slide_checks": {"verdicts": []},
        }
    )
    report = generate(
        TEMPLATE, CONTENT, dict(BRIEF), tmp_path / "gen", gateway=gateway
    )
    assert report["exemplar_rerank"] == "fallback"


def test_pipeline_rerank_provenance_with_planner_fallback(monkeypatch, tmp_path):
    """Планировщик честно откатился на deterministic, но rerank принят →
    паспорт несёт exemplar_rerank в prompt_versions и text-модель в
    model_profiles (PR #123 seam). Пин на v1 — см. test_pipeline_llm_rerank_end_to_end."""
    import deckdna.generation.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "planning_pipeline_version", lambda: "v1")
    pack = parse_file(CONTENT)
    broken = to_schema_dict(plan_deck(pack, Brief.model_validate(BRIEF)))
    broken["slides"] = broken["slides"][:1]  # семантически невалидный план

    def _answer(payload):
        return {
            "assignments": [
                {
                    "slide_index": 0,
                    "candidate_index": len(payload["candidates"]) - 1,
                }
            ]
        }

    gateway = MockProvider(
        fixtures={
            "storyline": broken,
            "exemplar_rerank": _answer,
            "slide_checks": {"verdicts": []},
        }
    )
    report = generate(
        TEMPLATE, CONTENT, dict(BRIEF), tmp_path / "gen", gateway=gateway
    )
    assert report["exemplar_rerank"] == "llm"
    prov = report["quality_passport"]["provenance"]
    assert prov["prompt_versions"]["exemplar_rerank"] == "1.1.0"
    assert "storyline" not in prov["prompt_versions"]  # план отвергнут
    assert {p["model_id"] for p in prov["model_profiles"]} == {"mock"}


def test_pipeline_deterministic_untouched(tmp_path):
    """gateway=None → реранк не вызывается, отчёт помечен deterministic."""
    report = generate(TEMPLATE, CONTENT, dict(BRIEF), tmp_path / "gen")
    assert report["exemplar_rerank"] == "deterministic"
