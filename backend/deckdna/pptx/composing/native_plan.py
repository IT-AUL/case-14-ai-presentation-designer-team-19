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
from deckdna.pptx.composing.card_reflow import card_slots, split_label
from deckdna.pptx.composing.native_compose import Box, choose_archetype, slide_frame
from deckdna.pptx.opc.package import OpcPackage

# Слайды-«рамки» колоды: у шаблона для них свой дизайн
FRAME_PURPOSES = frozenset(
    {Purpose.title, Purpose.section_divider, Purpose.thank_you, Purpose.qa, Purpose.cta}
)
_PROCESS_PURPOSES = frozenset({Purpose.process, Purpose.timeline})
_MAX_ITEMS = 7
# «значение» KPI: число с единицей, стоящее вместо заголовка пункта
_VALUE_RE = re.compile(
    r"^[~≈<>+−-]?\s*\d[\d\s.,]*\s*(%|x|×|млн|млрд|тыс|₽|\$|раз[а]?|ч|мин|с|дн[ея]й?)?$",
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


def best_frame(
    pkg: OpcPackage, candidates: list[str], slide_w: int, slide_h: int
) -> Frame | None:
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
        if unit.kind == Kind.title:
            continue
        if unit.kind not in (Kind.bullet, Kind.paragraph, Kind.subtitle, Kind.quote):
            return None
        if not unit.text or not unit.text.strip():
            continue
        label, body = split_label(unit.text)
        if label and _VALUE_RE.match(label.strip()):
            items.append({"heading": None, "body": body, "value": label.strip()})
        else:
            items.append({"heading": label, "body": body, "value": None})
    if not items or len(items) > _MAX_ITEMS:
        return None
    # KPI-подпись: заголовок из тела, если у значения нет своего
    for it in items:
        if it["value"] and not it["heading"] and it["body"]:
            head, _, rest = it["body"].partition(" — ")
            if rest and len(head.split()) <= 4:
                it["heading"], it["body"] = head, rest
    return items


def archetype_hint(slide: SlidePlan, strategy: Strategy) -> str | None:
    if slide.purpose in _PROCESS_PURPOSES:
        return "process"
    if strategy == Strategy.visual and slide.purpose in {Purpose.solution, Purpose.overview}:
        return "process" if slide.purpose == Purpose.solution else None
    return None


def decide_native(
    slide: SlidePlan,
    exemplar_xml: bytes,
    exemplar_part: str,
    used_before: set[str],
    strategy: Strategy,
    slide_w: int,
    slide_h: int,
) -> tuple[str, list[dict]] | None:
    """(архетип, пункты), если слайд собирается своей композицией."""
    if slide.purpose in FRAME_PURPOSES:
        return None
    items = slide_items(slide)
    if items is None:
        return None
    duplicate = exemplar_part in used_before
    if strategy == Strategy.faithful:
        if not duplicate:
            return None
    elif strategy != Strategy.visual:
        n_cards, per_card = card_slots(exemplar_xml, slide_w, slide_h)
        perfect = n_cards == len(items) and per_card in (1, 2)
        if perfect and not duplicate:
            return None
    return choose_archetype(items, archetype_hint(slide, strategy)), items
