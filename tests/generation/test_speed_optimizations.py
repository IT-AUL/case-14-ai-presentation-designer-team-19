"""Регрессионные тесты ускорения сборки трёх вариантов (Parallel pipeline):
- кэш шаблона на втором запуске не вызывает анализатор;
- три варианта используют один план и одну подготовку текстов (layout_fit / talk_tracks);
- пропуск этапа icons, если в шаблоне нет иконок и бриф их не просит;
- честная деградация этапов в паспорте при исчерпании сквозного бюджета / сбое провайдера.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
from deckdna.api import app as app_module
from deckdna.api.app import app
from deckdna.contracts.deck_plan import Brief
from deckdna.errors import DeckDNAError
from deckdna.generation import pipeline
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.story_director import plan_deck
from deckdna.providers.mock import MockProvider
from deckdna.template import autopsy, dna
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SYNTHETIC_TEMPLATE = FIXTURES / "pptx" / "synthetic_unseen.pptx"
CONTENT = FIXTURES / "content" / "poc_article.md"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

needs_render = pytest.mark.skipif(
    shutil.which("soffice") is None or not SYNTHETIC_TEMPLATE.exists(),
    reason="pipeline needs synthetic_unseen.pptx + soffice",
)


def test_icons_stage_skipped_when_template_has_no_icons_and_brief_silent() -> None:
    brief_no_icons = Brief(
        purpose="Квартальный отчёт по выручке",
        audience="руководство",
        language="ru",
        target_slide_count=4,
    )
    brief_with_icons = Brief(
        purpose="Квартальный отчёт с пиктограммами и иконками на карточках",
        audience="руководство",
        language="ru",
        target_slide_count=4,
    )
    pack = parse_file(CONTENT)
    plan = plan_deck(pack, brief_no_icons)
    gw = MockProvider(
        fixtures={"icon_labels": {"icons": [{"i": 0, "label": "рост", "is_icon": True}]}}
    )

    assert pipeline.brief_requests_icons(brief_no_icons, plan) is False
    assert pipeline.brief_requests_icons(brief_with_icons, plan) is True
    assert pipeline._should_run_icons(SYNTHETIC_TEMPLATE, brief_no_icons, plan, gw) is False
    assert pipeline._should_run_icons(SYNTHETIC_TEMPLATE, brief_with_icons, plan, gw) is True


@needs_render
def test_second_run_uses_cached_template_and_three_variants_share_one_plan(
    tmp_path: Path, monkeypatch, wait_generation
) -> None:
    """Приёмка Parallel pipeline:
    1) Второй запуск на том же шаблоне не вызывает анализатор;
    2) Три варианта используют один план и одну подготовку текстов, отличаясь раскладкой.
    """
    monkeypatch.setenv("DECKDNA_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    client = TestClient(app)

    parse_calls = 0
    real_parse = autopsy._parse_template

    def counting_parse(path: Path):
        nonlocal parse_calls
        parse_calls += 1
        return real_parse(path)

    monkeypatch.setattr(autopsy, "_parse_template", counting_parse)

    describe_calls = 0
    real_describe = dna.describe_exemplars

    async def counting_describe(*args, **kwargs):
        nonlocal describe_calls
        describe_calls += 1
        return await real_describe(*args, **kwargs)

    monkeypatch.setattr(dna, "describe_exemplars", counting_describe)

    fit_calls = 0
    real_layout_fit = pipeline.layout_fit.layout_fit_deck

    async def counting_layout_fit(*args, **kwargs):
        nonlocal fit_calls
        fit_calls += 1
        return await real_layout_fit(*args, **kwargs)

    monkeypatch.setattr(pipeline.layout_fit, "layout_fit_deck", counting_layout_fit)

    def _structure_fixture(payload: dict) -> dict:
        claims = [n["id"] for n in payload["evidence_graph"]["nodes"] if n["type"] == "claim"] or [
            n["id"] for n in payload["evidence_graph"]["nodes"]
        ]
        return {
            "slides": [
                {
                    "index": h["index"],
                    "purpose": h["purpose"],
                    "slide_brief": f"Задача слайда {h['index']}",
                    "evidence_ids": claims[:1],
                }
                for h in payload["batch"]["hints"]
            ]
        }

    def _writer_fixture(payload: dict) -> dict:
        return {
            "title_intent": f"Вывод: {payload['slide_brief']}",
            "key_message": "Ключевой тезис.",
            "bullets": ["Подробный пункт по существу вопроса."],
        }

    def _fit_fixture(payload: dict) -> dict:
        return {
            "candidate_index": 0,
            "title": payload["title"][:40],
            "bullets": [b[:60] for b in payload["bullets"]],
        }

    gateway = MockProvider(
        fixtures={
            "exemplar_describe": lambda payload: {
                "slides": [
                    {
                        "index": s["index"],
                        "description": "Контентный макет",
                        "is_entity_specific": False,
                        "is_template_meta": False,
                    }
                    for s in payload["slides"]
                ]
            },
            "deck_structure": _structure_fixture,
            "content_writer": _writer_fixture,
            "layout_fit": _fit_fixture,
            "slide_checks": {"verdicts": []},
        }
    )
    monkeypatch.setattr(app_module, "build_gateway", lambda config=None: gateway)

    # Запуск 1: загрузка шаблона и генерация 3 вариантов
    p1 = client.post("/api/v1/projects", json={"name": "speed-1", "target_slide_count": 5}).json()
    tpl1 = client.post(
        f"/api/v1/projects/{p1['id']}/templates",
        files={"file": (SYNTHETIC_TEMPLATE.name, SYNTHETIC_TEMPLATE.read_bytes(), PPTX_MIME)},
    ).json()
    pack1 = client.post(
        f"/api/v1/projects/{p1['id']}/content-packs",
        files={"files": (CONTENT.name, CONTENT.read_bytes(), "text/markdown")},
    ).json()["content_pack"]

    gen1 = client.post(
        f"/api/v1/projects/{p1['id']}/generations",
        json={
            "template_id": tpl1["id"],
            "content_pack_id": pack1["id"],
            "brief": {
                "purpose": "Проверить кэш и один план на 3 варианта",
                "audience": "жюри",
                "language": "ru",
                "target_slide_count": 5,
            },
            "variants": [
                {"strategy": "faithful"},
                {"strategy": "balanced"},
                {"strategy": "visual"},
            ],
            "use_llm": True,
        },
    )
    assert gen1.status_code == 202, gen1.text
    run1 = wait_generation(client, gen1.json()["generation_id"])
    assert run1["state"] == "completed", run1
    assert parse_calls == 1
    assert describe_calls == 1

    # 1. Три варианта используют один план и один вызов layout_fit_deck
    plan_ids = [v["deck_plan_id"] for v in run1["variants"]]
    assert len(set(plan_ids)) == 1
    assert fit_calls == 1

    # При этом скомпилированные презентации вариантов различаются раскладкой
    hashes = {
        hashlib.sha256(
            client.get(f"/api/v1/artifacts/{v['deck_artifact_id']}/download").content
        ).hexdigest()
        for v in run1["variants"]
    }
    assert len(hashes) >= 2

    # 2. Второй проект и второй запуск на том же шаблоне не вызывают анализатор
    parse_calls_before_second = parse_calls
    describe_calls_before_second = describe_calls

    p2 = client.post("/api/v1/projects", json={"name": "speed-2", "target_slide_count": 5}).json()
    tpl2 = client.post(
        f"/api/v1/projects/{p2['id']}/templates",
        files={"file": (SYNTHETIC_TEMPLATE.name, SYNTHETIC_TEMPLATE.read_bytes(), PPTX_MIME)},
    ).json()
    # Симулируем рестарт/чистую память STORE для второго шаблона, чтобы проверить дисковый кэш
    app_module.STORE.design_dna.pop(tpl2["id"], None)

    pack2 = client.post(
        f"/api/v1/projects/{p2['id']}/content-packs",
        files={"files": (CONTENT.name, CONTENT.read_bytes(), "text/markdown")},
    ).json()["content_pack"]

    gen2 = client.post(
        f"/api/v1/projects/{p2['id']}/generations",
        json={
            "template_id": tpl2["id"],
            "content_pack_id": pack2["id"],
            "brief": {
                "purpose": "Повторный прогон на прогретом шаблоне",
                "audience": "жюри",
                "language": "ru",
                "target_slide_count": 5,
            },
            "variants": [{"strategy": "balanced"}],
            "use_llm": True,
        },
    )
    assert gen2.status_code == 202, gen2.text
    run2 = wait_generation(client, gen2.json()["generation_id"])
    assert run2["state"] == "completed", run2

    assert parse_calls == parse_calls_before_second
    assert describe_calls == describe_calls_before_second


@needs_render
def test_budget_exhaustion_degrades_honestly_in_passport(tmp_path: Path, monkeypatch) -> None:
    """Parallel pipeline п.5: при исчерпании сквозного бюджета или таймауте провайдера
    этап деградирует честно (запись в passport.fallbacks), не роняя сборку."""
    brief = Brief(
        purpose="Проверка честной деградации при исчерпании бюджета",
        audience="команда",
        language="ru",
        target_slide_count=4,
    )
    pack = parse_file(CONTENT)
    plan = plan_deck(pack, brief)

    def _timeout_stage(_payload: dict) -> dict:
        raise DeckDNAError(
            code="provider_unavailable",
            message="provider stage deadline exceeded",
            stage="providers",
            retryable=True,
        )

    gateway = MockProvider(
        fixtures={
            "layout_fit": _timeout_stage,
            "talk_track": _timeout_stage,
            "slide_checks": _timeout_stage,
        }
    )

    report = pipeline.generate(
        SYNTHETIC_TEMPLATE,
        CONTENT,
        brief,
        tmp_path / "degraded",
        deck_plan=plan,
        content_pack=pack,
        gateway=gateway,
    )
    assert Path(report["artifacts"]["pptx"]).exists()
    assert Path(report["artifacts"]["pdf"]).exists()
    fallbacks = report["quality_passport"]["fallbacks"]
    features = {f["feature"] for f in fallbacks}
    assert "composing.layout_fit" in features or "composing.speaker_notes" in features
    assert "audit.contextual.coverage" in features or "audit.contextual" in features
    # Этап icons не запускался, так как в synthetic_unseen.pptx нет каталога иконок
    stage_names = [s["stage"] for s in report["stage_timings"]]
    assert "icons" not in stage_names
