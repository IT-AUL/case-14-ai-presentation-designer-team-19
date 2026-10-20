"""ADR-030: оценка достаточности контента, запрет заглушек в плане,
метки происхождения пунктов."""

from __future__ import annotations

import pytest
from deckdna.contracts.content_pack import (
    Block,
    ContentPack,
    ExternalSource,
    Kind,
    Origin,
    Section,
    SourceRef,
)
from deckdna.contracts.deck_plan import Brief, ContentFill
from deckdna.errors import DeckDNAError
from deckdna.planning import plan_deck, validate_deck_plan
from deckdna.planning.config import DeckBounds, GenerationConfig
from deckdna.planning.content_fill import (
    Status,
    annotate_unit_origins,
    estimate_content_capacity,
    resolve_content_fill,
    shrink_target,
)
from deckdna.planning.evidence import build_evidence_graph

CFG = GenerationConfig(deck=DeckBounds(min_slides=3, max_slides=40))


def _brief(target: int, fill: ContentFill | None = None) -> Brief:
    return Brief(
        purpose="Рассказать о цифровом дизайнере презентаций",
        audience="руководство",
        language="ru",
        target_slide_count=target,
        content_fill=fill,
    )


def _pack(sections: list[tuple[str, list[str]]], *, sources=None, artifact="a.md") -> ContentPack:
    return ContentPack(
        schema_version="1.0",
        id="pack-fill",
        language="ru",
        sections=[
            Section(
                id=f"s{i}",
                heading=heading,
                level=2,
                blocks=[
                    Block(kind=Kind.list, items=items, source_ref=SourceRef(artifact_id=artifact))
                ],
            )
            for i, (heading, items) in enumerate(sections)
        ],
        tables=[],
        assets=[],
        warnings=[],
        external_sources=sources or [],
    )


_POINT = "Пункт с содержательным текстом примерно на одну строку слайда, около ста символов."


def _rich(n_sections: int, points: int = 4) -> ContentPack:
    return _pack(
        [(f"Тема {i}", [f"{_POINT} {i}.{k}." for k in range(points)]) for i in range(n_sections)]
    )


class TestCapacity:
    def test_one_sentence_brief_is_short(self):
        pack = _pack([("Идея", ["Сделать презентацию одной фразой."])])
        s = estimate_content_capacity(pack, _brief(10))
        assert s.status == Status.short
        assert s.capacity == 3  # обложка + один слайд + финал
        assert s.missing_slides == 7
        assert s.covered_topics == ["Идея"]

    def test_enough_content_is_ok(self):
        # раздел из четырёх пунктов по ~90 символов — один полный слайд
        s = estimate_content_capacity(_rich(8), _brief(10))
        assert s.status == Status.ok
        assert s.capacity >= 10

    def test_much_more_content_is_excess(self):
        s = estimate_content_capacity(_rich(12), _brief(5))
        assert s.status == Status.excess

    def test_one_line_per_slide_is_not_counted_as_a_full_slide(self):
        # десять коротких строк — не десять слайдов
        pack = _pack([("Список", [f"Строка {i}." for i in range(10)])])
        s = estimate_content_capacity(pack, _brief(12))
        assert s.capacity < 6

    def test_report_is_serialisable(self):
        d = estimate_content_capacity(_rich(1), _brief(10)).to_dict()
        assert d["status"] == "short" and d["missing_slides"] == d["requested"] - d["capacity"]


class TestFillPolicy:
    def test_brief_beats_config_default(self):
        assert resolve_content_fill(_brief(5, ContentFill.shrink), "expand") == ContentFill.shrink
        assert resolve_content_fill(_brief(5), "expand") == ContentFill.expand

    def test_shrink_target_respects_bounds(self):
        brief = _brief(10)
        s = estimate_content_capacity(_pack([("Идея", ["Одна фраза."])]), brief)
        assert shrink_target(brief, s, min_slides=3).target_slide_count == 3
        assert shrink_target(brief, s, min_slides=5).target_slide_count == 5


class TestNoFillerSlides:
    def test_plan_never_contains_generic_section_slides(self):
        plan = plan_deck(_pack([("Идея", ["Одна короткая мысль."])]), _brief(12), config=CFG)
        assert all(not s.title_intent.startswith("Раздел") for s in plan.slides)
        assert len(plan.slides) == plan.brief.target_slide_count

    def test_validation_rejects_a_title_only_content_slide(self):
        plan = plan_deck(_rich(3), _brief(6), config=CFG)
        bookends = {"title", "agenda", "thank_you", "cta"}
        content = next(s for s in plan.slides if s.purpose.value not in bookends)
        hollow = content.model_copy(
            update={"content_units": [u for u in content.content_units if u.kind.value == "title"]}
        )
        broken = plan.model_copy(
            update={"slides": [hollow if s.id == content.id else s for s in plan.slides]}
        )
        with pytest.raises(DeckDNAError) as exc:
            validate_deck_plan(broken, CFG)
        assert exc.value.code == "validation_failed"
        assert content.index in exc.value.details["slides"]

    def test_section_divider_without_body_is_allowed(self):
        plan = plan_deck(_rich(3, points=1), _brief(10), config=CFG)
        validate_deck_plan(plan, CFG)  # разделители законно без тела


class TestOrigins:
    def _filled(self) -> ContentPack:
        pack = _pack([("Своё", [f"{_POINT} свой."])])
        model_section = Section(
            id="sec-model-0",
            heading="Из модели",
            level=2,
            blocks=[
                Block(
                    kind=Kind.list,
                    items=[f"{_POINT} модель."],
                    source_ref=SourceRef(artifact_id="model:knowledge"),
                )
            ],
        )
        web_section = Section(
            id="sec-web-0",
            heading="Из интернета",
            level=2,
            blocks=[
                Block(
                    kind=Kind.list,
                    items=[f"{_POINT} веб."],
                    source_ref=SourceRef(artifact_id="web:0"),
                )
            ],
        )
        return pack.model_copy(
            update={
                "sections": [*pack.sections, web_section, model_section],
                "external_sources": [
                    ExternalSource(id="model:knowledge", origin=Origin.model),
                    ExternalSource(id="web:0", origin=Origin.web, url="https://example.org/a"),
                ],
            }
        )

    def test_graph_nodes_carry_origin_and_lower_confidence(self):
        graph = build_evidence_graph(self._filled())
        by_text = {n.text: n for n in graph.nodes if n.text}
        assert by_text[f"{_POINT} свой."].origin is None
        assert by_text[f"{_POINT} веб."].origin.value == "web"
        assert by_text[f"{_POINT} модель."].origin.value == "model"
        assert by_text[f"{_POINT} модель."].confidence == 0.5

    def test_plan_units_get_origin_badges(self):
        pack = self._filled()
        plan = annotate_unit_origins(plan_deck(pack, _brief(5), config=CFG), pack)
        origins = {
            u.text: u.origin.value
            for s in plan.slides
            for u in s.content_units
            if u.origin is not None and u.text and _POINT in u.text
        }
        assert origins == {
            f"{_POINT} свой.": "user",
            f"{_POINT} веб.": "web",
            f"{_POINT} модель.": "model",
        }
        validate_deck_plan(plan, CFG)  # origin проходит JSON-схему

    def test_plain_pack_plan_is_untouched(self):
        pack = _rich(3)
        plan = plan_deck(pack, _brief(6), config=CFG)
        assert annotate_unit_origins(plan, pack) is plan


def test_a_long_bibliography_counts_as_one_slide():
    pack = _pack(
        [
            ("Идея", [f"{_POINT} 1."]),
            (
                "Источники",
                [
                    f"Автор {i}. Длинное название работы о презентациях, 2024." * 3
                    for i in range(20)
                ],
            ),
        ]
    )
    s = estimate_content_capacity(pack, _brief(10))
    assert s.capacity == 3 + 1 + 1  # обложка, план, финал + идея + один слайд источников
    assert s.status == Status.short


def test_short_bibliography_counts_as_zero_standalone_slides_and_becomes_footnote():
    pack = _pack(
        [
            ("Идея", [f"{_POINT} 1.", f"{_POINT} 2."]),
            ("Источники", ["[1] Официальный отчёт — https://example.org/report"]),
        ],
        sources=[
            ExternalSource(
                id="a.md",
                origin=Origin.web,
                title="Официальный отчёт",
                url="https://example.org/report",
            )
        ],
    )
    s = estimate_content_capacity(pack, _brief(10))
    # обложка + финал + идея (<=3 источников — сноской, без отдельного слайда)
    assert s.capacity == 2 + 1
    plan = annotate_unit_origins(plan_deck(pack, _brief(6), config=CFG), pack)
    assert not any("источник" in sl.title_intent.casefold() for sl in plan.slides)
    assert any(
        u.role == "footnote" and "example.org" in (u.text or "")
        for sl in plan.slides
        for u in sl.content_units
    )


def test_more_than_three_sources_merge_into_single_slide_without_empty_divider():
    from deckdna.contracts.deck_plan import ContentUnit, DensityBudget, Level, Purpose

    pack = _pack(
        [
            ("Идея", [f"{_POINT} 1.", f"{_POINT} 2."]),
            (
                "Источники",
                [f"[{i}] Источник {i} — https://example.org/s{i}" for i in range(1, 6)],
            ),
        ]
    )
    raw = plan_deck(pack, _brief(6), config=CFG)
    src_slide = next(s for s in raw.slides if "источник" in s.title_intent.casefold())
    title_kind = raw.slides[0].content_units[0].kind
    empty_div = src_slide.model_copy(
        update={
            "id": "slide-empty-src",
            "purpose": Purpose.section_divider,
            "title_intent": "Источники",
            "content_units": [ContentUnit(role="title", kind=title_kind, text="Источники")],
            "density_budget": DensityBudget(level=Level.sparse, max_items=1),
        }
    )
    cont_slide = src_slide.model_copy(
        update={
            "id": "slide-src-cont",
            "title_intent": "Источники — продолжение",
        }
    )
    injected = raw.model_copy(
        update={"slides": [raw.slides[0], raw.slides[1], empty_div, cont_slide, raw.slides[-1]]}
    )
    cleaned = annotate_unit_origins(injected, pack)
    src_slides = [s for s in cleaned.slides if "источник" in s.title_intent.casefold()]
    assert len(src_slides) == 1
    assert src_slides[0].title_intent == "Источники"
    assert src_slides[0].purpose != Purpose.section_divider


def test_section_dividers_only_kept_when_deck_at_least_8_and_section_has_2_slides():
    from deckdna.contracts.deck_plan import ContentUnit, DensityBudget, Level, Purpose

    pack = _rich(2, points=2)
    short_plan = plan_deck(pack, _brief(5), config=CFG)
    title_kind = short_plan.slides[0].content_units[0].kind
    div = short_plan.slides[1].model_copy(
        update={
            "id": "slide-div-1",
            "purpose": Purpose.section_divider,
            "title_intent": "Раздел",
            "content_units": [ContentUnit(role="title", kind=title_kind, text="Раздел")],
            "density_budget": DensityBudget(level=Level.sparse, max_items=1),
        }
    )
    with_div = short_plan.model_copy(
        update={"slides": [short_plan.slides[0], div, *short_plan.slides[1:]]}
    )
    cleaned = annotate_unit_origins(with_div, pack)
    assert not any(s.purpose == Purpose.section_divider for s in cleaned.slides)
