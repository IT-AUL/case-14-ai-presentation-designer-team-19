"""Достаточность контента для запрошенного числа слайдов (ADR-030 §1–2).

Детерминированно и без модели отвечает на вопрос «на сколько слайдов
хватает того, что дал пользователь», чтобы до планирования решить:
добирать контент (``expand``), сократить колоду (``shrink``) или спросить
пользователя (``ask``).

Ёмкость считается по плотности, а не по числу юнитов: планировщик умеет
резать текст до одного предложения на слайд, но слайд с одной строкой —
та же заглушка. Слайд «стоит» ``MIN_SLIDE_CHARS`` символов текста
(примерно два содержательных пункта); таблица, график, схема и картинка
— отдельный слайд каждый. Рамки колоды (обложка, план, финал) в ёмкость
входят всегда — их контент даёт сам бриф.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum

from deckdna.contracts.content_pack import ContentPack, ExternalSource
from deckdna.contracts.deck_plan import (
    Brief,
    ContentFill,
    ContentUnit,
    DeckPlan,
    Kind,
    Origin,
    Purpose,
)
from deckdna.contracts.deck_plan import (
    SlidePlan as SlideSpec,
)
from deckdna.planning.evidence import build_evidence_graph
from deckdna.planning.story_director import content_groups

# ~два содержательных пункта: меньше — слайд выглядит пустым
MIN_SLIDE_CHARS = 180
# во сколько раз ёмкость должна превышать запрос, чтобы предложить
# увеличить колоду вместо ужимания
EXCESS_RATIO = 1.5

_STRUCTURED_KINDS = frozenset({Kind.table, Kind.chart, Kind.diagram, Kind.image})
_SOURCES_HEADINGS = (
    "источники",
    "список источников",
    "литература",
    "references",
    "bibliography",
    "sources",
)


def is_sources_heading(heading: str | None) -> bool:
    """Раздел-список источников: в колоде это один слайд источников, а не
    содержание, сколько бы записей в нём ни было."""
    return bool(heading) and heading.casefold().strip().startswith(_SOURCES_HEADINGS)


_SENTENCE_ENDS = (".", "!", "?", "…")
_ASSUMPTION_PREFIX_RE = re.compile(
    r"^\s*(?:предположение|гипотеза|допущение|иллюстративный\s+расч[её]т|assumption|hypothesis)\s*:\s*",
    re.IGNORECASE,
)
_CONT_SUFFIX_RE = re.compile(
    r"\s*(?:[—–-]\s*(?:часть\s*\d+|продолжение|part\s*\d+|continued)"
    r"|\(\s*(?:продолжение|часть\s*\d+|continued|part\s*\d+)\s*\))\s*$",
    re.IGNORECASE,
)
_BOOKEND_PURPOSES = frozenset(
    {
        Purpose.title,
        Purpose.agenda,
        Purpose.section_divider,
        Purpose.thank_you,
        Purpose.qa,
        Purpose.cta,
    }
)
_CLOSING_PURPOSES = frozenset({Purpose.thank_you, Purpose.qa, Purpose.cta})


class Status(Enum):
    ok = "ok"
    short = "short"
    excess = "excess"


@dataclass(frozen=True)
class ContentSufficiency:
    requested: int
    capacity: int
    status: Status
    covered_topics: list[str] = field(default_factory=list)

    @property
    def missing_slides(self) -> int:
        return max(self.requested - self.capacity, 0)

    def to_dict(self) -> dict:
        return {
            "requested": self.requested,
            "capacity": self.capacity,
            "status": self.status.value,
            "missing_slides": self.missing_slides,
            "covered_topics": list(self.covered_topics),
        }


def _pieces(text: str) -> int:
    """Сколько частей можно получить из текста, не разрывая предложений."""
    return max(1, sum(text.count(end) for end in _SENTENCE_ENDS))


def estimate_content_capacity(pack: ContentPack, brief: Brief) -> ContentSufficiency:
    """Сколько полноценных слайдов даёт контент пака под этот бриф."""
    groups = content_groups(pack)
    content_sections = [(sec, units) for sec, units in groups if units]
    active_sections = [
        (sec, units)
        for sec, units in content_sections
        if not is_sources_heading(sec.heading) or len(units) > 3
    ]
    has_agenda = brief.target_slide_count >= 5 and len(active_sections) >= 2
    frame = 2 + int(has_agenda)  # обложка + финал (+ план)

    slots = 0
    for sec, units in content_sections:
        if is_sources_heading(sec.heading):
            if len(units) > 3:
                slots += 1
            continue
        if sec.id == "sec-missing-data":
            slots += 1
            continue
        structured = sum(1 for u in units if u.kind in _STRUCTURED_KINDS)
        texts = [u.text for u in units if u.kind not in _STRUCTURED_KINDS and u.text]
        chars = sum(len(t) for t in texts)
        text_slots = 0
        if chars:
            # не больше, чем текст физически делится без разрыва фраз
            pieces = sum(_pieces(t) for t in texts)
            text_slots = min(max(1, math.floor(chars / MIN_SLIDE_CHARS)), pieces)
        slots += structured + text_slots

    if pack.datasets:
        # ADR-033 §8: колода от выводов — «Ключевые выводы» + слайд на
        # представление данных (без справки)
        from deckdna.analysis.pipeline import analyze_pack

        views = sum(1 for a in analyze_pack(pack) for va in a.views if va.view.shape != "reference")
        slots += views + int(views >= 2)
    capacity = frame + slots if slots else frame
    requested = brief.target_slide_count
    if capacity < requested:
        status = Status.short
    elif capacity > requested * EXCESS_RATIO:
        status = Status.excess
    else:
        status = Status.ok
    from deckdna.planning.story_director import clean_purpose_text

    return ContentSufficiency(
        requested=requested,
        capacity=capacity,
        status=status,
        covered_topics=[h for sec, _ in content_sections if (h := clean_purpose_text(sec.heading))],
    )


def resolve_content_fill(brief: Brief, default: str = "expand") -> ContentFill:
    """Режим брифа или дефолт конфига (``content_fill.default``)."""
    return brief.content_fill or ContentFill(default)


def shrink_target(brief: Brief, sufficiency: ContentSufficiency, min_slides: int) -> Brief:
    """Бриф с числом слайдов по фактической ёмкости контента."""
    if brief.slide_count_policy == "exact":
        # Capacity is a heuristic; let the planner try the real material first.
        return brief
    target = max(min(sufficiency.capacity, brief.target_slide_count), min_slides)
    if target == brief.target_slide_count:
        return brief
    return brief.model_copy(update={"target_slide_count": target})


_ORIGIN_RANK = {"user": 0, "web": 1, "model": 2}


def _clean_assumption_text(text: str | None) -> str | None:
    if not text:
        return text
    cleaned = _ASSUMPTION_PREFIX_RE.sub("", text).strip()
    if cleaned and cleaned != text and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def _strip_slide_assumptions(slide: SlideSpec) -> tuple[SlideSpec, bool]:
    if any(u.locked for u in slide.content_units):
        return slide, False
    new_title = _clean_assumption_text(slide.title_intent) or slide.title_intent
    new_key = _clean_assumption_text(slide.key_message) or slide.key_message
    units_changed = False
    new_units: list[ContentUnit] = []
    for u in slide.content_units:
        ct = _clean_assumption_text(u.text)
        if ct != u.text:
            new_units.append(u.model_copy(update={"text": ct}))
            units_changed = True
        else:
            new_units.append(u)
    if new_title == slide.title_intent and new_key == slide.key_message and not units_changed:
        return slide, False
    return (
        slide.model_copy(
            update={
                "title_intent": new_title,
                "key_message": new_key,
                "content_units": new_units,
            }
        ),
        True,
    )


def _bibliography_entries(pack: ContentPack, slides: list[SlideSpec]) -> list[str]:
    entries: list[str] = []
    seen: set[str] = set()
    for sec in pack.sections:
        if not is_sources_heading(sec.heading):
            continue
        for blk in sec.blocks:
            for item in blk.items or ([blk.text] if blk.text else []):
                norm = (item or "").strip()
                if norm and norm.casefold() not in seen:
                    seen.add(norm.casefold())
                    entries.append(norm)
    for s in slides:
        if not is_sources_heading(s.title_intent) or s.purpose == Purpose.section_divider:
            continue
        for u in s.content_units:
            if u.kind == Kind.title or u.role == "footnote":
                continue
            norm = (u.text or "").strip()
            if norm and norm.casefold() not in seen:
                seen.add(norm.casefold())
                entries.append(norm)
    return entries


def _attach_footnote(
    slide: SlideSpec,
    text: str,
    *,
    origin: Origin | None,
    source_id: str | None,
) -> SlideSpec:
    if not text:
        return slide
    if any(u.role == "footnote" and u.text == text for u in slide.content_units):
        return slide
    ev = (
        list(slide.evidence_ids[:1])
        or [eid for u in slide.content_units for eid in (u.evidence_ids or [])[:1]][:1]
    )
    note_unit = ContentUnit(
        role="footnote",
        kind=Kind.note,
        text=text,
        origin=origin,
        source_id=source_id,
        evidence_ids=ev,
    )
    return slide.model_copy(update={"content_units": [*slide.content_units, note_unit]})


def _reconcile_sources_slides(
    slides: list[SlideSpec],
    pack: ContentPack,
    language: str,
    *,
    allow_drop: bool,
) -> tuple[list[SlideSpec], bool]:
    # Не трогаем слайд источников, явно добавленный точечным ресёрчем слайда (/slides/{id}/research)
    if any(s.id.startswith("slide-sources-") for s in slides):
        return slides, False

    web_sources: list[ExternalSource] = [
        s for s in pack.external_sources if s.origin.value == "web" and s.url
    ]
    bib_entries = _bibliography_entries(pack, slides)
    n_sources = max(len(web_sources), len(bib_entries))
    has_source_slides = any(is_sources_heading(s.title_intent) for s in slides)
    if n_sources == 0 and not has_source_slides:
        return slides, False

    src_by_id = {s.id: s for s in pack.external_sources if s.url or s.origin.value == "web"}
    changed = False

    if n_sources <= 3:
        from deckdna.research.citations import format_source_footnote

        has_other_content = any(
            s.purpose not in _BOOKEND_PURPOSES and not is_sources_heading(s.title_intent)
            for s in slides
        )
        out: list[SlideSpec] = []
        footnote_attached = False
        for s in slides:
            if is_sources_heading(s.title_intent) and allow_drop and has_other_content:
                changed = True
                continue
            if (
                s.purpose not in _BOOKEND_PURPOSES
                and not is_sources_heading(s.title_intent)
                and n_sources > 0
            ):
                used_srcs = [
                    src_by_id[u.source_id]
                    for u in s.content_units
                    if u.source_id and u.source_id in src_by_id
                ]
                if not used_srcs and any(u.origin == Origin.web for u in s.content_units):
                    used_srcs = list(web_sources)
                if used_srcs:
                    fn_text = format_source_footnote(used_srcs, language=language)
                    updated = _attach_footnote(
                        s,
                        fn_text,
                        origin=Origin.web,
                        source_id=used_srcs[0].id,
                    )
                    if updated is not s:
                        changed = True
                    s = updated
                    footnote_attached = True
            out.append(s)

        if n_sources > 0 and not footnote_attached:
            candidates = [
                i
                for i, s in enumerate(out)
                if s.purpose not in _BOOKEND_PURPOSES
                and not is_sources_heading(s.title_intent)
                and "не хватает" not in (s.title_intent or "").casefold()
                and "missing" not in (s.title_intent or "").casefold()
            ]
            if not candidates:
                candidates = [
                    i
                    for i, s in enumerate(out)
                    if s.purpose not in _BOOKEND_PURPOSES and not is_sources_heading(s.title_intent)
                ]
            if candidates:
                idx = candidates[0]
                if web_sources:
                    fn_text = format_source_footnote(web_sources, language=language)
                    origin = Origin.web
                    sid = web_sources[0].id
                else:
                    if language.startswith("ru"):
                        prefix = "Источник: " if len(bib_entries) == 1 else "Источники: "
                    else:
                        prefix = "Source: " if len(bib_entries) == 1 else "Sources: "
                    fn_text = prefix + "; ".join(bib_entries)
                    origin = None
                    sid = None
                updated = _attach_footnote(out[idx], fn_text, origin=origin, source_id=sid)
                if updated is not out[idx]:
                    out[idx] = updated
                    changed = True

        if len(out) >= 3:
            return out, changed
        return slides, False

    # n_sources > 3: один слайд «Источники», без пустого разделителя перед ним
    source_content_slides: list[SlideSpec] = []
    non_source_slides: list[SlideSpec] = []
    first_source_pos: int | None = None
    for s in slides:
        if is_sources_heading(s.title_intent):
            body_units = [u for u in s.content_units if u.kind != Kind.title]
            if s.purpose == Purpose.section_divider or not body_units:
                if allow_drop:
                    changed = True
                    continue
            if first_source_pos is None:
                first_source_pos = len(non_source_slides)
            source_content_slides.append(s)
        else:
            non_source_slides.append(s)

    if not source_content_slides:
        if changed and len(non_source_slides) >= 3:
            return non_source_slides, True
        return slides, False

    clean_heading = "Источники" if language.startswith("ru") else "Sources"
    primary = source_content_slides[0]
    merged_units: list[ContentUnit] = []
    seen_texts: set[str] = set()
    merged_ev: list[str] = list(primary.evidence_ids)
    title_added = False
    for s in source_content_slides:
        for eid in s.evidence_ids:
            if eid not in merged_ev:
                merged_ev.append(eid)
        for u in s.content_units:
            if u.kind == Kind.title:
                if not title_added:
                    merged_units.append(u.model_copy(update={"text": clean_heading}))
                    title_added = True
                continue
            key = (u.text or "").strip().casefold()
            if key and key not in seen_texts:
                seen_texts.add(key)
                merged_units.append(u)
    if len(source_content_slides) > 1 or primary.title_intent != clean_heading:
        changed = True
    merged_slide = primary.model_copy(
        update={
            "title_intent": clean_heading,
            "content_units": merged_units,
            "evidence_ids": merged_ev,
        }
    )
    pos = first_source_pos if first_source_pos is not None else len(non_source_slides)
    out = [*non_source_slides[:pos], merged_slide, *non_source_slides[pos:]]
    return out, changed


def _prune_dividers_and_clean_titles(
    slides: list[SlideSpec],
    *,
    allow_drop: bool,
) -> tuple[list[SlideSpec], bool]:
    changed = False
    out = list(slides)

    if allow_drop and any(s.purpose == Purpose.section_divider for s in out):
        # 1) В разделе после разделителя должно быть >= 2 содержательных слайдов
        valid_divider_ids: set[str] = set()
        for idx, s in enumerate(out):
            if s.purpose != Purpose.section_divider:
                continue
            section_content_count = 0
            for nxt in out[idx + 1 :]:
                if nxt.purpose == Purpose.section_divider or nxt.purpose in _CLOSING_PURPOSES:
                    break
                if nxt.purpose not in _BOOKEND_PURPOSES:
                    section_content_count += 1
            if section_content_count >= 2:
                valid_divider_ids.add(s.id)

        filtered = [
            s for s in out if s.purpose != Purpose.section_divider or s.id in valid_divider_ids
        ]
        # 2) Разделители разделов допустимы только когда итоговая колода >= 8 слайдов
        if len(filtered) < 8:
            filtered = [s for s in filtered if s.purpose != Purpose.section_divider]
        if len(filtered) != len(out) and len(filtered) >= 3:
            out = filtered
            changed = True

    # Если после сокращения остался всего 1 содержательный слайд, слайд повестки не нужен
    if allow_drop:
        content_count = sum(1 for s in out if s.purpose not in _BOOKEND_PURPOSES)
        if content_count < 2 and len(out) > 3:
            without_agenda = [
                s
                for s in out
                if s.purpose != Purpose.agenda or any(u.locked for u in s.content_units)
            ]
            if len(without_agenda) != len(out) and len(without_agenda) >= 3:
                out = without_agenda
                changed = True

    # 3) Убираем сиротские суффиксы «— часть 2» / «— продолжение», если до слайда
    # не было содержательного слайда с тем же базовым заголовком
    seen_base_titles: set[str] = set()
    cleaned_slides: list[SlideSpec] = []
    for s in out:
        title = s.title_intent or ""
        if _CONT_SUFFIX_RE.search(title):
            base = _CONT_SUFFIX_RE.sub("", title).strip()
            if base and base.casefold() not in seen_base_titles:
                new_units = [
                    u.model_copy(update={"text": base})
                    if u.kind == Kind.title and u.text == title
                    else u
                    for u in s.content_units
                ]
                s = s.model_copy(update={"title_intent": base, "content_units": new_units})
                title = base
                changed = True
        if s.purpose != Purpose.section_divider and title:
            base_key = _CONT_SUFFIX_RE.sub("", title).strip().casefold()
            if base_key:
                seen_base_titles.add(base_key)
        cleaned_slides.append(s)

    return cleaned_slides, changed


def annotate_unit_origins(plan: DeckPlan, pack: ContentPack) -> DeckPlan:
    """``ContentUnit.origin`` по фактам, на которые ссылается пункт (для
    меток «ваш контент / интернет / знания модели» на экране плана), плюс
    честная нормализация источников и разделителей разделов."""
    changed = False
    slides: list[SlideSpec] = []
    for s in plan.slides:
        cleaned_s, did_clean = _strip_slide_assumptions(s)
        if did_clean:
            changed = True
        slides.append(cleaned_s)

    if pack.external_sources:
        by_source = {s.id: s.origin.value for s in pack.external_sources}
        origin_of: dict[str, str] = {}
        # ADR-034 §0: id внешнего источника за пунктом (бейдж «интернет» со ссылкой)
        source_of: dict[str, str] = {}
        graph = build_evidence_graph(pack)
        for node in graph.nodes:
            origin_of[node.id] = node.origin.value if node.origin else "user"
            if node.source_ref.artifact_id in by_source:
                source_of[node.id] = node.source_ref.artifact_id
        # id детерминированного планировщика: <pack>:<section>[:b<i>]
        for section in pack.sections:
            section_origins = []
            section_sources = []
            for bi, block in enumerate(section.blocks):
                origin = by_source.get(block.source_ref.artifact_id, "user")
                origin_of[f"{pack.id}:{section.id}:b{bi}"] = origin
                if block.source_ref.artifact_id in by_source:
                    source_of[f"{pack.id}:{section.id}:b{bi}"] = block.source_ref.artifact_id
                    section_sources.append(block.source_ref.artifact_id)
                section_origins.append(origin)
            if section_origins:
                origin_of[f"{pack.id}:{section.id}"] = max(
                    section_origins, key=_ORIGIN_RANK.__getitem__
                )
            if section_sources:
                source_of[f"{pack.id}:{section.id}"] = section_sources[0]

        def unit_origin(unit: ContentUnit) -> Origin | None:
            found = [origin_of[e] for e in unit.evidence_ids or [] if e in origin_of]
            if not found:
                return unit.origin
            return Origin(max(found, key=_ORIGIN_RANK.__getitem__))

        def unit_source(unit: ContentUnit) -> str | None:
            found = [source_of[e] for e in unit.evidence_ids or [] if e in source_of]
            return found[0] if found else unit.source_id

        slides = [
            slide.model_copy(
                update={
                    "content_units": [
                        u
                        if u.kind == Kind.title and unit_origin(u) != Origin.model
                        else u.model_copy(
                            update={"origin": unit_origin(u), "source_id": unit_source(u)}
                        )
                        for u in slide.content_units
                    ]
                }
            )
            for slide in slides
        ]
        changed = True

    allow_drop = plan.brief.slide_count_policy != "exact"
    slides, src_changed = _reconcile_sources_slides(
        slides,
        pack,
        plan.language or pack.language or "ru",
        allow_drop=allow_drop,
    )
    if src_changed:
        changed = True

    slides, div_changed = _prune_dividers_and_clean_titles(slides, allow_drop=allow_drop)
    if div_changed:
        changed = True

    if not changed:
        return plan

    reindexed = [
        s if s.index == idx else s.model_copy(update={"index": idx}) for idx, s in enumerate(slides)
    ]
    kept_ids = {s.id for s in reindexed}
    new_sections = [
        sec.model_copy(update={"slide_ids": [sid for sid in sec.slide_ids if sid in kept_ids]})
        for sec in (plan.sections or [])
    ]
    new_sections = [sec for sec in new_sections if sec.slide_ids]
    new_brief = plan.brief
    if allow_drop and len(reindexed) >= 3 and len(reindexed) != plan.brief.target_slide_count:
        new_brief = plan.brief.model_copy(update={"target_slide_count": len(reindexed)})
    return plan.model_copy(
        update={
            "slides": reindexed,
            "sections": new_sections if plan.sections is not None else None,
            "brief": new_brief,
        }
    )
