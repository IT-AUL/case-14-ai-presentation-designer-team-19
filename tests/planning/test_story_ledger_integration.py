"""Fact-ledger safety at the public planning entrypoints."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from deckdna.analysis.fact_ledger import Fact, FactLedger, build_pack_fact_ledger
from deckdna.analysis.pipeline import analyze_pack
from deckdna.analysis.title_templates import render_title
from deckdna.contracts.deck_plan import Brief, Kind
from deckdna.contracts.variant_spec import Strategy
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning import story_rules as rules
from deckdna.planning.story_director import plan_deck, plan_deck_llm
from deckdna.planning.story_director_v2 import plan_deck_llm_v2
from deckdna.providers.prompts import load_prompt, render_prompt


def inputs():
    pack = parse_file(Path("examples/stage2/regions.xlsx"), artifact_id="regions")
    brief = Brief(
        purpose="Показать рост продаж",
        audience="руководство",
        language="ru",
        target_slide_count=8,
        key_message="Продажи выросли на 999999%",
    )
    return pack, brief


def test_standard_data_entrypoint_uses_ledger_and_ignores_unverified_copy():
    pack, brief = inputs()
    pack = pack.model_copy(
        update={
            "insights": [
                i.model_copy(
                    update={
                        "headline": "Поддельный рост 999999%",
                        "fact": "Поддельный рост 999999%",
                    }
                )
                for i in pack.insights
            ]
        }
    )
    ledger = build_pack_fact_ledger(analyze_pack(pack, brief.purpose))
    plan = plan_deck(pack, brief)
    assert plan.provenance.planner == "story-director/fact-ledger-1.0"
    assert any(u.chart_ref or u.table_ref for s in plan.slides for u in s.content_units)
    assert all(fid in ledger.by_id for s in plan.slides for fid in s.insight_ids or [])
    assert "999999" not in " ".join(u.text or "" for s in plan.slides for u in s.content_units)
    for slide in plan.slides[1:-1]:
        fact = ledger.by_id[slide.insight_ids[0]]
        assert slide.title_intent in [render_title(tid, fact) for tid in fact.allowed_title_ids]


@pytest.mark.parametrize("planner", [plan_deck_llm, plan_deck_llm_v2])
def test_data_llm_entrypoint_calls_story_once_with_compact_catalog(planner):
    pack, brief = inputs()
    calls = []

    class Gateway:
        async def text_json(self, name, payload, schema):
            calls.append((name, payload))
            return {"main_fact_id": "invented", "slides": [], "omitted_fact_ids": []}

    result = asyncio.run(planner(pack, brief, Gateway()))
    assert len(calls) == 1
    name, payload = calls[0]
    assert name == "story_ledger"
    assert payload["must_not_assume_direction"] is True
    assert all("values" not in f and "derived" not in f for f in payload["facts"])
    assert "999999" not in json.dumps(payload)
    assert result.model_dump() == plan_deck(pack, brief).model_dump()


def test_story_prompt_exists_and_renders_catalog():
    prompt = load_prompt("story_ledger")
    rendered = render_prompt(prompt, {"facts": [{"id": "fact-sentinel"}]})
    assert "fact-sentinel" in rendered
    assert "title_realization_id" in rendered


def valid_ledger_and_plan():
    fact = Fact(
        id="f",
        dataset_id="d",
        metric="Продажи",
        aggregation="value",
        scope={},
        kind="kpi",
        predicate="kpi",
        values={"value": 17},
    )
    ledger = FactLedger.from_facts([fact])
    return ledger, rules.beam_search_story_plan(ledger).model_dump()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(title="Invented 999"),
        lambda p: p["slides"][0].update(title="Invented 999"),
        lambda p: p["slides"][0].update(layout_intent="caused_by_999"),
        lambda p: p.update(omitted_fact_ids=["f"]),
        lambda p: p["slides"][0].update(fact_ids=[]),
    ],
)
def test_rejects_schema_extensions_and_inconsistent_selection(mutation):
    ledger, raw = valid_ledger_and_plan()
    mutation(raw)
    assert not rules.validate_story_ledger_plan(raw, ledger).ok


def test_main_fact_must_be_selected():
    ledger, raw = valid_ledger_and_plan()
    other = Fact(
        id="other",
        dataset_id="other",
        metric="Заказы",
        aggregation="value",
        scope={},
        kind="kpi",
        predicate="kpi",
        values={"value": 23},
    )
    ledger.by_id[other.id] = other
    raw["main_fact_id"] = other.id
    assert not rules.validate_story_ledger_plan(raw, ledger).ok


@pytest.mark.parametrize(
    "goal", ["Показать рост продаж", "Show growth in sales", "Доказать падение спроса"]
)
def test_goal_is_a_question_without_presupposed_direction(goal):
    info = rules.neutralize_goal_direction(goal)
    assert info["must_not_assume_direction"]
    assert info["neutral_question"].endswith("?")
    assert not any(w in info["neutral_question"].casefold() for w in ("рост", "падение", "growth"))


def test_three_standard_variants_preserve_semantics_and_chart_data():
    pack, brief = inputs()
    plans = [
        plan_deck(pack, brief, strategy=s)
        for s in (Strategy.faithful, Strategy.balanced, Strategy.visual)
    ]

    def signature(plan):
        return [
            (
                s.insight_ids,
                s.title_intent,
                [
                    (
                        u.text,
                        u.chart_ref,
                        u.table_ref,
                        u.chart_spec.model_dump() if u.chart_spec else None,
                    )
                    for u in s.content_units
                    if u.kind in (Kind.title, Kind.chart, Kind.table)
                    or (u.text and any(c.isdigit() for c in u.text))
                ],
            )
            for s in plan.slides
        ]

    assert signature(plans[0]) == signature(plans[1]) == signature(plans[2])


def test_low_value_duplicate_is_omitted_instead_of_attached_outside_objective():
    ledger, _ = valid_ledger_and_plan()
    strong = ledger.by_id["f"]
    weak = replace(strong, id="duplicate")
    weak.score = 0.001
    ledger.by_id[weak.id] = weak
    story = rules.beam_search_story_plan(ledger)
    assert weak.id in story.omitted_fact_ids
    assert all(weak.id not in slide.fact_ids for slide in story.slides)


def test_requested_count_is_an_upper_bound_for_a_model_story():
    ledger, raw = valid_ledger_and_plan()
    other = Fact(
        id="other",
        dataset_id="other",
        metric="Заказы",
        aggregation="value",
        scope={},
        kind="kpi",
        predicate="kpi",
        values={"value": 23},
    )
    ledger.by_id[other.id] = other
    raw["slides"] += [dict(raw["slides"][0], fact_ids=["other"])]
    result = rules.resolve_story_ledger_plan(raw, ledger, max_slides=3)
    assert len(result.slides) <= 1


def test_directional_key_message_does_not_select_or_create_facts():
    pack, brief = inputs()
    neutral = brief.model_copy(update={"key_message": None})
    biased = plan_deck(pack, brief)
    factual = plan_deck(pack, neutral)
    assert [(s.insight_ids, s.title_intent) for s in biased.slides] == [
        (s.insight_ids, s.title_intent) for s in factual.slides
    ]


def test_structure_stage_also_uses_the_single_ledger_story_call():
    from deckdna.planning.structure import plan_structure_llm

    pack, brief = inputs()
    calls = []

    class Gateway:
        async def text_json(self, name, payload, schema):
            calls.append(name)
            return {"main_fact_id": "invented", "slides": [], "omitted_fact_ids": []}

    result = asyncio.run(plan_structure_llm(pack, brief, Gateway()))
    assert calls == ["story_ledger"]
    assert result.total_slides == len(plan_deck(pack, brief).slides)


def test_kpi_view_is_one_content_slide_with_all_selected_facts_and_metadata():
    pack = parse_file(Path("examples/stage2/kpi.json"), artifact_id="kpi")
    _, brief = inputs()
    plan = plan_deck(pack, brief)
    content = plan.slides[1:-1]
    assert len(content) == 1
    assert len(content[0].insight_ids) >= 2
    assert content[0].chart["type"] == "kpi"
    assert content[0].chart["view_id"]


def test_reference_table_retains_explanation_metadata():
    pack = parse_file(Path("examples/stage2/products_reference.xlsx"), artifact_id="products")
    _, brief = inputs()
    plan = plan_deck(pack, brief)
    table_slide = next(s for s in plan.slides if any(u.kind == Kind.table for u in s.content_units))
    assert table_slide.chart["type"] == "table"
    assert table_slide.chart["view_id"]


def test_fact_cannot_be_repeated_in_overlapping_slide_groups():
    ledger, raw = valid_ledger_and_plan()
    other = Fact(
        id="other",
        dataset_id="other",
        metric="Заказы",
        aggregation="value",
        scope={},
        kind="kpi",
        predicate="kpi",
        values={"value": 23},
    )
    ledger.by_id[other.id] = other
    raw["slides"] += [
        dict(raw["slides"][0], fact_ids=["other"]),
        dict(raw["slides"][0], fact_ids=["f", "other"]),
    ]
    assert not rules.validate_story_ledger_plan(raw, ledger).ok


def test_required_source_text_survives_the_ledger_story_and_stays_locked():
    from deckdna.contracts.content_pack import Block, Section, SourceRef
    from deckdna.planning.mandatory import protect_mandatory_sections

    pack, brief = inputs()
    text = "Пилот разрешён только после согласования со службой безопасности."
    section = Section(
        id="requirements",
        heading="Условия пилота",
        level=2,
        blocks=[Block(kind="paragraph", text=text, source_ref=SourceRef(artifact_id="author.md"))],
    )
    pack = pack.model_copy(update={"sections": [*pack.sections, section]})
    brief = brief.model_copy(update={"mandatory_sections": ["requirements"]})
    plan = protect_mandatory_sections(plan_deck(pack, brief), pack)
    assert any(u.text == text and u.locked for s in plan.slides for u in s.content_units)
    assert len(plan.slides) <= brief.target_slide_count
    short_brief = brief.model_copy(update={"target_slide_count": 4})
    variants = rules.plan_three_variants_from_ledger(
        build_pack_fact_ledger(analyze_pack(pack)), short_brief, pack=pack
    )
    assert all(len(p.slides) == 4 for p in variants.values())


def test_main_answer_does_not_repeat_a_chart_that_is_shown_as_later_evidence():
    pack, brief = inputs()
    ledger = build_pack_fact_ledger(analyze_pack(pack, brief.purpose))
    grouped = {}
    for fact in ledger.by_id.values():
        grouped.setdefault(fact.view_id, []).append(fact)
    facts = next(fs for fs in grouped.values() if len(fs) >= 2)
    story = rules.StoryLedgerPlan(
        main_fact_id=facts[0].id,
        slides=[
            rules.StoryLedgerSlide(
                role=role, fact_ids=[fact.id], title_realization_id=fact.allowed_title_ids[0]
            )
            for role, fact in zip(("answer", "evidence"), facts[:2], strict=True)
        ],
    )
    plan = rules.compile_story_ledger_to_deck_plan(story, ledger, brief, pack=pack)
    assert not any(u.chart_ref for u in plan.slides[1].content_units)
    proof = next(u for u in plan.slides[2].content_units if u.chart_ref)
    expected = next(c for c in pack.charts if c.source_ref.range == facts[0].view_id)
    assert proof.chart_ref == expected.id
    assert proof.chart_spec.chart_type.value == expected.chart_type
    assert any(u.role == "source" for u in plan.slides[1].content_units)


def test_multiple_answer_roles_still_keep_a_chart_proof():
    pack, brief = inputs()
    ledger = build_pack_fact_ledger(analyze_pack(pack, brief.purpose))
    groups = {}
    for fact in ledger.by_id.values():
        groups.setdefault(fact.view_id, []).append(fact)
    facts = next(fs[:2] for fs in groups.values() if len(fs) >= 2)
    story = rules.StoryLedgerPlan(
        main_fact_id=facts[0].id,
        slides=[
            rules.StoryLedgerSlide(
                role="answer", fact_ids=[f.id], title_realization_id=f.allowed_title_ids[0]
            )
            for f in facts
        ],
    )
    plan = rules.compile_story_ledger_to_deck_plan(story, ledger, brief, pack=pack)
    assert any(u.chart_ref for slide in plan.slides for u in slide.content_units)
