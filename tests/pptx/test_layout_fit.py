"""ADR-016 Stage 3 (layout_fit.py::layout_fit_deck) — per-slide exemplar
assignment + text adaptation from ALREADY-WRITTEN content (Stage 2), not
a volume estimate the way rerank.py picks a template today. One call per
slide, candidate pool pre-filtered deterministically by
_PURPOSE_ARCHETYPE_HINTS (ADR-015) before the model sees it. Exemplars
are exclusive within a variant — a shared `claimed` set, seeded with
every baseline choice up front (not just successful Stage-3 picks),
coordinates concurrent per-slide calls."""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

import pytest
from deckdna.contracts.deck_plan import Brief
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.story_director import plan_deck
from deckdna.pptx.cloning.layout_fit import (
    LAYOUT_FIT_PROMPT,
    LayoutFitAnswer,
    _narrow_candidates,
    can_rewrite,
    layout_fit_deck,
    validate,
)
from deckdna.pptx.composing.minimal import slide_needs
from deckdna.pptx.opc.package import OpcPackage
from deckdna.providers.mock import MockProvider

FIXTURE = Path("tests/fixtures/pptx/vk_tech_template.pptx")
needs_fixture = pytest.mark.skipif(not FIXTURE.exists(), reason="organizer fixture missing")

BRIEF = Brief(purpose="test", audience="qa", language="ru", target_slide_count=10)
CONTENT = Path("tests/fixtures/content/poc_article.md")


def _plan_and_pkg():
    pack = parse_file(CONTENT)
    plan = plan_deck(pack, BRIEF)
    pkg = OpcPackage.open(FIXTURE)
    needs = slide_needs(plan, {}, {}, [])
    return plan, pkg, needs


def _run(coro):
    return asyncio.run(coro)


def _accepting_fixture(payload: dict) -> dict:
    return {
        "candidate_index": 0,
        "title": f"FIT: {payload['title']}",
        "bullets": payload["bullets"],
    }


@needs_fixture
def test_at_least_some_slides_get_fit_and_none_collide():
    plan, pkg, needs = _plan_and_pkg()
    gw = MockProvider(fixtures={LAYOUT_FIT_PROMPT: _accepting_fixture})

    choices, new_plan = _run(layout_fit_deck(pkg, plan, needs, gw))

    assert len(choices) == len(plan.slides)
    fitted_titles = [s.title_intent for s in new_plan.slides if s.title_intent.startswith("FIT:")]
    assert fitted_titles  # хотя бы часть слайдов реально прошла Stage 3

    # эксклюзивность: Stage 3 не должен ДОБАВИТЬ новых дублей поверх уже
    # существующих в чистом детерминированном baseline (см. докстринг
    # _fit_one_slide — известный, документированный остаточный edge case
    # только при малом пуле, не увеличивается этой стадией).
    from deckdna.pptx.cloning.exemplar import select_exemplar_slides

    unit_counts = [len(s.content_units) for s in plan.slides]
    baseline = select_exemplar_slides(
        pkg, count=len(plan.slides), needs=needs, unit_counts=unit_counts
    )
    baseline_dupes = sum(1 for n in Counter(c.slide_part for c in baseline).values() if n > 1)
    fitted_dupes = sum(1 for n in Counter(c.slide_part for c in choices).values() if n > 1)
    assert fitted_dupes <= baseline_dupes


@needs_fixture
def test_candidate_payload_includes_sample_text_for_semantic_judgment():
    """Live bug (28.09): a team-roster card exemplar (vk_tech_template.
    pptx's slide13 -- "Слайд-визитка команды", fields "Имя Фамилия"/
    "Должность"/"Название команды" repeated per person) got picked for
    unrelated technical content ("Функция использует существующий CDN...")
    -- structurally it's a fine "grid" archetype with plausible
    content_slots, so archetype/capacity signals alone can't catch a
    candidate built for a completely different KIND of content. Fix:
    forward `text_preview` (already computed by _candidate_card for
    rerank.py, just never reached layout_fit's own payload) as
    `sample_text` so the model can judge semantic fit itself, the same
    way a person would."""
    plan, pkg, needs = _plan_and_pkg()
    seen_payloads: list[dict] = []

    def _capturing_fixture(payload: dict) -> dict:
        seen_payloads.append(payload)
        return _accepting_fixture(payload)

    gw = MockProvider(fixtures={LAYOUT_FIT_PROMPT: _capturing_fixture})
    _run(layout_fit_deck(pkg, plan, needs, gw))

    assert seen_payloads  # sanity: at least one real call happened
    all_candidates = [c for p in seen_payloads for c in p["candidates"]]
    assert all("sample_text" in c for c in all_candidates)
    # at least one candidate across the whole real template carries
    # actual non-empty sample text (not just an empty string default)
    assert any(c["sample_text"].strip() for c in all_candidates)


def _build_design_dna(fixture_path: Path):
    from datetime import UTC, datetime

    from deckdna.template import autopsy
    from deckdna.template import dna as builder

    forensics = autopsy.analyze_template(fixture_path)
    slide_ids = {i: f"tsl_{i}" for i in range(forensics.slides)}
    build = builder.build_design_dna(
        fixture_path.read_bytes(), forensics, "tpl", "ana", slide_ids, datetime.now(UTC), "1.0"
    )
    return build.design_dna


@needs_fixture
def test_layout_fit_deck_forwards_design_dna_descriptions_end_to_end():
    """ADR-018 integration: every exemplar in Design DNA gets a
    synthetic, uniquely-identifiable content_description -- whichever
    candidates layout_fit_deck's real narrowing includes, their
    sample_text must be the ADR-018 description, never the raw
    text_preview (this doesn't depend on any ONE specific exemplar
    surviving narrowing, unlike testing against slide13 alone)."""
    plan, pkg, needs = _plan_and_pkg()
    design_dna = _build_design_dna(FIXTURE)
    for exemplar in design_dna.exemplars:
        exemplar.content_description = f"ADR018-DESC::{exemplar.part}"

    seen_payloads: list[dict] = []

    def _capturing_fixture(payload: dict) -> dict:
        seen_payloads.append(payload)
        return _accepting_fixture(payload)

    gw = MockProvider(fixtures={LAYOUT_FIT_PROMPT: _capturing_fixture})
    _run(layout_fit_deck(pkg, plan, needs, gw, design_dna=design_dna))

    all_candidates = [c for p in seen_payloads for c in p["candidates"]]
    assert all_candidates  # sanity: real calls happened
    assert all(c["sample_text"].startswith("ADR018-DESC::") for c in all_candidates)


def test_candidate_card_prefers_content_description_over_text_preview():
    """ADR-018 unit-level: _candidate_card (rerank.py, shared with
    layout_fit.py) exposes content_description from a caller-supplied
    dict; layout_fit's own payload builder (sample_text = content_
    description or text_preview) is what makes the override win."""
    from deckdna.pptx.cloning.rerank import _candidate_card, slide_layout_map

    pkg = OpcPackage.open(FIXTURE) if FIXTURE.exists() else None
    if pkg is None:
        pytest.skip("organizer fixture missing")
    part = "ppt/slides/slide13.xml"
    layouts = slide_layout_map(pkg)

    without = _candidate_card(pkg, part, layouts, {})
    assert without["content_description"] is None
    assert without["is_entity_specific"] is False

    with_description = _candidate_card(
        pkg,
        part,
        layouts,
        {},
        {part: ("Карточка члена команды: имя, должность", True)},
    )
    assert with_description["content_description"] == "Карточка члена команды: имя, должность"
    assert with_description["is_entity_specific"] is True
    # text_preview is untouched either way -- the override is additive
    assert with_description["text_preview"] == without["text_preview"]


async def _fit_one_slide_payload(candidates: list[dict]) -> dict:
    """Drive _fit_one_slide directly with a hand-built, already-narrowed
    candidate list -- avoids depending on whether a specific exemplar
    happens to survive _narrow_candidates' real ranking for some plan
    (that ranking is allowed to change; this test is about the payload
    the model receives, not about narrowing itself)."""
    from deckdna.contracts.deck_plan import (
        ContentUnit,
        DensityBudget,
        Kind,
        Level,
        Purpose,
        SlidePlan,
    )
    from deckdna.pptx.cloning.layout_fit import _fit_one_slide

    slide = SlidePlan(
        id="s0",
        index=0,
        purpose=Purpose.overview,
        title_intent="Заголовок",
        key_message="k",
        evidence_ids=["e1"],
        content_units=[
            ContentUnit(role="bullet", kind=Kind.bullet, text="Тезис.", evidence_ids=["e1"])
        ],
        density_budget=DensityBudget(level=Level.medium, max_chars=200),
    )
    seen: list[dict] = []

    class _CapturingGateway:
        provider_name = "capturing"

        async def text_json(self, prompt_name, payload, schema):
            seen.append(payload)
            return _accepting_fixture(payload)

    gw = _CapturingGateway()
    await _fit_one_slide(
        gw, slide, candidates, set(), "own", asyncio.Lock(), "ru", asyncio.Semaphore(1)
    )
    return seen[0]


def test_fit_one_slide_prefers_content_description_over_text_preview():
    """ADR-018: sample_text = content_description when present, else
    text_preview -- verified at the exact point where the payload is
    built, independent of whether a real narrowing pass would have
    included this candidate for some particular plan."""
    candidate = {
        "slide_part": "ppt/slides/slideN.xml",
        "layout_archetype": "grid",
        "content_slots": 37,
        "capabilities": [],
        "mean_run_len": 11,
        "text_preview": "Слайд-визитка команды",
        "content_description": "Карточка члена команды: имя, должность, фото",
    }
    payload = _run(_fit_one_slide_payload([candidate]))
    assert payload["candidates"][0]["sample_text"] == "Карточка члена команды: имя, должность, фото"


def test_fit_one_slide_falls_back_to_text_preview_without_description():
    candidate = {
        "slide_part": "ppt/slides/slideN.xml",
        "layout_archetype": "grid",
        "content_slots": 37,
        "capabilities": [],
        "mean_run_len": 11,
        "text_preview": "Слайд-визитка команды",
        "content_description": None,
    }
    payload = _run(_fit_one_slide_payload([candidate]))
    assert payload["candidates"][0]["sample_text"] == "Слайд-визитка команды"


@needs_fixture
def test_fit_preserves_non_bullet_units_on_slides_it_rewrites():
    """Regression: _fit_one_slide only ever sees/rewrites Kind.bullet
    units (early-gates on their absence) -- a slide that also carries a
    Kind.image/table/chart/diagram unit must keep it after a successful
    Stage 3 rewrite, not have it silently dropped (found while wiring
    ADR-017 -- a generated image unit would otherwise vanish on any
    slide Stage 3 successfully fit)."""
    from deckdna.contracts.deck_plan import ContentUnit, Kind

    plan, pkg, needs = _plan_and_pkg()
    # inject an extra, unrelated image unit onto every eligible slide so
    # at least one that Stage 3 actually fits carries it
    marked_slides = [
        s.model_copy(
            update={
                "content_units": [
                    *s.content_units,
                    ContentUnit(role="body", kind=Kind.image, asset_ref="keep-me"),
                ]
            }
        )
        for s in plan.slides
    ]
    plan = plan.model_copy(update={"slides": marked_slides})
    gw = MockProvider(fixtures={LAYOUT_FIT_PROMPT: _accepting_fixture})

    _choices, new_plan = _run(layout_fit_deck(pkg, plan, needs, gw))

    fitted = [s for s in new_plan.slides if s.title_intent.startswith("FIT:")]
    assert fitted  # sanity: at least one slide really went through Stage 3
    for slide in fitted:
        image_units = [u for u in slide.content_units if u.kind == Kind.image]
        assert sum(u.asset_ref == "keep-me" for u in image_units) == 1, (
            f"slide {slide.id} lost its non-bullet unit after Stage 3 rewrite"
        )


@needs_fixture
def test_mock_without_fixture_falls_back_entirely():
    plan, pkg, needs = _plan_and_pkg()
    gw = MockProvider()  # provider_name == "mock", нет fixture для layout_fit

    choices, new_plan = _run(layout_fit_deck(pkg, plan, needs, gw))

    assert new_plan is plan  # план не тронут вообще
    from deckdna.contracts.deck_plan import Kind
    from deckdna.pptx.cloning.exemplar import select_exemplar_slides

    unit_counts = [
        sum(1 for u in s.content_units if u.text and u.kind not in
            (Kind.title, Kind.image, Kind.table, Kind.chart, Kind.diagram))
        for s in plan.slides
    ]
    baseline = select_exemplar_slides(
        pkg, count=len(plan.slides), needs=needs, unit_counts=unit_counts,
        text_lengths=[
            max((len(u.text or "") for u in s.content_units if u.kind != Kind.title), default=0)
            for s in plan.slides
        ],
        purposes=[s.purpose.value for s in plan.slides],
    )
    assert [c.slide_part for c in choices] == [c.slide_part for c in baseline]


@needs_fixture
def test_provider_failure_falls_back_per_slide_not_whole_deck():
    plan, pkg, needs = _plan_and_pkg()

    class FailingGateway:
        provider_name = "failing"

        async def text_json(self, prompt_name, payload, schema):
            raise RuntimeError("down")

    choices, new_plan = _run(layout_fit_deck(pkg, plan, needs, FailingGateway()))

    assert len(choices) == len(plan.slides)
    assert new_plan is plan  # ни один слайд не изменился


@needs_fixture
def test_invented_number_is_rejected():
    plan, pkg, needs = _plan_and_pkg()

    def _respond(payload: dict) -> dict:
        return {
            "candidate_index": 0,
            "title": "Заголовок с выдуманным числом 777%",
            "bullets": ["Придуманный тезис."],
        }

    gw = MockProvider(fixtures={LAYOUT_FIT_PROMPT: _respond})
    choices, new_plan = _run(layout_fit_deck(pkg, plan, needs, gw))

    assert "777" not in " ".join(s.title_intent for s in new_plan.slides)


def test_narrow_candidates_filters_by_capability_first():
    from deckdna.contracts.deck_plan import Purpose

    candidates = [
        {"slide_part": "a", "layout_archetype": "chart_data", "capabilities": ["chart"]},
        {"slide_part": "b", "layout_archetype": "grid", "capabilities": []},
    ]
    narrowed = _narrow_candidates(candidates, Purpose.data, frozenset({"chart"}))
    assert [c["slide_part"] for c in narrowed] == ["a"]


def test_narrow_candidates_avoids_cropped_text_without_losing_unique_chart():
    from deckdna.contracts.deck_plan import Purpose

    cropped = {
        "slide_part": "cropped", "layout_archetype": "grid",
        "capabilities": ["chart"], "content_slots": 2,
        "has_offslide_content_slots": True,
    }
    safe = {
        "slide_part": "safe", "layout_archetype": "grid",
        "capabilities": [], "content_slots": 3,
        "has_offslide_content_slots": False,
    }
    assert [c["slide_part"] for c in _narrow_candidates(
        [cropped, safe], Purpose.overview, frozenset(), requested_body_slots=2
    )] == ["safe"]
    assert [c["slide_part"] for c in _narrow_candidates(
        [cropped, safe], Purpose.data, frozenset({"chart"}), requested_body_slots=2
    )] == ["cropped"]


def test_layout_fit_rejects_dangling_numeric_range():
    source = "Пилот строится на 30–50 участниках."
    fragment = LayoutFitAnswer(
        candidate_index=0, title="Пилотное исследование",
        bullets=["Дизайн: пилот строится на 30–50"],
    )
    assert "диапазоном" in (validate(source, fragment, max_chars=200) or "")
    fragment.bullets = ["Дизайн: пилот строится на 30–50 участниках."]
    assert validate(source, fragment, max_chars=200) is None


def test_narrow_candidates_falls_back_to_capable_pool_when_no_archetype_match():
    from deckdna.contracts.deck_plan import Purpose

    candidates = [
        {"slide_part": "a", "layout_archetype": "image_dominant", "capabilities": []},
        {"slide_part": "b", "layout_archetype": "other", "capabilities": []},
    ]
    # Purpose.custom не в _PURPOSE_ARCHETYPE_HINTS -> без сужения
    narrowed = _narrow_candidates(candidates, Purpose.custom, frozenset())
    assert {c["slide_part"] for c in narrowed} == {"a", "b"}


def test_narrow_candidates_orders_by_archetype_preference():
    from deckdna.contracts.deck_plan import Purpose

    candidates = [
        {"slide_part": "grid1", "layout_archetype": "grid", "capabilities": []},
        {"slide_part": "single1", "layout_archetype": "single_block", "capabilities": []},
    ]
    # Purpose.problem: (single_block, stacked_list) -- single_block раньше grid
    narrowed = _narrow_candidates(candidates, Purpose.problem, frozenset(), max_candidates=2)
    assert narrowed[0]["slide_part"] == "single1"


def test_narrow_candidates_deprioritizes_short_label_exemplars():
    """Found live (27.09): a diagram exemplar
    reported content_slots=37 (real, not a lie -- 37 non-decorative text
    runs genuinely exist) but mean_run_len~11 -- 37 tiny diagram-node
    labels, not room for full sentences. layout_fit had no signal these
    slots were short, picked the high-capacity-looking diagram for real
    prose, and compressed 5 sentences down to 2-3-word labels to fit.
    A real prose-capable candidate must now be preferred when one is
    available in the same archetype-narrowed pool."""
    from deckdna.contracts.deck_plan import Purpose

    diagram = {
        "slide_part": "diagram13",
        "layout_archetype": "row_grid",
        "capabilities": [],
        "content_slots": 37,
        "mean_run_len": 11.1,
    }
    prose_grid = {
        "slide_part": "cards16",
        "layout_archetype": "grid",
        "capabilities": [],
        "content_slots": 8,
        "mean_run_len": 45.0,
    }
    narrowed = _narrow_candidates([diagram, prose_grid], Purpose.overview, frozenset())
    assert narrowed[0]["slide_part"] == "cards16"

    # last resort: still reachable when it's the only option at all.
    only_diagram = _narrow_candidates([diagram], Purpose.overview, frozenset())
    assert [c["slide_part"] for c in only_diagram] == ["diagram13"]


def test_narrow_candidates_hard_excludes_entity_specific_for_non_team_purpose():
    """Live bug (28.09, second occurrence): a team-roster card exemplar
    STILL got picked for an unrelated 5-stage pipeline slide even with a
    correct content_description offered as a soft "prefer something
    else" hint -- deprioritization (like short-label exemplars get)
    wasn't a strong enough signal. is_entity_specific=True must be a
    HARD exclusion for any purpose other than Purpose.team -- the
    candidate must never even reach the model, not just rank last."""
    from deckdna.contracts.deck_plan import Purpose

    team_card = {
        "slide_part": "team13",
        "layout_archetype": "grid",
        "capabilities": [],
        "content_slots": 37,
        "mean_run_len": 11.0,
        "is_entity_specific": True,
    }
    generic_grid = {
        "slide_part": "cards16",
        "layout_archetype": "grid",
        "capabilities": [],
        "content_slots": 8,
        "mean_run_len": 45.0,
        "is_entity_specific": False,
    }

    # non-team purpose: the entity-specific card is gone entirely, not
    # just ranked last -- even when it's the ONLY candidate at all.
    narrowed = _narrow_candidates([team_card, generic_grid], Purpose.process, frozenset())
    assert [c["slide_part"] for c in narrowed] == ["cards16"]
    assert _narrow_candidates([team_card], Purpose.process, frozenset()) == []

    # Purpose.team: legitimately allowed, not excluded.
    narrowed_team = _narrow_candidates([team_card, generic_grid], Purpose.team, frozenset())
    assert "team13" in [c["slide_part"] for c in narrowed_team]


def test_can_rewrite_gates_offline_mock():
    assert not can_rewrite(MockProvider())
    assert can_rewrite(
        MockProvider(
            fixtures={LAYOUT_FIT_PROMPT: {"candidate_index": 0, "title": "x", "bullets": ["y"]}}
        )
    )


def test_validate_rejects_empty_bullet_and_invented_number():
    ok = LayoutFitAnswer(candidate_index=0, title="Вывод про рост на 22%.", bullets=["Тезис."])
    assert validate("Рост на 22% за пилот.", ok, max_chars=200) is None

    invented = LayoutFitAnswer(candidate_index=0, title="x", bullets=["Рост на 40%."])
    assert validate("Рост на 22%.", invented, max_chars=200) is not None

    empty = LayoutFitAnswer(candidate_index=0, title="x", bullets=["  "])
    assert validate("что угодно", empty, max_chars=200) is not None


def test_validate_rejects_lowercase_fragment_but_allows_leading_quote():
    """Found live (27.09): compressing to fit
    sometimes produced a fragment instead of a complete thought --
    "лента по истории пересылок и реакций." (lowercase, no verb). A
    leading guillemet quote («Рилсы») is a legitimate, good bullet start
    and must NOT be flagged."""
    source = "Лента подбирает видео по истории пересылок и реакций пользователя."

    fragment = LayoutFitAnswer(
        candidate_index=0,
        title="Вывод.",
        bullets=["лента по истории пересылок и реакций."],
    )
    reason = validate(source, fragment, max_chars=200)
    assert reason is not None
    assert "строчной" in reason

    lowercase_title = LayoutFitAnswer(
        candidate_index=0, title="вывод про важное.", bullets=["Тезис про важное."]
    )
    assert validate(source, lowercase_title, max_chars=200) is not None

    quoted_ok = LayoutFitAnswer(
        candidate_index=0,
        title="«Рилсы» помогают удержать аудиторию.",
        bullets=["«Рилсы» между «Чаты» и «Профиль»."],
    )
    assert validate(source, quoted_ok, max_chars=200) is None


def test_narrow_candidates_with_real_count_prefers_density_over_archetype():
    """Live defect (28.09 live-deck review): 11 of 13 content slides got
    a ~15-slot card-grid exemplar for 1-3 real bullets -> empty cards.
    With a real bullet count, density replaces archetype as the filter:
    a preferred-archetype 15-slot grid must not even reach the model
    when a fitting 2-slot candidate exists (archetype becomes a
    tie-break only, so a non-preferred archetype with fitting density
    is NOT filtered out)."""
    from deckdna.contracts.deck_plan import Purpose

    cards2 = {
        "slide_part": "cards2",
        "layout_archetype": "single_block",
        "capabilities": [],
        "content_slots": 2,
        "mean_run_len": 45.0,
    }
    grid15 = {
        "slide_part": "grid15",
        "layout_archetype": "grid",
        "capabilities": [],
        "content_slots": 15,
        "mean_run_len": 45.0,
    }
    narrowed = _narrow_candidates(
        [grid15, cards2], Purpose.overview, frozenset(), requested_body_slots=2
    )
    assert "grid15" not in [c["slide_part"] for c in narrowed]
    assert narrowed[0]["slide_part"] == "cards2"


def test_narrow_candidates_with_real_count_falls_back_to_capable_pool():
    """Density narrowing must never strand the slide: when NOTHING in
    the capable pool fits the requested band, the whole capable pool is
    the fallback (template fallback preserved)."""
    from deckdna.contracts.deck_plan import Purpose

    only = {
        "slide_part": "grid15",
        "layout_archetype": "grid",
        "capabilities": [],
        "content_slots": 15,
        "mean_run_len": 45.0,
    }
    narrowed = _narrow_candidates(
        [only], Purpose.overview, frozenset(), requested_body_slots=2
    )
    assert [c["slide_part"] for c in narrowed] == ["grid15"]


def test_narrow_candidates_with_real_count_prefers_closer_capacity():
    from deckdna.contracts.deck_plan import Purpose

    slots2 = {
        "slide_part": "slots2",
        "layout_archetype": "grid",
        "capabilities": [],
        "content_slots": 2,
        "mean_run_len": 45.0,
    }
    slots6 = {
        "slide_part": "slots6",
        "layout_archetype": "grid",
        "capabilities": [],
        "content_slots": 6,
        "mean_run_len": 45.0,
    }
    narrowed = _narrow_candidates(
        [slots2, slots6], Purpose.overview, frozenset(), requested_body_slots=5
    )
    assert narrowed[0]["slide_part"] == "slots6"


def test_narrow_candidates_with_real_count_keeps_hard_exclusions():
    """Capability and the ADR-018 entity-specific exclusion stay strict
    on the density path too -- a fitting-density team card still never
    reaches a non-team slide, and a candidate without a required
    capability is still dropped."""
    from deckdna.contracts.deck_plan import Purpose

    team_card = {
        "slide_part": "team13",
        "layout_archetype": "grid",
        "capabilities": [],
        "content_slots": 2,
        "mean_run_len": 45.0,
        "is_entity_specific": True,
    }
    generic = {
        "slide_part": "cards2",
        "layout_archetype": "grid",
        "capabilities": [],
        "content_slots": 2,
        "mean_run_len": 45.0,
        "is_entity_specific": False,
    }
    narrowed = _narrow_candidates(
        [team_card, generic], Purpose.process, frozenset(), requested_body_slots=2
    )
    assert [c["slide_part"] for c in narrowed] == ["cards2"]
    assert (
        _narrow_candidates(
            [team_card], Purpose.process, frozenset(), requested_body_slots=2
        )
        == []
    )

    chart_only = {
        "slide_part": "chart1",
        "layout_archetype": "chart_data",
        "capabilities": ["chart"],
        "content_slots": 5,
        "mean_run_len": 45.0,
    }
    prose = {
        "slide_part": "prose2",
        "layout_archetype": "grid",
        "capabilities": [],
        "content_slots": 2,
        "mean_run_len": 45.0,
    }
    narrowed = _narrow_candidates(
        [chart_only, prose], Purpose.data, frozenset({"chart"}),
        requested_body_slots=2,
    )
    assert [c["slide_part"] for c in narrowed] == ["chart1"]


@needs_fixture
def test_layout_fit_deck_passes_bullet_count_into_narrowing(monkeypatch):
    """The density signal is only as good as its input: layout_fit_deck
    must forward each slide's REAL bullet count (Stage-2 written units)
    into _narrow_candidates, or the fix never fires in prod."""
    from deckdna.contracts.deck_plan import Kind
    from deckdna.pptx.cloning import layout_fit as lf

    plan, pkg, needs = _plan_and_pkg()
    seen: list[int | None] = []
    original = lf._narrow_candidates

    def _spy(*args, **kwargs):
        seen.append(kwargs.get("requested_body_slots"))
        return original(*args, **kwargs)

    monkeypatch.setattr(lf, "_narrow_candidates", _spy)
    gw = MockProvider(fixtures={LAYOUT_FIT_PROMPT: _accepting_fixture})
    _run(lf.layout_fit_deck(pkg, plan, needs, gw))

    expected = [
        sum(u.kind == Kind.bullet and bool(u.text) for u in s.content_units)
        for s in plan.slides
    ]
    assert seen == expected
