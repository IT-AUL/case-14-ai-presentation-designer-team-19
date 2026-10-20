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
from dataclasses import dataclass
from itertools import chain

from pydantic import BaseModel, Field

from deckdna.contracts.content_pack import Block, ContentPack
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
from deckdna.errors import DeckDNAError
from deckdna.ingestion.pptx_content import is_speaker_note
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
# ADR-030: слайды-рамки колоды, которые по своей роли не несут тела —
# всё остальное обязано иметь содержание (см. validation.py).
_BOOKEND_PURPOSES = frozenset(
    {
        Purpose.title,
        Purpose.agenda,
        Purpose.section_divider,
        Purpose.thank_you,
        Purpose.cta,
        Purpose.qa,
    }
)
# No current caller (pipeline.py, api/app.py) threads design_dna through to
# plan_deck_llm -- the template's Design DNA isn't computed yet at this
# pipeline stage (the template package is opened later, for exemplar
# selection). Without explicit capacities, content still should not grow
# unbounded -- reuse _density()'s own sparse/medium/dense thresholds as a
# defensive default cap.
_DEFAULT_MAX_BULLETS = 6
_DEFAULT_MAX_BODY_CHARS = 800
_GENERIC_PURPOSES = frozenset(
    {
        "",
        "feature",
        "product",
        "project",
        "initiative",
        "report",
        "presentation",
        "deck",
        "slides",
        "slide",
        "balanced",
        "faithful",
        "visual",
        "custom",
        "default",
        "продукт",
        "проект",
        "инициатива",
        "отчёт",
        "отчет",
        "презентация",
        "доклад",
        "слайды",
        "колода",
        "сбалансированный",
        "строгий",
        "визуальный",
        "близко к шаблону",
    }
)


# Преамбула-запрос к сервису («Нужна презентация на 12 слайдов: …»,
# «Make a 6-slide deck about …») — инструкция автору, а не тема колоды.
# Отрезаем только когда за ней следует явный разделитель: без двоеточия
# фраза «Презентация продукта» — легитимный заголовок.
_META_REQUEST_RE = re.compile(
    r"^\s*(?:нужн\w*|сдела\w*|созда\w*|подготов\w*|сгенер\w*|напиш\w*|собери\w*|хочу\b|"
    r"make|create|build|prepare|generate|draft|need)\b[^:;.!?]{0,80}?\b"
    r"(?:презентац\w*|питч[-\w]*|дек[ауие]?\w*|колод\w*|слайд\w*|"
    r"presentations?|pitch(?:\s*deck)?|slide\s*deck|deck)\b[^:;.!?]{0,60}?[:—–-]\s*",
    re.IGNORECASE,
)


def clean_purpose_text(text: str | None) -> str | None:
    """Отсечь служебные токены и мета-запрос к сервису из текста брифа."""
    val = (text or "").strip()
    # «Нужна презентация на 12 слайдов: Умный помощник» — тема после
    # двоеточия, преамбула не должна попадать ни в заголовок, ни в лид.
    stripped = _META_REQUEST_RE.sub("", val).strip(" —–-")
    if stripped:
        val = stripped
    val = re.sub(
        r"^(?:(?:предположение|гипотеза|вывод|тезис|assumption|hypothesis):\s*)+",
        "",
        val,
        flags=re.IGNORECASE,
    ).strip()
    # Compare metadata after sentence punctuation, but preserve real copy
    # verbatim (e.g. "visual analytics" is a topic, not a strategy slug).
    if not val or val.casefold().rstrip(".!?…;:, ") in _GENERIC_PURPOSES:
        return None
    return val


def cover_topic(text: str | None) -> str | None:
    """Service headings are fallback copy, never a substitute for a real topic."""
    value = clean_purpose_text(text)
    if value and value.casefold() not in {"идея", "тема", "idea", "topic"}:
        return value
    return None


def split_cover_headline(raw_title: str | None) -> tuple[str | None, str | None]:
    """Split an overly long compound purpose/title ('Topic: details...') into
    a punchy cover headline and a descriptive subtitle."""
    cleaned = clean_purpose_text(raw_title)
    if not cleaned:
        return None, None
    cleaned = cleaned.rstrip("? ")
    if len(cleaned) > 55:
        for sep in (":", " — ", " – "):
            if sep in cleaned:
                left, right = cleaned.split(sep, 1)
                left = left.strip().rstrip(".!? ")
                right = right.strip().rstrip(".!? ")
                if 12 <= len(left) <= 75 and len(right) >= 8:
                    sub = right[0].upper() + right[1:] if right else None
                    return left, sub
    return cleaned, None


def cover_context(
    title: str, brief: Brief, *, fallback_subtitle: str | None = None
) -> tuple[str | None, str | None]:
    """Category and period without inventing a company name or reporting year."""
    ru = brief.language.startswith("ru")
    annual = bool(re.search(r"год|годов|annual|year|20\d{2}", title, re.I))
    business = bool(
        re.search(
            r"продаж|выручк|финанс|отч[её]т|бизнес|аналит|тариф|номенклатур|"
            r"sales|revenue|report|business|analytics|pricing|catalog",
            title,
            re.I,
        )
    )
    eyebrow = None
    if business:
        eyebrow = (
            ("ГОДОВОЙ ОТЧЕТ" if annual else "АНАЛИТИЧЕСКИЙ ОБЗОР")
            if ru
            else ("ANNUAL REPORT" if annual else "BUSINESS REVIEW")
        )
    subtitle = clean_purpose_text(brief.key_message)
    if (not subtitle or subtitle.casefold() == title.casefold()) and fallback_subtitle:
        subtitle = fallback_subtitle
    if not subtitle or subtitle.casefold() == title.casefold():
        year = re.search(r"\b20\d{2}\b", title)
        subtitle = (
            (
                (f"Период: {year.group()} год" if year else "Период: отчётный год")
                if ru
                else (f"Reporting period: {year.group()}" if year else "Annual review")
            )
            if annual and business
            else None
        )
    return eyebrow, subtitle


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
# mapping ... отложен"). Пробовал поднять до 8 под насыщенный контент
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


# Заголовок-вывод (handoff Q3): без модели «вывод» — это главная мысль
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
    title = (clean_purpose_text(parts[0]) or heading).rstrip(".;:… ")
    rest = parts[1].strip() if len(parts) > 1 else ""
    words = title.split()
    if not (_CONCLUSION_MIN_CHARS <= len(title) <= _CONCLUSION_MAX_CHARS):
        return None
    if not (_CONCLUSION_MIN_WORDS <= len(words) <= _CONCLUSION_MAX_WORDS):
        return None
    if title.endswith("?") or title.casefold() == heading.strip().casefold():
        return None
    return title, rest


# Окончания личных форм глагола (3-е лицо, наст./буд. время) — граница
# «темы» предложения: «Время сборки | укладывается в пять минут».
_RU_VERB_RE = re.compile(
    r"(?:ет|ёт|ит|ут|ют|ат|ят|ется|ётся|ится|утся|ются|атся|ятся|ал|ала|али|ил|ила|или)$"
)
_TOPIC_MAX_WORDS = 5


def _topic_title(text: str | None, lang: str | None) -> str | None:
    """Тема предложения (подлежащее с определениями) для заголовка слайда,
    когда заголовок раздела совпадает с обложкой (ADR-031 §9): короткий
    вход из одного раздела «Идея» давал слайды «Идея — часть 2», «Идея —
    часть 3». Только русский; без найденного глагола — None."""
    if not text or not (lang or "ru").startswith("ru"):
        return None
    first = _SENTENCE_RE.split(text.strip(), maxsplit=1)[0]
    words = first.split()
    for i, word in enumerate(words[: _TOPIC_MAX_WORDS + 1]):
        if i and _RU_VERB_RE.search(word.casefold().strip(",.;:")):
            topic = " ".join(words[:i]).strip(",.;: ")
            return topic[:1].upper() + topic[1:] if topic else None
    return None


@dataclass
class _ProseItem:
    """Пункт прозаического потока секции до сборки юнитов."""

    order: int
    text: str
    kind: str  # "bullet" | block.kind.value (paragraph/quote/code/note)
    in_list: bool
    block: int  # индекс блока в секции
    ev: list[str]


_SENT_END_RE = re.compile(r"[.!?…:;»\"')\]]\s*$")
_CONNECTOR_END_RE = re.compile(
    r"(?:[,—–]|\(|"
    r"\b(?:и|или|а|но|да|что|как|в|во|на|с|со|к|ко|по|за|для|от|до|из|у|о|об|про|"
    r"при|над|под|через|без|не|the|a|an|of|to|in|on|for|and|or|with|by))\s*$",
    re.IGNORECASE,
)


def _is_bare_label(text: str | None) -> bool:
    """Строка-метка без тела: «Что сделать / проверить:», «Шаги:»."""
    t = (text or "").rstrip()
    return t.endswith(":") and "\n" not in t


def _continuation(it: _ProseItem, prev: _ProseItem) -> bool:
    """*it* — обрывок, продолжающий *prev* пункт, а не самостоятельная
    карточка: скобочный фрагмент, начало со строчной буквы, продолжение
    внутри незакрытой скобки или после оборванной на связке фразы
    («…через 7 и» + «30 дней (R7/R30).»). Метка («…:») сама продолжается —
    её пункты собирает правило карточки, а не склейка в одну строку."""
    s = (it.text or "").lstrip()
    p = (prev.text or "").rstrip()
    if not s or _is_bare_label(p):
        return False
    if (
        p.count("(") > p.count(")")
        or p.count("[") > p.count("]")
        or bool(_CONNECTOR_END_RE.search(p))
    ):
        return True
    if s.startswith(("(", "[", "«", "„", "“")):
        return not bool(_SENT_END_RE.search(p))
    if it.in_list and prev.in_list:
        return False
    if s[:1].islower() and not bool(_SENT_END_RE.search(p)):
        return True
    return False


def _merge_fragile_items(items: list[_ProseItem]) -> list[_ProseItem]:
    """Склейка обрывков и сборка карточек «метка + её пункты».

    Парсеры режут список по переносам строки: «(например, …)» и хвосты
    предложений становятся отдельными пунктами, а строка-метка «Что
    сделать:» — карточкой без тела. Здесь обрывки приклеиваются к
    предыдущему пункту, а метка поглощает пункты следующего за ней списка:
    одна карточка «метка — заголовок, пункты — тело»."""
    merged: list[_ProseItem] = []
    for it in items:
        if merged and _continuation(it, merged[-1]):
            last = merged[-1]
            last.text = last.text.rstrip() + " " + it.text.lstrip()
            last.ev = list(dict.fromkeys([*last.ev, *it.ev]))
        else:
            merged.append(it)
    out: list[_ProseItem] = []
    i = 0
    while i < len(merged):
        it = merged[i]
        absorbed: list[_ProseItem] = []
        if _is_bare_label(it.text) and i + 1 < len(merged):
            nxt = merged[i + 1]
            if nxt.in_list:
                # метка + пункты её списка: соседи по блоку (метка — пункт
                # того же списка) или весь следующий список (метка-абзац)
                target = it.block if it.in_list else nxt.block
                j = i + 1
                while (
                    j < len(merged)
                    and merged[j].in_list
                    and merged[j].block == target
                    and not _is_bare_label(merged[j].text)
                ):
                    absorbed.append(merged[j])
                    j += 1
            elif not it.in_list and not _is_bare_label(nxt.text):
                # метка-абзац + следующий абзац — иначе пустая карточка
                absorbed.append(nxt)
        if absorbed:
            it.text = it.text.rstrip() + "\n" + "\n".join(a.text for a in absorbed)
            it.ev = list(dict.fromkeys([*it.ev, *(e for a in absorbed for e in a.ev)]))
            it.kind = "bullet"
            i += len(absorbed)
        out.append(it)
        i += 1
    return out


def _block_to_units(pack_id: str, section: PackSection) -> list[ContentUnit]:
    """Блок ContentPack -> ContentUnits. У bullet-списка на каждый item
    свой unit (в контракте нет контейнера items); сперва прозаический
    поток чистится от обрывков и голых меток (``_merge_fragile_items``)."""
    prose: list[_ProseItem] = []
    ordered: list[tuple[int, _ProseItem | tuple[Block, list[str]]]] = []
    order = 0
    for i, block in enumerate(section.blocks):
        ev = [f"{pack_id}:{section.id}:b{i}"]
        if is_speaker_note(block):
            continue  # ADR-032: заметки докладчика — в speaker_note, не на слайд
        if block.kind.value == "list":
            for item in block.items or []:
                it = _ProseItem(order, item or "", "bullet", True, i, ev)
                prose.append(it)
                order += 1
            continue
        if block.kind.value in ("paragraph", "quote", "code", "note"):
            it = _ProseItem(order, block.text or "", block.kind.value, False, i, ev)
            prose.append(it)
            order += 1
            continue
        ordered.append((order, (block, ev)))
        order += 1
    ordered.extend((it.order, it) for it in _merge_fragile_items(prose))
    ordered.sort(key=lambda e: e[0])

    units: list[ContentUnit] = []
    for _, entry in ordered:
        if isinstance(entry, _ProseItem):
            kind = {
                "bullet": Kind.bullet,
                "quote": Kind.quote,
                "code": Kind.paragraph,  # пробел схемы: отдельного kind=code нет
                "note": Kind.note,
            }.get(entry.kind, Kind.paragraph)
            units.append(
                ContentUnit(
                    role=(
                        "bullet"
                        if entry.kind == "bullet"
                        else "equation"
                        if is_equation(entry.text)
                        else "body"
                    ),
                    kind=kind,
                    text=entry.text,
                    evidence_ids=entry.ev,
                )
            )
            continue
        block, ev = entry
        kind = {
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
            text=text,
            evidence_ids=[node.id],
        )
    if node.type is EvidenceType.number:
        text = (node.text or (node.value.raw if node.value else None) or "").strip()
        if not text:
            return None
        return ContentUnit(role="number", kind=Kind.number, text=text, evidence_ids=[node.id])
    if node.type is EvidenceType.table:
        return ContentUnit(
            role="body",
            kind=Kind.table,
            text=node.text,
            table_ref=node.id,
            evidence_ids=[node.id],
        )
    if node.type is EvidenceType.visual:
        return ContentUnit(
            role="body",
            kind=Kind.image,
            text=node.text,
            asset_ref=node.id,
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
    """Распределяет (секция, units) в пределах budget слайдов.

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

    def has_structured_visual(slide) -> bool:
        return any(
            u.kind in {Kind.chart, Kind.table, Kind.diagram} for _, units in slide for u in units
        )

    while len(slides) > budget:
        # сливаем соседнюю пару с наименьшим суммарным объёмом (не сливая два разных визуала)
        i = min(
            range(len(slides) - 1),
            key=lambda j: (
                int(has_structured_visual(slides[j]) and has_structured_visual(slides[j + 1])),
                size(slides[j]) + size(slides[j + 1]),
            ),
        )
        slides[i : i + 2] = [slides[i] + slides[i + 1]]
    while len(slides) < budget:
        # Короткий список — один слайд. Деление только ради target давало
        # «часть 2» из одной строки; каждая половина должна нести материал.
        def splittable(units: list[ContentUnit]) -> bool:
            half = math.ceil(len(units) / 2)
            return len(units) >= 4 and all(
                sum(len(u.text or "") for u in chunk) >= 180
                or any(u.kind in {Kind.table, Kind.chart, Kind.diagram, Kind.image} for u in chunk)
                for chunk in (units[:half], units[half:])
            )

        cand = [(len(u), si) for si, slide in enumerate(slides) for _, u in slide if splittable(u)]
        if not cand:
            break
        _, si = max(cand)
        slide = slides.pop(si)
        # делим вклад с наибольшим числом units внутри слайда
        k = max(
            (j for j in range(len(slide)) if splittable(slide[j][1])),
            key=lambda j: len(slide[j][1]),
        )
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
                    if (
                        u.locked
                        or u.kind not in (Kind.paragraph, Kind.note, Kind.quote)
                        or not u.text
                    ):
                        continue
                    sents = [s for s in _SENTENCE_RE.split(u.text) if s.strip()]
                    half = math.ceil(len(sents) / 2)
                    substantial = all(
                        len(" ".join(chunk)) >= 180 for chunk in (sents[:half], sents[half:])
                    )
                    if substantial and (best is None or len(sents) > best[0]):
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
        # Итог короткого раздела повторяет его единственный слайд.
        occurrences = sum(any(s.id == sec.id and us for s, us in slide) for slide in slides)
        if occurrences < 2:
            continue
        sents = [s for u in units if u.text for s in [_first_sentence(u.text)] if s][:4]
        if len(sents) < 2:
            # итог из одного пункта повторяет сам слайд — заглушка (ADR-030)
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


def content_groups(pack: ContentPack) -> list[tuple[PackSection, list[ContentUnit]]]:
    """(секция, её content units) в порядке пака — общий вход планировщика
    и оценки ёмкости контента (ADR-030, ``planning/content_fill.py``)."""
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
    if len(groups) > 1 and pack.title_hint and groups[0][0].heading == pack.title_hint:
        _lead_sec, lead_units = groups[0]
        if lead_units:
            next_sec, next_units = groups[1]
            groups[1] = (next_sec, lead_units + next_units)
        groups = groups[1:]
    return groups


def plan_deck(
    pack: ContentPack,
    brief: Brief,
    config: GenerationConfig | None = None,
    strategy: Strategy = Strategy.balanced,
    *,
    fact_ledger: object | None = None,
    story_plan: object | None = None,
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
    from deckdna.planning.request_constraints import resolve_count_goal

    brief = resolve_count_goal(brief)
    validate_slide_count(brief.target_slide_count, cfg)
    brief_text = " ".join(
        filter(
            None,
            [
                getattr(brief, "purpose", None),
                getattr(brief, "key_message", None),
                getattr(brief, "call_to_action", None),
            ],
        )
    )

    if pack.datasets or fact_ledger is not None or story_plan is not None:
        from deckdna.analysis.fact_ledger import FactLedger, build_pack_fact_ledger
        from deckdna.analysis.pipeline import analyze_pack
        from deckdna.planning.story_rules import (
            beam_search_story_plan,
            compile_story_ledger_to_deck_plan,
            ledger_source_groups,
            resolve_story_ledger_plan,
        )

        ledger = (
            fact_ledger
            if isinstance(fact_ledger, FactLedger)
            else build_pack_fact_ledger(analyze_pack(pack, brief_text))
        )
        story_budget = max(3, brief.target_slide_count - len(ledger_source_groups(pack, brief)))
        resolved_story = (
            resolve_story_ledger_plan(
                story_plan,  # type: ignore[arg-type]
                ledger,
                brief=brief,
                max_slides=story_budget,
            )
            if story_plan is not None
            else beam_search_story_plan(
                ledger,
                brief=brief,
                max_slides=story_budget,
            )
        )
        plan = compile_story_ledger_to_deck_plan(
            resolved_story,
            ledger,
            brief,
            pack=pack,
            config=cfg,
            strategy=strategy,
        )
        validate_deck_plan(plan, cfg)
        return plan

    lang = brief.language or pack.language
    target = brief.target_slide_count
    groups = content_groups(pack)
    from deckdna.planning.mandatory import protect_mandatory_groups, protect_mandatory_sections
    from deckdna.planning.story_rules import (
        apply_storyline_and_grammar,
        order_groups_scqa,
        storyline_config,
    )

    groups = protect_mandatory_groups(groups, pack, brief)
    groups = order_groups_scqa(groups)
    story_cfg = storyline_config(cfg)
    content_sections = sum(1 for _, units in groups if units)

    has_agenda = (
        strategy != Strategy.faithful
        and target >= story_cfg.min_slides_for_agenda
        and (
            (len(pack.sections) >= 3 and content_sections >= 3)
            or (len(pack.sections) >= 2 and bool(pack.external_sources))
        )
    )
    reserved = 2 + int(has_agenda)  # title + closing (+ agenda)
    budget = max(target - reserved, 1)

    slide_groups = _allocate_slides(groups, budget)

    # Добор при дефиците контента, по убыванию пользы: легитимные
    # divider перед секцией -> сплит длинных текстов по предложениям ->
    # recap-слайды. Заглушек нет (ADR-030): если и этого не хватило,
    # колода честно короче запрошенной (см. ниже), а добирать контент —
    # задача стадии ресёрча до планирования (research/fill.py).
    deficit = budget - len(slide_groups)
    # divider имеет смысл только между разделами в длинных колодах (>= 8 слайдов)
    # при наличии >= 3 содержательных разделов: в коротком материале (1-2 факта/раздела)
    # разделитель-заглушка запрещён
    if deficit > 0 and content_sections >= 3 and target >= story_cfg.min_slides_for_agenda:
        deficit -= _inject_dividers(slide_groups, deficit)
    if deficit > 0:
        deficit -= _split_sentences(slide_groups, deficit)
    if deficit > 0 and pack.external_sources:
        while deficit > 0:
            cand = [
                (len(u), si, k)
                for si, sld in enumerate(slide_groups)
                for k, (_, u) in enumerate(sld)
                if len(u) >= 4
            ]
            if not cand:
                break
            _, si, k = max(cand)
            sld = slide_groups.pop(si)
            sec, units = sld[k]
            half = math.ceil(len(units) / 2)
            slide_groups.insert(si, sld[:k] + [(sec, units[half:])] + sld[k + 1 :])
            slide_groups.insert(si, sld[:k] + [(sec, units[:half])] + sld[k + 1 :])
            deficit -= 1
    # recap-слайды — синтезированная суммаризация: faithful и короткий материал (< 3 секций) их
    # не делают
    if deficit > 0 and strategy != Strategy.faithful and content_sections >= 3:
        deficit -= _inject_recaps(slide_groups, groups, deficit, pack.id, lang)

    slides: list[SlidePlan] = []
    used_titles: set[str] = set()

    def unique_title(base: str) -> str:
        base = clean_purpose_text(base) or ("Обзор" if lang == "ru" else "Overview")
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
    first_sec_heading = next(
        (clean_purpose_text(s.heading) for s in pack.sections if clean_purpose_text(s.heading)),
        None,
    )
    raw_title_text = (
        cover_topic(pack.title_hint)
        or clean_purpose_text(brief.purpose)
        or first_sec_heading
        or clean_purpose_text(pack.title_hint)
        or ("Презентация" if lang == "ru" else "Presentation")
    )
    split_head, split_sub = split_cover_headline(raw_title_text)
    title_text = split_head or raw_title_text
    display_purpose = clean_purpose_text(brief.purpose)
    title_units = [
        ContentUnit(role="title", kind=Kind.title, text=title_text, evidence_ids=meta_ev)
    ]
    # ADR-034 §3: главная мысль из ответов помощника — подзаголовком обложки
    eyebrow, subtitle = cover_context(title_text, brief, fallback_subtitle=split_sub)
    subtitle = subtitle or split_sub or display_purpose
    if eyebrow:
        title_units.append(
            ContentUnit(
                role="eyebrow",
                kind=Kind.subtitle,
                text=eyebrow,
                evidence_ids=meta_ev,
            )
        )
    if subtitle and subtitle.casefold() != title_text.strip().casefold():
        title_units.append(
            ContentUnit(
                role="subtitle",
                kind=Kind.subtitle,
                text=subtitle,
                evidence_ids=meta_ev,
            )
        )
    emit(
        SlidePlan(
            id="slide-0",
            index=0,
            purpose=Purpose.title,
            title_intent=unique_title(title_text),
            key_message=_first_sentence(display_purpose or title_text),
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
                key_message=("Структура доклада" if lang == "ru" else "Talk outline"),
                evidence_ids=[s.id for s in pack.sections] or meta_ev,
                content_units=[
                    ContentUnit(
                        role="agenda-item",
                        kind=Kind.bullet,
                        text=clean_purpose_text(s.heading) or f"Секция {i + 1}",
                        evidence_ids=[s.id],
                    )
                    for i, s in enumerate(pack.sections)
                ],
                density_budget=DensityBudget(
                    level=(
                        Level.sparse
                        if len(pack.sections) <= 3
                        else Level.medium
                        if len(pack.sections) <= 6
                        else Level.dense
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
        clean_sec_heading = clean_purpose_text(primary_sec.heading) or ""
        is_divider = not all_units and bool(clean_sec_heading)
        purpose = Purpose.section_divider if is_divider else _purpose_for(clean_sec_heading)
        base_title = clean_sec_heading or ("Дополнительно" if lang == "ru" else "More")
        idx = len(slides)
        first_para = next((u.text for u in all_units if u.kind == Kind.paragraph and u.text), None)
        source_notes = [
            b.text for sec, _ in slide_parts for b in sec.blocks if is_speaker_note(b) and b.text
        ]
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
            or any(u.kind in {Kind.chart, Kind.table} for u in all_units)
            or para_unit is None
            or para_unit.locked
            else _conclusion_title(para_unit.text, base_title)
        )
        # ADR-030: абзац, целиком ушедший в заголовок, оставил бы слайд с
        # одним заголовком — та же заглушка. Тогда заголовок остаётся
        # темой раздела, а текст — телом слайда.
        if (
            conclusion is not None
            and not conclusion[1]
            and not any(u is not para_unit and u.kind != Kind.title for u in all_units)
        ):
            conclusion = None
        if conclusion is not None and para_unit is not None:
            base_title_text, rest = conclusion
            # предложение переехало в заголовок — в теле не дублируем
            if rest:
                all_units = [
                    u.model_copy(update={"text": rest}) if u is para_unit else u for u in all_units
                ]
            else:
                all_units = [u for u in all_units if u is not para_unit]
            heading_note = f"Раздел: {base_title}"
            note = f"{heading_note}. {note}" if note else heading_note
            title = unique_title(base_title_text)
        else:
            # заголовок раздела совпал с обложкой: тема из текста слайда,
            # иначе «Идея — часть 2» повторяет титул (ADR-031 §9)
            topic = (
                _topic_title(first_para or next((u.text for u in all_units if u.text), None), lang)
                if base_title.strip().casefold() == title_text.strip().casefold()
                and purpose not in _TOPIC_TITLE_PURPOSES
                else None
            )
            title = unique_title(topic or base_title)
        if source_notes:
            # заметки автора исходной презентации — первыми
            note = " ".join([*source_notes, *([note] if note else [])])
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

    # ADR-030: раньше недостающие слайды добивались generic-слайдами
    # «Раздел» с одним заголовком. Теперь колода короче запрошенной, а
    # brief плана получает фактическое число слайдов — так инвариант
    # «число слайдов == brief.target_slide_count» остаётся честным, а
    # вызывающий код видит сокращение, сравнив его с исходным брифом.
    if not any(p.purpose not in _BOOKEND_PURPOSES for p in slides):
        raise DeckDNAError(
            code="invalid_input",
            message=(
                "content pack has no content to build slides from; add content "
                "or enable content_fill=expand with a model"
            ),
            stage="planning",
        )
    planned = len(slides) + 1  # + закрывающий слайд ниже
    if planned < target:
        logger.info(
            "plan_deck: content fills %d of %d requested slides; deck shortened "
            "instead of padding with empty section slides (ADR-030)",
            planned,
            target,
        )
        brief = brief.model_copy(update={"target_slide_count": planned})

    closing_purpose = (
        Purpose.cta if _purpose_for(brief.purpose) == Purpose.cta else Purpose.thank_you
    )
    closing_title = (
        "Следующие шаги"
        if closing_purpose == Purpose.cta
        else ("Спасибо за внимание" if lang == "ru" else "Thank you")
    )
    emit(
        SlidePlan(
            id=f"slide-{len(slides)}",
            index=len(slides),
            purpose=closing_purpose,
            title_intent=unique_title(closing_title),
            key_message=display_purpose or title_text,
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
        objective=display_purpose or title_text,
        audience=brief.audience,
        language=lang,
        sections=[
            PlanSection(
                id=sec.id,
                title=clean_purpose_text(sec.heading) or sec.id,
                slide_ids=section_slide_ids.get(sec.id, []),
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
    plan = apply_storyline_and_grammar(plan, pack, brief, cfg, strategy)
    plan = protect_mandatory_sections(plan, pack)
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


STORY_LEDGER_PROMPT = "story_ledger"


def plan_story_ledger(
    ledger,
    brief: Brief,
    *,
    beam_width: int = 8,
):
    """Детерминированный план истории по FactLedger (beam search, k=8)."""
    from deckdna.planning.story_rules import beam_search_story_plan

    return beam_search_story_plan(
        ledger,
        brief=brief,
        max_slides=brief.target_slide_count,
        k=beam_width,
    )


async def plan_story_ledger_llm(
    ledger,
    brief: Brief,
    gateway: ModelGateway | None = None,
    *,
    beam_width: int = 8,
):
    """LLM-контракт Story Director поверх FactLedger с детерминированным откатом.

    Модель получает компактный каталог ``build_fact_ledger_catalog`` и возвращает
    только JSON по схеме ``StoryLedgerPlan``. Сервер проверяет существование
    всех ``fact_id``, принадлежность ``title_realization_id ∈ allowed_title_ids``,
    наличие факта у каждого ``evidence``-слайда и отсутствие причинных переходов.
    При любом нарушении или ошибке шлюза возвращается детерминированный план
    ``beam_search_story_plan`` без падения.
    """
    from deckdna.planning.story_rules import (
        StoryLedgerPlan,
        build_fact_ledger_catalog,
        resolve_story_ledger_plan,
    )

    if gateway is None:
        return plan_story_ledger(ledger, brief, beam_width=beam_width)

    catalog = build_fact_ledger_catalog(ledger, brief=brief)
    candidate = None
    try:
        candidate = await gateway.text_json(STORY_LEDGER_PROMPT, catalog, StoryLedgerPlan)
    except Exception as exc:  # noqa: BLE001 — невалидный ответ или ошибка шлюза -> детерминированный план
        logger.warning(
            "story_ledger LLM call failed (%s); falling back to deterministic beam search",
            exc,
        )
        candidate = None

    return resolve_story_ledger_plan(
        candidate,
        ledger,
        brief=brief,
        max_slides=brief.target_slide_count,
        k=beam_width,
    )


async def plan_deck_llm(
    pack: ContentPack,
    brief: Brief,
    gateway: ModelGateway,
    design_dna: DesignDNA | None = None,
    config: GenerationConfig | None = None,
    strategy: Strategy = Strategy.balanced,
    *,
    fact_ledger: object | None = None,
    story_plan: object | None = None,
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

    from deckdna.planning.request_constraints import resolve_count_goal

    brief = resolve_count_goal(brief)
    if pack.datasets or fact_ledger is not None or story_plan is not None:
        from deckdna.analysis.fact_ledger import FactLedger, build_pack_fact_ledger
        from deckdna.analysis.pipeline import analyze_pack

        brief_text = " ".join(
            filter(
                None,
                [
                    getattr(brief, "purpose", None),
                    getattr(brief, "key_message", None),
                    getattr(brief, "call_to_action", None),
                ],
            )
        )
        ledger = (
            fact_ledger
            if isinstance(fact_ledger, FactLedger)
            else build_pack_fact_ledger(analyze_pack(pack, brief_text))
        )
        from deckdna.planning.story_rules import ledger_source_groups

        story_brief = brief.model_copy(
            update={
                "target_slide_count": max(
                    3, brief.target_slide_count - len(ledger_source_groups(pack, brief))
                )
            }
        )
        resolved_story = (
            story_plan
            if story_plan is not None
            else await plan_story_ledger_llm(ledger, story_brief, gateway)
        )
        return plan_deck(
            pack,
            brief,
            cfg,
            strategy=strategy,
            fact_ledger=ledger,
            story_plan=resolved_story,
        )

    baseline = plan_deck(pack, brief, cfg, strategy=strategy)
    eligible = [
        s
        for s in baseline.slides
        if s.purpose not in _PROTECTED_OUTLINE_PURPOSES
        and not any(
            u.locked or u.kind in {Kind.chart, Kind.table, Kind.diagram, Kind.image}
            for u in s.content_units
        )
    ]
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
                title_intent=clean_purpose_text(item.title_intent) or base.title_intent,
                key_message=clean_purpose_text(item.key_message) or base.key_message,
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
