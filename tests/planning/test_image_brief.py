"""ADR-017 Phase B: deterministic image-generation candidate selection.

No LLM involved here at all -- select_candidates/build_prompt are pure
functions over an already-planned DeckPlan, so these tests don't need a
gateway, MockProvider, or asyncio.
"""

from __future__ import annotations

import asyncio

from deckdna.contracts.deck_plan import (
    Brief,
    ContentUnit,
    DeckPlan,
    DensityBudget,
    DesiredVisual,
    Kind,
    Level,
    Provenance,
    Purpose,
    SlidePlan,
)
from deckdna.contracts.variant_spec import Strategy
from deckdna.planning import image_brief
from deckdna.providers.mock import MockProvider


def _slide(
    index: int,
    purpose: Purpose,
    *,
    desired_visual: DesiredVisual | None = None,
    title: str = "Заголовок",
    key_message: str = "Ключевая мысль",
) -> SlidePlan:
    return SlidePlan(
        id=f"s{index}",
        index=index,
        purpose=purpose,
        title_intent=title,
        key_message=key_message,
        evidence_ids=["e1"],
        content_units=[
            ContentUnit(role="bullet", kind=Kind.bullet, text="x", evidence_ids=["e1"])
        ],
        desired_visual=desired_visual,
        density_budget=DensityBudget(level=Level.medium, max_chars=200),
    )


def _plan(slides: list[SlidePlan]) -> DeckPlan:
    return DeckPlan(
        schema_version="1.0.0",
        id="p1",
        brief=Brief(
            purpose="x", audience="y", language="ru", target_slide_count=max(len(slides), 3)
        ),
        evidence_graph_id="g1",
        objective="o",
        audience="y",
        language="ru",
        slides=slides,
        provenance=Provenance(planner="p", prompt_version="v1", schema_version="1.0.0"),
    )


_ENABLED_CFG = {
    "enabled": True,
    "max_per_deck": 2,
    "style_suffix": "flat illustration, no text",
    "purposes": [Purpose.overview, Purpose.solution],
}


def test_image_generation_config_defaults_off():
    # Pass an explicit empty GenerationConfig rather than calling with no
    # args, so this stays a pure-logic test instead of depending on
    # configs/generation.default.yaml's actual on-disk content.
    from deckdna.planning.config import GenerationConfig

    cfg = image_brief.image_generation_config(GenerationConfig(raw={}))
    assert cfg["enabled"] is False
    assert cfg["max_per_deck"] == 2
    assert Purpose.overview in cfg["purposes"]


def test_image_generation_config_reads_content_section():
    from deckdna.planning.config import GenerationConfig

    cfg = image_brief.image_generation_config(
        GenerationConfig(
            raw={
                "content": {
                    "image_generation": {
                        "enabled": True,
                        "max_per_deck": 5,
                        "style_suffix": "custom style",
                        "purposes": ["team", "not_a_real_purpose"],
                    }
                }
            }
        )
    )
    assert cfg == {
        "enabled": True,
        "max_per_deck": 5,
        "style_suffix": "custom style",
        "purposes": [Purpose.team],  # bad entry dropped, not fatal
    }


def test_select_candidates_disabled_returns_nothing():
    plan = _plan([_slide(0, Purpose.overview)])
    cfg = dict(_ENABLED_CFG, enabled=False)
    assert image_brief.select_candidates(plan, Strategy.visual, cfg) == []


def test_select_candidates_only_for_visual_strategy():
    plan = _plan([_slide(0, Purpose.overview)])
    assert image_brief.select_candidates(plan, Strategy.balanced, _ENABLED_CFG) == []
    assert image_brief.select_candidates(plan, Strategy.faithful, _ENABLED_CFG) == []
    got = image_brief.select_candidates(plan, Strategy.visual, _ENABLED_CFG)
    assert len(got) == 1


def test_select_candidates_filters_by_purpose_allowlist():
    plan = _plan(
        [
            _slide(0, Purpose.title),  # not in allowlist
            _slide(1, Purpose.overview),  # in allowlist
            _slide(2, Purpose.thank_you),  # not in allowlist
        ]
    )
    got = image_brief.select_candidates(plan, Strategy.visual, _ENABLED_CFG)
    assert [s.index for s, _ in got] == [1]


def test_select_candidates_skips_slides_that_already_have_a_visual():
    plan = _plan(
        [
            _slide(0, Purpose.overview, desired_visual=DesiredVisual.image),
            _slide(1, Purpose.overview, desired_visual=DesiredVisual.table),
            _slide(2, Purpose.overview, desired_visual=DesiredVisual.none),
            _slide(3, Purpose.overview),  # None is also "nothing yet"
        ]
    )
    got = image_brief.select_candidates(plan, Strategy.visual, _ENABLED_CFG)
    assert [s.index for s, _ in got] == [2, 3]


def test_select_candidates_respects_max_per_deck_cap():
    plan = _plan([_slide(i, Purpose.overview) for i in range(5)])
    cfg = dict(_ENABLED_CFG, max_per_deck=2)
    got = image_brief.select_candidates(plan, Strategy.visual, cfg)
    assert len(got) == 2
    assert [s.index for s, _ in got] == [0, 1]  # deterministic plan order


def test_build_prompt_combines_title_and_key_message_with_style_suffix():
    slide = _slide(
        0, Purpose.overview, title="Решение экономит время", key_message="Автоматизация рутины"
    )
    prompt = image_brief.build_prompt(slide, "flat illustration, no text")
    assert prompt == "Решение экономит время. Автоматизация рутины. flat illustration, no text"


def test_build_prompt_never_asks_for_rendered_text():
    """Not a behavioral assertion the function enforces at runtime -- a
    documentation-anchored regression guard: the fixed suffix must keep
    saying no embedded text, since that's the whole reason prompts are
    built this way (see module docstring)."""
    slide = _slide(0, Purpose.overview)
    prompt = image_brief.build_prompt(slide, image_brief._DEFAULT_STYLE_SUFFIX)
    assert "no embedded text" in prompt


# --- generate_images (async, touches a gateway) -----------------------


class _FailingGateway:
    provider_name = "failing"

    async def image_generate(self, prompt: str, *, size: str = "1024x1024") -> bytes:
        raise RuntimeError("endpoint unreachable")


class _NoImageCapabilityGateway:
    """A gateway that simply doesn't implement image_generate at all —
    the pre-ADR-017 shape every existing caller already has."""

    provider_name = "old"


def _run(coro):
    return asyncio.run(coro)


def test_generate_images_noop_when_no_candidates():
    plan = _plan([_slide(0, Purpose.title)])  # title is never a candidate
    gateway = MockProvider()
    new_plan, generated = _run(
        image_brief.generate_images(plan, gateway, Strategy.visual, _ENABLED_CFG)
    )
    assert new_plan is plan
    assert generated == {}
    assert gateway.calls == []


def test_generate_images_noop_when_gateway_lacks_capability():
    plan = _plan([_slide(0, Purpose.overview)])
    new_plan, generated = _run(
        image_brief.generate_images(
            plan, _NoImageCapabilityGateway(), Strategy.visual, _ENABLED_CFG
        )
    )
    assert new_plan is plan
    assert generated == {}


def test_generate_images_success_adds_image_unit_and_returns_bytes():
    plan = _plan([_slide(0, Purpose.overview), _slide(1, Purpose.solution)])
    gateway = MockProvider(fixtures={"image_generate": lambda prompt: b"fake-png-bytes"})
    new_plan, generated = _run(
        image_brief.generate_images(plan, gateway, Strategy.visual, _ENABLED_CFG)
    )
    assert new_plan is not plan  # a real new DeckPlan, not the input mutated
    assert set(generated.values()) == {b"fake-png-bytes"}
    for slide in new_plan.slides:
        ref = f"generated:{slide.id}"
        assert ref in generated
        assert slide.desired_visual == DesiredVisual.image
        image_units = [u for u in slide.content_units if u.kind == Kind.image]
        assert len(image_units) == 1
        assert image_units[0].asset_ref == ref
        # original units are preserved, not replaced
        assert any(u.kind == Kind.bullet for u in slide.content_units)


def test_generate_images_provider_failure_is_an_honest_per_slide_skip():
    plan = _plan([_slide(0, Purpose.overview)])
    new_plan, generated = _run(
        image_brief.generate_images(plan, _FailingGateway(), Strategy.visual, _ENABLED_CFG)
    )
    assert new_plan is plan  # nothing changed -- honest fallback, not a crash
    assert generated == {}


def test_generate_images_calls_run_concurrently_not_sequentially():
    """Sanity check on the asyncio.gather wiring: all candidate calls are
    in flight before any of them resolves (proven by a shared counter
    peaking at len(candidates), not climbing one at a time)."""
    in_flight = {"current": 0, "peak": 0}

    async def slow_fixture(prompt: str) -> bytes:
        in_flight["current"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["current"])
        await asyncio.sleep(0.01)
        in_flight["current"] -= 1
        return b"x"

    class _AsyncFixtureGateway:
        provider_name = "async-fixture"

        async def image_generate(self, prompt: str, *, size: str = "1024x1024") -> bytes:
            return await slow_fixture(prompt)

    plan = _plan([_slide(i, Purpose.overview) for i in range(3)])
    cfg = dict(_ENABLED_CFG, max_per_deck=3)
    _run(image_brief.generate_images(plan, _AsyncFixtureGateway(), Strategy.visual, cfg))
    assert in_flight["peak"] == 3
