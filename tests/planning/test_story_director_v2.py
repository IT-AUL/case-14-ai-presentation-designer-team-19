"""ADR-016 assembly (story_director_v2.py::plan_deck_llm_v2) — Stage 1
(structure.py) + Stage 2 (content_writer.py) combined into a full
DeckPlan. Same public contract as story_director.py::plan_deck_llm (drop-
in replacement, see module docstring): pack+brief+gateway -> DeckPlan,
same Provenance semantics (planner/prompt_version/model_id)."""

from __future__ import annotations

import logging

from deckdna.contracts.content_pack import Block, ContentPack, Section, SourceRef
from deckdna.contracts.deck_plan import Brief, DeckPlan, Purpose
from deckdna.planning.config import DeckBounds, GenerationConfig
from deckdna.planning.content_writer import CONTENT_WRITER_PROMPT
from deckdna.planning.story_director import LLM_PLANNER_VERSION, PLANNER_VERSION, plan_deck
from deckdna.planning.story_director_v2 import plan_deck_llm_v2
from deckdna.planning.structure import STRUCTURE_PROMPT
from deckdna.providers.mock import MockProvider

CFG = GenerationConfig(deck=DeckBounds(min_slides=3, max_slides=40))


def _pack(n_sections: int = 6) -> ContentPack:
    sections = [
        Section(
            id=f"sec-{i}",
            heading=f"Секция {i}",
            level=2,
            blocks=[
                Block(
                    kind="paragraph",
                    text=f"Текст секции {i}. Ещё предложение про важное дело.",
                    source_ref=SourceRef(artifact_id="demo.md"),
                ),
            ],
        )
        for i in range(n_sections)
    ]
    return ContentPack(
        schema_version="1.0",
        id="pack-v2",
        language="ru",
        title_hint="V2 колода",
        sections=sections,
        tables=[],
        assets=[],
        warnings=[],
    )


def _brief(target: int = 10) -> Brief:
    return Brief(purpose="test", audience="qa", language="ru", target_slide_count=target)


def _structure_fixture():
    def _respond(payload: dict) -> dict:
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

    return _respond


def _writer_fixture():
    def _respond(payload: dict) -> dict:
        return {
            "title_intent": f"LLM title: {payload['slide_brief']}",
            "key_message": "LLM key message.",
            "bullets": ["LLM-написанный тезис про важное дело."],
        }

    return _respond


def _gateway(structure=None, writer=None) -> MockProvider:
    return MockProvider(
        fixtures={
            STRUCTURE_PROMPT: structure if structure is not None else _structure_fixture(),
            CONTENT_WRITER_PROMPT: writer if writer is not None else _writer_fixture(),
        }
    )


async def test_full_pipeline_produces_llm_written_content_for_eligible_slides():
    pack, brief = _pack(), _brief()
    gateway = _gateway()

    plan = await plan_deck_llm_v2(pack, brief, gateway, config=CFG)

    assert isinstance(plan, DeckPlan)
    assert len(plan.slides) == brief.target_slide_count
    assert plan.provenance.planner == LLM_PLANNER_VERSION
    assert plan.provenance.prompt_version == "deck_structure/1.1.0+content_writer/1.2.0"
    assert plan.provenance.model_id == "mock"
    assert plan.evidence_graph_id == f"eg-{pack.id}"

    baseline = plan_deck(pack, brief, CFG)
    base_by_index = {s.index: s for s in baseline.slides}
    protected = {Purpose.title, Purpose.thank_you, Purpose.cta}

    llm_touched = 0
    for slide in plan.slides:
        base = base_by_index[slide.index]
        if base.purpose in protected:
            assert slide.title_intent == base.title_intent
            assert slide.id == base.id
            continue
        if slide.title_intent.startswith("LLM title: "):
            llm_touched += 1
            assert slide.content_units
            assert all("LLM-написанный" in (u.text or "") for u in slide.content_units)
    assert llm_touched > 0


async def test_protected_bookends_are_never_sent_to_either_stage():
    pack, brief = _pack(), _brief()
    baseline = plan_deck(pack, brief, CFG)
    protected = {Purpose.title, Purpose.thank_you, Purpose.cta}
    protected_indices = {s.index for s in baseline.slides if s.purpose in protected}
    assert protected_indices

    gateway = _gateway()
    plan = await plan_deck_llm_v2(pack, brief, gateway, config=CFG)

    structure_sent: set[int] = set()
    for call in gateway.calls:
        if call["prompt"] == STRUCTURE_PROMPT:
            structure_sent.update(call["payload"]["batch"]["indices"])
    assert structure_sent.isdisjoint(protected_indices)

    for idx in protected_indices:
        base = next(s for s in baseline.slides if s.index == idx)
        got = next(s for s in plan.slides if s.index == idx)
        assert got.title_intent == base.title_intent
        assert got.content_units == base.content_units


async def test_mock_without_writer_fixture_falls_back_to_deterministic_content():
    """can_rewrite() гейт (content_writer.py) — Stage 1 может пройти
    (заземление evidence_ids защищает его структурно), но без fixture для
    content_writer Stage 2 не должен вызываться вообще, и ни один слайд
    не должен получить синтетический mock-текст как будто он настоящий."""
    pack, brief = _pack(), _brief()
    # provider_name == "mock", НЕТ ключа content_writer в fixtures вообще
    # (не {} — это тоже был бы валидный, просто пустой fixture-ответ).
    gateway = MockProvider(fixtures={STRUCTURE_PROMPT: _structure_fixture()})

    plan = await plan_deck_llm_v2(pack, brief, gateway, config=CFG)

    writer_calls = [c for c in gateway.calls if c["prompt"] == CONTENT_WRITER_PROMPT]
    assert writer_calls == []
    baseline = plan_deck(pack, brief, CFG)
    assert [s.title_intent for s in plan.slides] == [s.title_intent for s in baseline.slides]


def _sparse_pack_forcing_dividers() -> ContentPack:
    """5 коротких секций -> target=12 добирает дефицит section_divider'ами
    (см. test_story_director.py's test_no_empty_slides_and_named_dividers),
    а не generic-паддингом -- нужен baseline, где divider'ы РЕАЛЬНО есть."""
    return ContentPack(
        schema_version="1.0",
        id="pack-sparse-v2",
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


async def test_section_dividers_end_to_end_stay_title_only():
    """Живой баг: без protection, Stage 1 could
    fabricate a slide_brief describing a divider's TRANSITION role
    ("Слайд задаёт маршрут: от проблемы к решению...") that then leaked
    into title_intent via content_writer's fallback, publishing a
    title-only slide with a misleading, content-sounding title instead of
    an honest section_divider. Full pipeline check: dividers must come out
    of plan_deck_llm_v2 byte-identical to the baseline, never touched by
    either stage even when the mocked stages WOULD happily answer for
    them."""
    pack, brief = _sparse_pack_forcing_dividers(), _brief(target=12)
    baseline = plan_deck(pack, brief, CFG)
    dividers = [s for s in baseline.slides if s.purpose is Purpose.section_divider]
    assert dividers  # sanity: baseline действительно их создаёт

    gateway = _gateway()
    plan = await plan_deck_llm_v2(pack, brief, gateway, config=CFG)

    structure_sent: set[int] = set()
    writer_sent: set[int] = set()
    for call in gateway.calls:
        if call["prompt"] == STRUCTURE_PROMPT:
            structure_sent.update(call["payload"]["batch"]["indices"])
        elif call["prompt"] == CONTENT_WRITER_PROMPT:
            writer_sent.add(call["payload"].get("slide_brief"))

    divider_indices = {d.index for d in dividers}
    assert structure_sent.isdisjoint(divider_indices)

    for d in dividers:
        got = next(s for s in plan.slides if s.index == d.index)
        assert got.purpose is Purpose.section_divider
        assert got.title_intent == d.title_intent
        assert got.content_units == d.content_units
        assert "задаёт маршрут" not in got.title_intent


async def test_all_stages_failing_is_a_full_honest_fallback(caplog):
    pack, brief = _pack(), _brief()

    class FailingGateway:
        provider_name = "failing"

        async def text_json(self, prompt_name, payload, schema):
            raise RuntimeError("endpoint unreachable")

    with caplog.at_level(logging.WARNING):
        plan = await plan_deck_llm_v2(pack, brief, FailingGateway(), config=CFG)

    assert plan.provenance.planner == PLANNER_VERSION
    baseline = plan_deck(pack, brief, CFG)
    assert [s.title_intent for s in plan.slides] == [s.title_intent for s in baseline.slides]
    assert [s.content_units for s in plan.slides] == [s.content_units for s in baseline.slides]


async def test_evidence_ids_on_the_slide_match_what_content_units_actually_used():
    """evidence_ids заявленные слайдом не должны быть шире, чем реально
    процитировано в content_units — тот же инвариант, что и у v1
    (используется контекстным аудитом)."""
    pack, brief = _pack(), _brief()
    gateway = _gateway()
    plan = await plan_deck_llm_v2(pack, brief, gateway, config=CFG)

    for slide in plan.slides:
        if not slide.content_units:
            continue
        used = {eid for u in slide.content_units for eid in (u.evidence_ids or [])}
        if used:
            assert set(slide.evidence_ids) == used


async def test_writer_rejection_returns_full_baseline_not_internal_brief():
    """Живой баг (LLM-прогон конкурентной карты): writer вернул формально
    валидный ответ, но validate() отклонил его по росту (~1975 симв. при
    ориентире max_body_chars=800). write_slide_content's _fallback_units
    возвращает slide_brief (внутреннюю инструкцию вида "job for slide N"
    / "Ориентировать руководство...") и как title_intent, и как
    key_message; verbatim-units при этом НЕ пусты, так что прежний
    _slide_task публиковал инструкцию заголовком слайда. Любой fallback
    писателя — provider failure или validation rejection — обязан
    возвращать baseline-слайд целиком: slide_brief — внутренний текст
    планировщика, он непубликабелен никогда."""
    pack, brief = _pack(), _brief()

    def _overgrown_writer(payload: dict) -> dict:
        return {
            "title_intent": "Заголовок от модели.",
            "key_message": "Ключевое сообщение от модели.",
            "bullets": ["Очень длинный тезис про важное дело, " * 60],
        }

    gateway = _gateway(writer=_overgrown_writer)
    plan = await plan_deck_llm_v2(pack, brief, gateway, config=CFG)

    writer_calls = [c for c in gateway.calls if c["prompt"] == CONTENT_WRITER_PROMPT]
    assert writer_calls

    baseline = plan_deck(pack, brief, CFG)
    assert [s.title_intent for s in plan.slides] == [s.title_intent for s in baseline.slides]
    assert [s.key_message for s in plan.slides] == [s.key_message for s in baseline.slides]
    assert [s.content_units for s in plan.slides] == [s.content_units for s in baseline.slides]
    assert all("job for slide" not in s.title_intent for s in plan.slides)
    assert plan.provenance.planner == PLANNER_VERSION


async def test_equation_survives_structure_omission_and_writer_synthesis():
    from deckdna.ingestion.content_parsers import parse_markdown

    pack = parse_markdown(r"""# Evaluation
## Measurement
Explain the personalised index and its limitations.

\[
Index = 0.25 \times active - 0.75 \times idle
\]

The coefficients are hypotheses, not clinically validated values.
""")
    plan = await plan_deck_llm_v2(pack, _brief(3), _gateway(), config=CFG)
    equations = [u for s in plan.slides for u in s.content_units if u.role == "equation"]
    assert len(equations) == 1
    assert equations[0].text == "Index = 0.25 × active - 0.75 × idle"


async def test_writer_can_run_after_structure_failure_with_real_graph_evidence():
    def broken_structure(payload):
        raise RuntimeError("structure unavailable")

    gateway = _gateway(structure=broken_structure)
    plan = await plan_deck_llm_v2(_pack(), _brief(), gateway, config=CFG)
    assert any(call["prompt"] == CONTENT_WRITER_PROMPT for call in gateway.calls)
    assert any(slide.title_intent.startswith("LLM title:") for slide in plan.slides)
