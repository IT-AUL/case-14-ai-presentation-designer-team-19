"""Когда собирать слайд собственной композицией, а когда — эталоном шаблона.

Эксперты задачи прямо назвали приоритет: «умение создавать новые композиции
в рамках дизайн-системы» важнее точного воспроизведения готовых лейаутов.
``native_compose`` строит такие композиции (карточки, процесс, KPI, две
колонки, список) на «раме» слайда шаблона — его заголовке, фоне, логотипах —
токенами шаблона (шрифты, цвета, скругления, поля).

Решение детерминировано и задаёт ось различий трёх вариантов:

* **faithful** — эталоны шаблона; своя композиция только там, где эталон
  уже занят другим слайдом этой колоды (повтор одного и того же слайда
  шаблона выглядит как копипаст);
* **balanced** — эталон, только если его карточек ровно столько, сколько
  пунктов (идеальное совпадение формы); иначе своя композиция;
* **visual** — своя композиция для всех текстовых контентных слайдов,
  с упором на процесс и KPI.

Обложка, разделы и финал всегда остаются слайдами шаблона — это его
собственный дизайн. Слайды с таблицами, графиками, диаграммами и картинками
идут эталонами (их носители — в шаблоне).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from deckdna.contracts.deck_plan import Kind, Purpose, SlidePlan
from deckdna.contracts.variant_spec import Strategy
from deckdna.pptx.composing.card_reflow import _LABEL_SPLIT, card_slots, split_label
from deckdna.pptx.composing.native_compose import Box, choose_archetype, slide_frame
from deckdna.pptx.composing.text_replace import count_content_slots
from deckdna.pptx.opc.package import OpcPackage

# Слайды-«рамки» колоды: у шаблона для них свой дизайн
FRAME_PURPOSES = frozenset(
    {Purpose.title, Purpose.section_divider, Purpose.thank_you, Purpose.qa, Purpose.cta}
)
_PROCESS_PURPOSES = frozenset({Purpose.process, Purpose.timeline})
_MAX_ITEMS = 7
# «значение» KPI: число с единицей, стоящее вместо заголовка пункта
_VALUE_RE = re.compile(
    r"^[~≈<>+−-]?\s*\d[\d\s.,]*\s*(%|x|×|трлн|млрд|млн|тыс|руб\.?|₽|\$|€|раз[а]?|ч|мин|с|дн[ея]й?)?$",
    re.I,
)
_INLINE_KPI_RE = re.compile(
    r"(?<!\d)(?:[~≈<>+−-]\s*)?\d+(?:[\s.,]\d+)*\s*"
    r"(?:%|x|×|(?:трлн|млрд|млн|тыс)\.?(?:\s*(?:руб\.?|₽|\$|€|долл\.?))?|руб\.?|₽|\$|€|долл\.?|п\.?\s*п\.?)",
    re.I,
)


@dataclass(frozen=True)
class Frame:
    part: str
    xml: bytes
    region: Box
    tokens: dict


def _dep(pkg: OpcPackage, part: str, type_name: str) -> str | None:
    for rel, target in pkg.internal_dependencies(part):
        if rel.type_name == type_name and target in pkg.parts:
            return target
    return None


_Xml = bytes | None


def frame_context(pkg: OpcPackage, slide_part: str) -> tuple[_Xml, _Xml, _Xml]:
    """(layout, master, theme) XML слайда — для рамки и токенов."""
    layout = _dep(pkg, slide_part, "slideLayout")
    master = _dep(pkg, layout, "slideMaster") if layout else None
    theme = _dep(pkg, master, "theme") if master else None
    return tuple(pkg.parts.get(p) if p else None for p in (layout, master, theme))  # type: ignore[return-value]


def build_frame(pkg: OpcPackage, slide_part: str, slide_w: int, slide_h: int) -> Frame | None:
    layout, master, theme = frame_context(pkg, slide_part)
    try:
        xml, region, tokens = slide_frame(
            pkg.parts[slide_part],
            slide_w,
            slide_h,
            layout_xml=layout,
            master_xml=master,
            theme_xml=theme,
        )
    except (ValueError, KeyError):
        return None
    return Frame(slide_part, xml, region, tokens)


def best_frame(pkg: OpcPackage, candidates: list[str], slide_w: int, slide_h: int) -> Frame | None:
    """Лучшая рама колоды: без графики макета под контентом
    (``inherited_clutter == 0``) и с наибольшей свободной областью."""
    best: Frame | None = None
    best_key: tuple[float, float] | None = None
    for part in dict.fromkeys(candidates):
        frame = build_frame(pkg, part, slide_w, slide_h)
        if frame is None or frame.region.w <= 0 or frame.region.h <= 0:
            continue
        clutter = float(frame.tokens.get("inherited_clutter") or 0.0)
        area = (frame.region.w * frame.region.h) / float(slide_w * slide_h)
        if area < 0.25:
            continue
        key = (-clutter, area)
        if best_key is None or key > best_key:
            best, best_key = frame, key
    if best is not None and float(best.tokens.get("inherited_clutter") or 0.0) > 0.05:
        return None  # чистой рамы нет — своя композиция легла бы на графику макета
    return best


def slide_items(slide: SlidePlan) -> list[dict] | None:
    """Пункты слайда для композиции; None — слайд не чисто текстовый."""
    items: list[dict] = []
    for unit in slide.content_units:
        if unit.kind == Kind.title or unit.role == "footnote" or unit.kind == Kind.note:
            continue
        if unit.kind not in (Kind.bullet, Kind.paragraph, Kind.subtitle, Kind.quote):
            return None
        if not unit.text or not unit.text.strip():
            continue
        label, body = split_label(unit.text)
        if label and _VALUE_RE.match(label.strip()):
            items.append({"heading": None, "body": body, "value": label.strip()})
        else:
            if label is not None and (m := _LABEL_SPLIT.match(unit.text.strip())):
                # «Термин: определение» — разделитель остаётся в заголовке,
                # иначе «Термин определение» искажает текст пользователя
                sep = unit.text.strip()[m.end("label") : m.start("body")].strip()
                if sep:
                    label = f"{label}{':' if sep == ':' else f' {sep}'}"
            items.append({"heading": label, "body": body, "value": None})
    if not items or len(items) > _MAX_ITEMS:
        return None
    # Если фактов <= 3, выделяем крупные числа-показатели в value для карточек KPI
    if 1 <= len(items) <= 3 and slide.purpose != Purpose.agenda:
        for it in items:
            if not it["value"]:
                source_txt = f"{it.get('heading') or ''} {it.get('body') or ''}".strip()
                m_kpi = _INLINE_KPI_RE.search(source_txt)
                if m_kpi:
                    it["value"] = re.sub(r"\s+", " ", m_kpi.group(0).strip())
    # KPI-подпись: заголовок из тела, если у значения нет своего
    for it in items:
        if it["value"] and not it["heading"] and it["body"]:
            head, sep, rest = it["body"].partition(" — ")
            if rest and len(head.split()) <= 4:
                # разделитель остаётся в подписи — текст не искажаем
                it["heading"], it["body"] = head + sep, rest
    return items


def archetype_hint(
    slide: SlidePlan, strategy: Strategy, items: list[dict] | None = None
) -> str | None:
    if slide.purpose in _PROCESS_PURPOSES:
        return "process"
    if items is not None and 1 <= len(items) <= 3 and slide.purpose != Purpose.agenda:
        if any(it.get("value") for it in items):
            return "kpi"
        return "cards"
    if strategy == Strategy.visual and slide.purpose in {Purpose.solution, Purpose.overview}:
        return "process" if slide.purpose == Purpose.solution else None
    return None


def layout_family_cost(
    fit: float,
    family: str,
    history: list[str] | tuple[str, ...],
) -> float:
    """Формула штрафа выбора семейства макета:
    ``cost = fit + 0.8*(family == prev) + 0.3*count_in_last_4``."""
    prev = history[-1] if history else None
    last_4 = history[-4:] if history else ()
    same_as_prev = 1.0 if (prev is not None and family == prev) else 0.0
    count_in_last_4 = sum(1 for item in last_4 if item == family)
    return round(float(fit) + 0.8 * same_as_prev + 0.3 * count_in_last_4, 6)


def select_layout_family(
    candidate_fits: dict[str, float] | list[tuple[str, float]] | tuple[tuple[str, float], ...],
    history: list[str] | tuple[str, ...] = (),
    *,
    chart_type: str | None = None,
) -> str:
    """Выбрать семейство макета с минимальным ``cost`` без смены ``chart_type``.

    Три одинаковых семейства подряд запрещены, если есть подходящая альтернатива.
    """
    from deckdna.planning.story_rules import select_layout_family as _select_layout_family

    return _select_layout_family(candidate_fits, history, chart_type=chart_type)


def select_layout_family_sequence(
    slides_candidate_fits: list[dict[str, float] | list[tuple[str, float]]],
    *,
    chart_types: list[str | None] | None = None,
) -> list[str]:
    """Последовательный выбор семейств макетов для серии слайдов."""
    from deckdna.planning.story_rules import (
        select_layout_family_sequence as _select_layout_family_sequence,
    )

    return _select_layout_family_sequence(slides_candidate_fits, chart_types=chart_types)


def _compatible_native_archetypes(
    items: list[dict],
    primary: str,
) -> dict[str, float]:
    """Подходящие нативные семейства для пунктов слайда и их базовый ``fit``."""
    n = len(items)
    has_values = any(it.get("value") for it in items)
    has_headings = any(it.get("heading") for it in items)
    candidates: dict[str, float] = {primary: 0.0}
    if has_values and n <= 4:
        candidates.setdefault("kpi", 0.15)
    if 2 <= n <= 6:
        candidates.setdefault("cards", 0.15 if has_headings else 0.25)
    if 3 <= n <= 6:
        candidates.setdefault("process", 0.25)
    if n == 2:
        candidates.setdefault("two_column", 0.20)
    if n >= 2:
        candidates.setdefault("list", 0.30)
    return candidates


def decide_native(
    slide: SlidePlan,
    exemplar_xml: bytes,
    exemplar_part: str,
    used_before: set[str],
    strategy: Strategy,
    slide_w: int,
    slide_h: int,
    *,
    family_history: list[str] | tuple[str, ...] = (),
) -> tuple[str, list[dict]] | None:
    """(архетип, пункты), если слайд собирается своей композицией."""
    if slide.purpose in FRAME_PURPOSES:
        return None
    items = slide_items(slide)
    if items is None:
        return None
    duplicate = exemplar_part in used_before
    sparse_facts = 1 <= len(items) <= 3 and slide.purpose != Purpose.agenda
    has_kpi_values = sparse_facts and any(it.get("value") for it in items)
    is_facts_slide = sparse_facts and (
        "факт" in (slide.title_intent or "").casefold()
        or "fact" in (slide.title_intent or "").casefold()
        or any(
            getattr(u.origin, "value", u.origin) == "web"
            for u in slide.content_units
            if u.kind != Kind.title
        )
    )
    if strategy == Strategy.faithful:
        n_cards, _ = card_slots(exemplar_xml, slide_w, slide_h)
        if not duplicate and not (is_facts_slide and not n_cards):
            return None
    elif strategy != Strategy.visual:
        n_cards, per_card = card_slots(exemplar_xml, slide_w, slide_h)
        # Если фактов <= 3, не оставляем их мелким списком на пустом поле:
        # подходят только реальные карточки эталона (а для KPI — свой архетип kpi).
        if is_facts_slide:
            fits = (
                not has_kpi_values
                and bool(n_cards)
                and per_card in (1, 2)
                and n_cards * per_card >= len(items)
            )
        else:
            fits = (
                bool(n_cards) and per_card in (1, 2) and n_cards * per_card >= len(items)
            ) or count_content_slots(exemplar_xml, (slide_w, slide_h)) >= len(items)
        if fits and not duplicate:
            return None
    hint = archetype_hint(slide, strategy, items)
    primary = choose_archetype(items, hint)
    if family_history:
        cand_fits = _compatible_native_archetypes(items, primary)
        chosen = select_layout_family(cand_fits, family_history)
        return chosen, items
    return primary, items
