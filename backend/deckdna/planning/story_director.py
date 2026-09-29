"""Story Director v0 — детерминированный маппинг ContentPack + Brief → DeckPlan.

Без LLM-звонков (см. PLANNING_AND_VARIANTS.md §2 — тот же интерфейс, выход
строго DeckPlan): секции ContentPack распределяются по слайдам с учётом
``brief.target_slide_count`` и границ ``planning/validation.py``.
Плотные секции режутся на несколько слайдов, мелкие соседние секции при
нехватке бюджета объединяются, заголовки секций без контента становятся
section_divider-слайдами.

Известные пробелы контракта v0 (зафиксированы, схему не трогаем):
- ``ContentUnit.kind`` не покрывает код/источники данных — code-блоки
  мапятся в ``Kind.paragraph``;
- ``evidence_ids`` ссылаются на секции/блоки ContentPack — настоящий
  EvidenceGraph появится на следующей итерации ingestion.

``plan_deck_llm`` — опциональный LLM-путь через ModelGateway (промпт
``prompts/content_planning/storyline.v1.yaml``), см. докстринг функции.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from itertools import chain

from pydantic import BaseModel, Field

from deckdna.contracts.content_pack import ContentPack
from deckdna.contracts.content_pack import Section as PackSection
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
from deckdna.contracts.deck_plan import (
    Section as PlanSection,
)
from deckdna.contracts.design_dna import Capacities, DesignDNA
from deckdna.contracts.evidence_graph import EvidenceGraph
from deckdna.contracts.evidence_graph import Node as EvidenceNode
from deckdna.contracts.evidence_graph import Type as EvidenceType
from deckdna.contracts.serialize import to_schema_dict
from deckdna.contracts.variant_spec import Strategy
from deckdna.planning.config import GenerationConfig
from deckdna.planning.evidence import build_evidence_graph
from deckdna.planning.protected_content import is_equation
from deckdna.planning.validation import validate_deck_plan, validate_slide_count
from deckdna.providers.base import ModelGateway
from deckdna.providers.profiles import ModelProfileSource
from deckdna.providers.prompts import load_prompt

PLANNER_VERSION = "story-director/deterministic-0.1.0"
LLM_PLANNER_VERSION = "story-director/llm-hybrid-0.2.0"
STORYLINE_PROMPT = "storyline"
SCHEMA_VERSION = "1.0"

# Batched outline planning (v2, see prompts/content_planning/storyline.v2.yaml):
# a 27B-class model struggles to emit an entire deck as one exact-schema JSON
# blob, so the deck is planned in small independent batches run concurrently,
# each with its own honest per-batch fallback to the deterministic slide.
OUTLINE_BATCH_SIZE = 5
MAX_CONCURRENT_OUTLINE_CALLS = 4
# Structural bookend slides: boilerplate, not worth a model call, never
# rewritten by the LLM path (same "protected slides" principle as text_fit).
_PROTECTED_OUTLINE_PURPOSES = frozenset({Purpose.title, Purpose.thank_you, Purpose.cta})
# No current caller (pipeline.py, api/app.py) threads design_dna through to
# plan_deck_llm -- the template's Design DNA isn't computed yet at this
# pipeline stage (the template package is opened later, for exemplar
# selection). Without explicit capacities, content still should not grow
# unbounded -- reuse _density()'s own sparse/medium/dense thresholds as a
# defensive default cap.
_DEFAULT_MAX_BULLETS = 6
_DEFAULT_MAX_BODY_CHARS = 800
_GENERIC_PURPOSES = frozenset({
    "feature", "product", "project", "initiative", "report",
    "продукт", "проект", "инициатива", "отчёт", "отчет",
})


class SlideOutlineItem(BaseModel):
    """Один слайд из батч-ответа модели (storyline v2) — без content_units:
    само тело слайда собирается детерминированно из evidence_ids ниже."""

    index: int
    purpose: Purpose
    title_intent: str
    key_message: str
    evidence_ids: list[str] = Field(min_length=1)
    desired_visual: DesiredVisual | None = None


class SlideOutlineBatch(BaseModel):
    slides: list[SlideOutlineItem] = Field(min_length=1)
# Верхняя граница content_units на один слайд перед тем, как секция
# режется на несколько. Планировщик не знает, какой exemplar (со
# сколькими реально заполняемыми txBody-слотами, не визуальными
# "карточками" -- часть карточек decorative-only) достанется слайду:
# exemplar выбирается позже, в composing ("semantic slot
# mapping" отложен по архитектуре). Пробовал поднять до 8 под насыщенный контент
# на vk_tech_template.pptx: на 8-карточном exemplar'е с малым числом
# реальных текстовых слотов это дало жёсткое наложение текста друг на
# друга (хуже пустых карточек, не лучше) -- ёмкость слотов конкретного
# exemplar'а неизвестна планировщику, поднятие лимита не безопасно без
# semantic slot mapping. Оставлено на исходном 5.
MAX_UNITS_PER_SLIDE = 5

logger = logging.getLogger(__name__)

# Ключевые слова назначения purpose по заголовку секции (ru/en).
_PURPOSE_HINTS: tuple[tuple[Purpose, tuple[str, ...]], ...] = (
    (Purpose.problem, ("проблем", "problem", "pain", "вызов")),
    (Purpose.solution, ("решен", "solution", "подход", "архитектур", "approach")),
    (Purpose.benefits, ("преимущ", "выгод", "benefit", "ценност", "value")),
    (Purpose.data, ("данн", "метрик", "результат", "цифр", "data", "metric", "result", "kpi")),
    (Purpose.comparison, ("сравн", "альтернатив", "comparison", "vs")),
    (Purpose.timeline, ("план", "этап", "roadmap", "timeline", "график", "sprint")),
    (Purpose.process, ("процесс", "workflow", "pipeline", "process", "как работает")),
    (Purpose.team, ("команда", "team", "люди")),
    (Purpose.cta, ("следующ", "next", "cta", "контакт", "призыв", "call to action")),
    (Purpose.quote, ("цитат", "quote", "отзыв", "testimonial")),
)

_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")


def _purpose_for(heading: str) -> Purpose:
    h = heading.lower()
    for purpose, needles in _PURPOSE_HINTS:
        if any(n in h for n in needles):
            return purpose
    return Purpose.overview


def _first_sentence(text: str, limit: int = 200) -> str:
    sentence = _SENTENCE_RE.split(text.strip(), maxsplit=1)[0]
    return sentence[:limit]


# Заголовок-вывод (contract Q3): без модели «вывод» — это главная мысль
# слайда, т.е. первое предложение его же абзаца, если оно достаточно
# короткое, чтобы быть заголовком. Иначе остаётся заголовок раздела.
_CONCLUSION_MIN_CHARS = 24
_CONCLUSION_MAX_CHARS = 80
_CONCLUSION_MIN_WORDS = 4
_CONCLUSION_MAX_WORDS = 14
# слайды-навигация и служебные: заголовок раздела здесь и есть смысл
_TOPIC_TITLE_PURPOSES = frozenset(
    {
        Purpose.title,
        Purpose.agenda,
        Purpose.section_divider,
        Purpose.thank_you,
        Purpose.cta,
        Purpose.qa,
    }
)


def _conclusion_title(first_para: str | None, heading: str) -> tuple[str, str] | None:
    """(заголовок-вывод, остаток абзаца) из первого предложения абзаца.

    None — предложение не годится в заголовок (слишком длинное/короткое,
    вопрос, повтор заголовка раздела)."""
    if not first_para:
        return None
    parts = _SENTENCE_RE.split(first_para.strip(), maxsplit=1)
    title = parts[0].strip().rstrip(".;:… ")
    rest = parts[1].strip() if len(parts) > 1 else ""
    words = title.split()
    if not (_CONCLUSION_MIN_CHARS <= len(title) <= _CONCLUSION_MAX_CHARS):
        return None
    if not (_CONCLUSION_MIN_WORDS <= len(words) <= _CONCLUSION_MAX_WORDS):
        return None
    if title.endswith("?") or title.casefold() == heading.strip().casefold():
        return None
    return title, rest


def _block_to_units(pack_id: str, section: PackSection) -> list[ContentUnit]:
    """Блок ContentPack -> ContentUnits. У bullet-списка на каждый item
    свой unit (в контракте нет контейнера items)."""
    units: list[ContentUnit] = []
    for i, block in enumerate(section.blocks):
        ev = [f"{pack_id}:{section.id}:b{i}"]
        if block.kind.value == "list":
            for item in block.items or []:
                units.append(
                    ContentUnit(role="bullet", kind=Kind.bullet, text=item, evidence_ids=ev)
                )
            continue
        kind = {
            "paragraph": Kind.paragraph,
            "quote": Kind.quote,
            "code": Kind.paragraph,  # пробел схемы: отдельного kind=code нет
            "note": Kind.note,
            "image_ref": Kind.image,
            "table_ref": Kind.table,
            "chart_ref": Kind.chart,
            "diagram_ref": Kind.diagram,
        }.get(block.kind.value, Kind.paragraph)
        units.append(
            ContentUnit(
                role="equation" if is_equation(block.text or "") else "body",
                kind=kind,
                text=block.text,
                evidence_ids=ev,
                asset_ref=block.text if block.kind.value == "image_ref" else None,
                table_ref=block.text if block.kind.value == "table_ref" else None,
                chart_ref=block.text if block.kind.value == "chart_ref" else None,
                diagram_ref=block.text if block.kind.value == "diagram_ref" else None,
            )
        )
    return units


def _density(units: list[ContentUnit]) -> DensityBudget:
    n = len(units)
    chars = sum(len(u.text or "") for u in units)
    level = Level.sparse if n <= 3 else Level.medium if n <= 6 else Level.dense
    return DensityBudget(level=level, max_items=n, max_chars=chars or None)


def _desired_visual(units: list[ContentUnit]) -> DesiredVisual:
    kinds = {u.kind for u in units}
    if Kind.image in kinds:
        return DesiredVisual.image
    if Kind.table in kinds:
        return DesiredVisual.table
    # chart/diagram were missing here (found while checking image_brief.py's
    # ADR-017 filter, which skips slides that already have a
    # desired_visual): a slide with a real chart or diagram unit was
    # honestly reporting "none", which would let ADR-017 pile a generated
    # image onto a slide that already has a chart/diagram.
    if Kind.chart in kinds:
        return DesiredVisual.chart
    if Kind.diagram in kinds:
        return DesiredVisual.diagram
    return DesiredVisual.none


def _unit_from_evidence_node(node: EvidenceNode) -> ContentUnit | None:
    """Evidence node -> ContentUnit, texte verbatim (никогда не из ответа
    модели — модель в v2 только ВЫБИРАЕТ evidence_ids, не пишет тело)."""
    if node.type in (EvidenceType.claim, EvidenceType.entity, EvidenceType.source_span):
        text = (node.text or "").strip()
        if not text:
            return None
        return ContentUnit(
            role="equation" if is_equation(text) else "bullet",
            kind=Kind.paragraph if is_equation(text) else Kind.bullet,
            text=text, evidence_ids=[node.id],
        )
    if node.type is EvidenceType.number:
        text = (node.text or (node.value.raw if node.value else None) or "").strip()
        if not text:
            return None
        return ContentUnit(role="number", kind=Kind.number, text=text, evidence_ids=[node.id])
    if node.type is EvidenceType.table:
        return ContentUnit(
            role="body", kind=Kind.table, text=node.text, table_ref=node.id,
            evidence_ids=[node.id],
        )
    if node.type is EvidenceType.visual:
        return ContentUnit(
            role="body", kind=Kind.image, text=node.text, asset_ref=node.id,
            evidence_ids=[node.id],
        )
    return None  # section-узлы — контейнеры, не контент, молча пропускаем


def _units_from_evidence(
    evidence_ids: list[str],
    nodes_by_id: dict[str, EvidenceNode],
    capacities: Capacities | None,
) -> list[ContentUnit]:
    """evidence_ids (выбор модели) -> content_units (наш детерминированный
    текст). Жёсткий кап по design DNA capacities — защита от переполнения
    даже если модель процитировала больше узлов, чем просили в промпте;
    без capacities (см. _DEFAULT_MAX_BULLETS) — всё равно не безлимитно."""
    max_bullets = (capacities.max_bullets if capacities else None) or _DEFAULT_MAX_BULLETS
    max_chars = (capacities.max_body_chars if capacities else None) or _DEFAULT_MAX_BODY_CHARS
    units: list[ContentUnit] = []
    chars = 0
    for eid in evidence_ids:
        node = nodes_by_id.get(eid)
        if node is None:
            continue
        unit = _unit_from_evidence_node(node)
        if unit is None:
            continue
        unit_len = len(unit.text or "")
        if units and max_chars is not None and chars + unit_len > max_chars:
            break
        if max_bullets is not None and len(units) >= max_bullets:
            break
        units.append(unit)
        chars += unit_len
    return units


def _split_evenly(units: list[ContentUnit], parts: int) -> list[list[ContentUnit]]:
    """Делит units на parts почти равных чанков (для перераспределения
    секции на несколько слайдов)."""
    per = math.ceil(len(units) / parts)
    return [units[i : i + per] for i in range(0, len(units), per)]


def _allocate_slides(
    groups: list[tuple[PackSection, list[ContentUnit]]], budget: int
) -> list[list[tuple[PackSection, list[ContentUnit]]]]:
    """Распределяет (секция, units) ровно по budget слайдам.

    Каждый слайд — список вкладов (обычно одна секция; при нехватке
    бюджета соседние мелкие секции объединяются).
    """
    slides: list[list[tuple[PackSection, list[ContentUnit]]]] = []
    for sec, units in groups:
        if not units:
            # секция-заголовок без контента → divider
            slides.append([(sec, [])])
        else:
            chunks = _split_evenly(units, math.ceil(len(units) / MAX_UNITS_PER_SLIDE))
            slides.extend([[(sec, chunk)] for chunk in chunks])

    def size(slide) -> int:
        return sum(len(u) for _, u in slide)

    while len(slides) > budget:
        # сливаем соседнюю пару с наименьшим суммарным объёмом
        i = min(range(len(slides) - 1), key=lambda j: size(slides[j]) + size(slides[j + 1]))
        slides[i : i + 2] = [slides[i] + slides[i + 1]]
    while len(slides) < budget:
        # режем самый большой вклад пополам
        cand = [
            (len(u), si)
            for si, slide in enumerate(slides)
            for _, u in slide
            if len(u) > 1
        ]
        if not cand:
            break
        _, si = max(cand)
        slide = slides.pop(si)
        # делим вклад с наибольшим числом units внутри слайда
        k = max(range(len(slide)), key=lambda j: len(slide[j][1]))
        sec, units = slide[k]
        half = math.ceil(len(units) / 2)
        slides.insert(si, slide[:k] + [(sec, units[half:])] + slide[k + 1 :])
        slides.insert(si, slide[:k] + [(sec, units[:half])] + slide[k + 1 :])
    return slides


def _inject_dividers(
    slides: list[list[tuple[PackSection, list[ContentUnit]]]], deficit: int
) -> int:
    """До одного section_divider на секцию перед её первым слайдом.

    Легитимные разделители несут реальный заголовок секции — в отличие
    от generic-паддинга в конце колоды. Возвращает число добавленных.
    """
    done = {parts[0][0].id for parts in slides if not parts[0][1]}
    added = 0
    i = 0
    while i < len(slides) and added < deficit:
        sec = slides[i][0][0]
        if sec.id not in done and sec.heading:
            slides.insert(i, [(sec, [])])
            done.add(sec.id)
            added += 1
        i += 1
    return added


def _split_sentences(
    slides: list[list[tuple[PackSection, list[ContentUnit]]]], deficit: int
) -> int:
    """Режет многосоставные текстовые юниты по предложениям: хвост
    выносится на новый слайд — добор глубиной вместо пустых слайдов."""
    added = 0
    while added < deficit:
        # Сплитим самый БОЛЬШОЙ разбиваемый юнит (tie — самый ранний):
        # иначе вечно режется первый чанк и весь хвост paragraph'а
        # съезжает геометрическим рядом на последний новый слайд.
        best: tuple[int, int, int, int, list[str]] | None = None
        for si, slide in enumerate(slides):
            for k, (_sec, units) in enumerate(slide):
                for ui, u in enumerate(units):
                    if u.kind not in (Kind.paragraph, Kind.note, Kind.quote) or not u.text:
                        continue
                    sents = [s for s in _SENTENCE_RE.split(u.text) if s.strip()]
                    if len(sents) > 1 and (best is None or len(sents) > best[0]):
                        best = (len(sents), si, k, ui, sents)
        if best is None:
            break
        _, si, k, ui, sents = best
        head = math.ceil(len(sents) / 2)
        sec, units = slides[si][k]
        u = units[ui]
        first = u.model_copy(update={"text": " ".join(sents[:head])})
        # Хвост — ОДИН unit с объединённым текстом, а не список
        # односоставных sentence-юнитов: иначе длинный paragraph
        # (~50k символов) распадается на сотни юнитов, почти все
        # из которых не помещаются в слоты и массово падают в
        # dropped_units на composing'е.
        rest_unit = u.model_copy(update={"text": " ".join(sents[head:])})
        slides[si][k] = (sec, units[:ui] + [first] + units[ui + 1 :])
        slides.insert(si + 1, [(sec, [rest_unit])])
        added += 1
    return added


def _inject_recaps(
    slides: list[list[tuple[PackSection, list[ContentUnit]]]],
    groups: list[tuple[PackSection, list[ContentUnit]]],
    deficit: int,
    pack_id: str,
    lang: str,
) -> int:
    """До одного recap-слайда на секцию с контентом («Итоги: X», буллеты
    из первых предложений её юнитов) после последнего слайда секции."""
    last_idx: dict[str, int] = {}
    for i, slide in enumerate(slides):
        for sec, _ in slide:
            last_idx[sec.id] = i
    prefix = "Итоги" if lang == "ru" else "Key points"
    inserts: list[tuple[int, list[tuple[PackSection, list[ContentUnit]]]]] = []
    for sec, units in groups:
        if len(inserts) >= deficit:
            break
        if not units or not sec.heading or sec.id not in last_idx:
            continue
        sents = [s for u in units if u.text for s in [_first_sentence(u.text)] if s][:4]
        if not sents:
            continue
        recap_sec = PackSection(
            id=sec.id, heading=f"{prefix}: {sec.heading}", level=sec.level, blocks=[]
        )
        recap_units = [
            ContentUnit(
                role="bullet",
                kind=Kind.bullet,
                text=s,
                evidence_ids=[f"{pack_id}:{sec.id}"],
            )
            for s in sents
        ]
        inserts.append((last_idx[sec.id] + 1, [(recap_sec, recap_units)]))
    for pos, entry in sorted(inserts, key=lambda t: t[0], reverse=True):
        slides.insert(pos, entry)
    return len(inserts)


def plan_deck(
    pack: ContentPack,
    brief: Brief,
    config: GenerationConfig | None = None,
    strategy: Strategy = Strategy.balanced,
) -> DeckPlan:
    """ContentPack + Brief → DeckPlan (один вариант, детерминированный).

    ``strategy`` — дифференциация вариантов (OR-007): ``faithful`` держится
    ближе к исходной структуре контента — без agenda-слайда и без
    синтезированных recap-слайдов («Итоги: …»); dividers по заголовкам
    секций и разбиение длинных предложений остаются (это реальный
    исходный текст). ``balanced`` — дефолтное поведение; ``visual`` и
    ``custom`` на этой стадии совпадают с balanced (их различия —
    exemplar ranking и плотность текста в composing)."""
    cfg = config
    if cfg is None:
        from deckdna.planning.config import load_generation_config

        cfg = load_generation_config()
    validate_slide_count(brief.target_slide_count, cfg)

    lang = brief.language or pack.language
    target = brief.target_slide_count
    groups = [(s, _block_to_units(pack.id, s)) for s in pack.sections]
    # Live repro (balanced strategy, real generated deck): the document's
    # own lead paragraph -- content_parsers.py gives every format its own
    # "first heading becomes title_hint" pass, and that same heading also
    # becomes pack.sections[0] with whatever body text follows it (a
    # normal writing convention: H1 title, then an abstract-like lead-in
    # paragraph before the first real ## subsection) -- entered
    # _allocate_slides as an ordinary, independently mergeable section.
    # Being tiny (usually just one paragraph, no bullets), it was almost
    # always the smallest adjacent pair whenever the budget-vs-content
    # merge step ran, and since `slides[i:i+2] = [slides[i] + slides[i+1]]`
    # keeps list order, `primary_sec = slide_parts[0][0]` always picked
    # THIS section as "primary" -- so its neighbor's real bullets rendered
    # under the deck's own title text instead of their own section's
    # heading (e.g. "Проблема"'s bullets landing on a slide titled
    # "DeckDNA: компилятор презентаций..."). Folding its units onto the
    # next section up front (not dropping them -- they still reach a
    # slide, just under that section's own heading) removes it from the
    # mergeable pool entirely, so this specific title-theft can't happen;
    # only fires when sections[0] really is the redundant title-echo
    # (heading == title_hint), so a genuine first body section without a
    # separate H1 lead-in is untouched.
    if (
        len(groups) > 1
        and pack.title_hint
        and groups[0][0].heading == pack.title_hint
    ):
        _lead_sec, lead_units = groups[0]
        if lead_units:
            next_sec, next_units = groups[1]
            groups[1] = (next_sec, lead_units + next_units)
        groups = groups[1:]

    has_agenda = strategy != Strategy.faithful and target >= 5 and len(pack.sections) >= 2
    reserved = 2 + int(has_agenda)  # title + closing (+ agenda)
    budget = max(target - reserved, 1)

    slide_groups = _allocate_slides(groups, budget)

    # Добор при дефиците контента, по убыванию пользы: легитимные
    # divider перед секцией -> сплит длинных текстов по предложениям ->
    # recap-слайды -> (ниже) generic-паддинг как последний резерв.
    deficit = budget - len(slide_groups)
    if deficit > 0:
        deficit -= _inject_dividers(slide_groups, deficit)
    if deficit > 0:
        deficit -= _split_sentences(slide_groups, deficit)
    # recap-слайды — синтезированная суммаризация: faithful их не делает
    if deficit > 0 and strategy != Strategy.faithful:
        deficit -= _inject_recaps(slide_groups, groups, deficit, pack.id, lang)

    slides: list[SlidePlan] = []
    used_titles: set[str] = set()

    def unique_title(base: str) -> str:
        title = base
        n = 2
        while title in used_titles:
            title = f"{base} — часть {n}"
            n += 1
        used_titles.add(title)
        return title

    def emit(slide: SlidePlan) -> None:
        slides.append(slide)

    meta_ev = [f"{pack.id}:meta"]
    title_text = pack.title_hint or (
        pack.sections[0].heading if pack.sections and pack.sections[0].heading else None
    ) or ("Презентация" if lang == "ru" else "Presentation")
    purpose_text = (brief.purpose or "").strip()
    display_purpose = (
        purpose_text
        if purpose_text.casefold() not in _GENERIC_PURPOSES
        else None
    )
    title_units = [
        ContentUnit(role="title", kind=Kind.title, text=title_text, evidence_ids=meta_ev)
    ]
    if display_purpose:
        title_units.append(
            ContentUnit(
                role="subtitle", kind=Kind.subtitle,
                text=display_purpose, evidence_ids=meta_ev,
            )
        )
    emit(
        SlidePlan(
            id="slide-0",
            index=0,
            purpose=Purpose.title,
            title_intent=unique_title(title_text),
            key_message=_first_sentence(brief.purpose),
            evidence_ids=meta_ev,
            content_units=title_units,
            density_budget=_density([]),
        )
    )

    if has_agenda:
        emit(
            SlidePlan(
                id="slide-1",
                index=1,
                purpose=Purpose.agenda,
                title_intent=unique_title("План презентации" if lang == "ru" else "Agenda"),
                key_message=(
                    "Структура доклада" if lang == "ru" else "Talk outline"
                ),
                evidence_ids=[s.id for s in pack.sections] or meta_ev,
                content_units=[
                    ContentUnit(
                        role="agenda-item",
                        kind=Kind.bullet,
                        text=s.heading or f"Секция {i + 1}",
                        evidence_ids=[s.id],
                    )
                    for i, s in enumerate(pack.sections)
                ],
                density_budget=DensityBudget(
                    level=(
                        Level.sparse
                        if len(pack.sections) <= 3
                        else Level.medium if len(pack.sections) <= 6 else Level.dense
                    ),
                    max_items=len(pack.sections),
                ),
            )
        )

    section_slide_ids: dict[str, list[str]] = {}
    for slide_parts in slide_groups:
        primary_sec = slide_parts[0][0]
        all_units = [u for _, units in slide_parts for u in units]
        ev_ids = sorted(set(chain.from_iterable(u.evidence_ids or [] for u in all_units))) or [
            f"{pack.id}:{primary_sec.id}"
        ]
        is_divider = not all_units and primary_sec.heading
        purpose = Purpose.section_divider if is_divider else _purpose_for(primary_sec.heading)
        base_title = primary_sec.heading or ("Дополнительно" if lang == "ru" else "More")
        idx = len(slides)
        first_para = next((u.text for u in all_units if u.kind == Kind.paragraph and u.text), None)
        note = (
            "Сводка слайдов секций: "
            + ", ".join(sorted({s.heading for s, _ in slide_parts if s.heading}))
            if len(slide_parts) > 1
            else None
        )
        # Q3: заголовок — вывод (главная мысль слайда), а не тема. faithful
        # сохраняет заголовки источника как есть.
        # только абзац САМОГО раздела: вводный абзац документа, слитый в
        # первый раздел, заголовок этого раздела красть не должен
        own_prefix = f"{pack.id}:{primary_sec.id}:"
        para_unit = next(
            (
                u
                for u in all_units
                if u.kind == Kind.paragraph
                and u.role != "equation"
                and u.text
                and any(e.startswith(own_prefix) for e in u.evidence_ids or [])
            ),
            None,
        )
        conclusion = (
            None
            if strategy == Strategy.faithful
            or purpose in _TOPIC_TITLE_PURPOSES
            or para_unit is None
            else _conclusion_title(para_unit.text, base_title)
        )
        if conclusion is not None and para_unit is not None:
            base_title_text, rest = conclusion
            # предложение переехало в заголовок — в теле не дублируем
            if rest:
                all_units = [
                    u.model_copy(update={"text": rest}) if u is para_unit else u
                    for u in all_units
                ]
            else:
                all_units = [u for u in all_units if u is not para_unit]
            heading_note = f"Раздел: {base_title}"
            note = f"{heading_note}. {note}" if note else heading_note
            title = unique_title(base_title_text)
        else:
            title = unique_title(base_title)
        slide = SlidePlan(
            id=f"slide-{idx}",
            index=idx,
            purpose=purpose,
            title_intent=title,
            key_message=_first_sentence(first_para or base_title),
            evidence_ids=ev_ids,
            content_units=[
                ContentUnit(role="title", kind=Kind.title, text=title, evidence_ids=ev_ids[:1]),
                *all_units,
            ],
            desired_visual=_desired_visual(all_units),
            density_budget=_density(all_units),
            speaker_note=note,
        )
        emit(slide)
        for sec, _ in slide_parts:
            section_slide_ids.setdefault(sec.id, []).append(slide.id)

    # последний резерв: generic divider (теперь с title-юнитом — не пустой
    # слайд под integrity.empty_slide). Достижим только при патологическом
    # ratio target/контент — см. отчёт по задаче.
    while len(slides) < target - 1:
        title = unique_title("Раздел" if lang == "ru" else "Section")
        emit(
            SlidePlan(
                id=f"slide-{len(slides)}",
                index=len(slides),
                purpose=Purpose.section_divider,
                title_intent=title,
                key_message="Переход к следующему разделу",
                evidence_ids=meta_ev,
                content_units=[
                    ContentUnit(
                        role="title", kind=Kind.title, text=title, evidence_ids=meta_ev
                    )
                ],
                density_budget=_density([]),
            )
        )

    closing_purpose = (
        Purpose.cta if _purpose_for(brief.purpose) == Purpose.cta else Purpose.thank_you
    )
    emit(
        SlidePlan(
            id=f"slide-{len(slides)}",
            index=len(slides),
            purpose=closing_purpose,
            title_intent=unique_title(
                "Следующие шаги"
                if closing_purpose == Purpose.cta
                else ("Спасибо за внимание" if lang == "ru" else "Thank you")
            ),
            key_message=brief.purpose,
            evidence_ids=meta_ev,
            content_units=[
                ContentUnit(
                    role="title",
                    kind=Kind.title,
                    text=("Спасибо за внимание" if lang == "ru" else "Thank you"),
                    evidence_ids=meta_ev,
                )
            ],
            density_budget=_density([]),
        )
    )

    plan = DeckPlan(
        schema_version=SCHEMA_VERSION,
        # id включает стратегию: разные варианты одного пака — разные
        # планы с разными id (OR-007), коллизий в записи нет.
        id=f"plan-{pack.id.removeprefix('pack-')}-{strategy.value}",
        brief=brief,
        evidence_graph_id=f"pack:{pack.id}",
        objective=brief.purpose,
        audience=brief.audience,
        language=lang,
        sections=[
            PlanSection(
                id=sec.id, title=sec.heading or sec.id, slide_ids=section_slide_ids.get(sec.id, [])
            )
            for sec in pack.sections
        ],
        slides=slides,
        provenance=Provenance(
            planner=PLANNER_VERSION,
            prompt_version="v0.1",
            schema_version=SCHEMA_VERSION,
            model_id="deterministic",
            input_hashes=[hashlib.sha256(pack.model_dump_json().encode()).hexdigest()],
        ),
    )
    validate_deck_plan(plan, cfg)
    return plan


async def _run_outline_batch(
    gateway: ModelGateway,
    batch_slides: list[SlidePlan],
    *,
    brief_payload: dict,
    graph_payload: dict,
    capacities_payload: dict | None,
    deck_context: list[dict],
    known_evidence_ids: set[str],
    sem: asyncio.Semaphore,
) -> dict[int, SlideOutlineItem]:
    """Один батч-вызов storyline v2. Возвращает {index: item} только для
    слайдов, где модель ответила валидно И процитировала реальные evidence
    id — частичный успех отдаётся как есть, остальные индексы batch'а
    остаются пустыми (честный per-slide fallback у вызывающего кода)."""
    wanted = {s.index for s in batch_slides}
    payload = {
        "brief": brief_payload,
        "evidence_graph": graph_payload,
        "design_dna_capacities": capacities_payload,
        "deck_context": deck_context,
        "batch": {
            "indices": sorted(wanted),
            "hints": [
                {
                    "index": s.index,
                    "purpose": s.purpose.value,
                    "baseline_title": s.title_intent,
                    "baseline_evidence_count": len(s.evidence_ids),
                }
                for s in batch_slides
            ],
        },
    }
    async with sem:
        try:
            out = await gateway.text_json(STORYLINE_PROMPT, payload, SlideOutlineBatch)
        except Exception as exc:  # noqa: BLE001 — любой сбой провайдера = fallback этих слайдов
            logger.warning(
                "LLM outline batch %s failed (%s); using deterministic content for these slides",
                sorted(wanted),
                exc,
            )
            return {}
    if not isinstance(out, SlideOutlineBatch):
        return {}
    result: dict[int, SlideOutlineItem] = {}
    for item in out.slides:
        if item.index not in wanted or item.index in result:
            continue  # вне батча или дубль индекса — честно отклоняем именно этот item
        grounded = [eid for eid in item.evidence_ids if eid in known_evidence_ids]
        if not grounded:
            continue  # ни одной настоящей ссылки — контенту слайда не доверяем
        result[item.index] = item.model_copy(update={"evidence_ids": grounded})
    return result


async def plan_deck_llm(
    pack: ContentPack,
    brief: Brief,
    gateway: ModelGateway,
    design_dna: DesignDNA | None = None,
    config: GenerationConfig | None = None,
    strategy: Strategy = Strategy.balanced,
) -> DeckPlan:
    """LLM-путь Story Director (v2, батчами) — ContentPack + Brief → DeckPlan.

    Живая проверка показала: модель класса ~27-30B, которую просят одним
    вызовом выдать ВЕСЬ DeckPlan (точное число слайдов, per-slide лимиты,
    заземлённые факты — весь JSON целиком), достаточно часто не проходит
    валидацию, и вся колода откатывается на детерминированный план целиком
    — модель как бы подключена, а реально почти не используется.

    Поэтому здесь: (1) сперва всегда считается ``baseline = plan_deck()``
    — быстрый, бесплатный, гарантированно валидный план; (2) слайды без
    заголовочных/закрывающих ролей (``_PROTECTED_OUTLINE_PURPOSES`` —
    title/thank_you/cta не трогаем, как protected slides у text_fit) режутся
    на батчи по ``OUTLINE_BATCH_SIZE`` и уходят МАЛЕНЬКИМИ параллельными
    вызовами (``asyncio.gather``, ограничено ``MAX_CONCURRENT_OUTLINE_CALLS``)
    — модель выбирает только purpose/title_intent/key_message/evidence_ids
    для своего батча, не тело слайда; (3) ``content_units`` каждого слайда
    строятся ДЕТЕРМИНИРОВАННО из выбранных evidence_ids — текст берётся
    verbatim из EvidenceGraph, модель его не пишет (нулевой риск того, что
    bullet скажет не то, что в источнике); (4) любой слайд, где батч
    целиком упал, вернул чужой/дублирующий index или не сослался ни на один
    реальный evidence-узел, остаётся на baseline-содержимом — честный
    per-slide fallback вместо all-or-nothing.

    ``provenance.planner`` = ``LLM_PLANNER_VERSION``, если хотя бы один
    слайд реально пришёл от модели, иначе ``PLANNER_VERSION`` (полный
    fallback — так же честно, как раньше). ``strategy`` в промпт не
    попадает: структуру/бюджет батчей и baseline задаёт strategy у
    ``plan_deck()``, различие визуальных вариантов остаётся за нижними
    стадиями (exemplar ranking, composing).
    """
    cfg = config
    if cfg is None:
        from deckdna.planning.config import load_generation_config

        cfg = load_generation_config()
    validate_slide_count(brief.target_slide_count, cfg)

    baseline = plan_deck(pack, brief, cfg, strategy=strategy)
    eligible = [s for s in baseline.slides if s.purpose not in _PROTECTED_OUTLINE_PURPOSES]
    if not eligible:
        return baseline  # ничего осмысленного отдать модели — не звоним ей вовсе

    graph: EvidenceGraph = build_evidence_graph(pack)
    nodes_by_id = {n.id: n for n in graph.nodes}
    known_evidence_ids = set(nodes_by_id)
    capacities = design_dna.capacities if design_dna else None

    brief_payload = to_schema_dict(brief)
    graph_payload = to_schema_dict(graph)
    capacities_payload = to_schema_dict(capacities) if capacities else None
    deck_context = [
        {"index": s.index, "purpose": s.purpose.value, "title_intent": s.title_intent}
        for s in baseline.slides
    ]

    batches = [
        eligible[i : i + OUTLINE_BATCH_SIZE] for i in range(0, len(eligible), OUTLINE_BATCH_SIZE)
    ]
    sem = asyncio.Semaphore(MAX_CONCURRENT_OUTLINE_CALLS)
    batch_results = await asyncio.gather(
        *(
            _run_outline_batch(
                gateway,
                batch,
                brief_payload=brief_payload,
                graph_payload=graph_payload,
                capacities_payload=capacities_payload,
                deck_context=deck_context,
                known_evidence_ids=known_evidence_ids,
                sem=sem,
            )
            for batch in batches
        )
    )
    outline_by_index: dict[int, SlideOutlineItem] = {}
    for batch_result in batch_results:
        outline_by_index.update(batch_result)

    slides: list[SlidePlan] = []
    llm_used = 0
    for base in baseline.slides:
        item = outline_by_index.get(base.index)
        units = _units_from_evidence(item.evidence_ids, nodes_by_id, capacities) if item else []
        if item is None or not units:
            slides.append(base)
            continue
        # evidence_ids честно сужен до того, что реально попало в units —
        # _units_from_evidence мог откинуть часть item.evidence_ids по
        # capacity-капу, и слайд не должен заявлять больше опоры, чем
        # у него реально есть на слайде (used by contextual audit's
        # evidence excerpt).
        used_evidence_ids = [eid for u in units for eid in (u.evidence_ids or [])]
        slides.append(
            SlidePlan(
                id=base.id,
                index=base.index,
                purpose=item.purpose,
                title_intent=item.title_intent,
                key_message=item.key_message,
                evidence_ids=used_evidence_ids,
                content_units=units,
                desired_visual=item.desired_visual or _desired_visual(units),
                density_budget=_density(units),
                speaker_note=base.speaker_note,
                mandatory=base.mandatory,
            )
        )
        llm_used += 1

    plan = baseline.model_copy(update={"slides": slides})
    plan.provenance = Provenance(
        planner=LLM_PLANNER_VERSION if llm_used else PLANNER_VERSION,
        prompt_version=load_prompt(STORYLINE_PROMPT).version,
        schema_version=SCHEMA_VERSION,
        model_id=(
            gateway.used_model_ids().get("text")
            if isinstance(gateway, ModelProfileSource)
            else None
        ),
        input_hashes=[
            hashlib.sha256(pack.model_dump_json().encode()).hexdigest(),
            hashlib.sha256(graph.model_dump_json().encode()).hexdigest(),
        ],
    )
    plan.evidence_graph_id = graph.id
    # структура унаследована от baseline — это страховка, отказа не ожидаем
    validate_deck_plan(plan, cfg)
    logger.info(
        "LLM outline: %d/%d slides from the model, %d deterministic (baseline/fallback)",
        llm_used,
        len(slides),
        len(slides) - llm_used,
    )
    return plan
