"""Регрессии списка багов HANDOFF_BUGFIX_RU, без модели и рендера."""

from pathlib import Path

import pytest
from deckdna.contracts.content_pack import Block, ContentPack, ExternalSource, Section, SourceRef
from deckdna.contracts.deck_plan import Brief, ContentUnit, Kind, Origin, Purpose
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.agenda import sync_agenda
from deckdna.planning.content_fill import annotate_unit_origins
from deckdna.planning.story_director import plan_deck
from deckdna.pptx.composing.minimal import _slide_texts


def brief(n=8, language="ru"):
    return Brief(
        purpose="Сервис презентаций", audience="команда", language=language, target_slide_count=n
    )


def pack(items, *, model=False):
    return ContentPack(
        schema_version="1.0",
        id="pack-bugfix",
        language="ru",
        title_hint="Сервис",
        sections=[
            Section(
                id="results",
                heading="Результаты",
                level=2,
                blocks=[
                    Block(
                        kind="list",
                        items=items,
                        source_ref=SourceRef(
                            artifact_id="model:knowledge" if model else "input.md"
                        ),
                    )
                ],
            )
        ],
        tables=[],
        assets=[],
        warnings=[],
        external_sources=[ExternalSource(id="model:knowledge", origin="model")] if model else [],
    )


@pytest.mark.parametrize("language", ["ru", "en"])
def test_model_assumptions_are_not_labelled_in_slide_copy(language):
    """Префикс «Предположение:» в заголовке и буллетах читался как
    отладочная метка, а не вывод (F-81): происхождение текста остаётся
    в ``origin`` юнита и в заметках докладчика, в рендер не идёт."""
    source = pack(
        ["Шифрование защищает данные сервиса.", "Аудит регистрирует действия пользователей."],
        model=True,
    )
    plan = annotate_unit_origins(plan_deck(source, brief(3, language)), source)
    slide = plan.slides[1]
    title, texts, *_ = _slide_texts(slide, language=language)
    labels = ("Предположение:", "Assumption:")
    assert not (title or "").startswith(labels)
    assert all(not text.startswith(labels) for text in texts)
    # сама разметка происхождения никуда не делась — она просто не печатается
    assert any(u.kind == Kind.title and u.origin == Origin.model for u in slide.content_units)


def test_user_and_web_copy_are_not_labelled_as_assumptions():
    slide = plan_deck(pack(["Свой текст", "Ещё один пункт"]), brief(3)).slides[1]
    units = [u.model_copy(update={"origin": Origin.web}) for u in slide.content_units]
    marked = slide.model_copy(update={"content_units": units})
    assert _slide_texts(marked)[:2] == _slide_texts(slide)[:2]


def test_model_derived_title_without_a_title_unit_is_not_labelled():
    slide = plan_deck(pack(["Шифрование защищает данные."]), brief(3)).slides[1]
    slide = slide.model_copy(
        update={
            "content_units": [
                u.model_copy(update={"origin": Origin.model})
                for u in slide.content_units
                if u.kind != Kind.title
            ]
        }
    )
    assert not _slide_texts(slide)[0].startswith("Предположение:")


def test_synced_agenda_keeps_model_origin_without_printing_labels():
    source = pack(["Сервис шифрует данные.", "Аудит действий пользователей."], model=True)
    source = source.model_copy(
        update={
            "sections": [
                *source.sections,
                source.sections[0].model_copy(update={"id": "second", "heading": "Возможности"}),
            ]
        }
    )
    plan = annotate_unit_origins(plan_deck(source, brief()), source)
    agenda = next(s for s in sync_agenda(plan, source).slides if s.purpose == Purpose.agenda)
    assert all(u.origin == Origin.model for u in agenda.content_units if u.kind == Kind.bullet)
    assert all(not t.startswith("Предположение:") for t in _slide_texts(agenda)[1])
    assert _slide_texts(agenda)[0] == "План презентации"  # название шага — не предположение


@pytest.mark.parametrize("count", [2, 3])
def test_short_section_is_not_split_to_fill_slide_budget(count):
    points = [f"Результат {i}: подтверждённый короткий тезис." for i in range(count)]
    plan = plan_deck(pack(points), brief(8))
    content = [
        s
        for s in plan.slides
        if s.purpose
        not in {Purpose.title, Purpose.thank_you, Purpose.agenda, Purpose.section_divider}
    ]
    assert len(content) == 1
    assert [u.text for u in content[0].content_units if u.kind == Kind.bullet] == points


def test_agenda_title_is_subject_instead_of_presenter_narration():
    source = parse_file(Path("tests/fixtures/content/deckdna_pitch_rich.md"))
    plan = plan_deck(source, brief())
    agenda = next(s for s in plan.slides if s.purpose == Purpose.agenda)
    technical = "Повествование охватывает главные аспекты продукта"
    changed = agenda.model_copy(
        update={
            "title_intent": technical,
            "content_units": [
                ContentUnit(
                    kind=Kind.title, role="title", text=technical, evidence_ids=agenda.evidence_ids
                ),
                *agenda.content_units,
            ],
        }
    )
    plan = plan.model_copy(
        update={"slides": [changed if s.id == agenda.id else s for s in plan.slides]}
    )
    agenda = next(s for s in sync_agenda(plan, source).slides if s.purpose == Purpose.agenda)
    assert agenda.title_intent == "План презентации"
    assert next(u.text for u in agenda.content_units if u.kind == Kind.title) == agenda.title_intent


def test_supporting_insight_is_rendered_from_ledger_instead_of_model_meaning():
    source = parse_file(Path("examples/stage2/purchases.csv"))
    from deckdna.analysis.fact_ledger import FactLedger, build_pack_fact_ledger
    from deckdna.analysis.pipeline import analyze_pack
    from deckdna.analysis.title_templates import render_title

    analyses = analyze_pack(source)
    view = next(v for v in analyses[0].views if any(i.fact_type == "gap" for i in v.insights))
    gap = next(i for i in view.insights if i.fact_type == "gap")
    meaning = "Доля в 3,9 раза выше, чем у одежды"
    source = source.model_copy(
        update={
            "insights": [
                i.model_copy(update={"meaning": meaning}) if i.id == gap.id else i
                for i in source.insights
            ]
        }
    )
    ledger = build_pack_fact_ledger(analyses)
    plan = plan_deck(source, brief(), fact_ledger=FactLedger.from_facts([ledger.by_id[gap.id]]))
    data = next(
        s for s in plan.slides if gap.id in (s.insight_ids or []) and s.purpose == Purpose.data
    )
    texts = [u.text for u in data.content_units if u.kind != Kind.title]
    ledger = build_pack_fact_ledger(analyses)
    fact = ledger.by_id[gap.id]
    assert meaning not in texts
    assert render_title(fact.allowed_title_ids[0], fact) in texts
