"""Иконки шаблона на карточки колоды — по смыслу пунктов.

Работает на готовой колоде, для любого шаблона одинаково:

1. на слайде ищутся карточки (``card_reflow._find_cards``) и в каждой —
   маркер: видимая фигура без текста, не больше ``_MARKER_MAX_SHARE``
   площади карточки (точка, значок, кружок у заголовка карточки);
2. модель (промпт ``icon_match``) одним вызовом на колоду выбирает для
   каждого пункта иконку из библиотеки шаблона (``template/icons.py``) —
   по смыслу, различные в пределах слайда, или «нет подходящей»;
3. слайд получает иконки, только если они нашлись для ВСЕХ карточек
   (разнобой «у двух есть, у трёх нет» хуже отсутствия); иконка встаёт на
   место маркера, размером в свободное место над текстом карточки,
   векторная — перекрашивается в акцентный цвет темы.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

from lxml import etree
from pydantic import BaseModel, Field

from deckdna.pptx.composing.card_reflow import (
    _SHAPE_TAGS,
    _Box,
    _box,
    _find_cards,
    _is_visible,
    _text,
)
from deckdna.pptx.opc.package import (
    OpcPackage,
    Relationship,
    rels_name_for,
    serialize_rels,
)
from deckdna.template.icons import IconAsset

logger = logging.getLogger(__name__)

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
IMAGE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
PROMPT_NAME = "icon_match"
_MARKER_MAX_SHARE = 0.04
_MIN_SIDE = int(0.16 * 914400)
_MAX_SIDE = int(0.55 * 914400)
_GAP = int(0.05 * 914400)
_MAX_LABELS = 400


class SlideIcons(BaseModel):
    slide: int
    icons: list[int | None] = Field(default_factory=list)


class IconMatch(BaseModel):
    slides: list[SlideIcons] = Field(default_factory=list)


@dataclass
class _Target:
    part: str
    items: list[str]
    markers: list[tuple[etree._Element, _Box, _Box]]  # (маркер, его рамка, рамка места)


def can_match(gateway: object) -> bool:
    if gateway is None:
        return False
    if getattr(gateway, "provider_name", None) == "mock":
        return PROMPT_NAME in (getattr(gateway, "fixtures", None) or {})
    return True


def _room(marker: _Box, card_box: _Box, members: list[tuple[etree._Element, _Box]],
          marker_el: etree._Element) -> _Box | None:
    """Квадрат под иконку от верхнего левого угла маркера: до ближайшего
    текста ниже/правее внутри карточки и до края карточки."""
    bottom = card_box.b - _GAP
    right = card_box.r - _GAP
    for el, b in members:
        if el is marker_el or not _text(el):
            continue
        overlaps_x = min(b.r, marker.x + _MAX_SIDE) > max(b.x, marker.x)
        if overlaps_x and b.y >= marker.y:
            bottom = min(bottom, b.y - _GAP)
        overlaps_y = min(b.b, marker.y + _MAX_SIDE) > max(b.y, marker.y)
        if overlaps_y and b.x >= marker.r:
            right = min(right, b.x - _GAP)
    side = min(_MAX_SIDE, bottom - marker.y, right - marker.x)
    side = max(side, max(marker.w, marker.h))
    if side < _MIN_SIDE:
        return None
    return _Box(marker.x, marker.y, side, side)


def find_targets(pkg: OpcPackage, parts: list[str], slide_w: int, slide_h: int) -> list[_Target]:
    targets: list[_Target] = []
    for part in parts:
        root = etree.fromstring(pkg.parts[part])
        tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
        if tree is None:
            continue
        shapes = [(el, b) for el in tree if el.tag in _SHAPE_TAGS if (b := _box(el)) is not None]
        cards = _find_cards(shapes, slide_w, slide_h)
        if len(cards) < 2:
            continue
        items, markers = [], []
        for card in cards:
            text = " ".join(_text(m) for m, _ in card.members if _text(m)) or _text(card.container)
            cand = [
                (m, mb) for m, mb in card.members
                if not _text(m) and _is_visible(m)
                and mb.w * mb.h <= _MARKER_MAX_SHARE * card.box.w * card.box.h
            ]
            if not text.strip() or not cand:
                break
            m, mb = min(cand, key=lambda c: (c[1].y, c[1].x))
            room = _room(mb, card.box, card.members, m)
            if room is None:
                break
            items.append(text.strip()[:160])
            markers.append((m, mb, room))
        else:
            targets.append(_Target(part, items, markers))
    return targets


def _recolor(el: etree._Element) -> None:
    """Все сплошные цвета векторной иконки → акцент темы."""
    for fill in el.iter(f"{{{A}}}solidFill"):
        for child in list(fill):
            fill.remove(child)
        etree.SubElement(fill, f"{{{A}}}schemeClr", val="accent1")


def _max_id(root: etree._Element) -> int:
    ids = [int(c.get("id")) for c in root.iter(f"{{{P}}}cNvPr") if (c.get("id") or "").isdigit()]
    return max(ids, default=1)


def _place(pkg: OpcPackage, part: str, marker: etree._Element, room: _Box,
           icon: IconAsset, root: etree._Element, media_no: list[int]) -> bool:
    el = etree.fromstring(icon.xml.encode())
    tag = etree.QName(el).localname
    xfrm = el.find(f"{{{P}}}grpSpPr/{{{A}}}xfrm") if tag == "grpSp" else el.find(
        f"{{{P}}}spPr/{{{A}}}xfrm"
    )
    if xfrm is None:
        return False
    off, ext = xfrm.find(f"{{{A}}}off"), xfrm.find(f"{{{A}}}ext")
    iw, ih = int(ext.get("cx")), int(ext.get("cy"))
    scale = min(room.w / iw, room.h / ih)
    w, h = int(iw * scale), int(ih * scale)
    off.set("x", str(room.x + (room.w - w) // 2))
    off.set("y", str(room.y + (room.h - h) // 2))
    ext.set("cx", str(w))
    ext.set("cy", str(h))
    next_id = _max_id(root) + 1
    for c in el.iter(f"{{{P}}}cNvPr"):
        c.set("id", str(next_id))
        next_id += 1
    if icon.media_b64:
        media_no[0] += 1
        target = f"ppt/media/deckdna_icon{media_no[0]}.{icon.media_ext}"
        while target in pkg.parts:
            media_no[0] += 1
            target = f"ppt/media/deckdna_icon{media_no[0]}.{icon.media_ext}"
        pkg.parts[target] = base64.b64decode(icon.media_b64)
        rid = _add_rel(pkg, part, target)
        blip = el.find(f"{{{P}}}blipFill/{{{A}}}blip")
        if blip is None or rid is None:
            return False
        blip.set(f"{{{R}}}embed", rid)
        _ensure_default_ct(pkg, icon.media_ext or "png")
    else:
        _recolor(el)
    parent = marker.getparent()
    parent.replace(marker, el)
    return True


def _add_rel(pkg: OpcPackage, part: str, target: str) -> str | None:
    rels = pkg.rels(part)
    used = {r.id for r in rels}
    n = 1
    while f"rId{n}" in used:
        n += 1
    rid = f"rId{n}"
    rel_target = "../media/" + target.rsplit("/", 1)[-1]
    rels.append(Relationship(id=rid, type=IMAGE_REL, target=rel_target))
    pkg.parts[rels_name_for(part)] = serialize_rels(rels)
    return rid


def _ensure_default_ct(pkg: OpcPackage, ext: str) -> None:
    ct_part = "[Content_Types].xml"
    root = etree.fromstring(pkg.parts[ct_part])
    ns = root.nsmap.get(None)
    if any(d.get("Extension", "").lower() == ext for d in root.iter(f"{{{ns}}}Default")):
        return
    types = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
             "svg": "image/svg+xml", "emf": "image/x-emf", "wmf": "image/x-wmf"}
    el = etree.SubElement(root, f"{{{ns}}}Default")
    el.set("Extension", ext)
    el.set("ContentType", types.get(ext, "image/png"))
    root.insert(0, el)
    pkg.parts[ct_part] = etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                                        standalone=True)


async def place_icons(
    deck_path: str, icons: list[IconAsset], gateway: object, *, protected: set[str] | None = None
) -> dict[str, int]:
    """Расставить иконки в колоде на месте; отчёт {slides, icons}."""
    from pptx import Presentation

    report = {"slides_with_icons": 0, "icons_placed": 0, "candidate_slides": 0}
    if not icons or not can_match(gateway):
        return report
    prs = Presentation(deck_path)
    parts = [str(s.part.partname).lstrip("/") for s in prs.slides]
    parts = [p for p in parts if p not in (protected or set())]
    pkg = OpcPackage.open(deck_path)
    targets = find_targets(pkg, parts, int(prs.slide_width), int(prs.slide_height))
    report["candidate_slides"] = len(targets)
    if not targets:
        return report
    catalog = icons[:_MAX_LABELS]
    payload = {
        "icons": [{"id": k, "label": icon.label} for k, icon in enumerate(catalog)],
        "slides": [{"slide": n, "items": t.items} for n, t in enumerate(targets)],
    }
    try:
        answer = await gateway.text_json(PROMPT_NAME, payload, IconMatch)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — без подбора колода остаётся как была
        logger.warning("icon match failed (%s); deck left without icons", exc)
        return report
    by_slide = {s.slide: s.icons for s in answer.slides}
    media_no = [0]
    changed = False
    for n, target in enumerate(targets):
        chosen = by_slide.get(n) or []
        if len(chosen) != len(target.items) or any(
            k is None or not 0 <= k < len(catalog) for k in chosen
        ) or len(set(chosen)) != len(chosen):
            continue
        root = etree.fromstring(pkg.parts[target.part])
        tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
        # маркеры — из исходного разбора; ищем их двойники в свежем дереве
        live = {etree.tostring(el): el for el in tree}
        placed = 0
        for (marker, _mb, room), k in zip(target.markers, chosen, strict=True):
            twin = live.get(etree.tostring(marker))
            if twin is None:
                continue
            if _place(pkg, target.part, twin, room, catalog[k], root, media_no):
                placed += 1
        if placed == len(target.markers):
            pkg.parts[target.part] = etree.tostring(
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )
            report["slides_with_icons"] += 1
            report["icons_placed"] += placed
            changed = True
    if changed:
        pkg.save(deck_path)
    return report

