"""Тело для слайдов, добавленных пользователем на экране плана (ADR-030).

Пользователь может добавить слайд одним заголовком. Собрать его как есть
— значит выпустить заглушку, поэтому перед вёрсткой такой слайд получает
тело:

- есть ключевая мысль — она и становится телом: это слова пользователя
  (``origin=user``, ``locked`` — вёрстка её не переписывает);
- нет мысли, но есть модель — модель пишет пункты по заголовку, опираясь на
  факты пака (промпт ``slide_fill``); пункты помечены ``origin=model``;
- нет ни того, ни другого — слайд остаётся как есть, его поймает правило
  ``integrity.filler_slide``, а в отчёте он указан как незаполненный.
"""

from __future__ import annotations

import asyncio
import logging
import re

from pydantic import BaseModel, Field

from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.deck_plan import (
    Brief,
    ContentUnit,
    DeckPlan,
    Kind,
    Origin,
    SlidePlan,
)
from deckdna.contracts.serialize import to_schema_dict
from deckdna.planning.validation import _is_hollow
from deckdna.providers.base import ModelGateway
from deckdna.research.expand import MODEL_SOURCE_ID, existing_facts

logger = logging.getLogger(__name__)

SLIDE_FILL_PROMPT = "slide_fill"
_BULLETS = 4
_MIN_CHARS = 20
_MAX_CHARS = 240
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MAX_CONCURRENT = 4


class SlideFill(BaseModel):
    bullets: list[str] = Field(min_length=1)


def can_fill(gateway: object) -> bool:
    if gateway is None:
        return False
    if getattr(gateway, "provider_name", None) == "mock":
        return SLIDE_FILL_PROMPT in (getattr(gateway, "fixtures", None) or {})
    return True


def hollow_slides(plan: DeckPlan) -> list[SlidePlan]:
    return [s for s in plan.slides if _is_hollow(to_schema_dict(s))]


async def _model_bullets(
    slide: SlidePlan, plan: DeckPlan, brief: Brief, pack: ContentPack | None,
    gateway: ModelGateway, sem: asyncio.Semaphore,
) -> list[str]:
    payload = {
        "brief": {
            "purpose": brief.purpose,
            "audience": brief.audience,
            "language": brief.language,
            "tone": brief.tone,
            "forbidden_claims": brief.forbidden_claims or [],
        },
        "slide": {
            "title": slide.title_intent,
            "desired_visual": slide.desired_visual.value if slide.desired_visual else None,
        },
        "deck_outline": [s.title_intent for s in plan.slides if s.id != slide.id],
        "facts": existing_facts(pack) if pack is not None else [],
        "bullets": _BULLETS,
    }
    async with sem:
        try:
            result = await gateway.text_json(SLIDE_FILL_PROMPT, payload, SlideFill)
        except Exception as exc:  # noqa: BLE001 — слайд остаётся незаполненным
            logger.warning("slide_fill failed for %s (%s)", slide.id, exc)
            return []
    if not isinstance(result, SlideFill):
        return []
    bullets = []
    for raw in result.bullets:
        text = raw.strip()
        if _URL_RE.search(text) or not (_MIN_CHARS <= len(text) <= _MAX_CHARS):
            continue
        bullets.append(text)
    return bullets[: _BULLETS + 1]


async def complete_hollow_slides(
    plan: DeckPlan,
    brief: Brief,
    gateway: ModelGateway | None,
    pack: ContentPack | None = None,
) -> tuple[DeckPlan, dict[str, list[str]]]:
    """(план, отчёт ``{"from_key_message": [...], "from_model": [...],
    "unfilled": [...]}`` — id слайдов)."""
    report: dict[str, list[str]] = {"from_key_message": [], "from_model": [], "unfilled": []}
    targets = hollow_slides(plan)
    if not targets:
        return plan, report
    sem = asyncio.Semaphore(_MAX_CONCURRENT)
    use_model = can_fill(gateway)

    async def complete(slide: SlidePlan) -> tuple[str, list[ContentUnit], str]:
        evidence = list(slide.evidence_ids)
        message = (slide.key_message or "").strip()
        if message and message != slide.title_intent.strip():
            unit = ContentUnit(
                role="body", kind=Kind.paragraph, text=message, evidence_ids=evidence,
                origin=Origin.user, locked=True,
            )
            return slide.id, [unit], "from_key_message"
        if use_model:
            bullets = await _model_bullets(slide, plan, brief, pack, gateway, sem)
            if len(bullets) >= 2:
                units = [
                    ContentUnit(role="bullet", kind=Kind.bullet, text=b,
                                evidence_ids=[MODEL_SOURCE_ID], origin=Origin.model)
                    for b in bullets
                ]
                return slide.id, units, "from_model"
        return slide.id, [], "unfilled"

    results = await asyncio.gather(*(complete(s) for s in targets))
    added: dict[str, list[ContentUnit]] = {}
    for slide_id, units, kind in results:
        report[kind].append(slide_id)
        if units:
            added[slide_id] = units
    if not added:
        return plan, report
    slides = [
        s.model_copy(update={"content_units": [*s.content_units, *added[s.id]]})
        if s.id in added else s
        for s in plan.slides
    ]
    logger.info(
        "completed user slides: %d from key message, %d from model, %d unfilled",
        len(report["from_key_message"]), len(report["from_model"]), len(report["unfilled"]),
    )
    return plan.model_copy(update={"slides": slides}), report
