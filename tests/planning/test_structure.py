"""ADR-016 Stage 1 (structure.py::plan_structure_llm) — purpose + evidence
pool + one-line slide_brief per slide, batched the same way plan_deck_llm's
outline is (ADR-012): baseline always computed first, small parallel
batches, honest per-slide fallback to the baseline's own evidence
assignment. No title_intent/key_message here — Stage 2 (not yet built)
writes those from this skeleton, see structure.py's module docstring."""

import logging

import pytest
from deckdna.contracts.content_pack import Block, ContentPack, Section, SourceRef
from deckdna.contracts.deck_plan import Brief, Purpose
from deckdna.errors import DeckDNAError
from deckdna.planning.config import DeckBounds, GenerationConfig
from deckdna.planning.story_director import plan_deck
from deckdna.planning.structure import (
    STRUCTURE_BATCH_SIZE,
    STRUCTURE_PROMPT,
    DeckStructure,
    plan_structure_llm,
)
from deckdna.providers.mock import MockProvider

CFG = GenerationConfig(deck=DeckBounds(min_slides=3, max_slides=40))


def _pack(n_sections: int = 8) -> ContentPack:
    """8 секций -> заведомо больше STRUCTURE_BATCH_SIZE=5 eligible-слайдов,
    так что реальные прогоны бьются минимум на 2 батча."""
    sections = [
        Section(
            id=f"sec-{i}",
            heading=f"Секция {i}",
            level=2,
            blocks=[
                Block(
                    kind="paragraph",
                    text=f"Текст секции {i}. Ещё предложение.",
                    source_ref=SourceRef(artifact_id="demo.md"),
                ),
                Block(
                    kind="list",
                    items=[f"пункт {j} секции {i}" for j in range(3)],
                    source_ref=SourceRef(artifact_id="demo.md"),
                ),
            ],
        )
        for i in range(n_sections)
    ]
    return ContentPack(
        schema_version="1.0",
        id="pack-structure",
        language="ru",
        title_hint="Структурная колода",
        sections=sections,
        tables=[],
        assets=[],
        warnings=[],
    )


def _brief(target: int = 14) -> Brief:
    return Brief(
        purpose="Показать возможности DeckDNA",
        audience="Жюри ЛЦТ",
        language="ru",
        target_slide_count=target,
    )


def _claim_ids(payload: dict) -> list[str]:
    return [n["id"] for n in payload["evidence_graph"]["nodes"] if n["type"] == "claim"]


def _valid_structure_fixture():
    def _respond(payload: dict) -> dict:
        claims = _claim_ids(payload)
        return {
            "slides": [
                {
                    "index": hint["index"],
                    "purpose": hint["purpose"],
                    "slide_brief": f"LLM job for slide {hint['index']}",
                    "evidence_ids": claims[:1],
                }
                for hint in payload["batch"]["hints"]
            ]
        }

    return _respond


def _ungrounded_structure_fixture():
    def _respond(payload: dict) -> dict:
        return {
            "slides": [
                {
                    "index": hint["index"],
                    "purpose": hint["purpose"],
                    "slide_brief": "x",
                    "evidence_ids": ["ev_nonexistent"],
                }
                for hint in payload["batch"]["hints"]
            ]
        }

    return _respond


def _eligible_baseline_indices(pack, brief) -> list[int]:
    baseline = plan_deck(pack, brief, CFG)
    protected = {Purpose.title, Purpose.thank_you, Purpose.cta}
    return [s.index for s in baseline.slides if s.purpose not in protected]


async def test_llm_fills_eligible_slides_and_batches_calls():
    pack, brief = _pack(), _brief()
    eligible = _eligible_baseline_indices(pack, brief)
    assert len(eligible) > STRUCTURE_BATCH_SIZE  # гарантирует >=2 батча

    gateway = MockProvider(fixtures={STRUCTURE_PROMPT: _valid_structure_fixture()})
    structure = await plan_structure_llm(pack, brief, gateway, config=CFG)

    assert isinstance(structure, DeckStructure)
    assert len(structure.slides) == brief.target_slide_count
    assert structure.used_llm > 0
    assert structure.evidence_graph_id == f"eg-{pack.id}"

    structure_calls = [c for c in gateway.calls if c["prompt"] == STRUCTURE_PROMPT]
    assert len(structure_calls) >= 2  # реальное батчирование

    protected = {Purpose.title, Purpose.thank_you, Purpose.cta}
    baseline = plan_deck(pack, brief, CFG)
    base_by_index = {s.index: s for s in baseline.slides}

    llm_touched = 0
    for slide in structure.slides:
        base = base_by_index[slide.index]
        if base.purpose in protected:
            assert slide.id == base.id
            continue
        if slide.slide_brief.startswith("LLM job for slide"):
            llm_touched += 1
            assert all(eid.startswith("ev_") for eid in slide.evidence_ids)
    assert llm_touched > 0


async def test_partial_batch_failure_falls_back_only_for_that_batch(caplog):
    pack, brief = _pack(), _brief()
    calls = {"n": 0}

    def _flaky(payload: dict) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            return _valid_structure_fixture()(payload)
        raise RuntimeError("provider down for this batch")

    gateway = MockProvider(fixtures={STRUCTURE_PROMPT: _flaky})
    with caplog.at_level(logging.WARNING, logger="deckdna.planning.structure"):
        structure = await plan_structure_llm(pack, brief, gateway, config=CFG)

    assert structure.used_llm > 0
    assert len(structure.slides) == brief.target_slide_count
    briefs = [s.slide_brief for s in structure.slides]
    assert any(b.startswith("LLM job for slide") for b in briefs)  # первый батч прошёл
    assert "structure batch" in caplog.text  # второй честно залогирован как fallback


async def test_all_batches_failing_is_a_full_honest_fallback(caplog):
    pack, brief = _pack(), _brief()

    class FailingGateway:
        async def text_json(self, prompt_name, payload, schema):
            raise RuntimeError("endpoint unreachable")

    with caplog.at_level(logging.WARNING, logger="deckdna.planning.structure"):
        structure = await plan_structure_llm(pack, brief, FailingGateway(), config=CFG)

    assert structure.used_llm == 0
    baseline = plan_deck(pack, brief, CFG)
    from deckdna.planning.evidence import build_evidence_graph

    graph_ids = {node.id for node in build_evidence_graph(pack).nodes}
    for got, base in zip(structure.slides, baseline.slides, strict=True):
        assert got.slide_brief == (base.key_message or base.title_intent)
        # Content fallback resolves the baseline block IDs to actual graph
        # evidence; bookend/meta references can remain non-content IDs.
        if any(":b" in eid for eid in base.evidence_ids):
            assert got.evidence_ids and set(got.evidence_ids) <= graph_ids
    assert "structure batch" in caplog.text


async def test_ungrounded_evidence_ids_are_rejected_per_item():
    pack, brief = _pack(), _brief()
    gateway = MockProvider(fixtures={STRUCTURE_PROMPT: _ungrounded_structure_fixture()})

    structure = await plan_structure_llm(pack, brief, gateway, config=CFG)

    assert structure.used_llm == 0
    baseline = plan_deck(pack, brief, CFG)
    from deckdna.planning.evidence import build_evidence_graph

    graph_ids = {node.id for node in build_evidence_graph(pack).nodes}
    for got, base in zip(structure.slides, baseline.slides, strict=True):
        assert got.slide_brief == (base.key_message or base.title_intent)
        # Content fallback resolves the baseline block IDs to actual graph
        # evidence; bookend/meta references can remain non-content IDs.
        if any(":b" in eid for eid in base.evidence_ids):
            assert got.evidence_ids and set(got.evidence_ids) <= graph_ids


async def test_items_outside_the_requested_batch_are_ignored_not_trusted():
    pack, brief = _pack(), _brief()

    def _respond(payload: dict) -> dict:
        claims = _claim_ids(payload)
        hints = payload["batch"]["hints"]
        items = [
            {
                "index": hint["index"],
                "purpose": hint["purpose"],
                "slide_brief": f"LLM job for slide {hint['index']}",
                "evidence_ids": claims[:1],
            }
            for hint in hints
        ]
        items.append(dict(items[0]))  # дубль первого индекса
        items.append(
            {
                "index": 9999,
                "purpose": items[0]["purpose"],
                "slide_brief": "чужой батч",
                "evidence_ids": claims[:1],
            }
        )
        return {"slides": items}

    gateway = MockProvider(fixtures={STRUCTURE_PROMPT: _respond})
    structure = await plan_structure_llm(pack, brief, gateway, config=CFG)

    assert structure.used_llm > 0
    assert 9999 not in {s.index for s in structure.slides}
    assert len(structure.slides) == brief.target_slide_count


async def test_protected_slides_are_never_sent_to_the_model():
    pack, brief = _pack(), _brief()
    baseline = plan_deck(pack, brief, CFG)
    protected = {Purpose.title, Purpose.thank_you, Purpose.cta}
    protected_indices = {s.index for s in baseline.slides if s.purpose in protected}
    assert protected_indices  # sanity: baseline действительно их создаёт

    gateway = MockProvider(fixtures={STRUCTURE_PROMPT: _valid_structure_fixture()})
    structure = await plan_structure_llm(pack, brief, gateway, config=CFG)

    sent_indices: set[int] = set()
    for call in gateway.calls:
        sent_indices.update(call["payload"]["batch"]["indices"])
    assert sent_indices.isdisjoint(protected_indices)

    for idx in protected_indices:
        base = next(s for s in baseline.slides if s.index == idx)
        got = next(s for s in structure.slides if s.index == idx)
        assert got.id == base.id


def _sparse_pack() -> ContentPack:
    """5 коротких секций -> с target=12 baseline'у не хватает контента и
    он честно добирает section_divider'ами (см. test_story_director.py's
    test_no_empty_slides_and_named_dividers) вместо generic-паддинга."""
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


async def test_section_dividers_are_never_sent_to_the_model():
    """Живой баг: section_divider — легитимно
    content-free bookend, но SlideStructureItem.evidence_ids требует
    min_length=1, так что модель вынуждена придумывать evidence и
    slide_brief, описывающий саму роль слайда ("Слайд задаёт маршрут:
    от проблемы к решению...") -- тот текст затем утекал как title
    через content_writer's fallback, давая title-only слайд с
    обманчивым заголовком вместо честного divider'а. Fix: dividers
    protected так же, как title/thank_you/cta -- никогда не уходят в
    LLM, Stage 1 честно копирует baseline (см.
    structure.py::_STRUCTURE_PROTECTED_PURPOSES)."""
    pack, brief = _sparse_pack(), _brief(target=12)
    baseline = plan_deck(pack, brief, CFG)
    divider_indices = {
        s.index for s in baseline.slides if s.purpose is Purpose.section_divider
    }
    assert divider_indices  # sanity: baseline действительно их создаёт

    gateway = MockProvider(fixtures={STRUCTURE_PROMPT: _valid_structure_fixture()})
    structure = await plan_structure_llm(pack, brief, gateway, config=CFG)

    sent_indices: set[int] = set()
    for call in gateway.calls:
        sent_indices.update(call["payload"]["batch"]["indices"])
    assert sent_indices.isdisjoint(divider_indices)

    for idx in divider_indices:
        base = next(s for s in baseline.slides if s.index == idx)
        got = next(s for s in structure.slides if s.index == idx)
        assert got.id == base.id
        assert got.purpose is Purpose.section_divider
        assert got.evidence_ids == list(base.evidence_ids)


async def test_slide_count_out_of_bounds_raises_before_call():
    pack, brief = _pack(), _brief(target=10)
    tight = GenerationConfig(
        deck=DeckBounds(min_slides=3, max_slides=5, default_slide_count=4)
    )
    gateway = MockProvider(fixtures={STRUCTURE_PROMPT: _valid_structure_fixture()})

    with pytest.raises(DeckDNAError) as exc:
        await plan_structure_llm(pack, brief, gateway, config=tight)
    assert exc.value.code == "invalid_input"
    assert gateway.calls == []  # гейт до любого вызова модели


async def test_structure_cannot_drop_equation_evidence_from_source_slide():
    from deckdna.ingestion.content_parsers import parse_markdown
    from deckdna.planning.evidence import build_evidence_graph

    pack = parse_markdown(r"""# Measurement
## Formula
Explain what the index means.

\[
Index = 0.25 \times active - 0.75 \times idle
\]
""")
    gateway = MockProvider(fixtures={STRUCTURE_PROMPT: _valid_structure_fixture()})
    structure = await plan_structure_llm(pack, _brief(3), gateway, config=CFG)
    math_ids = {
        n.id for n in build_evidence_graph(pack).nodes if "Index =" in (n.text or "")
    }
    assert math_ids
    assert any(math_ids <= set(slide.evidence_ids) for slide in structure.slides)
