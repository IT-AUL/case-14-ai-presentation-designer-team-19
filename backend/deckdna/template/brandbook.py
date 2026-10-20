"""Брендбук (ADR-033 §9): разбор и слияние с шаблоном.

Входы, по убыванию доверия:

* **токены JSON:** W3C Design Tokens (``$value``/``$type``), Style
  Dictionary (``value``), Tokens Studio (``value``/``type``), простой
  ``{colors, fonts}``;
* **Figma:** экспорт переменных (``variables``/``valuesByMode`` с RGBA 0–1)
  тем же разбором; по ссылке и токену — REST API (``fetch_figma_variables``,
  без токена не вызывается);
* **PDF-гайдлайн:** HEX, RGB и CMYK с подписями («основной», «акцентный»),
  имена шрифтов, правила про логотип — из текста страниц;
* **логотипы** (картинки) и **примеры** (``.pptx``) — в ассеты брендбука;
  у примеров берутся шрифты и цвета темы, если токенов нет.

Слияние: ``apply_brandbook`` правит **пакет шаблона**, а не готовую колоду.
Палитра шаблона (акценты темы и насыщенные цвета, которыми нарисованы его
слайды) переводится в палитру бренда по ролям, шрифты темы и явные
гарнитуры — в шрифты бренда. Нейтральные цвета (чёрный, белый, серые) не
трогаются: контраст текста и фона остаётся шаблонным. Дальше колода
собирается как обычно, графики берут цвета из темы. Выбор типа графика
шаблона не видит, брендбук его не меняет.

``brand_priority = template`` — пакет не меняется, расхождения только
перечисляются в паспорте."""

from __future__ import annotations

import colorsys
import io
import json
import re
import statistics
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from deckdna.contracts.brandbook import (
    Brandbook,
    BrandColor,
    BrandConflict,
    BrandFont,
    BrandLogo,
    BrandRule,
    BrandSource,
)

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_ACCENTS = tuple(f"accent{i}" for i in range(1, 7))
_IMAGE_EXT = {"png", "jpg", "jpeg", "svg", "gif", "webp"}
_MONO = re.compile(r"mono|consolas|courier", re.I)
_HEX = re.compile(r"#?\b([0-9A-Fa-f]{6})\b")
_RGB = re.compile(r"\bRGB\s*[:(]?\s*(\d{1,3})[\s,;/]+(\d{1,3})[\s,;/]+(\d{1,3})", re.I)
_CMYK = re.compile(
    r"\bCMYK\s*[:(]?\s*(\d{1,3})[\s,;/%]+(\d{1,3})[\s,;/%]+(\d{1,3})[\s,;/%]+(\d{1,3})", re.I
)
_RPR = re.compile(
    rb"<a:(?:rPr|defRPr|endParaRPr)\b([^>]*?)(?<!/)>(.*?)</a:(?:rPr|defRPr|endParaRPr)>", re.S
)
_ROLE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("primary", ("primary", "основн", "главн", "brand", "фирмен")),
    ("secondary", ("secondary", "вторич", "дополнит")),
    ("accent", ("accent", "акцент", "highlight")),
    ("text", ("text", "ink", "foreground", "текст", "шрифт")),
    ("background", ("background", "bg", "surface", "фон")),
)
_FONT_ROLE_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("heading", ("heading", "headline", "title", "display", "заголов")),
    ("body", ("body", "text", "paragraph", "основн", "текст", "наборн")),
)


def _norm_hex(value: str) -> str | None:
    value = value.strip()
    m = re.fullmatch(r"#?([0-9A-Fa-f]{6})(?:[0-9A-Fa-f]{2})?", value)
    if m:
        return "#" + m.group(1).upper()
    m = re.fullmatch(r"#?([0-9A-Fa-f]{3})", value)
    if m:
        return "#" + "".join(ch * 2 for ch in m.group(1)).upper()
    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+).*\)", value)
    if m:
        return _rgb_hex(*(int(g) for g in m.groups()))
    return None


def _rgb_hex(r: float, g: float, b: float) -> str:
    return "#" + "".join(f"{max(0, min(255, round(v))):02X}" for v in (r, g, b))


def _cmyk_hex(c: int, m: int, y: int, k: int) -> str:
    c, m, y, k = (v / 100 for v in (c, m, y, k))
    return _rgb_hex(255 * (1 - c) * (1 - k), 255 * (1 - m) * (1 - k), 255 * (1 - y) * (1 - k))


def _rgb(hex_: str) -> tuple[float, float, float]:
    h = hex_.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def is_chromatic(hex_: str) -> bool:
    """Насыщенный цвет (не чёрный, не белый, не серый)."""
    _h, light, sat = colorsys.rgb_to_hls(*_rgb(hex_))
    return sat >= 0.25 and 0.12 <= light <= 0.92


def _luminance(hex_: str) -> float:
    def ch(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (ch(v) for v in _rgb(hex_))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _hls(hex_: str) -> tuple[float, float, float]:
    return colorsys.rgb_to_hls(*_rgb(hex_))


def _tint(brand: str, like: str) -> str:
    """Цвет бренда в светлоте цвета шаблона: подложка остаётся подложкой."""
    hue, light, sat = _hls(brand)
    _h, tpl_light, tpl_sat = _hls(like)
    if abs(tpl_light - light) < 0.15:
        return brand
    r, g, b = colorsys.hls_to_rgb(hue, tpl_light, min(sat, max(tpl_sat, 0.35)))
    return _rgb_hex(r * 255, g * 255, b * 255)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _distance(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(_rgb(a), _rgb(b), strict=True)) ** 0.5


def _role(name: str, words: tuple[tuple[str, tuple[str, ...]], ...]) -> str | None:
    """Роль по имени токена: сначала последний сегмент пути
    (``brand/secondary`` — вторичный, не основной), затем всё имя."""
    last = re.split(r"[./]", name.lower())[-1]
    for low in (last, name.lower()):
        for role, keys in words:
            if any(k in low for k in keys):
                return role
    return None


# --------------------------------------------------------------------- tokens


def _figma_color(value: Any) -> str | None:
    if isinstance(value, dict) and {"r", "g", "b"} <= set(value):
        return _rgb_hex(value["r"] * 255, value["g"] * 255, value["b"] * 255)
    return None


def _font_value(value: Any) -> str | None:
    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, dict):
        value = value.get("fontFamily") or value.get("family")
    if isinstance(value, str) and value.strip() and not value.startswith("{"):
        return value.split(",")[0].strip().strip("'\"")
    return None


@dataclass
class _Found:
    colors: list[BrandColor] = field(default_factory=list)
    fonts: list[BrandFont] = field(default_factory=list)
    rules: list[BrandRule] = field(default_factory=list)


def _walk_tokens(node: Any, path: list[str], source: str, out: _Found) -> None:
    if not isinstance(node, dict):
        return
    value = node.get("$value", node.get("value"))
    kind = str(node.get("$type", node.get("type", "")) or "").lower()
    name = ".".join(path)
    font_kind = kind in {"fontfamily", "fontfamilies", "typography"}
    if value is not None and (font_kind or not isinstance(value, dict | list)):
        if font_kind or "font" in name.lower():
            family = _font_value(value)
            if family:
                role = _role(name, _FONT_ROLE_WORDS) or "other"
                out.fonts.append(BrandFont(role=role, family=family, source=source))
            return
        hex_ = _norm_hex(str(value)) if isinstance(value, str) else None
        if hex_ and (kind in {"", "color"}):
            out.colors.append(BrandColor(
                name=name or hex_, hex=hex_, role=_role(name, _ROLE_WORDS) or "other",
                source=source,
            ))
        return
    for key, child in node.items():
        if key.startswith("$"):
            continue
        _walk_tokens(child, [*path, str(key)], source, out)


def _figma_variables(data: dict[str, Any], source: str, out: _Found) -> bool:
    """Figma Variables: экспорт (``variables``/``collections``) или ответ REST
    ``/v1/files/{key}/variables/local`` (``meta.variables``)."""
    variables: list[dict[str, Any]] = []
    if isinstance(data.get("meta"), dict) and isinstance(data["meta"].get("variables"), dict):
        variables = list(data["meta"]["variables"].values())
    elif isinstance(data.get("variables"), list):
        variables = data["variables"]
    elif isinstance(data.get("collections"), list):
        for coll in data["collections"]:
            variables.extend(coll.get("variables") or [])
    if not variables or not all(isinstance(v, dict) and "valuesByMode" in v for v in variables):
        return False
    for var in variables:
        modes = var.get("valuesByMode") or {}
        value = next(iter(modes.values()), None)
        name = str(var.get("name", ""))
        if var.get("resolvedType") == "COLOR" and (hex_ := _figma_color(value)):
            out.colors.append(BrandColor(name=name, hex=hex_,
                                         role=_role(name, _ROLE_WORDS) or "other",
                                         source=source))
        elif var.get("resolvedType") == "STRING" and "font" in name.lower():
            family = _font_value(value)
            if family:
                out.fonts.append(BrandFont(role=_role(name, _FONT_ROLE_WORDS) or "other",
                                           family=family, source=source))
    return True


def _simple(data: dict[str, Any], source: str, out: _Found) -> bool:
    colors, fonts = data.get("colors") or data.get("palette"), data.get("fonts")
    if colors is None and fonts is None:
        return False
    items = colors.items() if isinstance(colors, dict) else (
        ((c.get("name") or c.get("role") or "", c.get("hex") or c.get("value"))
         for c in colors if isinstance(c, dict)) if isinstance(colors, list) else ()
    )
    for name, value in items:
        if isinstance(value, dict):
            value = value.get("hex") or value.get("value") or value.get("$value")
        hex_ = _norm_hex(str(value)) if value else None
        if hex_:
            out.colors.append(BrandColor(name=str(name), hex=hex_,
                                         role=_role(str(name), _ROLE_WORDS) or "other",
                                         source=source))
    if isinstance(fonts, dict):
        for name, value in fonts.items():
            family = _font_value(value)
            if family:
                out.fonts.append(BrandFont(role=_role(str(name), _FONT_ROLE_WORDS) or "other",
                                           family=family, source=source))
    for rule in data.get("rules") or []:
        if isinstance(rule, str):
            out.rules.append(BrandRule(text=rule, source=source))
    return True


def looks_like_tokens(data: Any) -> bool:
    """JSON похож на токены бренда (для автоопределения типа файла)."""
    if not isinstance(data, dict):
        return False
    out = _Found()
    if _figma_variables(data, "", out):
        return True
    if ("colors" in data or "palette" in data or "fonts" in data) and not {
        "sections", "blocks", "schema_version"
    } & set(data):
        return _simple(data, "", out) and bool(out.colors or out.fonts)
    text = json.dumps(data)[:20000]
    if '"$value"' in text or '"$type"' in text:
        return True
    _walk_tokens(data, [], "", out)
    return len(out.colors) >= 2 and '"value"' in text


def parse_tokens(data: dict[str, Any], source: str) -> _Found:
    out = _Found()
    if _figma_variables(data, source, out):
        return out
    if not _simple(data, source, out):
        _walk_tokens(data, [], source, out)
    return out


# ------------------------------------------------------------------------ pdf


def parse_pdf_text(text: str, source: str) -> _Found:
    """HEX / RGB / CMYK с подписью на той же строке, шрифты, правила."""
    out = _Found()
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        found: list[str] = []
        for m in _HEX.finditer(line):
            if m.group(0).startswith("#") or re.search(r"hex|цвет|color", line, re.I):
                found.append("#" + m.group(1).upper())
        found += [_rgb_hex(*(int(g) for g in m.groups())) for m in _RGB.finditer(line)]
        found += [_cmyk_hex(*(int(g) for g in m.groups())) for m in _CMYK.finditer(line)]
        label = re.split(r"[:—–\-]|#|\bHEX\b|\bRGB\b|\bCMYK\b", line, maxsplit=1)[0].strip()
        for hex_ in found[:1]:
            if hex_ in seen:
                continue
            seen.add(hex_)
            out.colors.append(BrandColor(name=label or hex_, hex=hex_,
                                         role=_role(line, _ROLE_WORDS) or "other", source=source))
        m = re.search(r"(?:шрифт|font|typeface|гарнитур\w*)[^:]*:\s*([A-Za-zА-Яа-я0-9 \-]+)",
                      line, re.I)
        if m:
            family = m.group(1).strip().split("  ")[0].strip()
            if family:
                out.fonts.append(BrandFont(role=_role(line, _FONT_ROLE_WORDS) or "other",
                                           family=family, source=source))
        rule = re.search(r"\d\s*(?:мм|mm|px|пикс)|отступ|clear", line, re.I)
        if rule and re.search(r"логотип|logo", line, re.I):
            out.rules.append(BrandRule(text=line, source=source))
    return out


def _pdf_text(data: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages[:40])


def looks_like_brand_pdf(data: bytes) -> bool:
    try:
        text = _pdf_text(data)
    except Exception:  # noqa: BLE001 — нечитаемый PDF решает парсер контента
        return False
    found = parse_pdf_text(text, "")
    words = re.search(r"брендбук|brand ?book|guideline|гайдлайн|фирменн|палитр|palette", text, re.I)
    return bool(words) and len(found.colors) >= 2


# -------------------------------------------------------------------- template


def _theme_parts(files: dict[str, bytes]) -> list[str]:
    return sorted(n for n in files if re.fullmatch(r"ppt/theme/theme\d+\.xml", n))


def _main_theme(files: dict[str, bytes]) -> str | None:
    rels = files.get("ppt/slideMasters/_rels/slideMaster1.xml.rels")
    if rels:
        m = re.search(rb'Target="\.\./theme/(theme\d+\.xml)"', rels)
        if m:
            return f"ppt/theme/{m.group(1).decode()}"
    parts = _theme_parts(files)
    return parts[0] if parts else None


def theme_of(files: dict[str, bytes]) -> tuple[dict[str, str], dict[str, str]]:
    """Цвета (``accent1`` → ``#0077FF``) и шрифты (``major``/``minor``)
    главной темы."""
    part = _main_theme(files)
    if part is None:
        return {}, {}
    root = etree.fromstring(files[part])
    colors: dict[str, str] = {}
    scheme = root.find(f".//{{{A}}}clrScheme")
    for slot in scheme if scheme is not None else []:
        name = etree.QName(slot).localname
        srgb = slot.find(f"{{{A}}}srgbClr")
        sys_ = slot.find(f"{{{A}}}sysClr")
        val = srgb.get("val") if srgb is not None else (
            sys_.get("lastClr") if sys_ is not None else None)
        if val:
            colors[name] = "#" + val.upper()
    fonts: dict[str, str] = {}
    for kind in ("major", "minor"):
        latin = root.find(f".//{{{A}}}{kind}Font/{{{A}}}latin")
        if latin is not None and latin.get("typeface"):
            fonts[kind] = latin.get("typeface")
    return colors, fonts


def _drawing_parts(files: dict[str, bytes]) -> list[str]:
    return [
        n for n in files
        if n.endswith(".xml") and re.match(
            r"ppt/(slides|slideLayouts|slideMasters|charts|diagrams)/[^/]+\.xml$", n)
    ]


@dataclass
class TemplateLook:
    """Палитра и шрифты шаблона: что видно на слайдах."""

    theme_colors: dict[str, str]
    theme_fonts: dict[str, str]
    palette: list[str]  # насыщенные цвета по убыванию веса, главный первым
    fonts: list[str]  # гарнитуры, не моноширинные, по убыванию числа прогонов
    heading_font: str | None
    body_font: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "palette": self.palette, "fonts": self.fonts,
            "heading_font": self.heading_font, "body_font": self.body_font,
            "theme_colors": self.theme_colors, "theme_fonts": self.theme_fonts,
        }


def template_look(data: bytes | dict[str, bytes]) -> TemplateLook:
    files = data if isinstance(data, dict) else _files(data)
    colors, fonts = theme_of(files)
    weight: Counter[str] = Counter()
    for slot in _ACCENTS:
        hex_ = colors.get(slot)
        if hex_ and is_chromatic(hex_):
            weight[hex_] += 5 if slot == "accent1" else 2
    sizes: dict[str, list[int]] = {}
    runs: Counter[str] = Counter()
    for part in _drawing_parts(files):
        xml = files[part]
        for m in re.finditer(rb'srgbClr val="([0-9A-Fa-f]{6})"', xml):
            hex_ = "#" + m.group(1).decode().upper()
            if is_chromatic(hex_):
                weight[hex_] += 1
        if part.startswith(("ppt/slides/", "ppt/slideLayouts/", "ppt/slideMasters/")):
            for m in _RPR.finditer(xml):
                face = re.search(rb'<a:latin typeface="([^"]+)"', m.group(2))
                if not face:
                    continue
                name = face.group(1).decode("utf-8", "replace")
                if name.startswith("+") or _MONO.search(name):
                    continue
                runs[name] += 1
                sz = re.search(rb'\bsz="(\d+)"', m.group(1))
                if sz:
                    sizes.setdefault(name, []).append(int(sz.group(1)))
    # сначала цвета средней светлоты (ими рисуют главное), затем светлые
    # подложки и тёмные оттенки
    palette = sorted(
        (hex_ for hex_, _ in weight.most_common()),
        key=lambda h: (not 0.25 <= _hls(h)[1] <= 0.7, -weight[h]),
    )
    # главный цвет темы (accent1, иначе dk2) — главный и в палитре
    main = next((colors[k] for k in ("accent1", "dk2")
                 if colors.get(k) and is_chromatic(colors[k])
                 and 0.25 <= _hls(colors[k])[1] <= 0.7), None)
    if main in palette:
        palette.remove(main)
        palette.insert(0, main)
    names = [n for n, _ in runs.most_common()]
    for kind in ("major", "minor"):
        if fonts.get(kind) and fonts[kind] not in names:
            names.append(fonts[kind])
    heading = body = None
    sized = {n: statistics.median(s) for n, s in sizes.items() if len(s) >= 3}
    if sized:
        heading = max(sized, key=lambda n: sized[n])
        rest = [n for n in names if n != heading]
        body = rest[0] if rest else heading
    else:
        heading, body = fonts.get("major"), fonts.get("minor")
    return TemplateLook(colors, fonts, palette, names, heading, body)


def _files(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}


# ------------------------------------------------------------------- building


def _ordered_palette(book: Brandbook) -> list[str]:
    """Цвета бренда для акцентов: основной, вторичный, акценты, прочие
    насыщенные. Текст и фон в палитру акцентов не идут."""
    order = {"primary": 0, "secondary": 1, "accent": 2, "other": 3}
    chromatic = [c for c in book.colors if c.role in order and is_chromatic(c.hex)]
    chromatic.sort(key=lambda c: order[c.role])
    seen: list[str] = []
    for c in chromatic:
        if c.hex not in seen:
            seen.append(c.hex)
    return seen


def _merge(
    found: list[tuple[_Found, str]],
) -> tuple[list[BrandColor], list[BrandFont], list[BrandRule]]:
    """Первый источник по доверию выигрывает роль; прочие цвета добавляются."""
    colors: list[BrandColor] = []
    fonts: list[BrandFont] = []
    rules: list[BrandRule] = []
    for f, _kind in found:
        for c in f.colors:
            if any(x.hex == c.hex for x in colors):
                continue
            if c.role in {"primary", "secondary", "text", "background"} and any(
                x.role == c.role for x in colors
            ):
                c = c.model_copy(update={"role": "accent" if is_chromatic(c.hex) else "other"})
            colors.append(c)
        for ft in f.fonts:
            if ft.role != "other" and any(x.role == ft.role for x in fonts):
                continue
            if not any(x.family == ft.family and x.role == ft.role for x in fonts):
                fonts.append(ft)
        rules.extend(f.rules)
    if not any(c.role == "primary" for c in colors):
        first = next((i for i, c in enumerate(colors) if is_chromatic(c.hex)), None)
        if first is not None:
            colors[first] = colors[first].model_copy(update={"role": "primary"})
    others = [f for f in fonts if f.role == "other"]
    if not any(f.role == "heading" for f in fonts) and others:
        fonts[fonts.index(others[0])] = others[0].model_copy(update={"role": "heading"})
        others = others[1:]
    if not any(f.role == "body" for f in fonts):
        if others:
            fonts[fonts.index(others[0])] = others[0].model_copy(update={"role": "body"})
        elif (head := next((f for f in fonts if f.role == "heading"), None)) is not None:
            fonts.append(BrandFont(role="body", family=head.family, source=head.source))
    return colors, fonts, rules


_RANK = {"tokens": 0, "figma": 1, "pdf": 2, "example": 3}


def build_brandbook(
    files: list[tuple[str, bytes]],
    brandbook_id: str,
    *,
    project_id: str | None = None,
    store_asset: Any = None,
) -> Brandbook:
    """Брендбук из файлов. ``store_asset(name, data, media_type) -> id``
    сохраняет логотипы (в API — артефакты проекта)."""
    found: list[tuple[_Found, str]] = []
    book = Brandbook(id=brandbook_id, project_id=project_id)
    for name, data in files:
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        try:
            if ext == "json":
                raw = json.loads(data.decode("utf-8-sig"))
                kind = "figma" if _figma_variables(raw, name, _Found()) else "tokens"
                f = parse_tokens(raw, name)
                if isinstance(raw, dict) and isinstance(raw.get("name"), str):
                    book.name = book.name or raw["name"]
                found.append((f, kind))
                book.sources.append(BrandSource(file_name=name, kind=kind, parsed=True,
                                                note=f"{len(f.colors)} цв., {len(f.fonts)} шр."))
            elif ext == "pdf":
                f = parse_pdf_text(_pdf_text(data), name)
                found.append((f, "pdf"))
                book.sources.append(BrandSource(file_name=name, kind="pdf", parsed=True,
                                                note=f"{len(f.colors)} цв., {len(f.fonts)} шр."))
            elif ext in _IMAGE_EXT:
                media = "image/svg+xml" if ext == "svg" else (
                    f"image/{'jpeg' if ext == 'jpg' else ext}")
                art = store_asset(name, data, media) if store_asset else None
                book.logos.append(BrandLogo(file_name=name, artifact_id=art, media_type=media))
                book.sources.append(BrandSource(file_name=name, kind="logo", parsed=True))
            elif ext in {"pptx", "potx"}:
                look = template_look(data)
                f = _Found(
                    colors=[BrandColor(name=f"{name}: {i + 1}", hex=h,
                                       role="primary" if i == 0 else "accent", source=name)
                            for i, h in enumerate(look.palette[:4])],
                    fonts=[BrandFont(role=r, family=fam, source=name)
                           for r, fam in (("heading", look.heading_font), ("body", look.body_font))
                           if fam],
                )
                found.append((f, "example"))
                book.examples.append(name)
                book.sources.append(BrandSource(file_name=name, kind="example", parsed=True,
                                                note="цвета и шрифты темы примера"))
            else:
                book.sources.append(BrandSource(file_name=name, kind="unknown", parsed=False,
                                                note="формат брендбука не распознан"))
        except Exception as exc:  # noqa: BLE001 — один файл не валит брендбук
            book.sources.append(BrandSource(file_name=name, kind="unknown", parsed=False,
                                            note=f"{type(exc).__name__}: {exc}"[:200]))
            book.warnings.append(f"{name}: не удалось прочитать")
    found.sort(key=lambda item: _RANK.get(item[1], 9))
    book.colors, book.fonts, book.rules = _merge(found)
    if not book.colors:
        book.warnings.append("В брендбуке не нашлось цветов.")
    if not book.fonts:
        book.warnings.append("В брендбуке не нашлось шрифтов.")
    return book


# -------------------------------------------------------------------- merging


@dataclass
class BrandPlan:
    color_map: dict[str, str]  # цвет шаблона → цвет бренда
    font_map: dict[str, str]  # гарнитура шаблона → гарнитура бренда
    theme_colors: dict[str, str]  # слот темы → цвет бренда
    theme_fonts: dict[str, str]  # major/minor → гарнитура бренда
    conflicts: list[BrandConflict]
    palette: list[str] = field(default_factory=list)  # цвета бренда по порядку


def plan_brand(look: TemplateLook, book: Brandbook, priority: str = "brandbook") -> BrandPlan:
    """Что поменять в шаблоне и где шаблон с брендом расходятся."""
    resolved = "brandbook" if priority == "brandbook" else "template"
    brand = _ordered_palette(book)
    color_map: dict[str, str] = {}
    conflicts: list[BrandConflict] = []
    roles = ["primary", "secondary"] + ["accent"] * 8
    for i, tpl in enumerate(look.palette):
        if not brand:
            break
        new = _tint(brand[i % len(brand)], tpl)
        color_map[tpl] = new
        if i < 2 and _distance(tpl, new) > 0.12:
            conflicts.append(BrandConflict(
                field=f"color.{roles[i]}", kind="color", template=tpl, brandbook=new,
                resolved=resolved,
                note="главный цвет шаблона и бренда разные" if i == 0 else None,
            ))
    # насыщенные акценты темы — в цвета бренда; белые и серые слоты темы
    # шаблоны используют как фон и подложки, они остаются
    theme_colors: dict[str, str] = {}
    for k, slot in enumerate(_ACCENTS):
        tpl = look.theme_colors.get(slot)
        if brand and tpl and is_chromatic(tpl):
            theme_colors[slot] = color_map.get(tpl) or _tint(brand[k % len(brand)], tpl)
    # dk2/lt2 некоторые шаблоны красят в фирменный цвет и заливают им
    # карточки: насыщенный слот идёт в бренд, нейтральный остаётся
    for slot in ("dk2", "lt2"):
        tpl = look.theme_colors.get(slot)
        if brand and tpl and is_chromatic(tpl):
            theme_colors[slot] = color_map.get(tpl) or _tint(brand[0], tpl)
    # Explicit semantic roles override neutral text/background tokens too.
    for role, slots in (("text", ("dk1",)), ("background", ("lt1",))):
        token = book.color(role)
        if token is None:
            continue
        for slot in slots:
            old = look.theme_colors.get(slot)
            theme_colors[slot] = token.hex
            if old:
                color_map[old] = token.hex
    font_map: dict[str, str] = {}
    theme_fonts: dict[str, str] = {}
    head, body = book.font("heading"), book.font("body")
    if head:
        theme_fonts["major"] = head.family
        if look.heading_font and look.heading_font != head.family:
            font_map[look.heading_font] = head.family
            conflicts.append(BrandConflict(field="font.heading", kind="font",
                                           template=look.heading_font, brandbook=head.family,
                                           resolved=resolved))
    if body:
        theme_fonts["minor"] = body.family
        for name in look.fonts:
            if name not in font_map and name != body.family:
                font_map[name] = body.family
        if look.body_font and look.body_font != body.family:
            conflicts.append(BrandConflict(field="font.body", kind="font",
                                           template=look.body_font, brandbook=body.family,
                                           resolved=resolved))
    primary = brand[0] if brand else None
    if primary and resolved == "brandbook":
        light = look.theme_colors.get("lt1")
        if light and contrast(primary, light) < 3.0:
            conflicts.append(BrandConflict(
                field="color.primary", kind="contrast", template=light, brandbook=primary,
                resolved=resolved,
                note=f"контраст основного цвета с фоном {contrast(primary, light):.1f}:1 < 3:1",
            ))
    return BrandPlan(color_map, font_map, theme_colors, theme_fonts, conflicts, brand)


def _patch_theme(xml: bytes, plan: BrandPlan) -> bytes:
    root = etree.fromstring(xml)
    scheme = root.find(f".//{{{A}}}clrScheme")
    if scheme is not None:
        for slot in scheme:
            name = etree.QName(slot).localname
            new = plan.theme_colors.get(name)
            if not new:
                continue
            for child in list(slot):
                slot.remove(child)
            etree.SubElement(slot, f"{{{A}}}srgbClr", val=new.lstrip("#"))
    for kind, key in (("major", "major"), ("minor", "minor")):
        family = plan.theme_fonts.get(key)
        latin = root.find(f".//{{{A}}}{kind}Font/{{{A}}}latin")
        if family and latin is not None:
            latin.set("typeface", family)
            for attr in ("panose", "pitchFamily", "charset"):
                latin.attrib.pop(attr, None)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _patch_drawing(xml: bytes, plan: BrandPlan, chart: bool) -> bytes:
    def color(m: re.Match[bytes]) -> bytes:
        old = "#" + m.group(1).decode().upper()
        new = plan.color_map.get(old)
        return m.group(0) if not new else b'srgbClr val="' + new.lstrip("#").encode() + b'"'

    out = re.sub(rb'srgbClr val="([0-9A-Fa-f]{6})"', color, xml)

    def face(m: re.Match[bytes]) -> bytes:
        name = m.group(2).decode("utf-8", "replace")
        new = plan.font_map.get(name)
        if not new:
            return m.group(0)
        return m.group(1) + new.encode("utf-8") + b'"'

    out = re.sub(rb'(<a:(?:latin|ea|cs) typeface=")([^"]+)"', face, out)
    if chart and plan.palette:
        # цвета серий графика-донора, не попавшие в палитру шаблона, — к
        # цветам бренда по порядку: донор отдаёт стиль, цвета — бренд
        root = etree.fromstring(out)
        for k, ser in enumerate(root.iter(f"{{{C}}}ser")):
            sppr = ser.find(f"{{{C}}}spPr")
            if sppr is None:
                continue
            for fill in sppr.iter(f"{{{A}}}srgbClr"):
                val = "#" + (fill.get("val") or "").upper()
                if is_chromatic(val) and val not in plan.color_map.values():
                    fill.set("val", plan.palette[k % len(plan.palette)].lstrip("#"))
        out = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    return out


@dataclass
class BrandReport:
    brandbook_id: str
    priority: str
    applied: bool
    colors: dict[str, str]
    fonts: dict[str, str]
    conflicts: list[BrandConflict]
    template: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "brandbook_id": self.brandbook_id,
            "priority": self.priority,
            "applied": self.applied,
            "colors": self.colors,
            "fonts": self.fonts,
            "conflicts": [c.model_dump(mode="json") for c in self.conflicts],
            "template": self.template,
        }


def brandbook_cache_key(
    book: Brandbook | dict[str, Any] | bytes | str | None,
    priority: str = "brandbook",
) -> str:
    """Отдельный ключ кэша брендбука (не инвалидирует кэш шаблона)."""
    import hashlib

    h = hashlib.sha256()
    if book is None:
        h.update(b"none")
    elif isinstance(book, bytes):
        h.update(book)
    elif isinstance(book, str):
        h.update(book.encode("utf-8"))
    elif isinstance(book, Brandbook):
        payload = {
            "colors": [c.model_dump(mode="json") for c in book.colors],
            "fonts": [f.model_dump(mode="json") for f in book.fonts],
            "logos": [lg.model_dump(mode="json") for lg in book.logos],
            "rules": [r.model_dump(mode="json") for r in book.rules],
        }
        h.update(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    elif isinstance(book, dict):
        h.update(json.dumps(book, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    else:
        h.update(str(book).encode("utf-8"))
    h.update(f":{priority}".encode())
    return h.hexdigest()


def apply_brandbook(
    template: bytes, book: Brandbook, priority: str = "brandbook",
) -> tuple[bytes, BrandReport]:
    """Пакет шаблона в цветах и шрифтах бренда (или без изменений при
    ``priority = template``) и отчёт для паспорта."""
    if priority not in {"brandbook", "template"}:
        raise ValueError(f"unknown brand_priority: {priority}")
    files = _files(template)
    look = template_look(files)
    plan = plan_brand(look, book, priority)
    report = BrandReport(
        brandbook_id=book.id, priority=priority, applied=priority == "brandbook",
        colors=plan.color_map if priority == "brandbook" else {},
        fonts=plan.font_map if priority == "brandbook" else {},
        conflicts=plan.conflicts,
        template={"palette": look.palette[:6], "heading_font": look.heading_font,
                  "body_font": look.body_font},
    )
    if priority == "template" or not (
        plan.color_map or plan.theme_colors or plan.font_map or plan.theme_fonts
    ):
        return template, report
    changed: dict[str, bytes] = {}
    for part in _theme_parts(files):
        changed[part] = _patch_theme(files[part], plan)
    for part in _drawing_parts(files):
        new = _patch_drawing(files[part], plan, chart=part.startswith("ppt/charts/"))
        if new != files[part]:
            changed[part] = new
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(template)) as src, zipfile.ZipFile(
        buf, "w", zipfile.ZIP_DEFLATED
    ) as dst:
        for info in src.infolist():
            data = changed.get(info.filename)
            dst.writestr(info, data if data is not None else src.read(info.filename))
    return buf.getvalue(), report


# ---------------------------------------------------------------------- figma


def fetch_figma_variables(
    file_key: str, token: str | None, *, transport: Any = None,
) -> dict | None:
    """Переменные Figma по REST API. Без токена — ``None`` (не ошибка)."""
    if not token:
        return None
    import httpx

    with httpx.Client(transport=transport, timeout=20.0) as client:
        resp = client.get(f"https://api.figma.com/v1/files/{file_key}/variables/local",
                          headers={"X-Figma-Token": token})
        resp.raise_for_status()
        return resp.json()


def figma_file_key(url: str) -> str | None:
    m = re.search(r"figma\.com/(?:file|design)/([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


def summary(book: Brandbook) -> dict[str, Any]:
    return {
        "id": book.id,
        "name": book.name,
        "palette": [c.model_dump(mode="json") for c in book.colors],
        "fonts": [f.model_dump(mode="json") for f in book.fonts],
        "logos": [lg.model_dump(mode="json") for lg in book.logos],
        "rules": [r.text for r in book.rules],
        "sources": [s.model_dump(mode="json") for s in book.sources],
        "warnings": book.warnings,
    }


__all__ = [
    "BrandPlan",
    "BrandReport",
    "TemplateLook",
    "apply_brandbook",
    "brandbook_cache_key",
    "build_brandbook",
    "contrast",
    "fetch_figma_variables",
    "figma_file_key",
    "is_chromatic",
    "looks_like_brand_pdf",
    "looks_like_tokens",
    "parse_pdf_text",
    "parse_tokens",
    "plan_brand",
    "summary",
    "template_look",
    "theme_of",
]
