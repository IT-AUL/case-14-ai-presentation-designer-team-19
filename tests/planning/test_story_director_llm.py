"""LLM-путь Story Director v2: plan_deck_llm — batched outline + детерминированный
контент из evidence (см. story_director.py:plan_deck_llm для полного объяснения
дизайна). Ключевое отличие от v1 (один вызов = весь DeckPlan): baseline всегда
считается первым, слайды режутся на маленькие параллельные батчи, любой сбой —
per-slide honest fallback на baseline, а не всей колоды целиком."""

import logging

import pytest
from deckdna.contracts.content_pack import Block, ContentPack, Section, SourceRef
from deckdna.contracts.deck_plan import Brief, DeckPlan, Purpose
from deckdna.contracts.design_dna import Capacities, DesignDNA
from deckdna.errors import DeckDNAError
from deckdna.planning.config import DeckBounds, GenerationConfig
from deckdna.planning.story_director import (
    LLM_PLANNER_VERSION,
    OUTLINE_BATCH_SIZE,
    PLANNER_VERSION,
    STORYLINE_PROMPT,
    plan_deck,
    plan_deck_llm,
)
from deckdna.providers.mock import MockProvider

CFG = GenerationConfig(deck=DeckBounds(min_slides=3, max_slides=40))


def _pack(n_sections: int = 8) -> ContentPack:
    """8 секций -> заведомо больше OUTLINE_BATCH_SIZE=5 eligible-слайдов,
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
        id="pack-llm",
        language="ru",
        title_hint="LLM колода",
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


def _valid_outline_fixture():
    """Callable-фикстура: отвечает валидно на ЛЮБОЙ батч, заземлённый на
    реальные claim-узлы графа, который приходит внутри самого payload."""

    def _respond(payload: dict) -> dict:
        claims = _claim_ids(payload)
        return {
            "slides": [
                {
                    "index": hint["index"],
                    "purpose": hint["purpose"],
                    "title_intent": f"LLM: {hint['baseline_title']}",
                    "key_message": "Вывод модели.",
                    "evidence_ids": claims[:1],
                }
                for hint in payload["batch"]["hints"]
            ]
        }

    return _respond


def _ungrounded_outline_fixture():
    """Валидная схема, но evidence_ids не существуют в графе -> заземление
    отклоняет каждый item."""

    def _respond(payload: dict) -> dict:
        return {
            "slides": [
                {
                    "index": hint["index"],
                    "purpose": hint["purpose"],
                    "title_intent": "x",
                    "key_message": "x",
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


async def test_llm_hybrid_fills_eligible_slides_and_batches_calls():
    pack, brief = _pack(), _brief()
    eligible = _eligible_baseline_indices(pack, brief)
    assert len(eligible) > OUTLINE_BATCH_SIZE  # гарантирует >=2 батча

    gateway = MockProvider(fixtures={STORYLINE_PROMPT: _valid_outline_fixture()})
    plan = await plan_deck_llm(pack, brief, gateway, config=CFG)

    assert isinstance(plan, DeckPlan)
    assert len(plan.slides) == brief.target_slide_count
    assert plan.provenance.planner == LLM_PLANNER_VERSION
    assert plan.provenance.prompt_version == "2.0.1"
    assert plan.provenance.model_id == "mock"
    assert plan.evidence_graph_id == f"eg-{pack.id}"

    # реальное батчирование — больше одного вызова storyline
    storyline_calls = [c for c in gateway.calls if c["prompt"] == STORYLINE_PROMPT]
    assert len(storyline_calls) >= 2

    by_index = {s.index: s for s in plan.slides}
    protected = {Purpose.title, Purpose.thank_you, Purpose.cta}
    baseline = plan_deck(pack, brief, CFG)
    base_by_index = {s.index: s for s in baseline.slides}

    llm_touched = 0
    for idx, base in base_by_index.items():
        slide = by_index[idx]
        if base.purpose in protected:
            # protected — никогда не тронуты моделью, даже если она ответила
            assert slide.title_intent == base.title_intent
            assert slide.id == base.id
        elif slide.title_intent.startswith("LLM: "):
            llm_touched += 1
            # тело слайда — verbatim evidence, не выдумка модели
            assert slide.content_units
            assert all((u.evidence_ids or [""])[0].startswith("ev_") for u in slide.content_units)
    assert llm_touched > 0


async def test_partial_batch_failure_falls_back_only_for_that_batch(caplog):
    pack, brief = _pack(), _brief()
    calls = {"n": 0}

    def _flaky(payload: dict) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            return _valid_outline_fixture()(payload)
        raise RuntimeError("provider down for this batch")

    gateway = MockProvider(fixtures={STORYLINE_PROMPT: _flaky})
    with caplog.at_level(logging.WARNING, logger="deckdna.planning.story_director"):
        plan = await plan_deck_llm(pack, brief, gateway, config=CFG)

    assert plan.provenance.planner == LLM_PLANNER_VERSION  # >=1 слайд от модели
    assert len(plan.slides) == brief.target_slide_count
    titles = [s.title_intent for s in plan.slides]
    assert any(t.startswith("LLM: ") for t in titles)  # первый батч прошёл
    assert "outline batch" in caplog.text  # второй честно залогирован как fallback


async def test_all_batches_failing_is_a_full_honest_fallback(caplog):
    pack, brief = _pack(), _brief()

    class FailingGateway:
        async def text_json(self, prompt_name, payload, schema):
            raise RuntimeError("endpoint unreachable")

    with caplog.at_level(logging.WARNING, logger="deckdna.planning.story_director"):
        plan = await plan_deck_llm(pack, brief, FailingGateway(), config=CFG)

    assert plan.provenance.planner == PLANNER_VERSION
    baseline = plan_deck(pack, brief, CFG)
    assert [s.title_intent for s in plan.slides] == [s.title_intent for s in baseline.slides]
    assert "outline batch" in caplog.text


async def test_ungrounded_evidence_ids_are_rejected_per_item():
    """Модель отвечает валидной схемой, но ссылается на несуществующие
    evidence_ids на каждом слайде -> ни одному item'у не доверяем, честный
    полный fallback (тот же наблюдаемый эффект, что при отказе провайдера,
    но через другой гейт — заземление, а не транспорт)."""
    pack, brief = _pack(), _brief()
    gateway = MockProvider(fixtures={STORYLINE_PROMPT: _ungrounded_outline_fixture()})

    plan = await plan_deck_llm(pack, brief, gateway, config=CFG)

    assert plan.provenance.planner == PLANNER_VERSION
    baseline = plan_deck(pack, brief, CFG)
    assert [s.title_intent for s in plan.slides] == [s.title_intent for s in baseline.slides]


async def test_items_outside_the_requested_batch_are_ignored_not_trusted():
    """Модель дублирует индекс и добавляет индекс, которого не просили —
    оба честно отбрасываются, батч засчитывается по оставшимся валидным
    item'ам, без исключений и без порчи чужого батча."""
    pack, brief = _pack(), _brief()

    def _respond(payload: dict) -> dict:
        claims = _claim_ids(payload)
        hints = payload["batch"]["hints"]
        items = [
            {
                "index": hint["index"],
                "purpose": hint["purpose"],
                "title_intent": f"LLM: {hint['baseline_title']}",
                "key_message": "Вывод модели.",
                "evidence_ids": claims[:1],
            }
            for hint in hints
        ]
        # дубль первого индекса + индекс далеко за пределами батча
        items.append(dict(items[0]))
        items.append(
            {
                "index": 9999,
                "purpose": items[0]["purpose"],
                "title_intent": "чужой батч",
                "key_message": "x",
                "evidence_ids": claims[:1],
            }
        )
        return {"slides": items}

    gateway = MockProvider(fixtures={STORYLINE_PROMPT: _respond})
    plan = await plan_deck_llm(pack, brief, gateway, config=CFG)

    assert plan.provenance.planner == LLM_PLANNER_VERSION
    assert 9999 not in {s.index for s in plan.slides}
    assert len(plan.slides) == brief.target_slide_count


async def test_protected_slides_are_never_sent_to_the_model():
    pack, brief = _pack(), _brief()
    baseline = plan_deck(pack, brief, CFG)
    protected = {Purpose.title, Purpose.thank_you, Purpose.cta}
    protected_indices = {s.index for s in baseline.slides if s.purpose in protected}
    assert protected_indices  # sanity: baseline действительно их создаёт

    gateway = MockProvider(fixtures={STORYLINE_PROMPT: _valid_outline_fixture()})
    plan = await plan_deck_llm(pack, brief, gateway, config=CFG)

    sent_indices: set[int] = set()
    for call in gateway.calls:
        sent_indices.update(call["payload"]["batch"]["indices"])
    assert sent_indices.isdisjoint(protected_indices)

    for idx in protected_indices:
        base = next(s for s in baseline.slides if s.index == idx)
        got = next(s for s in plan.slides if s.index == idx)
        assert got.title_intent == base.title_intent


async def test_capacities_cap_bullets_deterministically_even_if_model_cites_more():
    pack, brief = _pack(), _brief()
    dna = DesignDNA.model_construct(capacities=Capacities(max_bullets=2))

    def _respond(payload: dict) -> dict:
        claims = _claim_ids(payload)  # 4 claim-узла на секцию в _pack()
        hint = payload["batch"]["hints"][0]
        return {
            "slides": [
                {
                    "index": hint["index"],
                    "purpose": hint["purpose"],
                    "title_intent": f"LLM: {hint['baseline_title']}",
                    "key_message": "Вывод модели.",
                    "evidence_ids": claims[:4],  # просит больше, чем разрешает cap
                }
            ]
        }

    gateway = MockProvider(fixtures={STORYLINE_PROMPT: _respond})
    plan = await plan_deck_llm(pack, brief, gateway, design_dna=dna, config=CFG)

    touched = [s for s in plan.slides if s.title_intent.startswith("LLM: ")]
    assert touched
    assert all(len(s.content_units) <= 2 for s in touched)


async def test_untrusted_gateway_gets_no_model_id_without_profile_source():
    pack, brief = _pack(), _brief()

    class PlainGateway:
        async def text_json(self, prompt_name, payload, schema):
            return schema.model_validate(_valid_outline_fixture()(payload))

    plan = await plan_deck_llm(pack, brief, PlainGateway(), config=CFG)

    assert plan.provenance.planner == LLM_PLANNER_VERSION
    assert plan.provenance.model_id is None  # не ModelProfileSource — честно None


async def test_slide_count_out_of_bounds_raises_before_call():
    pack, brief = _pack(), _brief(target=10)
    tight = GenerationConfig(
        deck=DeckBounds(min_slides=3, max_slides=5, default_slide_count=4)
    )
    gateway = MockProvider(fixtures={STORYLINE_PROMPT: _valid_outline_fixture()})

    with pytest.raises(DeckDNAError) as exc:
        await plan_deck_llm(pack, brief, gateway, config=tight)
    assert exc.value.code == "invalid_input"
    assert gateway.calls == []  # гейт до любого вызова модели
