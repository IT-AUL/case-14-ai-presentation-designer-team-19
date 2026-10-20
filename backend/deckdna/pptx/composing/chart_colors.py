"""Цвета графика по фону слайда и теме шаблона (ADR-033 §6).

``schemeClr tx1`` внутри части графика не проходит через карту цветов
мастера: на тёмной теме (``tx1 → lt1``) подписи выходили чёрными на
чёрном. Здесь цвета считаются явно: фон слайда (слайд → макет → мастер),
цвет текста — тот из ``dk1``/``lt1`` темы, что контрастнее с фоном;
цвета серий — акценты темы по порядку, пропуская сливающиеся с фоном."""

from __future__ import annotations

from dataclasses import dataclass

from lxml import etree

from deckdna.pptx.opc.package import parse_rels, rels_name_for, resolve_target

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_SLOTS = (
    "dk1",
    "lt1",
    "dk2",
    "lt2",
    "accent1",
    "accent2",
    "accent3",
    "accent4",
    "accent5",
    "accent6",
    "hlink",
    "folHlink",
)
_ALIASES = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}
MIN_SERIES_CONTRAST = 1.6


@dataclass(frozen=True)
class ChartColors:
    ink: str | None = None  # подписи, оси, заголовки осей
    grid: str | None = None  # линии сетки
    series: tuple[str, ...] = ()  # цвета серий
    muted: str | None = None  # приглушённый акцент для pre-attentive focus


def _rel_target(parts: dict[str, bytes], owner: str, kind: str) -> str | None:
    raw = parts.get(rels_name_for(owner))
    if raw is None:
        return None
    for rel in parse_rels(raw):
        if rel.type.endswith("/" + kind) and not rel.is_external:
            return resolve_target(owner, rel.target)
    return None


def _theme(parts: dict[str, bytes], master: str) -> dict[str, str]:
    theme_part = _rel_target(parts, master, "theme")
    if theme_part is None or theme_part not in parts:
        return {}
    root = etree.fromstring(parts[theme_part])
    scheme = root.find(f".//{{{A}}}clrScheme")
    out: dict[str, str] = {}
    if scheme is None:
        return out
    for slot in _SLOTS:
        el = scheme.find(f"{{{A}}}{slot}")
        if el is None:
            continue
        srgb = el.find(f"{{{A}}}srgbClr")
        sys_clr = el.find(f"{{{A}}}sysClr")
        if srgb is not None:
            out[slot] = srgb.get("val", "").upper()
        elif sys_clr is not None:
            out[slot] = (sys_clr.get("lastClr") or "").upper()
    return out


def _clr_map(parts: dict[str, bytes], master: str) -> dict[str, str]:
    root = etree.fromstring(parts[master])
    cmap = root.find(f"{{{P}}}clrMap")
    return dict(cmap.attrib) if cmap is not None else {}


def _resolve(el: etree._Element | None, theme: dict[str, str], cmap: dict[str, str]) -> str | None:
    if el is None:
        return None
    srgb = el.find(f".//{{{A}}}srgbClr")
    if srgb is not None:
        return srgb.get("val", "").upper()
    scheme = el.find(f".//{{{A}}}schemeClr")
    if scheme is not None:
        val = scheme.get("val", "")
        slot = cmap.get(val, _ALIASES.get(val, val)) if val in _ALIASES else val
        return theme.get(slot)
    return None


def _bg_of(xml: bytes, theme: dict[str, str], cmap: dict[str, str]) -> tuple[bool, str | None]:
    """(есть ли явный фон, его цвет или None — картинка)."""
    root = etree.fromstring(xml)
    bg = root.find(f"{{{P}}}cSld/{{{P}}}bg")
    if bg is None:
        return False, None
    pr = bg.find(f"{{{P}}}bgPr")
    if pr is not None:
        for child in pr:
            tag = etree.QName(child).localname
            if tag == "solidFill":
                return True, _resolve(child, theme, cmap)
            if tag == "gradFill":
                return True, _resolve(child.find(f".//{{{A}}}gs"), theme, cmap)
            if tag in ("blipFill", "pattFill"):
                return True, None
    ref = bg.find(f"{{{P}}}bgRef")
    if ref is not None:
        return True, _resolve(ref, theme, cmap)
    return False, None


def _lum(hex_color: str) -> float:
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))

    def ch(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _mix(a: str, b: str, t: float) -> str:
    """a → b на долю t (для неяркой сетки)."""
    ca = [int(a[i : i + 2], 16) for i in (0, 2, 4)]
    cb = [int(b[i : i + 2], 16) for i in (0, 2, 4)]
    return "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(ca, cb, strict=True))


def categorical_palette(colors: tuple[str, ...], bg: str | None, count: int) -> tuple[str, ...]:
    """Extend a runtime palette with its own shades, never a fixed brand palette.

    Preserve distinct donor colors first. Farthest-point sampling then selects
    light/dark shades so narrow or monochrome themes do not repeat legend keys.
    Background contrast applies to both original colors and derived shades.
    """
    chosen = list(dict.fromkeys(c for c in colors
                               if bg is None or contrast(c, bg) >= MIN_SERIES_CONTRAST))
    if not chosen or len(chosen) >= count:
        return tuple(chosen)

    def rgb(c):
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))

    def distance(a, b):
        return sum((x - y) ** 2 for x, y in zip(rgb(a), rgb(b), strict=True))

    candidates = list(dict.fromkeys(
        _mix(c, target, step / 10)
        for c in chosen for target in ("000000", "FFFFFF") for step in range(1, 10)
    ))
    candidates = [c for c in candidates if c not in chosen
                  and (bg is None or contrast(c, bg) >= MIN_SERIES_CONTRAST)]
    while len(chosen) < count and candidates:
        next_color = max(candidates, key=lambda c: min(distance(c, x) for x in chosen))
        chosen.append(next_color)
        candidates.remove(next_color)
    return tuple(chosen)


def _panel_shapes(tree, tx=0.0, ty=0.0, kx=1.0, ky=1.0):
    """Rectangles in slide coordinates, including scaled nested groups."""
    if tree is None:
        return
    for shape in tree:
        if shape.tag == f"{{{P}}}grpSp":
            transform = shape.find(f"{{{P}}}grpSpPr/{{{A}}}xfrm")
            if transform is None or transform.get("rot", "0") != "0":
                continue
            try:
                off, ext, child_off, child_ext = (
                    transform.find(f"{{{A}}}{tag}") for tag in ("off", "ext", "chOff", "chExt")
                )
                sx = kx * int(ext.get("cx")) / int(child_ext.get("cx"))
                sy = ky * int(ext.get("cy")) / int(child_ext.get("cy"))
                x = tx + kx * int(off.get("x")) - sx * int(child_off.get("x"))
                y = ty + ky * int(off.get("y")) - sy * int(child_off.get("y"))
            except (AttributeError, ValueError, TypeError, ZeroDivisionError):
                continue
            yield from _panel_shapes(shape, x, y, sx, sy)
        elif shape.tag == f"{{{P}}}sp":
            yield shape, tx, ty, kx, ky


def _panel_color(
    xml: bytes,
    region: tuple[int, int, int, int],
    theme: dict,
    cmap: dict,
) -> str | None:
    """Topmost opaque rectangular panel containing the entire chart.

    Partial, rotated, translucent and image fills cannot establish a uniform
    background, so they do not override the inherited slide background.
    """
    root = etree.fromstring(xml)
    color = None
    x, y, w, h = region
    for shape, tx, ty, kx, ky in _panel_shapes(root.find(f"{{{P}}}cSld/{{{P}}}spTree")):
        pr = shape.find(f"{{{P}}}spPr")
        if pr is None:
            continue
        geom = pr.find(f"{{{A}}}prstGeom")
        transform = pr.find(f"{{{A}}}xfrm")
        fill = pr.find(f"{{{A}}}solidFill")
        if (
            geom is None
            or geom.get("prst") not in {"rect", "roundRect"}
            or transform is None
            or transform.get("rot", "0") != "0"
            or fill is None
            or any(etree.QName(n).localname.startswith("alpha") for n in fill.iter())
        ):
            continue
        off, ext = transform.find(f"{{{A}}}off"), transform.find(f"{{{A}}}ext")
        if off is None or ext is None:
            continue
        try:
            sx, sy = int(off.get("x")), int(off.get("y"))
            sw, sh = int(ext.get("cx")), int(ext.get("cy"))
        except (ValueError, TypeError):
            continue
        sx, sy, sw, sh = tx + kx * sx, ty + ky * sy, kx * sw, ky * sh
        if sx <= x and sy <= y and sx + sw >= x + w and sy + sh >= y + h:
            resolved = _resolve(fill, theme, cmap)
            if resolved:
                color = resolved
    return color


def slide_background(
    parts: dict[str, bytes],
    slide_part: str,
    region: tuple[int, int, int, int] | None = None,
) -> tuple[str | None, dict, dict]:
    """(цвет фона слайда, тема, карта цветов). Цвет None — фон картинкой
    или неизвестен."""
    layout = _rel_target(parts, slide_part, "slideLayout")
    master = _rel_target(parts, layout, "slideMaster") if layout else None
    if master is None or master not in parts:
        return None, {}, {}
    theme = _theme(parts, master)
    cmap = _clr_map(parts, master)
    bg = theme.get(cmap.get("bg1", "lt1"))
    for owner in (slide_part, layout, master):
        if owner is None or owner not in parts:
            continue
        found, color = _bg_of(parts[owner], theme, cmap)
        if found:
            bg = color
            break
    if region is not None:
        for owner in (master, layout, slide_part):
            if owner and owner in parts:
                bg = _panel_color(parts[owner], region, theme, cmap) or bg
    return bg, theme, cmap


def chart_colors(
    parts: dict[str, bytes],
    slide_part: str,
    region: tuple[int, int, int, int] | None = None,
) -> ChartColors:
    bg, theme, cmap = slide_background(parts, slide_part, region)
    if not theme:
        return ChartColors()
    dark, light = theme.get("dk1"), theme.get("lt1")
    if bg is None:
        # фон картинкой: текст по карте мастера
        ink = theme.get(cmap.get("tx1", "dk1"))
        return ChartColors(ink=ink, grid=None, series=())
    candidates = [c for c in (dark, light, theme.get("dk2"), theme.get("lt2")) if c]
    ink = max(candidates, key=lambda c: contrast(c, bg)) if candidates else None
    if ink is None or contrast(ink, bg) < 2.0:
        # сломанная тема (фон и «текстовый» слот — один цвет) не должна
        # давать невидимые подписи: берём чистый чёрный или белый по фону
        ink = max(("000000", "FFFFFF"), key=lambda c: contrast(c, bg))
    accents = [theme.get(f"accent{i}") for i in range(1, 7)]
    series = tuple(c for c in accents if c and contrast(c, bg) >= MIN_SERIES_CONTRAST)
    if not series and ink:
        series = (ink,)
    grid = _mix(bg, ink, 0.2) if ink else None
    # приглушённый контекстный цвет — акцент, сдвинутый к фону на 55%;
    # читается на любом фоне и не спорит с акцентным лидером
    muted = _mix(series[0], bg, 0.55) if series else None
    return ChartColors(ink=ink, grid=grid, series=series, muted=muted)


__all__ = ["ChartColors", "chart_colors", "contrast", "slide_background"]
