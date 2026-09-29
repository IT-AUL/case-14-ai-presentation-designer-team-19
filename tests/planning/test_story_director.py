"""Story Director v0: ContentPack + Brief -> DeckPlan (детерминированный)."""

import json
from pathlib import Path

import pytest
from deckdna.contracts.content_pack import Block, ContentPack, Section, SourceRef
from deckdna.contracts.deck_plan import Brief, Kind, Purpose
from deckdna.errors import DeckDNAError
from deckdna.planning import plan_deck
from deckdna.planning.config import DeckBounds, GenerationConfig
from jsonschema import validate

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "deck-plan.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text())

CFG = GenerationConfig(deck=DeckBounds(min_slides=3, max_slides=40))


def _pack(n_sections: int = 4, blocks_per: int = 3) -> ContentPack:
    sections = []
    for i in range(n_sections):
        blocks = [
            Block(
                kind="paragraph",
                text=f"Секция {i}: первое предложение. Второе предложение.",
                source_ref=SourceRef(artifact_id="demo.md"),
            ),
            Block(
                kind="list",
                items=[f"пункт {j}" for j in range(blocks_per)],
                source_ref=SourceRef(artifact_id="demo.md"),
            ),
        ]
        sections.append(
            Section(id=f"sec-{i}", heading=f"Секция {i}", level=2, blocks=blocks)
        )
    return ContentPack(
        schema_version="1.0",
        id="pack-test",
        language="ru",
        title_hint="Тестовая колода",
        sections=sections,
        tables=[],
        assets=[],
        warnings=[],
    )


def _brief(target: int = 10) -> dict:
    return {
        "purpose": "Показать возможности DeckDNA",
        "audience": "Жюри ЛЦТ",
        "language": "ru",
        "target_slide_count": target,
    }


class TestPlanStructure:
    def test_schema_valid(self):
        plan = plan_deck(_pack(), Brief(**_brief(10)), config=CFG)
        validate(instance=json.loads(plan.model_dump_json(exclude_none=True)), schema=SCHEMA)

    def test_slide_count_and_order(self):
        plan = plan_deck(_pack(4), Brief(**_brief(10)), config=CFG)
        assert len(plan.slides) == 10
        assert plan.slides[0].purpose is Purpose.title
        assert plan.slides[1].purpose is Purpose.agenda
        assert plan.slides[-1].purpose in (Purpose.thank_you, Purpose.cta)
        assert [s.index for s in plan.slides] == list(range(10))
        assert len({s.id for s in plan.slides}) == 10

    def test_evidence_and_titles(self):
        plan = plan_deck(_pack(), Brief(**_brief(12)), config=CFG)
        titles = set()
        for slide in plan.slides:
            assert slide.evidence_ids, f"{slide.id}: пустые evidence_ids"
            assert slide.title_intent not in titles
            titles.add(slide.title_intent)

    def test_section_slide_mapping(self):
        plan = plan_deck(_pack(3), Brief(**_brief(5)), config=CFG)
        assert plan.sections
        for sec in plan.sections:
            assert sec.slide_ids, f"секция {sec.id} не попала ни на один слайд"

    def test_min_deck(self):
        plan = plan_deck(_pack(1, 1), Brief(**_brief(3)), config=CFG)
        assert len(plan.slides) == 3
        assert plan.slides[0].purpose is Purpose.title
        assert plan.slides[-1].purpose is Purpose.thank_you

    def test_purpose_keywords(self):
        pack = _pack(0)
        block = Block(
            kind="paragraph", text="текст", source_ref=SourceRef(artifact_id="a")
        )
        pack.sections = [
            Section(id="s0", heading="Проблема", level=1, blocks=[block]),
            Section(id="s1", heading="Roadmap", level=1, blocks=[block]),
        ]
        plan = plan_deck(pack, Brief(**_brief(4)), config=CFG)
        purposes = [s.purpose for s in plan.slides]
        assert purposes[0] is Purpose.title
        assert purposes[1] is Purpose.problem
        assert purposes[2] is Purpose.timeline

    def test_bullets_split_into_units(self):
        plan = plan_deck(_pack(1, 4), Brief(**_brief(4)), config=CFG)
        structural = (Purpose.title, Purpose.thank_you, Purpose.cta)
        content = [s for s in plan.slides if s.purpose not in structural]
        bullets = [u for s in content for u in s.content_units if u.kind is Kind.bullet]
        assert len(bullets) == 4

    def test_deterministic(self):
        a = plan_deck(_pack(), Brief(**_brief(10)), config=CFG)
        b = plan_deck(_pack(), Brief(**_brief(10)), config=CFG)
        assert a.model_dump_json() == b.model_dump_json()

    def test_generic_purpose_is_not_rendered_as_cover_subtitle(self):
        plan = plan_deck(
            _pack(),
            Brief(**{**_brief(10), "purpose": "product"}),
            config=CFG,
        )
        title = plan.slides[0]
        assert not any(
            unit.kind is Kind.subtitle and unit.text == "product"
            for unit in title.content_units
        )
        assert title.key_message == "product"


class TestLimits:
    def test_schema_guard_rails(self):
        """target_slide_count <3 или >40 отсекается ещё на Brief
        (schema guard rails, см. config.py)."""
        from pydantic import ValidationError

        for bad in (2, 41):
            with pytest.raises(ValidationError):
                Brief(**_brief(bad))

    def test_config_bounds_enforced(self):
        tight = GenerationConfig(deck=DeckBounds(min_slides=10, max_slides=12))
        with pytest.raises(DeckDNAError, match="configured range"):
            plan_deck(_pack(), Brief(**_brief(5)), config=tight)
        plan_deck(_pack(), Brief(**_brief(11)), config=tight)


def _pack_with_lead_intro(n_sections: int = 4, blocks_per: int = 3) -> ContentPack:
    """Реальный писательский паттерн (H1 title + lead-абзац, потом ##
    секции) -- content_parsers.py заводит для этого свою же секцию[0]
    (heading == pack.title_hint), отдельную от настоящих H2-секций."""
    lead = Section(
        id="sec-lead",
        heading="Заголовок документа",
        level=1,
        blocks=[
            Block(
                kind="paragraph",
                text="Вводный абзац документа перед первым разделом.",
                source_ref=SourceRef(artifact_id="demo.md"),
            )
        ],
    )
    body = _pack(n_sections, blocks_per).sections
    return ContentPack(
        schema_version="1.0",
        id="pack-lead-test",
        language="ru",
        title_hint="Заголовок документа",
        sections=[lead, *body],
        tables=[],
        assets=[],
        warnings=[],
    )


class TestLeadIntroSectionNeverStealsATitle:
    """Live репро (сгенерированная колода, balanced-стратегия): H1-lead
    абзац -- своя крошечная "секция" без реального заголовка -- почти
    всегда оказывался наименьшей соседней парой при merge-под-бюджет,
    и т.к. merge сохраняет порядок списка, ``primary_sec =
    slide_parts[0][0]`` всегда выбирал ЕЁ заголовком слайда --
    настоящие буллеты соседней секции («Проблема») рендерились под
    заголовком-дублем названия колоды. Абзац теперь заранее
    подмешивается в первую настоящую секцию и не участвует в merge
    как отдельная единица -- контент не теряется, просто больше не
    крадёт чужой заголовок."""

    def test_lead_paragraph_folded_not_orphaned(self):
        """Малый бюджет, вынуждающий merge: заголовок первого слайда с
        контентом — заголовок ПЕРВОЙ настоящей секции, не документа."""
        pack = _pack_with_lead_intro(n_sections=4, blocks_per=3)
        plan = plan_deck(pack, Brief(**_brief(target=6)), config=CFG)

        assert plan.slides[0].purpose == Purpose.title  # заголовок колоды не трогаем

        body_slides = plan.slides[1:]
        # ни один заголовок body-слайда не совпадает с заголовком колоды —
        # старый баг переносил его на слайд с реальными буллетами секции
        assert all(
            sp.title_intent.split(" — часть")[0] != pack.title_hint
            for sp in body_slides
        )
        # текст вступительного абзаца не потерян — долетел до какого-то слайда
        assert any(
            any(
                "Вводный абзац документа" in (cu.text or "")
                for cu in sp.content_units
            )
            for sp in body_slides
        )

    def test_lead_paragraph_lands_on_first_real_section_title(self):
        """Прицельно: вступительный абзац оказывается на слайде,
        озаглавленном первой настоящей секцией («Секция 0»)."""
        pack = _pack_with_lead_intro(n_sections=4, blocks_per=3)
        plan = plan_deck(pack, Brief(**_brief(target=6)), config=CFG)

        carrying = [
            sp
            for sp in plan.slides
            if any(
                "Вводный абзац документа" in (cu.text or "")
                for cu in sp.content_units
            )
        ]
        assert len(carrying) == 1
        assert carrying[0].title_intent.startswith("Секция 0")

    def test_no_lead_section_is_a_noop(self):
        """Гейт срабатывает только когда sections[0].heading ==
        title_hint — пак без такого паттерна ведёт себя как раньше."""
        pack = _pack(n_sections=4, blocks_per=3)  # title_hint не совпадает ни с чем
        plan_with = plan_deck(pack, Brief(**_brief(target=6)), config=CFG)
        # тот же пак, но title_hint искусственно не совпадает -- baseline
        assert pack.title_hint != pack.sections[0].heading
        assert len(plan_with.slides) == 6  # инвариант длины не задет

    def test_single_section_pack_untouched(self):
        """len(groups) <= 1 -- гейт не пытается сворачивать список в
        пустоту."""
        lead = Section(
            id="sec-only",
            heading="Одна секция",
            level=1,
            blocks=[
                Block(
                    kind="paragraph",
                    text="Единственный абзац во всём документе.",
                    source_ref=SourceRef(artifact_id="demo.md"),
                )
            ],
        )
        pack = ContentPack(
            schema_version="1.0",
            id="pack-single",
            language="ru",
            title_hint="Одна секция",
            sections=[lead],
            tables=[],
            assets=[],
            warnings=[],
        )
        plan = plan_deck(pack, Brief(**_brief(target=4)), config=CFG)
        assert len(plan.slides) == 4  # не падает, не пустой список


class TestSparseContentPadding:
    """Мало контента + большой target: добор без пустых дивайдеров."""

    def _sparse_pack(self) -> ContentPack:
        return ContentPack(
            schema_version="1.0",
            id="pack-sparse",
            language="ru",
            title_hint="Разрежённая колода",
            sections=[
                Section(
                    id=f"sec-{i}",
                    heading=h,
                    level=2,
                    blocks=[
                        Block(
                            kind="paragraph",
                            text=f"Текст секции {i}. Второе предложение.",
                            source_ref=SourceRef(artifact_id="d.md"),
                        )
                    ],
                )
                for i, h in enumerate(
                    ["Проблема", "Решение", "Метрики", "Команда", "Следующие шаги"]
                )
            ],
            tables=[],
            assets=[],
            warnings=[],
        )

    def test_no_empty_slides_and_named_dividers(self):
        """Сценарий бага: 5 секций + target=12 — раньше 2 голых 'Раздел'."""
        plan = plan_deck(self._sparse_pack(), Brief(**_brief(12)), config=CFG)
        assert len(plan.slides) == 12
        assert all(s.content_units for s in plan.slides), "пустой слайд"
        dividers = [s for s in plan.slides if s.purpose is Purpose.section_divider]
        headings = {s.heading for s in self._sparse_pack().sections}
        # доборные divider'ы несут реальные заголовки секций, не 'Раздел'
        assert {d.title_intent for d in dividers} <= headings
        # divider стоит перед слайдами своей секции, не скоплением в конце
        for d in dividers:
            nxt = plan.slides[d.index + 1]
            assert nxt.purpose is not Purpose.section_divider, (
                f"два дивайдера подряд на {d.index}"
            )

    def test_sentence_split_deepens_content(self):
        """При дефиците длинные абзацы режутся по предложениям —
        добор идёт реальным контентом, а не только дивайдерами.
        Хвост сплита остаётся одним unit'ом (не sentence-per-unit),
        иначе ~50k paragraph взрывается в сотни юнитов."""
        pack = ContentPack(
            schema_version="1.0",
            id="pack-long",
            language="ru",
            sections=[
                Section(
                    id="s0",
                    heading="Тема",
                    level=2,
                    blocks=[
                        Block(
                            kind="paragraph",
                            text="Раз. Два. Три. Четыре. Пять. Шесть.",
                            source_ref=SourceRef(artifact_id="d.md"),
                        )
                    ],
                )
            ],
            tables=[],
            assets=[],
            warnings=[],
        )
        plan = plan_deck(pack, Brief(**_brief(8)), config=CFG)
        paras = [
            u.text
            for s in plan.slides
            for u in s.content_units
            if u.kind is Kind.paragraph
        ]
        joined = " ".join(paras)
        for sent in ("Раз.", "Два.", "Три.", "Четыре.", "Пять.", "Шесть."):
            assert sent in joined, paras
        # ни один юнит не должен раздуваться в sentence-per-unit:
        # юнитов не больше, чем слайдов с контентом + 1
        assert len(paras) <= len(plan.slides), paras

    def test_huge_paragraph_bounded_units_no_loss(self):
        """~50k paragraph при дефиците: юниты ограничены (не тысячи),
        контент не теряется и сохраняет порядок (sentence-split хвост —
        один unit, сплитится самый большой)."""
        para = " ".join(f"Предложение номер {i}." for i in range(500))
        assert len(para) > 10_000
        pack = ContentPack(
            schema_version="1.0",
            id="pack-huge",
            language="ru",
            sections=[
                Section(
                    id="s0",
                    heading="Тема",
                    level=2,
                    blocks=[
                        Block(
                            kind="paragraph",
                            text=para,
                            source_ref=SourceRef(artifact_id="d.md"),
                        )
                    ],
                )
            ],
            tables=[],
            assets=[],
            warnings=[],
        )
        plan = plan_deck(pack, Brief(**_brief(12)), config=CFG)
        para_units = [
            u.text
            for s in plan.slides
            for u in s.content_units
            if u.kind is Kind.paragraph
        ]
        # было ~500 юнитов (по одному на предложение); теперь — слайды
        assert len(para_units) <= len(plan.slides), len(para_units)
        joined = " ".join(para_units)
        assert joined == para  # ни символа не потеряно, порядок сохранён
        # распределение сбалансировано: ни один слайд не несёт больше
        # половины всего текста (раньше хвост съезжал на последний)
        biggest = max(len(t) for t in para_units)
        assert biggest <= len(para) // 2, biggest

    def test_extreme_deficit_still_no_empty(self):
        """Патологический ratio: даже generic-паддинг несёт title-юнит."""
        pack = ContentPack(
            schema_version="1.0",
            id="pack-tiny",
            language="ru",
            sections=[
                Section(
                    id="s0",
                    heading="Тема",
                    level=2,
                    blocks=[
                        Block(
                            kind="paragraph",
                            text="Коротко.",
                            source_ref=SourceRef(artifact_id="d.md"),
                        )
                    ],
                )
            ],
            tables=[],
            assets=[],
            warnings=[],
        )
        plan = plan_deck(pack, Brief(**_brief(15)), config=CFG)
        assert len(plan.slides) == 15
        assert all(s.content_units for s in plan.slides)
        assert all(s.evidence_ids for s in plan.slides)
        # recap-слайд присутствует как структурный добор
        assert any(s.title_intent.startswith("Итоги:") for s in plan.slides)


class TestEmptyContent:
    def test_empty_pack_still_plans(self):
        pack = ContentPack(
            schema_version="1.0",
            id="pack-empty",
            language="ru",
            sections=[],
            tables=[],
            assets=[],
            warnings=[],
        )
        plan = plan_deck(pack, Brief(**_brief(3)), config=CFG)
        assert len(plan.slides) == 3
        validate(instance=json.loads(plan.model_dump_json(exclude_none=True)), schema=SCHEMA)


class TestConclusionTitles:
    """Q3: без модели заголовок слайда — вывод (первое предложение его
    абзаца), а не тема; faithful сохраняет заголовки источника."""

    def _pack(self):
        from deckdna.contracts.content_pack import Block, ContentPack, Kind, Section, SourceRef

        ref = SourceRef(artifact_id="a")
        sections = [
            Section(
                id=f"s{i}",
                heading=f"Тема {i}",
                level=2,
                blocks=[
                    Block(
                        kind=Kind.paragraph,
                        text=text,
                        source_ref=ref,
                    )
                ],
            )
            for i, text in enumerate(
                [
                    "Рынок растёт на 30% в год. Вторая мысль остаётся в теле слайда.",
                    "Вопрос ли это, спорно ли и вообще так ли важно знать? Да.",
                    "Коротко. Ещё текст.",
                    "Ручная адаптация контента под фирменный шаблон — узкое место.",
                ]
            )
        ]
        return ContentPack(
            schema_version="1.0",
            id="p",
            language="ru",
            title_hint="Документ",
            sections=sections,
            tables=[],
            assets=[],
            warnings=[],
        )

    def _plan(self, strategy):
        from deckdna.contracts.deck_plan import Brief
        from deckdna.contracts.variant_spec import Strategy  # noqa: F401

        return plan_deck(
            self._pack(),
            Brief(purpose="Показать рынок", audience="все", language="ru", target_slide_count=10),
            config=CFG,
            strategy=Strategy(strategy),
        )

    def test_title_is_the_conclusion_and_is_not_duplicated_in_the_body(self):
        plan = self._plan("balanced")
        slide = next(s for s in plan.slides if s.title_intent == "Рынок растёт на 30% в год")
        bodies = [u.text for u in slide.content_units if u.kind.value == "paragraph"]
        assert bodies == ["Вторая мысль остаётся в теле слайда."]
        assert "Раздел: Тема 0" in (slide.speaker_note or "")

    def test_whole_paragraph_moves_into_the_title_without_leaving_an_empty_unit(self):
        plan = self._plan("balanced")
        slide = next(s for s in plan.slides if s.title_intent.startswith("Ручная адаптация"))
        assert not [u for u in slide.content_units if u.kind.value == "paragraph"]

    def test_unsuitable_sentences_keep_the_section_heading(self):
        plan = self._plan("balanced")
        titles = {s.title_intent for s in plan.slides}
        assert "Тема 1" in titles  # вопрос
        assert "Тема 2" in titles  # слишком короткое

    def test_faithful_keeps_source_headings(self):
        plan = self._plan("faithful")
        titles = {s.title_intent for s in plan.slides}
        assert {"Тема 0", "Тема 1", "Тема 2", "Тема 3"} <= titles


class TestDesiredVisual:
    """Regression: _desired_visual() only ever checked image/table, so a
    slide with a real chart or diagram unit honestly reported "none" —
    found while checking planning/image_brief.py's ADR-017 filter, which
    skips slides that already have a desired_visual (a chart/diagram
    slide would otherwise also get a generated image piled on top)."""

    def _units(self, *kinds: Kind):
        from deckdna.contracts.deck_plan import ContentUnit

        return [
            ContentUnit(role="body", kind=k, text="x", evidence_ids=["e1"]) for k in kinds
        ]

    def test_chart_unit_is_detected(self):
        from deckdna.contracts.deck_plan import DesiredVisual
        from deckdna.planning.story_director import _desired_visual

        assert (
            _desired_visual(self._units(Kind.bullet, Kind.chart)) == DesiredVisual.chart
        )

    def test_diagram_unit_is_detected(self):
        from deckdna.contracts.deck_plan import DesiredVisual
        from deckdna.planning.story_director import _desired_visual

        assert (
            _desired_visual(self._units(Kind.bullet, Kind.diagram)) == DesiredVisual.diagram
        )

    def test_image_still_takes_priority_over_chart(self):
        from deckdna.contracts.deck_plan import DesiredVisual
        from deckdna.planning.story_director import _desired_visual

        assert (
            _desired_visual(self._units(Kind.chart, Kind.image)) == DesiredVisual.image
        )

    def test_plain_bullets_report_none(self):
        from deckdna.contracts.deck_plan import DesiredVisual
        from deckdna.planning.story_director import _desired_visual

        assert _desired_visual(self._units(Kind.bullet)) == DesiredVisual.none
