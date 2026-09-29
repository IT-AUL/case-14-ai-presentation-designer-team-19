"""Optional LLM rerank of the deterministic exemplar candidate pool.

Детерминированный ``select_exemplar_slides`` остаётся ядром: LLM-слой
только переупорядочивает ВЫБОР среди уже собранного кандидатного пула —
на вход он получает ту же поверхность сигналов (профили ранов, капабилити,
visual_richness, text preview) плюс намерение каждого планового слайда.

Контракты не меняются: DeckPlan/ExemplarChoice/ModelGateway исходные.
Запросы режутся на батчи по ``RERANK_BATCH_SIZE`` слайдов (тот же принцип,
что у storyline v2 — маленький ask на 27B-класс модель надёжнее одного
большого) и уходят параллельно (``asyncio.gather``); любая ошибка
провайдера или невалидный ответ честно откатывает соответствующий батч
(а на уровне отдельного назначения — уже существующая per-index проверка
ниже) на детерминированный baseline, а не всю колоду разом.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from lxml import etree
from pydantic import BaseModel

from deckdna.contracts.deck_plan import Purpose
from deckdna.contracts.variant_spec import Strategy
from deckdna.pptx.composing.text_replace import count_content_slots
from deckdna.pptx.opc.package import OpcPackage

from .exemplar import (
    A,
    ExemplarChoice,
    LayoutArchetype,
    RunProfile,
    _presentation_slide_size,
    _ranked_candidate_pool,
    has_offslide_content_slots,
    layout_archetype,
    run_profile,
    select_exemplar_slides,
    shape_count,
    slide_capabilities,
    slide_layout_map,
    slide_parts,
    visual_richness,
)

if TYPE_CHECKING:
    from ...contracts.deck_plan import DeckPlan
    from ...providers.base import ModelGateway

logger = logging.getLogger(__name__)

EXEMPLAR_RERANK_PROMPT = "exemplar_rerank"

MAX_RERANK_SLIDES = 25
MAX_RERANK_CANDIDATES = 16
RERANK_BATCH_SIZE = 8
MAX_CONCURRENT_RERANK_CALLS = 4
_TEXT_PREVIEW_CHARS = 80

# Deterministic prior: which LayoutArchetype(s) usually fit a slide of
# this Purpose. Not a hard filter -- a hint surfaced to the rerank model
# alongside each candidate's own archetype (see _candidate_card), so
# matching becomes "does this label match my slide's kind of content"
# instead of weighing raw geometric numbers a weak model reasons about
# unreliably (see LayoutArchetype's docstring for the full motivation).
# Order matters: first entry is the best fit. Purposes not listed (or
# Purpose.custom) get no hint -- an empty list, not a guess.
_PURPOSE_ARCHETYPE_HINTS: dict[Purpose, tuple[LayoutArchetype, ...]] = {
    Purpose.title: (LayoutArchetype.single_block,),
    Purpose.agenda: (LayoutArchetype.stacked_list,),
    Purpose.section_divider: (LayoutArchetype.single_block,),
    Purpose.overview: (LayoutArchetype.grid, LayoutArchetype.stacked_list),
    Purpose.problem: (LayoutArchetype.single_block, LayoutArchetype.stacked_list),
    Purpose.solution: (LayoutArchetype.grid, LayoutArchetype.stacked_list),
    Purpose.benefits: (LayoutArchetype.grid, LayoutArchetype.icon_badge_grid),
    Purpose.data: (LayoutArchetype.chart_data, LayoutArchetype.table_data),
    Purpose.comparison: (LayoutArchetype.two_column, LayoutArchetype.table_data),
    Purpose.timeline: (LayoutArchetype.stacked_list, LayoutArchetype.row_grid),
    Purpose.process: (LayoutArchetype.stacked_list, LayoutArchetype.row_grid),
    Purpose.quote: (LayoutArchetype.single_block,),
    Purpose.team: (LayoutArchetype.grid,),
    Purpose.cta: (LayoutArchetype.single_block,),
    Purpose.qa: (LayoutArchetype.single_block,),
    Purpose.thank_you: (LayoutArchetype.single_block,),
}


class ExemplarAssignment(BaseModel):
    """Выбор exemplar'а для одного планового слайда."""

    slide_index: int
    candidate_index: int
    reason: str = ""


class ExemplarRerankOutput(BaseModel):
    """Ответ LLM: назначение candidate_index на slide_index."""

    assignments: list[ExemplarAssignment]


def _text_preview(pkg: OpcPackage, part: str) -> str:
    """Самый длинный непустой a:t слайда, обрезанный — сигнал «о чём слайд»."""
    root = etree.fromstring(pkg.parts[part])
    longest = max(
        ((t.text or "").strip() for t in root.findall(f".//{{{A}}}t")),
        key=len,
        default="",
    )
    return longest[:_TEXT_PREVIEW_CHARS]


def _candidate_card(
    pkg: OpcPackage,
    part: str,
    layouts: dict[str, str | None],
    profiles: dict[str, RunProfile],
    description_by_part: dict[str, tuple[str, bool]] | None = None,
) -> dict[str, Any]:
    profile = profiles.get(part) or run_profile(pkg, part)
    return {
        "index": -1,  # проставляется при сборке payload
        "slide_part": part,
        "layout_part": layouts.get(part),
        "shape_count": shape_count(pkg, part),
        # Реальная заполняемая ёмкость (см. count_content_slots) -- главный
        # сигнал плотности для модели. long_runs оставлен для обратной
        # совместимости payload'а, но известно недооценивает плотные
        # карточные сетки в разы (см. ExemplarChoice.content_slots).
        "content_slots": count_content_slots(pkg.parts[part], _presentation_slide_size(pkg)),
        "has_offslide_content_slots": has_offslide_content_slots(pkg, part),
        "long_runs": profile.long_runs,
        "mean_run_len": profile.mean_nonempty_len,
        "visual_richness": visual_richness(pkg, part),
        "capabilities": sorted(slide_capabilities(pkg, part)),
        "layout_archetype": layout_archetype(pkg, part, profile).value,
        "text_preview": _text_preview(pkg, part),
        # ADR-018: real LLM-written one-liner ("Карточка члена команды:
        # имя, должность, фото"), computed once at template analyze time
        # and cached in Design DNA -- None when the caller didn't pass
        # descriptions (older Design DNA, no model at analyze time, or a
        # caller like the CLI/skill path that has no DesignDNA at all).
        # Preferred over text_preview (a crude "longest run" guess) by
        # layout_fit.py's payload builder when present.
        "content_description": (description_by_part or {}).get(part, (None, False))[0],
        # ADR-018 (v1.1.0): hard-exclusion signal, not just a prompt hint
        # -- see layout_fit.py's _narrow_candidates. False when no
        # description was available at all (nothing to be entity-specific
        # about).
        "is_entity_specific": (description_by_part or {}).get(part, (None, False))[1],
    }


def _candidate_pool(
    pkg: OpcPackage,
    ranked: list[str],
    *,
    max_candidates: int,
) -> list[str]:
    """Детерминированный кандидатный пул для rerank — тот же расширенный
    ``ranked + extras``, из которого выбирает ``select_exemplar_slides``
    (extras = остальные слайды шаблона по content-likeness), под cap'ом."""
    extras = [s for s in slide_parts(pkg) if s not in set(ranked)]
    extras.sort(
        key=lambda s: (
            -run_profile(pkg, s).long_runs,
            -shape_count(pkg, s),
            s,
        )
    )
    candidates = ranked + extras
    safe = [part for part in candidates if not has_offslide_content_slots(pkg, part)]
    if not safe:
        return candidates[:max_candidates]
    # Preserve an unsafe donor only if its structured capability has no
    # safe equivalent. A chart or table must not disappear from the pool
    # merely because its source slide also contains a cropped label.
    safe_caps = [slide_capabilities(pkg, part) for part in safe]
    essential: list[str] = []
    represented: set[frozenset[str]] = set()
    for part in candidates:
        if part in safe:
            continue
        caps = slide_capabilities(pkg, part)
        if caps and caps not in represented and not any(
            caps <= available for available in safe_caps
        ):
            essential.append(part)
            represented.add(caps)
    return safe[:max(0, max_candidates - len(essential))] + essential[:max_candidates]


async def _rerank_batch(
    gateway: ModelGateway,
    requests_batch: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    strategy: Strategy,
    sem: asyncio.Semaphore,
) -> list[ExemplarAssignment]:
    """Один батч-вызов rerank'а. Пустой список = честный fallback этого
    батча (звонящий код уже трактует «нет назначения» как «оставить
    baseline для этого слайда» — здесь ничего специального не нужно)."""
    payload = {
        "requests": requests_batch,
        "candidates": candidates,
        "strategy": strategy.value,
    }
    async with sem:
        try:
            output = await gateway.text_json(
                EXEMPLAR_RERANK_PROMPT, payload, ExemplarRerankOutput
            )
        except Exception as exc:  # noqa: BLE001 — любой сбой провайдера = fallback батча
            logger.warning(
                "exemplar rerank batch (slides %s) failed, deterministic fallback: %s",
                [r["slide_index"] for r in requests_batch],
                exc,
            )
            return []
    if not isinstance(output, ExemplarRerankOutput):
        return []
    return output.assignments


async def llm_exemplar_choices(
    pkg: OpcPackage,
    plan: DeckPlan,
    needs: list[frozenset[str]],
    gateway: ModelGateway,
    *,
    strategy: Strategy = Strategy.balanced,
    max_candidates: int = MAX_RERANK_CANDIDATES,
    max_slides: int = MAX_RERANK_SLIDES,
) -> list[ExemplarChoice] | None:
    """LLM-rerank кандидатного пула. ``None`` → честный fallback на
    детерминированный выбор (любая ошибка/пустой пул/переполнение лимитов)."""
    count = len(plan.slides)
    if count == 0 or len(needs) != count:
        return None
    baseline = select_exemplar_slides(
        pkg, count=count, needs=needs, strategy=strategy
    )
    if len(baseline) != count:
        return None

    ranked, dominant_layout, layout_slide_count, profiles = _ranked_candidate_pool(
        pkg, strategy
    )
    pool = _candidate_pool(pkg, ranked, max_candidates=max_candidates)
    if len(pool) < 2:
        return None  # реранкать нечего — выбор и так однозначен

    layouts = slide_layout_map(pkg)
    candidates = [_candidate_card(pkg, part, layouts, profiles) for part in pool]
    for i, card in enumerate(candidates):
        card["index"] = i
    caps_by_index = {
        i: frozenset(card["capabilities"]) for i, card in enumerate(candidates)
    }

    requested = min(count, max_slides)
    requests = [
        {
            "slide_index": i,
            "purpose": slide.purpose.value,
            "title_intent": slide.title_intent,
            "key_message": slide.key_message,
            "needs": sorted(needs[i]),
            "preferred_archetypes": [
                a.value for a in _PURPOSE_ARCHETYPE_HINTS.get(slide.purpose, ())
            ],
        }
        for i, slide in enumerate(plan.slides[:requested])
    ]

    batches = [
        requests[i : i + RERANK_BATCH_SIZE] for i in range(0, len(requests), RERANK_BATCH_SIZE)
    ]
    sem = asyncio.Semaphore(MAX_CONCURRENT_RERANK_CALLS)
    batch_results = await asyncio.gather(
        *(_rerank_batch(gateway, batch, candidates, strategy, sem) for batch in batches)
    )
    all_assignments = [a for batch in batch_results for a in batch]

    assigned: dict[int, str] = {}
    used_candidates: set[int] = set()
    for a in all_assignments:
        if not (
            0 <= a.slide_index < requested
            and 0 <= a.candidate_index < len(pool)
        ):
            continue  # вне предъявленных слайдов/пула — невалидно
        if a.slide_index in assigned:
            continue  # первое валидное назначение на слайд выигрывает
        if not needs[a.slide_index] <= caps_by_index[a.candidate_index]:
            continue  # назначение без нужной капабилити — честно отклоняем
        if candidates[a.candidate_index]["has_offslide_content_slots"] and any(
            not card["has_offslide_content_slots"]
            and needs[a.slide_index] <= caps_by_index[j]
            for j, card in enumerate(candidates)
        ):
            continue  # модель не должна выбирать обрезанные подписи при безопасной альтернативе
        if a.candidate_index in used_candidates and len(used_candidates) < len(pool):
            continue  # дубль exemplar'а при свободных кандидатах — baseline
        assigned[a.slide_index] = pool[a.candidate_index]
        used_candidates.add(a.candidate_index)

    result: list[ExemplarChoice] = []
    changed = False
    for i, base in enumerate(baseline):
        part = assigned.get(i)
        if part is None or part == base.slide_part:
            result.append(base)
            continue
        changed = True
        profile = profiles.get(part) or run_profile(pkg, part)
        result.append(
            ExemplarChoice(
                slide_part=part,
                layout_part=layouts.get(part) or "",
                shape_count=shape_count(pkg, part),
                layout_slide_count=layout_slide_count,
                long_runs=profile.long_runs,
                content_slots=count_content_slots(pkg.parts[part], _presentation_slide_size(pkg)),
            )
        )
    if not changed:
        # LLM ответила, но ни одно назначение не принято/не отличается от
        # baseline — вывод модели на колоду не повлиял → честный fallback.
        return None
    return result
