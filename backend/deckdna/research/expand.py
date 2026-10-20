"""Добор контента из знаний модели (ADR-030 §3, шаг 5).

Используется, когда веб-поиск выключен, недоступен или нашёл меньше, чем
нужно. Модель пишет недостающие разделы; код отсекает всё, что выдаёт
себя за подтверждённый факт: ссылки, дубли уже имеющегося текста,
повторы разделов, пустые и слишком длинные пункты. Каждый добранный блок
ссылается на ``ExternalSource(origin=model)`` — в графе фактов он получает
``origin=model`` и пониженный confidence, а в колоде маркируется как
«не подтверждено источником».
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from deckdna.contracts.content_pack import (
    Block,
    ContentPack,
    ExternalSource,
    Kind,
    Origin,
    SourceRef,
)
from deckdna.contracts.content_pack import Section as PackSection
from deckdna.contracts.deck_plan import Brief
from deckdna.providers.base import ModelGateway
from deckdna.research.citations import MODEL_BENCHMARK_TITLE_EN, MODEL_BENCHMARK_TITLE_RU
from deckdna.research.gaps import analyze_brief_gaps

logger = logging.getLogger(__name__)

EXPAND_PROMPT = "content_expand"
MODEL_SOURCE_ID = "model:knowledge"

_MIN_BULLET_CHARS = 20
_MAX_BULLET_CHARS = 240
_MAX_HEADING_CHARS = 80
_MAX_BULLETS = 6
_EXISTING_FACTS_BUDGET = 3000
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


class ExpandedSection(BaseModel):
    heading: str = Field(min_length=1)
    bullets: list[str] = Field(min_length=1)


class ExpandResult(BaseModel):
    sections: list[ExpandedSection]


def can_expand(gateway: object) -> bool:
    """Есть ли у gateway настоящая модель для добора.

    Offline-mock синтезирует схемно-валидные, но бессмысленные строки —
    писать их в колоду как контент нельзя. Mock участвует, только если
    ему явно задан fixture этого промпта (тесты)."""
    if gateway is None:
        return False
    if getattr(gateway, "provider_name", None) == "mock":
        return EXPAND_PROMPT in (getattr(gateway, "fixtures", None) or {})
    return True


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def existing_facts(pack: ContentPack, budget: int = _EXISTING_FACTS_BUDGET) -> list[str]:
    """Тексты пака (в порядке чтения) в пределах бюджета символов."""
    facts: list[str] = []
    used = 0
    for section in pack.sections:
        for block in section.blocks:
            for text in [block.text, *(block.items or [])]:
                if not text or not text.strip():
                    continue
                if used + len(text) > budget:
                    return facts
                facts.append(text.strip())
                used += len(text)
    return facts


def _clean_sections(
    result: ExpandResult,
    pack: ContentPack,
    wanted: int,
    bullets_per_section: int,
) -> list[tuple[str, list[str]]]:
    known_headings = {_norm(s.heading) for s in pack.sections if s.heading}
    known_texts = {_norm(t) for t in existing_facts(pack, budget=10**9)}
    out: list[tuple[str, list[str]]] = []
    for section in result.sections:
        heading = section.heading.strip().rstrip(".")
        if not heading or len(heading) > _MAX_HEADING_CHARS or _norm(heading) in known_headings:
            continue
        bullets: list[str] = []
        for raw in section.bullets:
            text = raw.strip()
            if _URL_RE.search(text):
                continue  # ссылке не на что опереться — модель источник не читала
            if not (_MIN_BULLET_CHARS <= len(text) <= _MAX_BULLET_CHARS):
                continue
            key = _norm(text)
            if key in known_texts:
                continue
            known_texts.add(key)
            bullets.append(text)
            if len(bullets) >= min(bullets_per_section + 1, _MAX_BULLETS):
                break
        if len(bullets) < 2:
            continue  # один пункт на слайд — та же заглушка
        known_headings.add(_norm(heading))
        out.append((heading, bullets))
        if len(out) >= wanted:
            break
    return out


async def expand_with_model(
    pack: ContentPack,
    brief: Brief,
    gateway: ModelGateway,
    *,
    sections_needed: int,
    bullets_per_section: int = 4,
    covered_topics: list[str] | None = None,
) -> tuple[list[PackSection], ExternalSource | None]:
    """Новые разделы из знаний модели. Сбой провайдера — пустой результат
    (вызывающий код сокращает колоду), а не падение генерации."""
    if sections_needed <= 0 or not can_expand(gateway):
        return [], None
    gaps = analyze_brief_gaps(pack, brief, max_queries=sections_needed)
    payload = {
        "brief": {
            "purpose": brief.purpose,
            "audience": brief.audience,
            "language": brief.language,
            "tone": brief.tone,
            "forbidden_claims": brief.forbidden_claims or [],
        },
        "covered_topics": covered_topics or [s.heading for s in pack.sections if s.heading],
        "existing_facts": existing_facts(pack),
        "sections_needed": sections_needed,
        "bullets_per_section": bullets_per_section,
        "missing_dimensions": gaps.to_prompt_list(brief.language or "ru"),
    }
    try:
        result = await gateway.text_json(EXPAND_PROMPT, payload, ExpandResult)
    except Exception as exc:  # noqa: BLE001 — сбой провайдера = добора нет
        logger.warning("content_expand failed (%s); deck will be shortened instead", exc)
        return [], None
    if not isinstance(result, ExpandResult):
        return [], None
    cleaned = _clean_sections(result, pack, sections_needed, bullets_per_section)
    if not cleaned:
        return [], None
    ref = SourceRef(artifact_id=MODEL_SOURCE_ID)
    sections = [
        PackSection(
            id=f"sec-model-{i}",
            heading=heading,
            level=2,
            blocks=[Block(kind=Kind.list, items=bullets, source_ref=ref)],
        )
        for i, (heading, bullets) in enumerate(cleaned)
    ]
    source = ExternalSource(
        id=MODEL_SOURCE_ID,
        origin=Origin.model,
        title=MODEL_BENCHMARK_TITLE_RU
        if (brief.language or "ru").startswith("ru")
        else MODEL_BENCHMARK_TITLE_EN,
        retrieved_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    logger.info(
        "content_expand: %d section(s), %d bullet(s) from model knowledge",
        len(sections),
        sum(len(b) for _, b in cleaned),
    )
    return sections, source
