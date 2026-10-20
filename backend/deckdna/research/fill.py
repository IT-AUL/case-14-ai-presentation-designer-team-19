"""Стадия добора контента до запрошенного числа слайдов (ADR-030 §2–3).

Выполняется один раз на прогон, до планирования, общая для всех
вариантов: это три варианта вёрстки одной презентации, факты у них одни.

Решение по ``brief.content_fill`` (дефолт — ``content_fill.default`` в
конфиге):

- контента хватает (``ok``) или он с избытком (``excess``) — пак не
  меняется, планировщик сам ужмёт колоду до запроса;
- ``shrink`` — колода сокращается до фактической ёмкости контента;
- ``expand`` — веб-ресёрч (если настроен поиск), затем знания модели;
  если добрать нечем или не хватило — цель не урезается, планировщик
  использует весь настоящий контент и короче делает колоду, только когда
  контент действительно кончился (без заглушек);
- ``ask`` — в интерактивном вызове (``POST /plans``) ничего не делается,
  вызывающий код показывает оценку и варианты; в остальных путях (CLI,
  ``POST /generations``, навык в агентной платформе) спросить некого —
  ведёт себя как ``expand``.

Без модели добор невозможен — ``resolved=no_model`` в отчёте.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

from deckdna.contracts.content_pack import (
    Block,
    ContentPack,
    ExternalSource,
    Kind,
    SourceRef,
)
from deckdna.contracts.content_pack import Section as PackSection
from deckdna.contracts.deck_plan import Brief, ContentFill
from deckdna.planning.config import GenerationConfig, load_generation_config
from deckdna.planning.content_fill import (
    ContentSufficiency,
    Status,
    estimate_content_capacity,
    is_sources_heading,
    resolve_content_fill,
    shrink_target,
)
from deckdna.planning.diagram_detect import detect_diagrams
from deckdna.providers.base import ModelGateway
from deckdna.research.config import ResearchConfig, load_research_config
from deckdna.research.expand import can_expand, expand_with_model
from deckdna.research.gaps import extract_core_subject
from deckdna.research.web import Page, SearchProvider, WebResearch, research_web

logger = logging.getLogger(__name__)

# промпт просит раздел на слайд; длинные пункты дают больше — тогда
# пак с избытком, и планировщик ужмёт его до запроса
_SLOTS_PER_MODEL_SECTION = 1
_MAX_MODEL_ROUNDS = 2
MISSING_DATA_SECTION_ID = "sec-missing-data"
MISSING_DATA_ARTIFACT_ID = "brief:missing-data"

_ASSUMPTION_PREFIX_RE = re.compile(
    r"^\s*(?:предположение|гипотеза|допущение|assumption|hypothesis)\s*[:—–-]\s*",
    re.IGNORECASE,
)
_UNGROUNDED_STAT_RE = re.compile(
    r"(?:\d+(?:[.,]\d+)?\s*(?:%|процент\w*|п\.?\s*п\.?|трлн|млрд|млн|тыс\.?(?!\s*минут)|"
    r"₽|руб\w*|\$|€|billion|million|trillion|усл\.?\s*ед\w*)|"
    r"\b\d+[.,]\d+\b|"
    r"условн\w+\s+единиц\w*|"
    r"(?:направлен\w+|сегмент\w*|категор\w+|канал\w*)\s+[A-DА-Г]\b|"
    r"демонстрационн\w+\s+сценари\w*|"
    r"illustrative\s+scenario)",
    re.IGNORECASE,
)
_DATA_SEEKING_RE = re.compile(
    r"(?:аналитик\w*|продаж\w*|выручк\w*|финанс\w*|отч[её]т\w*|итог\w*|динамик\w*|"
    r"показател\w*|метрик\w*|рентабельн\w*|маржинальн\w*|выгрузк\w*|ebitda|"
    r"sales|revenue|financial|analytics|metrics)",
    re.IGNORECASE,
)


def _strip_assumption_prefix(text: str) -> str:
    cleaned = _ASSUMPTION_PREFIX_RE.sub("", text.strip()).strip()
    return cleaned or text.strip()


def _user_text_corpus(pack: ContentPack, brief: Brief) -> str:
    parts: list[str] = [brief.purpose or "", brief.key_message or ""]
    for sec in pack.sections:
        if is_sources_heading(sec.heading) or sec.id == MISSING_DATA_SECTION_ID:
            continue
        for blk in sec.blocks:
            if blk.text:
                parts.append(blk.text)
            if blk.items:
                parts.extend(blk.items)
    return " ".join(parts).casefold()


def _has_ungrounded_stats(text: str, user_corpus: str) -> bool:
    for match in _UNGROUNDED_STAT_RE.finditer(text):
        token = re.sub(r"\s+", " ", match.group(0).strip().casefold())
        if token and token not in user_corpus:
            return True
    return False


def _sanitize_model_sections(
    sections: list[PackSection], pack: ContentPack, brief: Brief
) -> list[PackSection]:
    """Убирает из модельного добора неподтверждённые числа, синтетические
    сценарии («Направление A/B/C/D», «усл. ед.») и префиксы «Предположение:»."""
    user_corpus = _user_text_corpus(pack, brief)
    cleaned_sections: list[PackSection] = []
    for sec in sections:
        heading = _strip_assumption_prefix(sec.heading or "")
        if not heading or _has_ungrounded_stats(heading, user_corpus):
            continue
        blocks: list[Block] = []
        for blk in sec.blocks:
            items = [
                cleaned
                for raw in (blk.items or ([blk.text] if blk.text else []))
                if raw
                and (cleaned := _strip_assumption_prefix(raw))
                and not _has_ungrounded_stats(cleaned, user_corpus)
            ]
            if len(items) >= 2:
                blocks.append(blk.model_copy(update={"items": items, "text": None}))
        if blocks:
            cleaned_sections.append(sec.model_copy(update={"heading": heading, "blocks": blocks}))
    return cleaned_sections


def _needs_missing_data_slide(pack: ContentPack, brief: Brief) -> bool:
    """Нужен ли честный слайд «Каких данных не хватает» при нехватке фактов."""
    if pack.tables or pack.charts or pack.datasets:
        return False
    body_texts: list[str] = []
    for sec in pack.sections:
        if is_sources_heading(sec.heading) or sec.id == MISSING_DATA_SECTION_ID:
            continue
        for blk in sec.blocks:
            if blk.text:
                body_texts.append(blk.text.strip())
            if blk.items:
                body_texts.extend(i.strip() for i in blk.items if i and i.strip())
    joined = " ".join(body_texts).strip()
    purpose = (brief.purpose or "").strip()
    norm_joined = re.sub(r"[^\w]+", " ", joined.casefold()).strip()
    norm_purpose = re.sub(r"[^\w]+", " ", purpose.casefold()).strip()
    topic_only = bool(norm_joined) and norm_joined == norm_purpose
    has_user_numbers = bool(re.search(r"\d", joined))
    asks_for_data = bool(_DATA_SEEKING_RE.search(f"{purpose} {joined}")) and not has_user_numbers
    return topic_only or asks_for_data


def _missing_data_questions(pack: ContentPack, brief: Brief, language: str) -> list[str]:
    subject = extract_core_subject(brief.purpose or "", language)
    if language.startswith("ru"):
        subj_tail = f" ({subject})" if subject and len(subject) <= 35 else ""
        return [
            f"Финансовая отчётность{subj_tail}: приложите выручку, маржу и динамику "
            "по периодам (XLSX/CSV)",
            "Выгрузка продаж и сегментов: укажите доли категорий, каналов сбыта и филиалов",
            "Операционные метрики и цели: добавьте трафик, средний чек и плановые ориентиры",
        ]
    subj_tail = f" ({subject})" if subject and len(subject) <= 35 else ""
    return [
        f"Financial statements{subj_tail}: attach revenue, margin, and period tables (XLSX/CSV)",
        "Sales and segment breakdown: provide category, channel, and regional shares",
        "Operational metrics and targets: add traffic, average order value, and KPIs",
    ]


def _is_bare_topic_echo(sec: PackSection, brief: Brief) -> bool:
    norm_purpose = re.sub(r"[^\w]+", " ", (brief.purpose or "").casefold()).strip()
    if not norm_purpose:
        return False
    texts: list[str] = []
    for blk in sec.blocks:
        if blk.text:
            texts.append(blk.text.strip())
        if blk.items:
            texts.extend(i.strip() for i in blk.items if i and i.strip())
    norm_sec = re.sub(r"[^\w]+", " ", " ".join(texts).casefold()).strip()
    return bool(norm_sec) and norm_sec == norm_purpose


def _append_missing_data_section(
    filled: ContentPack, pack: ContentPack, brief: Brief, language: str
) -> tuple[ContentPack, list[str]]:
    questions = _missing_data_questions(pack, brief, language)
    if any(s.id == MISSING_DATA_SECTION_ID for s in filled.sections):
        return filled, questions
    heading = "Каких данных не хватает" if language.startswith("ru") else "What data is missing"
    section = PackSection(
        id=MISSING_DATA_SECTION_ID,
        heading=heading,
        level=2,
        blocks=[
            Block(
                kind=Kind.list,
                items=questions,
                source_ref=SourceRef(artifact_id=MISSING_DATA_ARTIFACT_ID),
            )
        ],
    )
    non_bib = [
        s
        for s in filled.sections
        if not is_sources_heading(s.heading) and not _is_bare_topic_echo(s, brief)
    ]
    bib = [s for s in filled.sections if is_sources_heading(s.heading)]
    return filled.model_copy(update={"sections": [*non_bib, section, *bib]}), questions


@dataclass
class FillOutcome:
    pack: ContentPack
    brief: Brief
    mode: ContentFill
    resolved: str
    before: ContentSufficiency
    after: ContentSufficiency
    needs_decision: bool = False
    web: WebResearch | None = None
    model_sections: int = 0
    elapsed_s: float = 0.0
    # ADR-032 §4: схемы, найденные в тексте до планирования
    diagrams_detected: int = 0
    # ADR-033 §5: «что значит / что следует» моделью для данных
    interpretation: dict | None = None
    missing_data_questions: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return (
            self.web is not None
            and bool(self.web.sections)
            or self.model_sections > 0
            or self.diagrams_detected > 0
            or bool(self.missing_data_questions)
            or bool(
                self.interpretation
                and (self.interpretation.get("interpreted") or self.interpretation.get("intent"))
            )
        )

    def to_dict(self) -> dict:
        origins = {"user": 0, "web": 0, "model": 0}
        by_source = {s.id: s.origin.value for s in self.pack.external_sources}
        for section in self.pack.sections:
            if section.id == MISSING_DATA_SECTION_ID:
                continue
            for block in section.blocks:
                count = len([t for t in [block.text, *(block.items or [])] if t])
                origins[by_source.get(block.source_ref.artifact_id, "user")] += count
        built_slides = min(self.after.capacity, self.brief.target_slide_count)
        shortfall_slides = max(0, self.before.requested - built_slides)
        is_ru = (self.brief.language or self.pack.language or "ru").startswith("ru")
        shortfall_reason: str | None = None
        if shortfall_slides > 0:
            req_n = self.before.requested
            if self.missing_data_questions:
                shortfall_reason = (
                    f"Подтверждённых данных хватило на {built_slides} из {req_n} "
                    "запрошенных слайдов; без внутренней отчётности и таблиц показателей "
                    "числа не додумываются."
                    if is_ru
                    else f"Verified material sufficed for {built_slides} of {req_n} "
                    "requested slides; internal statements or datasets are required."
                )
            elif self.resolved == "shrink":
                shortfall_reason = (
                    f"Колода сокращена до {built_slides} слайдов по фактическому объёму материала."
                    if is_ru
                    else f"Deck shortened to {built_slides} slides to match available material."
                )
            else:
                shortfall_reason = (
                    f"Доступного материала достаточно для {built_slides} из {req_n} "
                    "запрошенных слайдов без пустых разделителей и выдуманных фактов."
                    if is_ru
                    else f"Available material supports {built_slides} of {req_n} "
                    "requested slides without filler or fabricated facts."
                )
        honest_status = (
            "missing_data"
            if self.missing_data_questions and origins["web"] == 0 and origins["model"] == 0
            else "shortfall"
            if shortfall_slides > 0
            else "ok"
        )
        return {
            "mode": self.mode.value,
            "resolved": self.resolved,
            "honest_status": honest_status,
            "needs_decision": self.needs_decision,
            "requested_slides": self.before.requested,
            "target_slides": self.brief.target_slide_count,
            "built_slides": built_slides,
            "shortfall_slides": shortfall_slides,
            "shortfall_reason": shortfall_reason,
            "missing_data_questions": list(self.missing_data_questions),
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "facts_by_origin": origins,
            "web": self.web.to_dict() if self.web is not None else None,
            "model_sections": self.model_sections,
            "diagrams_detected": self.diagrams_detected,
            "interpretation": self.interpretation,
            "elapsed_s": round(self.elapsed_s, 2),
        }


def drop_sources(pack: ContentPack, source_ids: frozenset[str]) -> ContentPack:
    """ADR-034 §0: убрать из пака факты исключённых источников (крестик у
    источника на экране плана). Принимает id источника или его URL."""
    gone = {
        s.id for s in pack.external_sources if s.id in source_ids or (s.url and s.url in source_ids)
    }
    if not gone:
        return pack
    sections = []
    for section in pack.sections:
        blocks = [b for b in section.blocks if b.source_ref.artifact_id not in gone]
        if blocks:
            sections.append(section.model_copy(update={"blocks": blocks}))
    return pack.model_copy(
        update={
            "sections": sections,
            "external_sources": [s for s in pack.external_sources if s.id not in gone],
        }
    )


def _bibliography_entry(source: ExternalSource, language: str) -> str:
    date = ""
    if source.retrieved_at:
        try:
            date = datetime.fromisoformat(source.retrieved_at).strftime("%d.%m.%Y")
        except ValueError:
            date = ""
    title = source.title or source.publisher or source.url or ""
    publisher = source.publisher or (urlparse(source.url).hostname if source.url else "")
    if language.startswith("ru"):
        tail = f" (дата обращения: {date})" if date else ""
        return f"{title}. {publisher}. URL: {source.url}{tail}"
    tail = f" (accessed {date})" if date else ""
    return f"{title}. {publisher}. {source.url}{tail}"


def merge_into_pack(
    pack: ContentPack,
    sections: list[PackSection],
    sources: list[ExternalSource],
    language: str,
) -> ContentPack:
    """Добранные разделы — после пользовательских, до списка источников;
    веб-источники — записями в список источников (существующий или новый)."""
    if not sections and not sources:
        return pack
    user_sections = [s for s in pack.sections if not is_sources_heading(s.heading)]
    bibliography = [s for s in pack.sections if is_sources_heading(s.heading)]
    web_sources = [s for s in sources if s.url]
    if web_sources:
        entries = [
            Block(
                kind=Kind.list,
                items=[_bibliography_entry(src, language)],
                source_ref=SourceRef(artifact_id=src.id),
            )
            for src in web_sources
        ]
        if bibliography:
            first = bibliography[0]
            bibliography[0] = first.model_copy(update={"blocks": [*first.blocks, *entries]})
        else:
            bibliography = [
                PackSection(
                    id="sec-research-sources",
                    heading="Источники" if language.startswith("ru") else "Sources",
                    level=2,
                    blocks=entries,
                )
            ]
    known = {s.id for s in pack.external_sources}
    return pack.model_copy(
        update={
            "sections": [*user_sections, *sections, *bibliography],
            "external_sources": [
                *pack.external_sources,
                *(s for s in sources if s.id not in known),
            ],
        }
    )


async def fill_content(
    pack: ContentPack,
    brief: Brief,
    gateway: ModelGateway | None,
    *,
    config: GenerationConfig | None = None,
    research_config: ResearchConfig | None = None,
    ask_allowed: bool = False,
    search: SearchProvider | None = None,
    fetch=None,  # Callable[[str], Awaitable[Page | None]] — подмена загрузки в тестах
    on_event: Callable[[str, dict], None] | None = None,
    exclude_source_ids: list[str] | None = None,
) -> FillOutcome:
    """``on_event(type, data)`` — прогресс для живого экрана плана
    (``research.started|queries|source|model|finished``); вызывается
    синхронно, ошибки колбэка стадию не роняют."""
    started = time.monotonic()
    from deckdna.planning.request_constraints import resolve_count_goal

    brief = resolve_count_goal(brief)
    from deckdna.planning.mandatory import mandatory_anchors

    mandatory_anchors(pack, brief.mandatory_sections)

    def emit(event_type: str, data: dict) -> None:
        if on_event is None:
            return
        try:
            on_event(event_type, data)
        except Exception:  # noqa: BLE001 — журнал событий не важнее добора
            logger.exception("content fill event callback failed")

    cfg = config or load_generation_config()
    rc = research_config or load_research_config(cfg)
    excluded = frozenset(exclude_source_ids or ())
    if excluded:
        pack = drop_sources(pack, excluded)
    mode = resolve_content_fill(brief, rc.default_fill)
    # ADR-032 §4: процессы и этапы из текста — схемой, до оценки ёмкости
    # (схема — отдельный слайд-визуал) и до добора
    pack, diagrams_detected = await detect_diagrams(pack)
    before = estimate_content_capacity(pack, brief)
    # F-72b: достаточно исходного материала — модельный поиск схем до
    # проверки ёмкости мог потратить минуту впустую. Локальные схемы
    # сохраняются, модель помогает только при реальном дефиците.
    if before.status == Status.short and gateway is not None:
        pack, model_diagrams = await detect_diagrams(pack, gateway)
        diagrams_detected += model_diagrams
        if model_diagrams:
            before = estimate_content_capacity(pack, brief)

    def outcome(
        resolved: str,
        *,
        out_pack: ContentPack = pack,
        out_brief: Brief = brief,
        needs_decision: bool = False,
        web: WebResearch | None = None,
        model_sections: int = 0,
        missing_data_questions: list[str] | None = None,
    ) -> FillOutcome:
        after = (
            before
            if out_pack is pack and out_brief is brief
            else estimate_content_capacity(out_pack, out_brief)
        )
        result = FillOutcome(
            pack=out_pack,
            brief=out_brief,
            mode=mode,
            resolved=resolved,
            before=before,
            after=after,
            needs_decision=needs_decision,
            web=web,
            model_sections=model_sections,
            elapsed_s=time.monotonic() - started,
            diagrams_detected=diagrams_detected,
            missing_data_questions=list(missing_data_questions or ()),
        )
        logger.info(
            "content fill: mode=%s resolved=%s requested=%d capacity %d -> %d, target=%d",
            mode.value,
            resolved,
            before.requested,
            before.capacity,
            result.after.capacity,
            out_brief.target_slide_count,
        )
        emit(
            "research.finished",
            {
                "resolved": resolved,
                "needs_decision": needs_decision,
                "requested_slides": before.requested,
                "target_slides": out_brief.target_slide_count,
                "capacity_before": before.capacity,
                "capacity_after": result.after.capacity,
            },
        )
        return result

    if pack.datasets and before.status != Status.excess:
        # ADR-033 §8: история строится из данных и ответов пользователя —
        # добор фактов из интернета или знаний модели не нужен. Модель
        # (если есть) только формулирует смысл и следствие выводов (§5).
        from deckdna.analysis.interpret import classify_intent, interpret_pack
        from deckdna.planning.data_story import brief_text

        data_pack, intent = await classify_intent(pack, brief_text(brief), gateway)
        data_pack, interp = await interpret_pack(data_pack, brief, gateway)
        interp.intent = intent
        result = outcome("data", out_pack=data_pack)
        result.interpretation = interp.to_dict()
        return result
    if before.status == Status.ok:
        return outcome("none")
    if before.status == Status.excess:
        return outcome("condense", needs_decision=ask_allowed and mode == ContentFill.ask)
    if mode == ContentFill.ask and ask_allowed:
        return outcome("ask", needs_decision=True)
    if mode == ContentFill.shrink:
        return outcome("shrink", out_brief=shrink_target(brief, before, cfg.deck.min_slides))

    language = brief.language or pack.language or "ru"
    needs_missing_data = _needs_missing_data_slide(pack, brief)
    filled = pack
    missing_questions: list[str] = []

    if gateway is None or not (can_expand(gateway) or rc.web.available or search is not None):
        if needs_missing_data and MISSING_DATA_SECTION_ID not in excluded:
            filled, missing_questions = _append_missing_data_section(filled, pack, brief, language)
            current = estimate_content_capacity(filled, brief)
            return outcome(
                "no_model",
                out_pack=filled,
                out_brief=shrink_target(brief, current, cfg.deck.min_slides),
                missing_data_questions=missing_questions,
            )
        return outcome("no_model", out_pack=filled)

    deadline = started + rc.budget_s
    emit(
        "research.started",
        {
            "mode": mode.value,
            "requested_slides": before.requested,
            "capacity": before.capacity,
            "missing_slides": before.missing_slides,
            "web": bool(rc.web.available or search is not None),
        },
    )
    web: WebResearch | None = None
    if rc.web.available or search is not None:
        web = await research_web(
            pack,
            brief,
            gateway,
            rc.web,
            missing_slides=before.missing_slides,
            covered_topics=before.covered_topics,
            deadline=deadline,
            search=search,
            fetch=fetch,
            on_event=emit,
            exclude_source_ids=excluded,
        )
        filled = merge_into_pack(filled, web.sections, web.sources, language)

    model_sections = 0
    current = estimate_content_capacity(filled, brief)
    allow_model = (
        rc.model.enabled
        and can_expand(gateway)
        and (not needs_missing_data or getattr(gateway, "provider_name", None) == "mock")
    )
    rounds = 0
    while (
        current.status == Status.short
        and allow_model
        and rounds < _MAX_MODEL_ROUNDS
        and time.monotonic() < deadline
    ):
        rounds += 1
        needed = max(1, math.ceil(current.missing_slides / _SLOTS_PER_MODEL_SECTION))
        sections, source = await expand_with_model(
            filled,
            brief,
            gateway,
            sections_needed=needed,
            bullets_per_section=rc.model.bullets_per_section,
            covered_topics=[s.heading for s in filled.sections if s.heading],
        )
        if not sections or source is None:
            break
        sections = _sanitize_model_sections(sections, pack, brief)
        if not sections:
            break
        model_sections += len(sections)
        emit("research.model", {"sections": [s.heading for s in sections]})
        filled = merge_into_pack(filled, sections, [source], language)
        current = estimate_content_capacity(filled, brief)

    if current.status == Status.short:
        if needs_missing_data and MISSING_DATA_SECTION_ID not in excluded:
            filled, missing_questions = _append_missing_data_section(filled, pack, brief, language)
            current = estimate_content_capacity(filled, brief)
        out_brief = (
            shrink_target(brief, current, cfg.deck.min_slides) if filled is not pack else brief
        )
        return outcome(
            "expand_partial" if filled is not pack else "expand_failed",
            out_pack=filled,
            out_brief=out_brief,
            web=web,
            model_sections=model_sections,
            missing_data_questions=missing_questions,
        )
    return outcome("expand", out_pack=filled, web=web, model_sections=model_sections)


__all__ = [
    "MISSING_DATA_ARTIFACT_ID",
    "MISSING_DATA_SECTION_ID",
    "FillOutcome",
    "Page",
    "fill_content",
    "merge_into_pack",
]
