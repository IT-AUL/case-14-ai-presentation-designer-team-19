"""Стилизация контента моделью (навык ``content_style``).

``plan_deck_llm`` строит ``content_units`` ДЕТЕРМИНИРОВАННО и дословно из
``EvidenceGraph`` (см. докстринг ``_unit_from_evidence_node`` в
``story_director.py``) — это гарантирует заземление (никаких выдуманных
фактов), но означает, что стиль и длина текста ровно те, что были в
исходном материале пользователя: слишком длинные предложения, разговорный
тон, корявая грамматика — всё это доезжает до слайда как есть.

Этот навык — отдельный проход ПОСЛЕ планирования, ДО подбора exemplar'а и
компоновки: модель переписывает текст каждого content_unit’а деловым,
грамотным языком, сохраняя все факты. В отличие от ``text_fit``
(``pptx/fitting/text_fit.py``), это не про переполнение рамки — это про
качество текста; геометрический text_fit ниже по пайплайну не убирается и
остаётся страховкой для случаев, которые эта стадия не выправила
(например, exemplar с ещё меньшей ёмкостью, чем ожидал density_budget).

Как и там: бюджет символов — не жёсткий, ответ принимается только если
модель не добавила чисел, которых не было в исходнике, и не раздула текст
многократно; при отказе провайдера или невалидном ответе конкретный
content_unit просто остаётся с исходным (дословным) текстом — часть
плана честно не меняется, план целиком никогда не роняется из-за этого
прохода.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re

from pydantic import BaseModel, Field

from deckdna.contracts.deck_plan import ContentUnit, DeckPlan, Kind, SlidePlan
from deckdna.evaluation.measures import extract_numbers
from deckdna.planning.config import load_generation_config
from deckdna.providers.base import ModelGateway

logger = logging.getLogger(__name__)

PROMPT_NAME = "content_style"
STYLE_BATCH_SIZE = 10
MAX_CONCURRENT_STYLE_CALLS = 4
_MAX_GROWTH_RATIO = 1.6
# короткие лейблы/цифры не выигрывают от «делового тона» и рискуют быть
# исковерканы — стилизуются только content_units длиннее этого порога.
_MIN_STYLE_LEN = 20


class StyleItem(BaseModel):
    """Один переписанный элемент — индекс из запроса, новый текст."""

    index: int
    text: str


class ContentStyleOutput(BaseModel):
    items: list[StyleItem] = Field(default_factory=list)


def _bare(numbers: set[str]) -> set[str]:
    return {re.sub(r"[^\d.]", "", n) for n in numbers}


def validate(source: str, text: str) -> str | None:
    """Причина отказа или ``None``, если переписанный текст годится."""
    text = text.strip()
    if not text:
        return "модель вернула пустой текст"
    invented = _bare(extract_numbers(text)) - _bare(extract_numbers(source))
    if invented:
        return "модель добавила числа, которых нет в исходнике: " + ", ".join(sorted(invented))
    if len(text) > math.ceil(len(source) * _MAX_GROWTH_RATIO):
        return f"результат сильно длиннее исходника: {len(text)} против {len(source)} симв."
    return None


def content_styling_enabled() -> bool:
    """configs/generation.default.yaml → content_styling.enabled (default true)."""
    styling = load_generation_config().raw.get("content_styling") or {}
    return bool(styling.get("enabled", True))


def can_rewrite(gateway: object) -> bool:
    """Есть ли у gateway настоящая модель для стилизации.

    Offline-mock синтезирует схемно-валидные, но бессмысленные строки —
    писать их в план нельзя. Mock участвует, только если ему явно задан
    fixture для этого промпта (тесты)."""
    if getattr(gateway, "provider_name", None) == "mock":
        return PROMPT_NAME in (getattr(gateway, "fixtures", None) or {})
    return gateway is not None


def _target_chars(slide: SlidePlan, unit_count: int) -> int | None:
    max_chars = slide.density_budget.max_chars if slide.density_budget else None
    if not max_chars or unit_count <= 0:
        return None
    return max(_MIN_STYLE_LEN, max_chars // unit_count)


def _eligible(unit: ContentUnit) -> bool:
    return (
        unit.kind is Kind.bullet
        and bool(unit.text)
        and len(unit.text.strip()) >= _MIN_STYLE_LEN
    )


async def style_deck_content(
    plan: DeckPlan,
    gateway: ModelGateway,
    *,
    language: str = "ru",
    max_concurrent: int = MAX_CONCURRENT_STYLE_CALLS,
) -> DeckPlan:
    """Переписать ``content_units`` плана деловым языком (best-effort).

    Возвращает НОВЫЙ ``DeckPlan`` — вход не мутируется. Любой сбой
    провайдера, пустой пул или ответ, не прошедший ``validate()``,
    оставляет соответствующий content_unit с исходным текстом; план
    целиком никогда не откатывается из-за этого прохода."""
    jobs: list[tuple[int, int, str, int | None]] = []  # (slide_i, unit_i, text, target_chars)
    for si, slide in enumerate(plan.slides):
        bullet_units = [u for u in slide.content_units if _eligible(u)]
        target = _target_chars(slide, len(bullet_units)) if bullet_units else None
        for ui, unit in enumerate(slide.content_units):
            if _eligible(unit):
                jobs.append((si, ui, unit.text or "", target))

    if not jobs:
        return plan

    requests = [
        {
            "index": i,
            "text": text,
            "target_chars": target,
            "slide_title": plan.slides[si].title_intent,
            "key_message": plan.slides[si].key_message,
            "language": language,
        }
        for i, (si, ui, text, target) in enumerate(jobs)
    ]
    batches = [
        requests[i : i + STYLE_BATCH_SIZE] for i in range(0, len(requests), STYLE_BATCH_SIZE)
    ]
    sem = asyncio.Semaphore(max(1, max_concurrent))

    async def run_batch(batch: list[dict]) -> list[StyleItem]:
        async with sem:
            try:
                output = await gateway.text_json(
                    PROMPT_NAME, {"items": batch}, ContentStyleOutput
                )
            except Exception as exc:  # noqa: BLE001 — сбой провайдера = fallback батча
                logger.warning(
                    "content_style batch (indices %s) failed, keeping source text: %s",
                    [r["index"] for r in batch],
                    exc,
                )
                return []
        if not isinstance(output, ContentStyleOutput):
            return []
        return output.items

    batch_results = await asyncio.gather(*(run_batch(b) for b in batches))
    rewritten_by_index: dict[int, str] = {}
    for items in batch_results:
        for item in items:
            if not (0 <= item.index < len(jobs)):
                continue
            source = jobs[item.index][2]
            reason = validate(source, item.text)
            if reason is not None:
                logger.info("content_style rejected index %d: %s", item.index, reason)
                continue
            rewritten_by_index[item.index] = item.text.strip()

    if not rewritten_by_index:
        return plan

    new_slides: list[SlidePlan] = []
    for si, slide in enumerate(plan.slides):
        updates: dict[int, str] = {
            ui: rewritten_by_index[i]
            for i, (jsi, ui, _text, _target) in enumerate(jobs)
            if jsi == si and i in rewritten_by_index
        }
        if not updates:
            new_slides.append(slide)
            continue
        new_units = [
            unit.model_copy(update={"text": updates[ui]}) if ui in updates else unit
            for ui, unit in enumerate(slide.content_units)
        ]
        new_slides.append(slide.model_copy(update={"content_units": new_units}))
    return plan.model_copy(update={"slides": new_slides})
