"""Нативные композиции внутри дизайн-системы шаблона.

Проблема: пул эталонных слайдов шаблона мал (≈9 пригодных слайдов на колоду
из 12), слайды повторяются, а контент не совпадает с сеткой эталона. Эксперты
просят «новые композиции в рамках дизайн-системы шаблона».

Решение — разделить эталон на РАМКУ и КОНТЕНТ:

1. ``slide_frame`` — берёт любой слайд шаблона, оставляет рамку (заголовок,
   колонтитулы, фон, панель-подложку, мелкий декор и логотипы у краёв),
   удаляет контент (карточки, тексты, картинки, группы, таблицы) и
   возвращает свободную область контента + дизайн-токены, прочитанные с
   исходного слайда (шрифты, кегли, цвета текста, акцент, заливка и
   скругление карточек, фон, палитра темы);
2. ``compose`` — рисует в этой области новую композицию из нативных
   редактируемых фигур (``p:sp`` с ``a:prstGeom`` и текстом в ``a:txBody``,
   линии ``p:cxnSp``): карточки, процесс, KPI, две колонки, список.
   Кегли подбираются так, чтобы текст влезал (оценка по метрике шрифта,
   шаг 1pt, не ниже 11pt); цвет текста при нехватке контраста к подложке
   (WCAG 4.5) сдвигается к чёрному/белому с сохранением оттенка.

Ничего не знает о конкретных шаблонах: всё — из геометрии и явных атрибутов
входного слайда (и, если переданы, его макета/мастера/темы). Картинок и
растеризации нет.
"""

from __future__ import annotations

import colorsys
import copy
import re
from collections import Counter
from dataclasses import dataclass
from xml.sax.saxutils import escape

from lxml import etree

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_DECL = f'xmlns:a="{A}" xmlns:p="{P}" xmlns:r="{R}"'

EMU_PER_PT = 12700
ARCHETYPES = ("cards", "process", "kpi", "two_column", "list")

# ── пороги классификации рамки (доли площади / сторон слайда)
_BG_AREA = 0.80  # фон: фигура на весь слайд
_PANEL_AREA = 0.40  # подложка-панель под заголовком и контентом
_DECOR_AREA = 0.015  # мелкий декор / логотип
_EDGE_BAND = 0.12  # «у края»: центр в этой полосе от любого края
_EDGE_MARGIN = 0.03  # минимальный отступ области контента от края слайда
_OBSTACLE_AREA = 0.20  # фигуры рамки меньше этого вырезаются из области
_CARD_AREA = (0.012, 0.45)  # «карточка» среди удаляемых фигур
_ACCENT_AREA = 0.02  # маркеры / полоски акцента

# ── типографика и сетка
_MIN_BODY_PT = 11.0
_LINE_FACTOR = 1.25  # высота строки (как у детерминированного аудита)
_FIT_SLACK = 0.94  # текст должен занимать не больше этой доли рамки
_CONTRAST = 4.8  # WCAG AA 4.5 + запас на округления трансформов
_DEFAULT_INS = 91440  # bodyPr lIns/rIns по умолчанию
_WORD_RE = re.compile(r"[^\W\d_]{2,}")
_STEP_RE = re.compile(r"^\s*(\d+[.)\s]|шаг|этап|стадия|фаза|step|stage|phase)", re.IGNORECASE)
_NUM_RE = re.compile(r"\d")
_CYR_RE = re.compile("[Ѐ-ӿ]")

_TITLE_PH = {"title", "ctrTitle"}
_FOOTER_PH = {"ftr", "sldNum", "dt"}
_STRUCT_TAGS = {"nvGrpSpPr", "grpSpPr", "extLst"}
_SCHEME_ALIAS = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}


# ────────────────────────────── геометрия ──────────────────────────────


@dataclass
class Box:
    """Прямоугольник в EMU (левый верхний угол + размеры)."""

    x: int
    y: int
    w: int
    h: int

    @property
    def r(self) -> int:
        return self.x + self.w

    @property
    def b(self) -> int:
        return self.y + self.h

    @property
    def area(self) -> int:
        return max(0, self.w) * max(0, self.h)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def contains(self, o: Box, tol: int = 0) -> bool:
        return (
            o.x >= self.x - tol
            and o.y >= self.y - tol
            and o.r <= self.r + tol
            and o.b <= self.b + tol
        )

    def contains_point(self, x: float, y: float) -> bool:
        return self.x <= x <= self.r and self.y <= y <= self.b

    def overlaps(self, o: Box) -> bool:
        return min(self.r, o.r) > max(self.x, o.x) and min(self.b, o.b) > max(self.y, o.y)

    def union(self, o: Box) -> Box:
        x, y = min(self.x, o.x), min(self.y, o.y)
        return Box(x, y, max(self.r, o.r) - x, max(self.b, o.b) - y)


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _xfrm(el: etree._Element) -> etree._Element | None:
    tag = _local(el)
    if tag == "grpSp":
        return el.find(f"{{{P}}}grpSpPr/{{{A}}}xfrm")
    if tag == "graphicFrame":
        return el.find(f"{{{P}}}xfrm")
    return el.find(f"{{{P}}}spPr/{{{A}}}xfrm")


def _box(el: etree._Element) -> Box | None:
    x = _xfrm(el)
    if x is None:
        return None
    off, ext = x.find(f"{{{A}}}off"), x.find(f"{{{A}}}ext")
    if off is None or ext is None:
        return None
    try:
        return Box(int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy")))
    except (TypeError, ValueError):
        return None


def _text(el: etree._Element) -> str:
    return "".join(t.text or "" for t in el.iter(f"{{{A}}}t")).strip()


def _ph_type(el: etree._Element) -> str | None:
    ph = el.find(f"./*/{{{P}}}nvPr/{{{P}}}ph")
    if ph is None:
        return None
    return ph.get("type", "body")


def _shape_id(el: etree._Element) -> int | None:
    c = el.find(f"./*/{{{P}}}cNvPr")
    try:
        return int(c.get("id")) if c is not None else None
    except (TypeError, ValueError):
        return None


def _max_id(root: etree._Element) -> int:
    ids = [int(v) for v in root.xpath("//p:cNvPr/@id", namespaces={"p": P}) if str(v).isdigit()]
    return max(ids, default=1)


def _top_shapes(tree: etree._Element) -> list[tuple[etree._Element, Box | None]]:
    return [(el, _box(el)) for el in tree if _local(el) and _local(el) not in _STRUCT_TAGS]


def _invisible(el: etree._Element) -> bool:
    """Фигура ничего не рисует: без текста, без заливки и контура (служебные
    рамки-«каркасы», которые часто оставляют экспортёры)."""
    if _local(el) != "sp" or _text(el):
        return False
    sp_pr = el.find(f"{{{P}}}spPr")
    if sp_pr is None:
        return False
    style = el.find(f"{{{P}}}style")
    has_fill = any(
        sp_pr.find(f"{{{A}}}{t}") is not None
        for t in ("solidFill", "gradFill", "blipFill", "pattFill", "grpFill")
    )
    if has_fill:
        return False
    if sp_pr.find(f"{{{A}}}noFill") is None and style is not None:
        ref = style.find(f"{{{A}}}fillRef")
        if ref is not None and ref.get("idx", "0") != "0":
            return False
    ln = sp_pr.find(f"{{{A}}}ln")
    ln_off = ln is not None and ln.find(f"{{{A}}}noFill") is not None
    if ln_off:
        return True
    if ln is not None and ln.find(f"{{{A}}}solidFill") is not None:
        return False
    if style is not None:
        ref = style.find(f"{{{A}}}lnRef")
        if ref is not None and ref.get("idx", "0") != "0":
            return False
    return True


def _dump(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _parse(xml: bytes | None) -> etree._Element | None:
    if not xml:
        return None
    try:
        return etree.fromstring(xml)
    except etree.XMLSyntaxError:
        return None


# ────────────────────────────── цвета ──────────────────────────────

_MOD_TAGS = ("tint", "shade", "alpha", "lumMod", "lumOff")


def _color_el(clr: etree._Element | None) -> dict | None:
    if clr is None:
        return None
    tag = _local(clr)
    if tag == "srgbClr":
        out: dict = {"srgb": (clr.get("val") or "").upper()}
    elif tag == "schemeClr":
        out = {"scheme": clr.get("val")}
    elif tag == "sysClr":
        out = {"srgb": (clr.get("lastClr") or "000000").upper()}
    else:
        return None
    for mod in clr:
        name = _local(mod)
        # прозрачность шаблона не переносим: сплошные цвета детерминированы
        if name in _MOD_TAGS and name != "alpha" and mod.get("val"):
            out[name] = int(mod.get("val"))
    return out


def _color_of(fill_parent: etree._Element | None) -> dict | None:
    """Цвет ``a:solidFill`` внутри *fill_parent* как токен
    ``{"srgb"|"scheme": val, <трансформы>}``; None — нет сплошной заливки."""
    if fill_parent is None:
        return None
    solid = fill_parent.find(f"{{{A}}}solidFill")
    if solid is None or not len(solid):
        return None
    return _color_el(solid[0])


def _color_key(c: dict) -> tuple:
    return tuple(sorted(c.items()))


def _clr_xml(c: dict) -> str:
    mods = "".join(f'<a:{m} val="{int(c[m])}"/>' for m in _MOD_TAGS if m in c)
    if "srgb" in c:
        return f'<a:srgbClr val="{c["srgb"]}">{mods}</a:srgbClr>'
    return f'<a:schemeClr val="{c.get("scheme", "tx1")}">{mods}</a:schemeClr>'


def _fill_xml(c: dict | None) -> str:
    return "<a:noFill/>" if c is None else f"<a:solidFill>{_clr_xml(c)}</a:solidFill>"


def _hex(rgb: tuple[float, ...]) -> str:
    return "".join(f"{max(0, min(255, round(v * 255))):02X}" for v in rgb)


def _rgb(c: dict | None, palette: dict | None = None) -> tuple[float, float, float] | None:
    """RGB 0..1 цветового токена (srgb или слот палитры темы) с трансформами."""
    if not c:
        return None
    if "srgb" in c:
        val = c["srgb"]
    else:
        slot = _SCHEME_ALIAS.get(c.get("scheme"), c.get("scheme"))
        val = (palette or {}).get(slot)
    if not val or len(val) != 6:
        return None
    try:
        rgb = tuple(int(val[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None
    for m in _MOD_TAGS:
        if m not in c or m == "alpha":
            continue
        v = c[m] / 100000
        if m == "tint":
            rgb = tuple(ch + (1 - ch) * v for ch in rgb)
        elif m == "shade":
            rgb = tuple(ch * v for ch in rgb)
        else:
            h, lum, s = colorsys.rgb_to_hls(*rgb)
            lum = min(1.0, lum * v) if m == "lumMod" else min(1.0, max(0.0, lum + v))
            rgb = colorsys.hls_to_rgb(h, lum, s)
    return rgb  # type: ignore[return-value]


def _lum(rgb: tuple[float, ...]) -> float:
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _readable(fg: dict, bg: tuple[float, ...] | None, palette: dict | None) -> dict:
    """Цвет текста с контрастом ≥ 4.5 к подложке *bg*: при нехватке —
    смешивание к чёрному/белому (оттенок сохраняется). Неразрешимое — как есть."""
    rgb = _rgb(fg, palette)
    if rgb is None or bg is None or _ratio(rgb, bg) >= _CONTRAST:
        return fg
    target = (0.0, 0.0, 0.0) if _lum(bg) > 0.18 else (1.0, 1.0, 1.0)
    for step in range(1, 11):
        t = step / 10
        mixed = tuple(c + (tc - c) * t for c, tc in zip(rgb, target, strict=True))
        if _ratio(mixed, bg) >= _CONTRAST:
            return {"srgb": _hex(mixed)}
    return {"srgb": _hex(target)}


def _is_accentish(c: dict) -> bool:
    """Насыщенный цвет — кандидат в акцент (не серый, не почти чёрный)."""
    if "scheme" in c:
        return str(c["scheme"]).startswith("accent")
    rgb = _rgb(c)
    if rgb is None:
        return False
    hi, lo = max(rgb), min(rgb)
    return hi >= 0.3 and (hi - lo) / max(hi, 1e-6) >= 0.35


def _theme_palette(theme: etree._Element | None) -> dict[str, str]:
    """{слот clrScheme: RRGGBB} темы."""
    out: dict[str, str] = {}
    scheme = theme.find(f".//{{{A}}}clrScheme") if theme is not None else None
    if scheme is None:
        return out
    for slot in scheme:
        for clr in slot:
            tag = _local(clr)
            if tag == "srgbClr" and clr.get("val"):
                out[_local(slot)] = clr.get("val").upper()
            elif tag == "sysClr" and (clr.get("lastClr") or clr.get("val")):
                out[_local(slot)] = (clr.get("lastClr") or clr.get("val")).upper()
    return out


def _bg_of(root: etree._Element | None) -> dict | None:
    """Сплошной фон ``p:bg`` (bgPr или bgRef) части слайда/макета/мастера."""
    if root is None:
        return None
    bg = root.find(f"{{{P}}}cSld/{{{P}}}bg")
    if bg is None:
        return None
    c = _color_of(bg.find(f"{{{P}}}bgPr"))
    if c is not None:
        return c
    ref = bg.find(f"{{{P}}}bgRef")
    if ref is not None and len(ref):
        return _color_el(ref[0])
    return None


# ────────────────────────────── токены ──────────────────────────────


def _runs(el: etree._Element) -> list[tuple[etree._Element | None, str]]:
    out = []
    for r in el.iter(f"{{{A}}}r"):
        t = r.find(f"{{{A}}}t")
        txt = (t.text or "") if t is not None else ""
        if txt.strip():
            out.append((r.find(f"{{{A}}}rPr"), txt.strip()))
    return out


def _latin(rpr: etree._Element | None) -> str | None:
    if rpr is None:
        return None
    lat = rpr.find(f"{{{A}}}latin")
    if lat is None:
        return None
    face = lat.get("typeface") or ""
    if not face or face.startswith("+"):  # +mj-lt / +mn-lt — шрифт темы
        return None
    return face


def _sz(rpr: etree._Element | None) -> float | None:
    if rpr is None or not rpr.get("sz"):
        return None
    try:
        return int(rpr.get("sz")) / 100
    except ValueError:
        return None


def _mode(counter: Counter):
    return counter.most_common(1)[0][0] if counter else None


def _round_half(v: float) -> float:
    return round(v * 2) / 2


def _title_size(title_el: etree._Element | None, layout: etree._Element | None) -> float | None:
    """Кегль заголовка: явный sz рана → lstStyle фигуры → плейсхолдер макета."""
    if title_el is None:
        return None
    lvl1 = f"{{{P}}}txBody/{{{A}}}lstStyle/{{{A}}}lvl1pPr/{{{A}}}defRPr"
    cands = [title_el]
    if layout is not None:
        cands += [el for el in layout.iter(f"{{{P}}}sp") if _ph_type(el) in _TITLE_PH]
    for el in cands:
        sizes = [s for rpr, _ in _runs(el) if (s := _sz(rpr)) is not None]
        if sizes and el is title_el:
            return max(sizes)
        d = el.find(lvl1)
        if d is not None and _sz(d):
            return _sz(d)
        if sizes:
            return max(sizes)
    return None


def _extract_tokens(
    shapes: list[tuple[etree._Element, Box | None]],
    title_el: etree._Element | None,
    removed: list[tuple[etree._Element, Box | None]],
    background: dict | None,
    palette: dict[str, str],
    sw: int,
    sh: int,
) -> dict:
    """Дизайн-токены с ИСХОДНОГО слайда (до удаления контента)."""
    area = sw * sh
    title_fonts: Counter = Counter()
    body_fonts: Counter = Counter()
    sizes: Counter = Counter()
    body_runs: list[tuple[etree._Element | None, str]] = []
    for el, _ in shapes:
        runs = _runs(el)
        if el is title_el:
            for rpr, txt in runs:
                if (f := _latin(rpr)) is not None:
                    title_fonts[f] += len(txt)
            continue
        for rpr, txt in runs:
            if (f := _latin(rpr)) is not None:
                body_fonts[f] += len(txt)
            if (s := _sz(rpr)) is not None:
                sizes[s] += len(txt)
            body_runs.append((rpr, txt))

    # «тело» — самый частый кегль (по числу символов); крупнее или bold — заголовки
    body_sz = _mode(sizes)
    text_colors: Counter = Counter()
    head_colors: Counter = Counter()
    for rpr, txt in body_runs:
        c = _color_of(rpr)
        if c is None:
            continue
        s = _sz(rpr)
        bold = rpr is not None and rpr.get("b") == "1"
        if bold or (s is not None and body_sz is not None and s > body_sz):
            head_colors[_color_key(c)] += len(txt)
        else:
            text_colors[_color_key(c)] += len(txt)

    bg_rgb = _rgb(background, palette) if background else None
    dark_bg = bg_rgb is not None and _lum(bg_rgb) < 0.2

    text_color = dict(_mode(text_colors)) if text_colors else None
    if text_color is None:
        text_color = {"srgb": "F2F2F2"} if dark_bg else {"scheme": "tx1"}
    heading_color = dict(_mode(head_colors)) if head_colors else text_color

    accents: Counter = Counter()
    fills: Counter = Counter()
    corners: Counter = Counter()
    radii: list[float] = []
    removed_ids = {id(el) for el, _ in removed}
    for el, b in shapes:
        if b is None or _local(el) != "sp" or _text(el):
            continue
        c = _color_of(el.find(f"{{{P}}}spPr"))
        frac = b.area / area
        # акцент: мелкие насыщенные заливки (маркеры, полоски) по всему слайду
        if c is not None and frac < _ACCENT_AREA and _is_accentish(c):
            accents[_color_key(c)] += 1
        if id(el) not in removed_ids or not (_CARD_AREA[0] <= frac <= _CARD_AREA[1]):
            continue
        geom = el.find(f"{{{P}}}spPr/{{{A}}}prstGeom")
        prst = geom.get("prst") if geom is not None else None
        if prst not in ("rect", "roundRect") or _invisible(el):
            continue
        if c is not None:
            fills[_color_key(c)] += 1
        corners[prst == "roundRect"] += 1
        if prst == "roundRect":
            adj = 16667
            gd = geom.find(f"{{{A}}}avLst/{{{A}}}gd")
            if gd is not None:
                m = re.search(r"val\s+(\d+)", gd.get("fmla") or "")
                if m:
                    adj = int(m.group(1))
            radii.append(adj / 100000 * min(b.w, b.h))

    accent = dict(_mode(accents)) if accents else {"scheme": "accent1"}
    if fills:
        card_fill = dict(_mode(fills))
    elif background is not None:
        # производная от фона: чуть светлее на тёмном, чуть темнее на светлом
        card_fill = (
            {**background, "lumMod": 85000, "lumOff": 15000}
            if dark_bg
            else {**background, "lumMod": 95000}
        )
    else:
        card_fill = {"scheme": "bg1", "lumMod": 95000}
    rounded = bool(corners) and corners[True] >= corners[False]
    radius = sorted(radii)[len(radii) // 2] if radii else 0.015 * min(sw, sh)

    body_pt = min(18.0, max(12.0, body_sz)) if body_sz else 14.0
    heading_pt = min(24.0, max(14.0, _round_half(body_pt * 1.3)))
    return {
        "title_font": _mode(title_fonts),
        "body_font": _mode(body_fonts),
        "body_size_pt": body_pt,
        "heading_size_pt": heading_pt,
        "text_color": text_color,
        "heading_color": heading_color,
        "accent": accent,
        "card_fill": card_fill,
        "corner": "roundRect" if rounded else "rect",
        "corner_radius_emu": int(radius),
        "background": background,
        "dark_background": dark_bg,
        "palette": palette,
    }


# ────────────────────────────── рамка ──────────────────────────────


def _max_run_sz(el: etree._Element) -> float:
    return max((s for rpr, _ in _runs(el) if (s := _sz(rpr)) is not None), default=0.0)


def _find_title(shapes: list[tuple[etree._Element, Box | None]], sh: int) -> etree._Element | None:
    """Заголовок: плейсхолдер title/ctrTitle, иначе самая верхняя из крупных
    (по явному кеглю) текстовых фигур в верхних 40% слайда."""
    for el, _ in shapes:
        if _ph_type(el) in _TITLE_PH:
            return el
    cands = [
        (el, b)
        for el, b in shapes
        if _local(el) == "sp" and b is not None and b.y < 0.4 * sh and _WORD_RE.search(_text(el))
    ]
    if not cands:
        return None
    # самый верхний среди крупных (≥70% максимального кегля): крупная цифра
    # KPI ниже заголовка не должна «перехватить» роль заголовка
    top_sz = max(_max_run_sz(el) for el, _ in cands)
    big = [(el, b) for el, b in cands if _max_run_sz(el) >= 0.7 * top_sz]
    return min(big, key=lambda eb: (eb[1].y, eb[1].x))[0]


def _layout_title_box(layout: etree._Element | None) -> Box | None:
    """Геометрия заголовка из макета — у слайдового ``p:ph`` её часто нет."""
    if layout is None:
        return None
    for el in layout.iter(f"{{{P}}}sp"):
        if _ph_type(el) in _TITLE_PH:
            return _box(el)
    return None


def _near_edge(b: Box, sw: int, sh: int) -> bool:
    return (
        b.cx < _EDGE_BAND * sw
        or b.cx > (1 - _EDGE_BAND) * sw
        or b.cy < _EDGE_BAND * sh
        or b.cy > (1 - _EDGE_BAND) * sh
    )


def _cut_region(region: Box, obstacle: Box, gap: int) -> Box:
    """Вырезать препятствие из области: отрезать сторону с наименьшей потерей."""
    if not region.overlaps(obstacle):
        return region
    options = [
        Box(region.x, obstacle.b + gap, region.w, region.b - obstacle.b - gap),
        Box(region.x, region.y, region.w, obstacle.y - gap - region.y),
        Box(obstacle.r + gap, region.y, region.r - obstacle.r - gap, region.h),
        Box(region.x, region.y, obstacle.x - gap - region.x, region.h),
    ]
    options = [o for o in options if o.w > 0 and o.h > 0]
    return max(options, key=lambda o: o.area) if options else region


def _inherited_shapes(
    layout: etree._Element | None,
    master: etree._Element | None,
    sw: int,
    sh: int,
    max_area: float = _OBSTACLE_AREA,
    pictures_any_size: bool = False,
) -> list[Box]:
    """Видимые не-плейсхолдерные фигуры макета/мастера (логотипы, плашки)
    площадью меньше *max_area* слайда — рисуются на любом слайде макета.
    *pictures_any_size* — картинки учитываются при любой площади (фоновая
    иллюстрация на весь слайд тоже «занимает» область)."""
    parts = [layout]
    if master is not None and (layout is None or layout.get("showMasterSp") != "0"):
        parts.append(master)
    out = []
    for part in parts:
        tree = part.find(f"{{{P}}}cSld/{{{P}}}spTree") if part is not None else None
        if tree is None:
            continue
        for el, b in _top_shapes(tree):
            if b is None or _ph_type(el) is not None or _invisible(el):
                continue
            big_ok = pictures_any_size and _local(el) in ("pic", "grpSp", "graphicFrame")
            if (b.area >= max_area * sw * sh and not big_ok) or not Box(0, 0, sw, sh).overlaps(b):
                continue
            out.append(b)
    return out


def slide_frame(
    slide_xml: bytes,
    slide_w: int,
    slide_h: int,
    *,
    layout_xml: bytes | None = None,
    master_xml: bytes | None = None,
    theme_xml: bytes | None = None,
) -> tuple[bytes, Box, dict]:
    """Рамка слайда шаблона без контента + свободная область + токены.

    Остаётся: заголовок (плейсхолдер title/ctrTitle или самая крупная
    текстовая фигура сверху), колонтитулы (ftr/sldNum/dt), фон (≥80%
    площади), панель-подложка под заголовком и контентом, мелкий декор и
    логотипы у краёв (<1.5% площади, центр в 12%-полосе у края, без
    текста, вне зоны контента). Всё прочее удаляется.

    Область контента — под заголовком (или сбоку от него, если контент
    эталона стоял рядом) в пределах объединения видимых удалённых фигур;
    если удалять было нечего — поля 5% и низ на 88% высоты. Из области
    вырезаются оставшийся декор и видимые фигуры макета/мастера.

    ``layout_xml``/``master_xml``/``theme_xml`` необязательны: геометрия и
    кегль заголовка, фон, логотипы макета, палитра темы (для контраста)."""
    root = etree.fromstring(slide_xml)
    tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    if tree is None:
        raise ValueError("slide has no p:spTree")
    layout, master = _parse(layout_xml), _parse(master_xml)
    palette = _theme_palette(_parse(theme_xml))
    sw, sh = slide_w, slide_h
    area = sw * sh
    shapes = _top_shapes(tree)
    title_el = _find_title(shapes, sh)
    title_box = None
    if title_el is not None:
        title_box = _box(title_el) or _layout_title_box(layout)

    keep: set[int] = set()
    decor: list[tuple[etree._Element, Box]] = []
    for el, b in shapes:
        if el is title_el or _ph_type(el) in _FOOTER_PH:
            keep.add(id(el))
            continue
        if b is None:
            continue
        frac = b.area / area
        texty = bool(_text(el))
        if frac >= _BG_AREA and not texty:
            keep.add(id(el))
        elif (
            frac >= _PANEL_AREA
            and _local(el) == "sp"
            and not texty
            and title_box is not None
            and b.contains_point(title_box.cx, title_box.cy)
        ):
            keep.add(id(el))  # подложка под заголовком и контентом
        elif frac < _DECOR_AREA and _near_edge(b, sw, sh) and not texty:
            decor.append((el, b))

    decor_ids = {id(el) for el, _ in decor}
    union: Box | None = None
    for el, b in shapes:
        if id(el) in keep or id(el) in decor_ids or b is None or _invisible(el):
            continue
        union = b if union is None else union.union(b)
    # декор внутри зоны контента (маркеры карточек у поля, крайние элементы
    # декоративного ряда) — это контент; допуск 3% стороны слайда
    zone = None
    if union is not None:
        tx, ty = int(0.03 * sw), int(0.03 * sh)
        zone = Box(union.x - tx, union.y - ty, union.w + 2 * tx, union.h + 2 * ty)
    for el, b in decor:
        if zone is None or not zone.contains_point(b.cx, b.cy):
            keep.add(id(el))

    background = None
    for el, b in shapes:
        if id(el) in keep and b is not None and b.area >= _BG_AREA * area and _local(el) == "sp":
            background = _color_of(el.find(f"{{{P}}}spPr")) or background
    background = background or _bg_of(root) or _bg_of(layout) or _bg_of(master)

    removed = [(el, b) for el, b in shapes if id(el) not in keep]
    tokens = _extract_tokens(shapes, title_el, removed, background, palette, sw, sh)
    tokens["title_size_pt"] = _title_size(title_el, layout)
    for el, _ in removed:
        tree.remove(el)

    gap = int(0.03 * sh)
    mx, my = int(_EDGE_MARGIN * sw), int(_EDGE_MARGIN * sh)
    if title_el is None:
        title_el, title_box = _add_title_box(tree, root, tokens, sw, sh)
    if title_box is None:
        title_box = Box(int(0.05 * sw), int(0.05 * sh), int(0.9 * sw), int(0.15 * sh))
    if tokens["title_size_pt"] is None:
        # кегль не задан нигде в доступных частях: оценка по высоте рамки (2 строки)
        tokens["title_size_pt"] = max(20.0, min(40.0, title_box.h / EMU_PER_PT / 2.6))
    tokens["title_shape_id"] = _shape_id(title_el)
    tokens["title_box"] = [title_box.x, title_box.y, title_box.w, title_box.h]

    if union is None:
        top = title_box.b + gap
        region = Box(int(0.05 * sw), top, int(0.9 * sw), int(0.88 * sh) - top)
    elif union.x >= title_box.r or union.r <= title_box.x:
        region = union  # контент эталона стоял сбоку от заголовка
    else:
        top = max(union.y, title_box.b + gap)
        region = Box(union.x, top, union.w, union.b - top)
        if region.h < 0.3 * sh:  # контент эталона был над/у заголовка
            top = title_box.b + gap
            region = Box(union.x, top, union.w, int(0.88 * sh) - top)
    x0, y0 = max(region.x, mx), max(region.y, my)
    x1, y1 = min(region.r, sw - mx), min(region.b, sh - my)
    region = Box(x0, y0, x1 - x0, y1 - y0)
    # оставшийся декор, колонтитулы и логотипы макета не должны попасть в область
    obstacles = [
        b
        for el, b in _top_shapes(tree)
        if el is not title_el
        and b is not None
        and b.area < _OBSTACLE_AREA * area
        and not _invisible(el)
    ]
    obstacles += _inherited_shapes(layout, master, sw, sh)
    for b in obstacles:
        region = _cut_region(region, b, int(0.02 * sh))
    region = _cut_region(region, title_box, gap)
    # «захламлённость» области графикой макета/мастера (крупные декоративные
    # панели, иллюстрации): 0 — чистая рамка; вызывающему стоит предпочитать
    # рамки с меньшим значением
    covered = 0
    for b in _inherited_shapes(layout, master, sw, sh, _BG_AREA, pictures_any_size=True):
        ix = min(b.r, region.r) - max(b.x, region.x)
        iy = min(b.b, region.b) - max(b.y, region.y)
        if ix > 0 and iy > 0:
            covered += ix * iy
    tokens["inherited_clutter"] = round(min(1.0, covered / max(1, region.area)), 3)
    return _dump(root), region, tokens


def _add_title_box(
    tree: etree._Element, root: etree._Element, tokens: dict, sw: int, sh: int
) -> tuple[etree._Element, Box]:
    """Заголовка на эталоне нет — добавить текстовую рамку в токенах шаблона."""
    box = Box(int(0.05 * sw), int(0.05 * sh), int(0.9 * sw), int(0.14 * sh))
    size = tokens.get("title_size_pt") or min(40.0, tokens["heading_size_pt"] * 1.6)
    tokens["title_size_pt"] = size
    run = (" ", size, True, tokens["heading_color"], tokens.get("title_font"))
    el = _sp(_max_id(root) + 1, "Title", box, None, None, [_Par([run])], text_box=True, anchor="b")
    tree.append(el)
    return el, box


# ────────────────────────────── оценка текста ──────────────────────────────


def _char_w(ch: str) -> float:
    if ch == " ":
        return 0.32
    if ch.isdigit():
        return 0.64
    if ch.isalpha():
        if ch.isupper():
            return 0.74
        return 0.64 if _CYR_RE.match(ch) else 0.56
    return 0.45


def _text_w(text: str, pt: float, bold: bool = False) -> float:
    """Ширина строки в EMU (консервативная средняя метрика глифов)."""
    w = sum(_char_w(c) for c in text) * pt * EMU_PER_PT
    return w * (1.1 if bold else 1.0)


def _lines(text: str, pt: float, width: float, bold: bool = False) -> tuple[int, bool]:
    """(число строк при переносе по словам, влезает ли самое длинное слово)."""
    total, fits = 0, True
    space = _text_w(" ", pt, bold)
    for hard in text.split("\n"):
        n, cur = 1, 0.0
        for word in hard.split():
            ww = _text_w(word, pt, bold)
            if ww > width:
                fits = False
            if cur == 0.0:
                cur = ww
            elif cur + space + ww <= width:
                cur += space + ww
            else:
                n += 1
                cur = ww
        total += n
    return total, fits


# ран абзаца: (текст, кегль, bold, цвет, шрифт)
_Run = tuple


@dataclass
class _Par:
    runs: list[_Run]
    space_before_pt: float = 0.0
    algn: str = "l"
    bullet: dict | None = None  # цвет маркера списка
    indent_emu: int = 0

    @property
    def text(self) -> str:
        return "".join(r[0] for r in self.runs)

    @property
    def size(self) -> float:
        return max(r[1] for r in self.runs)

    @property
    def bold(self) -> bool:
        return all(r[2] for r in self.runs)


def _pars_height(pars: list[_Par], width: float) -> tuple[float, bool]:
    h, fits = 0.0, True
    for p in pars:
        n, ok = _lines(p.text, p.size, width - p.indent_emu, p.bold)
        fits = fits and ok
        h += n * p.size * _LINE_FACTOR * EMU_PER_PT + p.space_before_pt * EMU_PER_PT
    return h, fits


# ────────────────────────────── XML фигур ──────────────────────────────


def _run_xml(text: str, size: float, bold: bool, color: dict, font: str | None) -> str:
    lang = "ru-RU" if _CYR_RE.search(text) else "en-US"
    latin = f'<a:latin typeface="{escape(font, {chr(34): "&quot;"})}"/>' if font else ""
    return (
        f'<a:r><a:rPr lang="{lang}" sz="{int(round(size * 100))}" b="{1 if bold else 0}" '
        f'dirty="0"><a:solidFill>{_clr_xml(color)}</a:solidFill>{latin}</a:rPr>'
        f"<a:t>{escape(text)}</a:t></a:r>"
    )


def _p_xml(p: _Par) -> str:
    spc = (
        f'<a:spcBef><a:spcPts val="{int(round(p.space_before_pt * 100))}"/></a:spcBef>'
        if p.space_before_pt
        else ""
    )
    if p.bullet is not None:
        bu = (
            f"<a:buClr>{_clr_xml(p.bullet)}</a:buClr>"
            '<a:buFont typeface="Arial"/><a:buChar char="&#8226;"/>'
        )
        ind = f'marL="{p.indent_emu}" indent="-{p.indent_emu}"'
    else:
        bu = "<a:buNone/>"
        ind = 'marL="0" indent="0"'
    runs = "".join(_run_xml(*r) for r in p.runs)
    return (
        f'<a:p><a:pPr {ind} algn="{p.algn}"><a:lnSpc><a:spcPct val="100000"/></a:lnSpc>'
        f"{spc}{bu}</a:pPr>{runs}</a:p>"
    )


def _sp(
    sid: int,
    name: str,
    box: Box,
    fill: dict | None,
    geom: str | None,
    pars: list[_Par] | None,
    *,
    text_box: bool = False,
    anchor: str = "t",
    ins: tuple[int, int, int, int] = (0, 0, 0, 0),
    radius: int = 0,
) -> etree._Element:
    """Нативная фигура ``p:sp``: геометрия, заливка без контура, текст.
    *ins* — внутренние поля (left, top, right, bottom) в EMU."""
    geom = geom or "rect"
    av = ""
    if geom == "roundRect":
        adj = int(min(50000, max(0, radius / max(1, min(box.w, box.h)) * 100000)))
        av = f'<a:gd name="adj" fmla="val {adj}"/>'
    li, ti, ri, bi = ins
    paras = "".join(_p_xml(p) for p in pars) if pars else '<a:p><a:endParaRPr lang="ru-RU"/></a:p>'
    body = (
        f'<p:txBody><a:bodyPr wrap="square" lIns="{li}" tIns="{ti}" rIns="{ri}" bIns="{bi}" '
        f'rtlCol="0" anchor="{anchor}"><a:noAutofit/></a:bodyPr><a:lstStyle/>{paras}</p:txBody>'
    )
    tx = ' txBox="1"' if text_box else ""
    xml = (
        f'<p:sp {_NS_DECL}><p:nvSpPr><p:cNvPr id="{sid}" name="{escape(name)} {sid}"/>'
        f"<p:cNvSpPr{tx}/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm>"
        f'<a:off x="{int(box.x)}" y="{int(box.y)}"/><a:ext cx="{max(1, int(box.w))}" '
        f'cy="{max(1, int(box.h))}"/></a:xfrm><a:prstGeom prst="{geom}"><a:avLst>{av}</a:avLst>'
        f"</a:prstGeom>{_fill_xml(fill)}<a:ln><a:noFill/></a:ln></p:spPr>{body}</p:sp>"
    )
    return etree.fromstring(xml)


def _line(
    sid: int,
    name: str,
    p1: tuple[int, int],
    p2: tuple[int, int],
    color: dict,
    width_pt: float,
    arrow: bool = False,
) -> etree._Element:
    """Прямая линия ``p:cxnSp`` (горизонтальная или вертикальная)."""
    (x1, y1), (x2, y2) = p1, p2
    tail = '<a:tailEnd type="triangle" w="med" len="med"/>' if arrow else ""
    xml = (
        f'<p:cxnSp {_NS_DECL}><p:nvCxnSpPr><p:cNvPr id="{sid}" name="{escape(name)} {sid}"/>'
        f"<p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr><p:spPr><a:xfrm>"
        f'<a:off x="{min(x1, x2)}" y="{min(y1, y2)}"/>'
        f'<a:ext cx="{abs(x2 - x1)}" cy="{abs(y2 - y1)}"/></a:xfrm>'
        f'<a:prstGeom prst="line"><a:avLst/></a:prstGeom>'
        f'<a:ln w="{int(width_pt * EMU_PER_PT)}"><a:solidFill>{_clr_xml(color)}</a:solidFill>'
        f"{tail}</a:ln></p:spPr></p:cxnSp>"
    )
    return etree.fromstring(xml)


# ────────────────────────────── заголовок ──────────────────────────────


def _find_title_in_frame(tree: etree._Element, tokens: dict, sh: int) -> etree._Element | None:
    tid = tokens.get("title_shape_id")
    shapes = _top_shapes(tree)
    if tid is not None:
        for el, _ in shapes:
            if _shape_id(el) == tid:
                return el
    return _find_title(shapes, sh)


def _set_title(el: etree._Element, title: str, tokens: dict) -> None:
    """Заменить текст заголовка, сохранив pPr первого абзаца и rPr первого
    рана; если текст не влезает в рамку заголовка — уменьшить кегль."""
    tx = el.find(f"{{{P}}}txBody")
    if tx is None:
        tx = etree.SubElement(el, _q(P, "txBody"))
        etree.SubElement(tx, _q(A, "bodyPr"))
        etree.SubElement(tx, _q(A, "lstStyle"))
    paras = tx.findall(f"{{{A}}}p")
    ppr = paras[0].find(f"{{{A}}}pPr") if paras else None
    first = tx.find(f".//{{{A}}}r/{{{A}}}rPr")
    rpr = copy.deepcopy(first) if first is not None else etree.Element(_q(A, "rPr"))
    for p in paras:
        tx.remove(p)
    p = etree.SubElement(tx, _q(A, "p"))
    if ppr is not None:
        p.append(copy.deepcopy(ppr))

    box = _box(el)
    if box is None and tokens.get("title_box"):
        box = Box(*tokens["title_box"])
    size = _sz(rpr) or tokens.get("title_size_pt")
    if box is not None and size:
        body_pr = tx.find(f"{{{A}}}bodyPr")

        def ins(attr: str, default: int) -> int:
            v = body_pr.get(attr) if body_pr is not None else None
            return int(v) if v and v.lstrip("-").isdigit() else default

        width = box.w - ins("lIns", _DEFAULT_INS) - ins("rIns", _DEFAULT_INS)
        height = box.h - ins("tIns", 45720) - ins("bIns", 45720)
        ln = 1.0
        pct = ppr.find(f"{{{A}}}lnSpc/{{{A}}}spcPct") if ppr is not None else None
        if pct is not None and (pct.get("val") or "").isdigit():
            ln = int(pct.get("val")) / 100000
        bold = rpr.get("b") == "1"
        new = size
        floor = max(14.0, 0.55 * size)
        while new > floor:
            n, ok = _lines(title, new, width, bold)
            if ok and n * new * _LINE_FACTOR * ln * EMU_PER_PT <= height * _FIT_SLACK:
                break
            new -= 1
        if new < size:
            rpr.set("sz", str(int(round(new * 100))))
    for i, line in enumerate(title.split("\n")):
        if i:
            etree.SubElement(p, _q(A, "br")).append(copy.deepcopy(rpr))
        r = etree.SubElement(p, _q(A, "r"))
        r.append(copy.deepcopy(rpr))
        etree.SubElement(r, _q(A, "t")).text = line


# ────────────────────────────── архетипы ──────────────────────────────


def choose_archetype(items: list[dict], hint: str | None = None) -> str:
    """Архетип по форме контента.

    kpi — ≥2 пунктов с числовым ``value`` (и не больше 4 пунктов);
    process — подсказка ``hint="process"`` или все заголовки-шаги («Шаг 1»,
    «Этап», «1.»); two_column — ровно 2 пункта; list — больше 6 пунктов
    или ни у одного нет заголовка; иначе cards."""
    n = len(items)
    numeric = sum(1 for it in items if it.get("value") and _NUM_RE.search(str(it["value"])))
    if numeric >= 2 and n <= 4:
        return "kpi"
    heads = [str(it.get("heading") or "") for it in items]
    if 2 <= n <= 6 and (hint == "process" or all(_STEP_RE.match(h) for h in heads)):
        return "process"
    if n == 2:
        return "two_column"
    if n > 6 or not any(heads):
        return "list"
    return "cards"


@dataclass
class _Ctx:
    tree: etree._Element
    region: Box
    tokens: dict
    sw: int
    sh: int
    next_id: int

    def nid(self) -> int:
        self.next_id += 1
        return self.next_id

    @property
    def palette(self) -> dict:
        return self.tokens.get("palette") or {}

    @property
    def font(self) -> str | None:
        return self.tokens.get("body_font")

    @property
    def display_font(self) -> str | None:
        return self.tokens.get("title_font") or self.tokens.get("body_font")

    def v_offset(self, block_h: float, below_title: float) -> int:
        """Вертикальный сдвиг блока в области: под заголовком — ближе к верху
        (*below_title*), сбоку от заголовка — почти по центру."""
        free = max(0.0, self.region.h - block_h)
        tb = self.tokens.get("title_box")
        beside = bool(tb) and (self.region.x >= tb[0] + tb[2] or self.region.r <= tb[0])
        return self.region.y + int(free * (0.45 if beside else below_title))

    def surface(self, fill: dict | None) -> tuple[float, float, float] | None:
        """RGB подложки текста: заливка фигуры или фон слайда (белый — дефолт)."""
        if fill is not None:
            return _rgb(fill, self.palette)
        bg = self.tokens.get("background")
        if bg is not None:
            return _rgb(bg, self.palette)
        return (0.1, 0.1, 0.1) if self.tokens.get("dark_background") else (1.0, 1.0, 1.0)

    def colors(self, fill: dict | None) -> tuple[dict, dict, dict]:
        """(заголовок, текст, акцент) с контрастом к подложке *fill*."""
        s = self.surface(fill)
        t = self.tokens
        return (
            _readable(t["heading_color"], s, self.palette),
            _readable(t["text_color"], s, self.palette),
            _readable(t["accent"], s, self.palette),
        )


_REF_WIDTH = 12192000  # 13.33" — опорная ширина слайда для потолка кегля


def _sizes(tokens: dict, slide_w: int) -> list[tuple[float, float]]:
    """Пары (body, heading) по убыванию с шагом 1pt до 11pt.

    Старт — кегль тела шаблона; при избытке места текст может вырасти до
    ×1.25 (но не выше 18pt, приведённых к ширине слайда): редкий контент не
    теряется мелким шрифтом в пустой области."""
    body = float(tokens.get("body_size_pt") or 14.0)
    head = float(tokens.get("heading_size_pt") or _round_half(body * 1.3))
    ratio = head / body
    cap = 18.0 * slide_w / _REF_WIDTH
    out = []
    b = max(body, _round_half(min(body * 1.25, cap)))
    while b >= _MIN_BODY_PT - 1e-6:
        out.append((b, max(b + 1, _round_half(b * ratio))))
        b -= 1
    return out or [(_MIN_BODY_PT, _MIN_BODY_PT + 2)]


def _item_pars(
    ctx: _Ctx,
    it: dict,
    body: float,
    head: float,
    fill: dict | None,
    *,
    value_pt: float | None = None,
) -> list[_Par]:
    hc, tc, ac = ctx.colors(fill)
    pars: list[_Par] = []
    if value_pt and it.get("value"):
        pars.append(_Par([(str(it["value"]), value_pt, True, ac, ctx.display_font)]))
    if it.get("heading"):
        sb = 0.35 * body if pars else 0.0
        pars.append(_Par([(str(it["heading"]), head, True, hc, ctx.font)], space_before_pt=sb))
    if it.get("body"):
        sb = 0.5 * body if pars else 0.0
        pars.append(_Par([(str(it["body"]), body, False, tc, ctx.font)], space_before_pt=sb))
    return pars


def _fit_sizes(ctx: _Ctx, build, avail_h: float):
    """Первый (крупнейший) кегль, при котором build(body, head) влезает в
    avail_h; иначе — минимальный. build → (need_h, ok, payload).
    Результат: (body, head, need_h, payload, fits)."""
    last = None
    for body, head in _sizes(ctx.tokens, ctx.sw):
        need, ok, payload = build(body, head)
        fits = ok and need <= avail_h
        last = (body, head, need, payload, fits)
        if fits:
            return last
    return last


def _grids(n: int, kpi: bool) -> list[tuple[int, int]]:
    """Кандидаты сетки (ряды, колонки) в порядке предпочтения."""
    if kpi:
        return [(1, n), (2, 2), (n, 1)] if n == 4 else [(1, n), (n, 1)]
    if n <= 3:
        return [(1, n), (n, 1)]
    if n == 4:
        return [(2, 2), (4, 1), (1, 4)]
    return [(2, 3), (3, 2)]


def _plan_cards(ctx: _Ctx, items: list[dict], rows: int, cols: int, kpi: bool) -> dict:
    t, reg = ctx.tokens, ctx.region
    gx, gy = int(0.02 * ctx.sw), int(0.03 * ctx.sh)
    cw = (reg.w - (cols - 1) * gx) / cols
    avail_h = (reg.h - (rows - 1) * gy) / rows
    fill = t["card_fill"]
    pad = int(max(0.018 * ctx.sw, 1.1 * t["body_size_pt"] * EMU_PER_PT))
    pad = int(min(pad, 0.12 * cw, 0.2 * avail_h))
    with_value = kpi or any(it.get("value") for it in items)

    def build(body: float, head: float):
        marker = 0 if kpi else int(0.28 * head * EMU_PER_PT) + int(0.9 * head * EMU_PER_PT)
        vpt = _round_half(body * 2.6) if kpi else _round_half(head * 1.25)
        pars = [
            _item_pars(ctx, it, body, head, fill, value_pt=vpt if with_value else None)
            for it in items
        ]
        hs = [_pars_height(p, cw - 2 * pad) for p in pars]
        need = max(h for h, _ in hs) / _FIT_SLACK + 2 * pad + marker
        return need, all(ok for _, ok in hs), (pars, marker)

    body, head, need, (pars, marker), fits = _fit_sizes(ctx, build, avail_h)
    return {
        "fits": fits,
        "body": body,
        "head": head,
        "need": need,
        "pars": pars,
        "marker": marker,
        "rows": rows,
        "cols": cols,
        "cw": cw,
        "avail_h": avail_h,
        "pad": pad,
        "gx": gx,
        "gy": gy,
    }


def _compose_cards(ctx: _Ctx, items: list[dict], *, kpi: bool = False) -> bool:
    t, reg = ctx.tokens, ctx.region
    n = len(items)
    plans = [_plan_cards(ctx, items, r, c, kpi) for r, c in _grids(n, kpi)]
    # лучшая сетка: влезает, крупнее кегль; при равенстве — порядок предпочтения
    best = max(enumerate(plans), key=lambda ip: (ip[1]["fits"], ip[1]["body"], -ip[0]))[1]
    rows, cols, cw, avail_h = best["rows"], best["cols"], best["cw"], best["avail_h"]
    pad, marker, head, gx, gy = best["pad"], best["marker"], best["head"], best["gx"], best["gy"]
    # высота карточек: по контенту с воздухом, но не «щелью» и не выше доступной
    card_h = int(min(avail_h, max(best["need"] * 1.2, (0.5 if kpi else 0.6) * avail_h)))
    block_h = rows * card_h + (rows - 1) * gy
    y0 = ctx.v_offset(block_h, 0.35 if kpi else 0.15)
    fill, geom = t["card_fill"], t.get("corner", "rect")
    for i in range(n):
        r, c = divmod(i, cols)
        in_row = min(cols, n - r * cols)
        shift = (cols - in_row) * (cw + gx) / 2  # неполный ряд — по центру
        box = Box(int(reg.x + shift + c * (cw + gx)), int(y0 + r * (card_h + gy)), int(cw), card_h)
        card = _sp(
            ctx.nid(),
            "Card",
            box,
            fill,
            geom,
            best["pars"][i],
            ins=(pad, pad + marker, pad, pad),
            radius=t.get("corner_radius_emu", 0),
        )
        ctx.tree.append(card)
        if marker:
            mb = Box(
                box.x + pad,
                box.y + pad,
                int(2.2 * head * EMU_PER_PT),
                int(0.28 * head * EMU_PER_PT),
            )
            ctx.tree.append(_sp(ctx.nid(), "Marker", mb, t["accent"], "rect", None))
    return best["fits"]


def _step_colors(ctx: _Ctx) -> tuple[dict, dict]:
    """(заливка круга, цвет цифры). Светлый акцент — тёмная цифра на нём;
    тёмный — белая цифра, при нехватке контраста круг чуть затемняется."""
    t = ctx.tokens
    fill = dict(t["accent"])
    rgb = _rgb(fill, ctx.palette)
    white = (1.0, 1.0, 1.0)
    if rgb is None or _ratio(white, rgb) >= _CONTRAST:
        return fill, {"srgb": "FFFFFF"}
    if _lum(rgb) > 0.25:
        dark = t.get("background") if t.get("dark_background") else {"srgb": "1A1A1A"}
        return fill, _readable(dark or {"srgb": "1A1A1A"}, rgb, ctx.palette)
    for lm in (90000, 80000, 70000, 60000):
        cand = {**t["accent"], "lumMod": lm}
        if _ratio(white, _rgb(cand, ctx.palette)) >= _CONTRAST:
            return cand, {"srgb": "FFFFFF"}
    return fill, _readable({"srgb": "000000"}, rgb, ctx.palette)


def _compose_process(ctx: _Ctx, items: list[dict]) -> bool:
    """Шаги в ряд (круг с номером, стрелка, текст под кругом); если в ряд не
    влезает — столбцом (круги слева, текст справа, стрелки вниз)."""
    reg = ctx.region
    n = len(items)
    gx = int(0.025 * ctx.sw)
    gy = int(0.025 * ctx.sh)
    cw = (reg.w - (n - 1) * gx) / n
    row_h = (reg.h - (n - 1) * gy) / n

    def d_row(head: float) -> int:
        return int(min(2.6 * head * EMU_PER_PT, 0.45 * cw, 0.3 * reg.h))

    def d_col(head: float) -> int:
        return int(min(2.4 * head * EMU_PER_PT, 0.9 * row_h, 0.2 * reg.w))

    def build_row(body: float, head: float):
        d = d_row(head)
        text_top = d + int(0.9 * head * EMU_PER_PT)
        pars = [_item_pars(ctx, it, body, head, None) for it in items]
        hs = [_pars_height(p, cw) for p in pars]
        need = max(h for h, _ in hs) / _FIT_SLACK
        return text_top + need, all(ok for _, ok in hs), (pars, need)

    def build_col(body: float, head: float):
        d = d_col(head)
        tw = reg.w - d - int(1.2 * head * EMU_PER_PT)
        pars = [_item_pars(ctx, it, body, head, None) for it in items]
        hs = [_pars_height(p, tw) for p in pars]
        need = max(max(h for h, _ in hs) / _FIT_SLACK, d)
        return n * need + (n - 1) * gy, all(ok for _, ok in hs), (pars, need)

    row = _fit_sizes(ctx, build_row, reg.h)
    col = _fit_sizes(ctx, build_col, reg.h)
    row_ok, col_ok = row[4], col[4]
    fill, num = _step_colors(ctx)
    use_row = row_ok and (not col_ok or row[0] >= col[0] - 1)
    if not row_ok and not col_ok:
        use_row = cw >= 0.2 * ctx.sw
    body, head, total, (pars, need), fits = row if use_row else col

    def label(i: int) -> list[_Par]:
        run = (str(i + 1), _round_half(head * 1.1), True, num, ctx.display_font)
        return [_Par([run], algn="ctr")]

    if use_row:
        d = d_row(head)
        text_top = d + int(0.9 * head * EMU_PER_PT)
        block_h = min(reg.h, int(text_top + need))
        y0 = ctx.v_offset(block_h, 0.3)
        text_h = int(min(reg.b - (y0 + text_top), need * 1.1))
        for i in range(n):
            x = int(reg.x + i * (cw + gx))
            ctx.tree.append(
                _sp(ctx.nid(), "Step", Box(x, y0, d, d), fill, "ellipse", label(i), anchor="ctr")
            )
            if i < n - 1:
                nx = int(reg.x + (i + 1) * (cw + gx))
                cy, lg = y0 + d // 2, int(0.25 * d)
                ctx.tree.append(
                    _line(ctx.nid(), "Connector", (x + d + lg, cy), (nx - lg, cy), fill, 1.5, True)
                )
            box = Box(x, y0 + text_top, int(cw), text_h)
            ctx.tree.append(_sp(ctx.nid(), "Step text", box, None, "rect", pars[i], text_box=True))
        return fits

    d = d_col(head)
    tx = reg.x + d + int(1.2 * head * EMU_PER_PT)
    step_h = int(min(row_h, need * 1.1))
    block_h = n * step_h + (n - 1) * gy
    y0 = ctx.v_offset(block_h, 0.3)
    for i in range(n):
        y = int(y0 + i * (step_h + gy))
        ctx.tree.append(
            _sp(ctx.nid(), "Step", Box(reg.x, y, d, d), fill, "ellipse", label(i), anchor="ctr")
        )
        if i < n - 1:
            cx, lg = reg.x + d // 2, int(0.2 * d)
            ny = int(y0 + (i + 1) * (step_h + gy))
            if ny - lg - (y + d + lg) > 0.15 * d:
                ctx.tree.append(
                    _line(ctx.nid(), "Connector", (cx, y + d + lg), (cx, ny - lg), fill, 1.5, True)
                )
        box = Box(tx, y, reg.r - tx, step_h)
        ctx.tree.append(_sp(ctx.nid(), "Step text", box, None, "rect", pars[i], text_box=True))
    return fits


def _compose_two_column(ctx: _Ctx, items: list[dict]) -> bool:
    reg = ctx.region
    gap = int(0.06 * ctx.sw)
    cw = (reg.w - gap) / 2

    def geometry(head: float) -> tuple[int, int]:
        bar_h = int(0.28 * head * EMU_PER_PT)
        return bar_h, bar_h + int(0.9 * head * EMU_PER_PT)

    def build(body: float, head: float):
        _, text_top = geometry(head)
        pars = [_item_pars(ctx, it, body, head, None) for it in items]
        hs = [_pars_height(p, cw) for p in pars]
        need = max(h for h, _ in hs) / _FIT_SLACK
        return text_top + need, all(ok for _, ok in hs), (pars, need)

    body, head, _, (pars, need), fits = _fit_sizes(ctx, build, reg.h)
    bar_h, text_top = geometry(head)
    block_h = int(min(reg.h, text_top + need * 1.1))
    y0 = ctx.v_offset(block_h, 0.1)
    _, tc, accent = ctx.colors(None)
    for i in range(2):
        x = int(reg.x + i * (cw + gap))
        bar = Box(x, y0, int(2.2 * head * EMU_PER_PT), bar_h)
        ctx.tree.append(_sp(ctx.nid(), "Marker", bar, accent, "rect", None))
        col = Box(x, y0 + text_top, int(cw), block_h - text_top)
        ctx.tree.append(_sp(ctx.nid(), "Column", col, None, "rect", pars[i], text_box=True))
    mid = int(reg.x + cw + gap / 2)
    divider = {**tc, "alpha": 35000}
    ctx.tree.append(_line(ctx.nid(), "Divider", (mid, y0), (mid, y0 + block_h), divider, 0.75))
    return fits


def _compose_list(ctx: _Ctx, items: list[dict]) -> bool:
    reg = ctx.region
    width = int(min(reg.w, max(0.6 * ctx.sw, reg.w * 0.85)))
    hc, tc, accent = ctx.colors(None)

    def build(body: float, head: float):
        size = _round_half(body * 1.1)
        indent = int(1.1 * size * EMU_PER_PT)
        pars = []
        for it in items:
            runs: list[_Run] = []
            h, b = it.get("heading"), it.get("body")
            if it.get("value"):
                runs.append((f"{it['value']} ", size, True, accent, ctx.display_font))
            if h:
                runs.append((f"{h}: " if b else str(h), size, True, hc, ctx.font))
            if b:
                runs.append((str(b), size, False, tc, ctx.font))
            if runs:
                sb = 0.8 * size if pars else 0.0
                pars.append(_Par(runs, space_before_pt=sb, bullet=accent, indent_emu=indent))
        hh, ok = _pars_height(pars, width)
        return hh / _FIT_SLACK, ok, pars

    _, _, need, pars, fits = _fit_sizes(ctx, build, reg.h)
    # короткий список — просторнее: интервал между пунктами растёт до 1.6 кегля
    for _ in range(4):
        if need * 1.25 > reg.h * 0.6:
            break
        for p in pars[1:]:
            p.space_before_pt = min(1.6 * p.size, p.space_before_pt * 1.25)
        need = _pars_height(pars, width)[0] / _FIT_SLACK
    h = int(min(reg.h, need * 1.1))
    box = Box(reg.x, ctx.v_offset(h, 0.05), width, h)
    ctx.tree.append(_sp(ctx.nid(), "List", box, None, "rect", pars, text_box=True))
    return fits


_LIMITS = {"cards": (1, 6), "process": (2, 6), "kpi": (2, 4), "two_column": (2, 2), "list": (1, 99)}


def compose(
    frame_xml: bytes,
    region: Box,
    tokens: dict,
    archetype: str,
    title: str,
    items: list[dict],
    slide_w: int,
    slide_h: int,
) -> bytes:
    """Новая композиция в области *region* рамки *frame_xml*.

    *items* — ``[{"heading": str|None, "body": str|None, "value": str|None}]``.
    Архетипы: cards (1–6), process (2–6), kpi (2–4), two_column (2),
    list (1–7+). Если число пунктов не подходит архетипу — cards (1–6)
    или list. Если текст не влезает даже на 11pt — следующий, более ёмкий
    архетип, в конце — список с сокращёнными (…) текстами. Все фигуры
    строго внутри *region*, id уникальны в слайде."""
    if archetype not in ARCHETYPES:
        raise ValueError(f"unknown archetype: {archetype}")
    items = [it for it in items if it.get("heading") or it.get("body") or it.get("value")]
    n = len(items)
    lo, hi = _LIMITS[archetype]
    if not lo <= n <= hi:
        archetype = "cards" if 1 <= n <= 6 else "list"
    root = etree.fromstring(frame_xml)
    tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    if tree is None:
        raise ValueError("frame has no p:spTree")
    title_el = _find_title_in_frame(tree, tokens, slide_h)
    if title_el is not None:
        _set_title(title_el, title, tokens)
    if not items:
        return _dump(root)
    base_id = _max_id(root)
    chain = {
        "cards": ["cards", "list"],
        "kpi": ["kpi", "cards", "list"],
        "process": ["process", "list"],
        "two_column": ["two_column", "list"],
        "list": ["list"],
    }[archetype]
    chain = [a for a in chain if _LIMITS[a][0] <= n <= _LIMITS[a][1]] or ["list"]
    draw = {
        "cards": _compose_cards,
        "kpi": lambda c, its: _compose_cards(c, its, kpi=True),
        "process": _compose_process,
        "two_column": _compose_two_column,
        "list": _compose_list,
    }
    # не влезает даже на 11pt — следующий, более ёмкий архетип; в конце —
    # список с сокращёнными текстами (многоточие), но не переполнение
    attempts = [(a, items) for a in chain]
    keep = 0.75
    while keep > 0.04:
        attempts.append(("list", _shorten(items, keep)))
        keep *= 0.8
    for arch, its in attempts:
        ctx = _Ctx(tree, region, tokens, slide_w, slide_h, base_id)
        mark = len(tree)
        if draw[arch](ctx, its):
            break
        for el in list(tree)[mark:]:
            tree.remove(el)
    else:
        draw["list"](_Ctx(tree, region, tokens, slide_w, slide_h, base_id), attempts[-1][1])
    return _dump(root)


def _shorten(items: list[dict], keep: float) -> list[dict]:
    """Тексты пунктов, укороченные до доли *keep* слов (с многоточием)."""
    out = []
    for it in items:
        body = str(it.get("body") or "")
        words = body.split()
        k = max(3, int(len(words) * keep))
        out.append({**it, "body": " ".join(words[:k]) + "…" if len(words) > k else body})
    return out
