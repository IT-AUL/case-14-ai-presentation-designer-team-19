"""Stage 2 of ADR-016 (Structure -> Writer -> Layout-fit -> Audit): per-slide
content writer.

Takes ONE slide's Stage-1 structure (structure.py::SlideStructure --
purpose, evidence pool, slide_brief) and writes its actual title and body,
SYNTHESIZING from the evidence pool rather than citing it verbatim the way
``plan_deck_llm`` does today (ADR-012). One call PER SLIDE, not batched
like Stage 1 -- meant to run in full parallel across every slide of every
variant (see ADR-016's latency/parallelism section), the actual "vызывать
максимально параллельно" part of the redesign.

Grounding discipline: the same number/date-matching check as
``text_fit.py``/``content_style.py`` (``extract_numbers`` diff against the
evidence pool text) -- a real but PARTIAL defense; a model can still
invent a QUALITATIVE claim the number-check won't catch. Flagged as a
known residual risk in ADR-016, mitigated by (a) the evidence pool as the
prompt's only source material, (b) the existing contextual (VLM) audit
downstream. On any provider failure or validation rejection, the slide
falls back to VERBATIM content built directly from its evidence pool
(the same construction ``story_director.py::_units_from_evidence``
already uses for ``plan_deck_llm``'s own fallback) -- never left with
nothing, and never less grounded than today's behavior.
"""

from __future__ import annotations

import asyncio
import logging
import re

from pydantic import BaseModel, Field

from deckdna.contracts.deck_plan import ContentUnit, Kind
from deckdna.contracts.design_dna import Capacities
from deckdna.contracts.evidence_graph import Node as EvidenceNode
from deckdna.evaluation.measures import (
    ends_with_unqualified_range,
    extract_numbers,
    starts_with_capital,
)
from deckdna.planning.protected_content import is_equation
from deckdna.planning.structure import SlideStructure
from deckdna.providers.base import ModelGateway

logger = logging.getLogger(__name__)

CONTENT_WRITER_PROMPT = "content_writer"
MAX_CONCURRENT_WRITER_CALLS = 8
_DEFAULT_MAX_BULLETS = 6
_DEFAULT_MAX_BODY_CHARS = 800
# Синтез — не построчная выжимка одного evidence-узла, он ожидаемо длиннее
# суммы source-фрагментов (объединяет несколько тезисов в связный текст);
# порог ловит настоящее "модель разошлась", а не нормальный синтез.
_MAX_GROWTH_RATIO = 2.0


class SlideContent(BaseModel):
    """Ответ модели на ОДИН слайд — заголовок/вывод/тело, синтезированные
    из его evidence-пула."""

    title_intent: str = Field(min_length=1)
    key_message: str = Field(min_length=1)
    bullets: list[str] = Field(min_length=1)


def can_rewrite(gateway: object) -> bool:
    """Есть ли у gateway настоящая модель для синтеза контента.

    Unlike Stage 1 (structure.py), whose grounding gate rejects
    MockProvider's synthesized evidence_ids structurally (they can't
    match real graph node ids), this stage's ``validate()`` only checks
    for invented NUMBERS -- offline-synthesized gibberish with no numbers
    in it would sail through untouched and get written into the deck as
    if it were real content. Same guard as ``text_fit.can_rewrite``/
    ``content_style.can_rewrite``: mock participates only with an
    explicit fixture for this prompt (tests)."""
    if getattr(gateway, "provider_name", None) == "mock":
        return CONTENT_WRITER_PROMPT in (getattr(gateway, "fixtures", None) or {})
    return gateway is not None


def _bare(numbers: set[str]) -> set[str]:
    return {re.sub(r"[^\d.]", "", n) for n in numbers}


def validate(pool_text: str, answer: SlideContent, max_chars: int) -> str | None:
    """Причина отказа или ``None``, если ответ модели годится."""
    if not answer.title_intent.strip() or not answer.key_message.strip():
        return "модель вернула пустой title_intent/key_message"
    bullets = [b.strip() for b in answer.bullets]
    if any(not b for b in bullets):
        return "модель вернула пустой bullet"
    # Same check as layout_fit.py's Stage 3 validate() -- found live
    # 28.09 that a Stage-2 fragment ("лента по истории пересылок и
    # реакций.") shipped unchecked when Stage 3 never touched that slide
    # (it doesn't process every slide). Shared via evaluation/measures.py
    # so both stages stay in sync.
    if not starts_with_capital(answer.title_intent):
        return (
            "title_intent начинается со строчной буквы (похоже на обрывок): "
            f"{answer.title_intent[:40]!r}"
        )
    for b in bullets:
        if not starts_with_capital(b):
            return f"bullet начинается со строчной буквы (похоже на обрывок): {b[:40]!r}"
    for label, value in (
        ("title_intent", answer.title_intent),
        ("key_message", answer.key_message),
        *(("bullet", b) for b in bullets),
    ):
        if ends_with_unqualified_range(value):
            return f"{label} заканчивается диапазоном без единицы или объекта: {value[:60]!r}"
    body_text = " ".join([answer.title_intent, answer.key_message, *bullets])
    invented = _bare(extract_numbers(body_text)) - _bare(extract_numbers(pool_text))
    if invented:
        return "модель добавила числа, которых нет в evidence-пуле: " + ", ".join(
            sorted(invented)
        )
    total = len(answer.title_intent) + len(answer.key_message) + sum(len(b) for b in bullets)
    if total > max_chars * _MAX_GROWTH_RATIO:
        return f"текст сильно длиннее ожидаемого: {total} симв. при ориентире {max_chars}"
    return None


def _fallback_units(
    slide: SlideStructure,
    nodes_by_id: dict[str, EvidenceNode],
    capacities: Capacities | None,
) -> tuple[str, str, list[ContentUnit]]:
    """Дословные bullet'ы из evidence-пула (тот же путь, что и
    ``plan_deck_llm``'s собственный fallback) — используется, когда
    провайдер недоступен или ответ не прошёл заземление. title_intent/
    key_message — честная заглушка (``slide_brief``), не идеальный
    "вывод", но заземлённый и небессмысленный."""
    from deckdna.planning.story_director import _units_from_evidence

    units = _units_from_evidence(slide.evidence_ids, nodes_by_id, capacities)
    from deckdna.planning.story_director import _unit_from_evidence_node

    for eid in slide.evidence_ids:
        node = nodes_by_id.get(eid)
        if node and is_equation(node.text or "") and not any(
            eid in (u.evidence_ids or []) for u in units
        ):
            unit = _unit_from_evidence_node(node)
            if unit is not None:
                units.append(unit)
    return slide.slide_brief, slide.slide_brief, units


async def write_slide_content(
    gateway: ModelGateway,
    slide: SlideStructure,
    nodes_by_id: dict[str, EvidenceNode],
    capacities: Capacities | None,
    language: str,
    sem: asyncio.Semaphore,
) -> tuple[str, str, list[ContentUnit], bool]:
    """(title_intent, key_message, content_units, use_llm) для ОДНОГО
    слайда. ``use_llm=False`` — сработал fallback (пустой пул, сбой
    провайдера или отказ валидации); вызывающий код честно не засчитывает
    такой слайд в LLM-провенанс, как и ``plan_deck_llm`` сегодня."""
    pool_nodes = [nodes_by_id[eid] for eid in slide.evidence_ids if eid in nodes_by_id]
    pool_text = "\n".join(n.text or "" for n in pool_nodes)
    if not pool_text.strip():
        title, key_message, units = _fallback_units(slide, nodes_by_id, capacities)
        return title, key_message, units, False

    max_bullets = (capacities.max_bullets if capacities else None) or _DEFAULT_MAX_BULLETS
    max_chars = (capacities.max_body_chars if capacities else None) or _DEFAULT_MAX_BODY_CHARS

    payload = {
        "purpose": slide.purpose.value,
        "slide_brief": slide.slide_brief,
        "evidence_pool": [n.text for n in pool_nodes if n.text],
        "max_bullets": max_bullets,
        "max_body_chars": max_chars,
        "preserved_elements": [n.text for n in pool_nodes if is_equation(n.text or "")],
        "language": language,
    }
    async with sem:
        try:
            answer = await gateway.text_json(CONTENT_WRITER_PROMPT, payload, SlideContent)
        except Exception as exc:  # noqa: BLE001 — сбой провайдера = честный fallback
            logger.warning(
                "content writer failed for slide %d (%s); verbatim fallback",
                slide.index,
                exc,
            )
            title, key_message, units = _fallback_units(slide, nodes_by_id, capacities)
            return title, key_message, units, False
    if not isinstance(answer, SlideContent):
        title, key_message, units = _fallback_units(slide, nodes_by_id, capacities)
        return title, key_message, units, False

    reason = validate(pool_text, answer, max_chars)
    if reason is not None:
        logger.info(
            "content writer rejected for slide %d: %s; verbatim fallback", slide.index, reason
        )
        title, key_message, units = _fallback_units(slide, nodes_by_id, capacities)
        return title, key_message, units, False

    # Do not silently slice a complete thought away when a model exceeds
    # its item budget. Join adjacent full thoughts; Stage 3 sees real text.
    bullets = [b.strip() for b in answer.bullets]
    while len(bullets) > max_bullets:
        bullets[-2:] = [" ".join(bullets[-2:])]
    units = [
        ContentUnit(role="bullet", kind=Kind.bullet, text=b, evidence_ids=slide.evidence_ids)
        for b in bullets
    ]
    # The model only ever WRITES bullets from the pool's text -- any
    # non-text evidence in the same pool (an image/table/chart/number
    # node the slide's evidence_ids also referenced) must survive
    # untouched, the same way _fallback_units's own _units_from_evidence
    # already handles it for the fallback path. claim/entity/source_span
    # nodes map to Kind.bullet too (_unit_from_evidence_node) -- those are
    # excluded here since the model already synthesized bullets from that
    # same text; appending the verbatim version would duplicate it. Found
    # while fixing the analogous bug in layout_fit.py (Stage 3).
    from deckdna.planning.story_director import _unit_from_evidence_node

    non_bullet_units = [
        unit
        for node in pool_nodes
        if (unit := _unit_from_evidence_node(node)) is not None and unit.kind != Kind.bullet
    ]
    protected_texts = {u.text for u in non_bullet_units if u.role == "equation"}
    units = [u for u in units if u.text not in protected_texts]
    units.extend(non_bullet_units)
    return answer.title_intent.strip(), answer.key_message.strip(), units, True
