"""ADR-017 Phase B: deterministic selection of image-generation candidates.

Deliberately NOT an LLM decision. The ADR's whole point is to keep this
optional, stretch feature's (TZ §2.3.1, top-10 only) LLM surface area at
zero: which slides get a generated image, and what to ask the image
model for, are both computed from already-written plan text plus a
purpose allowlist -- no new model call, no new grounding risk.
"""

from __future__ import annotations

import asyncio
import logging

from deckdna.contracts.deck_plan import (
    ContentUnit,
    DeckPlan,
    DesiredVisual,
    Kind,
    Purpose,
    SlidePlan,
)
from deckdna.contracts.variant_spec import Strategy
from deckdna.planning.config import GenerationConfig, load_generation_config
from deckdna.providers.base import ModelGateway

logger = logging.getLogger(__name__)

MAX_CONCURRENT_IMAGE_CALLS = 4

_DEFAULT_ENABLED = False
_DEFAULT_MAX_PER_DECK = 2
_DEFAULT_STYLE_SUFFIX = (
    "minimalist flat business illustration, no embedded text, "
    "corporate presentation style"
)
_DEFAULT_PURPOSES = (
    Purpose.overview,
    Purpose.solution,
    Purpose.benefits,
    Purpose.team,
    Purpose.data,
)


def image_generation_config(cfg: GenerationConfig | None = None) -> dict:
    """configs/generation.default.yaml → content.image_generation.

    Off by default (ADR-017 stretch feature, needs a live pilot on a real
    image endpoint before any default flip -- same discipline as
    planning.pipeline_version's v1→v3 flip). Unknown/malformed purpose
    strings are dropped, not fatal -- an honest smaller allowlist beats a
    crash over a typo in a config file."""
    cfg = cfg or load_generation_config()
    section = (cfg.raw.get("content") or {}).get("image_generation") or {}
    purposes_raw = section.get("purposes")
    if purposes_raw is None:
        purposes = list(_DEFAULT_PURPOSES)
    else:
        purposes = []
        for p in purposes_raw:
            try:
                purposes.append(Purpose(p))
            except ValueError:
                continue
    return {
        "enabled": bool(section.get("enabled", _DEFAULT_ENABLED)),
        "max_per_deck": int(section.get("max_per_deck", _DEFAULT_MAX_PER_DECK)),
        "style_suffix": str(section.get("style_suffix", _DEFAULT_STYLE_SUFFIX)),
        "purposes": purposes,
    }


def build_prompt(slide: SlidePlan, style_suffix: str) -> str:
    """Deterministic prompt from already-written text + a fixed style
    suffix. Deliberately never asks for text rendered on the image --
    text-to-image models reliably mangle it, and the slide already gets
    its own real title/bullets from the template's own text frames."""
    parts = [p for p in (slide.title_intent, slide.key_message) if p]
    subject = ". ".join(parts) or slide.title_intent
    return f"{subject}. {style_suffix}"


def select_candidates(
    plan: DeckPlan, strategy: Strategy, config: dict | None = None
) -> list[tuple[SlidePlan, str]]:
    """(slide, prompt) pairs for up to ``config["max_per_deck"]`` slides.

    Only ``strategy == visual`` -- the visual variant is the one meant to
    look visual, faithful/balanced are unaffected. Only slides whose
    purpose is in the allowlist AND that don't already carry a
    ``desired_visual`` (a slide with a real user image or table already
    has something to show). Deterministic order (plan order), so the
    same plan always yields the same candidates -- no randomness to make
    reproducible test fixtures awkward.
    """
    cfg = config if config is not None else image_generation_config()
    if strategy != Strategy.visual or not cfg["enabled"]:
        return []
    purposes = set(cfg["purposes"])
    max_per_deck = cfg["max_per_deck"]
    candidates: list[tuple[SlidePlan, str]] = []
    for slide in plan.slides:
        if len(candidates) >= max_per_deck:
            break
        if slide.purpose not in purposes:
            continue
        if slide.desired_visual not in (None, DesiredVisual.none):
            continue
        candidates.append((slide, build_prompt(slide, cfg["style_suffix"])))
    return candidates


async def _generate_one(
    gateway: ModelGateway, slide: SlidePlan, prompt: str, sem: asyncio.Semaphore
) -> bytes | None:
    """One image_generate call. ``None`` (not an exception) is the honest
    "this slide gets no generated image" signal -- a provider failure, a
    gateway with no image capability, or empty bytes back are all treated
    the same way callers treat every other ADR-016/017 model call: the
    slide simply keeps what it already had, never blocks the variant."""
    async with sem:
        try:
            data = await gateway.image_generate(prompt)
        except Exception as exc:  # noqa: BLE001 — provider failure = honest per-slide skip
            logger.warning(
                "image_generate failed for slide %d (%s); slide left without a generated image",
                slide.index,
                exc,
            )
            return None
    return data or None


async def generate_images(
    plan: DeckPlan,
    gateway: ModelGateway,
    strategy: Strategy,
    config: dict | None = None,
) -> tuple[DeckPlan, dict[str, bytes]]:
    """Generate images for the deterministically-selected candidate
    slides (see :func:`select_candidates`) and fold them into a NEW
    DeckPlan.

    Returns ``(plan, {})`` unchanged whenever there is nothing to do:
    feature disabled, wrong strategy, no candidates, or a gateway that
    doesn't implement ``image_generate`` at all (an older/offline
    provider — checked via ``hasattr`` since ``ModelGateway`` is a
    structural Protocol, not something every caller's mock necessarily
    implements in full). All candidate calls run concurrently
    (``asyncio.gather``, same discipline as Stage 2/3 of ADR-016) —
    there are at most ``max_per_deck`` of them, an order of magnitude
    fewer than the per-slide text calls elsewhere in the pipeline.

    A successful slide gets ONE new ``Kind.image`` content unit appended
    (``asset_ref="generated:<slide.id>"``) and ``desired_visual`` set to
    ``image``; the returned dict maps that same ref to the raw bytes,
    ready for ``generate_deck(..., generated_images=...)``. A slide whose
    call failed keeps its plan entry byte-identical to the input.
    """
    candidates = select_candidates(plan, strategy, config)
    if not candidates or not hasattr(gateway, "image_generate"):
        return plan, {}
    sem = asyncio.Semaphore(MAX_CONCURRENT_IMAGE_CALLS)
    results = await asyncio.gather(
        *(_generate_one(gateway, slide, prompt, sem) for slide, prompt in candidates)
    )
    generated: dict[str, bytes] = {}
    updated_by_id: dict[str, SlidePlan] = {}
    for (slide, _prompt), data in zip(candidates, results, strict=True):
        if data is None:
            continue
        ref = f"generated:{slide.id}"
        generated[ref] = data
        updated_by_id[slide.id] = slide.model_copy(
            update={
                "content_units": [
                    *slide.content_units,
                    ContentUnit(role="body", kind=Kind.image, asset_ref=ref),
                ],
                "desired_visual": DesiredVisual.image,
            }
        )
    if not generated:
        return plan, {}
    new_slides = [updated_by_id.get(s.id, s) for s in plan.slides]
    return plan.model_copy(update={"slides": new_slides}), generated
