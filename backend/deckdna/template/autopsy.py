"""Template Autopsy — forensic analysis of an arbitrary .pptx/.potx.

Produces the facts the pipeline and pitch rely on: package census
(slides/masters/layouts/themes/media/charts/embeddings), per-layout
slide usage, and the declared-vs-observed font split. Pure stdlib+lxml:
works before any heavier parser stage exists and doubles as the
forensic tool behind the benchmark claims.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS = {"a": A, "p": P}

_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_LAYOUT_RE = re.compile(r"slideLayout(\d+)\.xml")
_RELS_LAYOUT_RE = re.compile(r"slideLayout(\d+)\.xml")


@dataclass
class TemplateForensics:
    path: str
    slides: int = 0
    masters: int = 0
    layouts: int = 0
    themes: int = 0
    media: int = 0
    charts: int = 0
    embeddings: int = 0
    tables: int = 0
    layout_usage: dict[str, int] = field(default_factory=dict)
    declared_fonts: list[str] = field(default_factory=list)
    observed_fonts: dict[str, int] = field(default_factory=dict)
    # declared-палитра каждой темы: theme name -> {слот: hex RRGGBB}
    theme_palettes: dict[str, dict[str, str]] = field(default_factory=dict)
    # observed-палитра: реально используемые solid fill на слайдах,
    # топ-10 hex RRGGBB -> число применений
    observed_colors: dict[str, int] = field(default_factory=dict)

    @property
    def dominant_layout(self) -> tuple[str, int] | None:
        if not self.layout_usage:
            return None
        lid, count = max(self.layout_usage.items(), key=lambda kv: kv[1])
        return lid, count

    @property
    def dominant_layout_share(self) -> float:
        dom = self.dominant_layout
        if not dom or not self.slides:
            return 0.0
        return dom[1] / self.slides

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "slides": self.slides,
            "masters": self.masters,
            "layouts": self.layouts,
            "themes": self.themes,
            "media": self.media,
            "charts": self.charts,
            "embeddings": self.embeddings,
            "tables": self.tables,
            "layout_usage": dict(self.layout_usage),
            "dominant_layout": self.dominant_layout,
            "declared_fonts": self.declared_fonts,
            "observed_fonts": self.observed_fonts,
            "theme_palettes": self.theme_palettes,
            "observed_colors": self.observed_colors,
        }


def _count_part(namelist: list[str], pattern: str) -> int:
    rx = re.compile(pattern)
    return sum(1 for n in namelist if rx.match(n))


def _declared_fonts(zf: zipfile.ZipFile, names: list[str]) -> list[str]:
    fonts: list[str] = []
    for name in names:
        if not re.match(r"^ppt/theme/theme\d+\.xml$", name):
            continue
        root = etree.fromstring(zf.read(name))
        for tag in ("majorFont", "minorFont"):
            node = root.find(f".//{{{A}}}fontScheme/{{{A}}}{tag}/{{{A}}}latin")
            if node is not None:
                tf = node.get("typeface", "")
                if tf and tf not in fonts:
                    fonts.append(tf)
    return fonts


def _slot_hex(slot: etree._Element) -> str | None:
    """hex цвет слота clrScheme: a:srgbClr@val, иначе a:sysClr@lastClr
    (фолбэк, который PowerPoint сам подставляет для системного цвета)."""
    for child in slot:
        tag = etree.QName(child).localname
        if tag == "srgbClr" and child.get("val"):
            return child.get("val").upper()
        if tag == "sysClr" and child.get("lastClr"):
            return child.get("lastClr").upper()
    return None


def _theme_palettes_by_part(
    zf: zipfile.ZipFile, names: list[str]
) -> dict[str, dict[str, str]]:
    """clrScheme каждой темы по имени парта: {ppt/theme/themeN.xml: {слот: hex}}."""
    by_part: dict[str, dict[str, str]] = {}
    for name in names:
        if not re.match(r"^ppt/theme/theme\d+\.xml$", name):
            continue
        root = etree.fromstring(zf.read(name))
        scheme = root.find(f".//{{{A}}}clrScheme")
        if scheme is None:
            continue
        palette = {
            etree.QName(slot).localname: hexv
            for slot in scheme
            if (hexv := _slot_hex(slot)) is not None
        }
        if palette:
            by_part[name] = palette
    return by_part


def _theme_palettes(
    zf: zipfile.ZipFile, names: list[str]
) -> dict[str, dict[str, str]]:
    """Declared-палитра каждой темы: {имя темы: {слот: hex}}.

    Слоты a:clrScheme (dk1/lt1/dk2/lt2/accent1-6/hlink/folHlink).
    Ключ — a:theme@name (или имя партa, если name отсутствует); при
    совпадении имён темы суффиксируются -2, -3, …
    """
    by_part = _theme_palettes_by_part(zf, names)
    palettes: dict[str, dict[str, str]] = {}
    for name in sorted(by_part):
        m = re.match(r"^ppt/theme/theme(\d+)\.xml$", name)
        palette = by_part[name]
        root = etree.fromstring(zf.read(name))
        theme_name = root.get("name") or f"theme{m.group(1)}"
        key = theme_name
        n = 2
        while key in palettes:
            key = f"{theme_name}-{n}"
            n += 1
        palettes[key] = palette
    return palettes


def _observed_fonts(zf: zipfile.ZipFile, names: list[str]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for name in names:
        if not _SLIDE_RE.match(name):
            continue
        root = etree.fromstring(zf.read(name))
        for latin in root.iter(f"{{{A}}}latin"):
            tf = latin.get("typeface", "")
            if tf and not tf.startswith("+"):
                counter[tf] += 1
        # run-level fonts set via a:rPr latin are covered above; also count
        # east-asian/cs only if needed — latin dominates these templates.
    return counter


def _rels_targets(
    zf: zipfile.ZipFile, names: list[str], rels_name: str, type_suffix: str
) -> list[str]:
    """Имена партов, на которые указывают rels данного типа.

    Target в .rels относителен к каталогу исходного парта — нормализуем
    в полный part name (posixpath), т.к. дальше по цепочке
    slide→layout→master→theme нужны точные пути, а не номера файлов.
    """
    if rels_name not in names:
        return []
    base = posixpath.dirname(rels_name)
    # ppt/x/_rels/y.xml.rels — парт живёт в ppt/x/, не в _rels
    if posixpath.basename(base) == "_rels":
        base = posixpath.dirname(base)
    out: list[str] = []
    root = etree.fromstring(zf.read(rels_name))
    for rel in root:
        if not rel.get("Type", "").endswith(f"/{type_suffix}"):
            continue
        target = rel.get("Target", "")
        if target and not re.match(r"^[a-z]+://", target):
            out.append(posixpath.normpath(posixpath.join(base, target)))
    return out


def _slide_theme_part(
    zf: zipfile.ZipFile, names: list[str], slide_part: str
) -> str | None:
    """Тема слайда через цепочку slide→layout→master→theme rels."""
    rels = f"{posixpath.dirname(slide_part)}/_rels/{posixpath.basename(slide_part)}.rels"
    for layout in _rels_targets(zf, names, rels, "slideLayout"):
        lrels = f"{posixpath.dirname(layout)}/_rels/{posixpath.basename(layout)}.rels"
        for master in _rels_targets(zf, names, lrels, "slideMaster"):
            mrels = f"{posixpath.dirname(master)}/_rels/{posixpath.basename(master)}.rels"
            for theme in _rels_targets(zf, names, mrels, "theme"):
                return theme
    return None


# Алиасы schemeClr к слотам clrScheme (ECMA-376: tx* = dk*, bg* = lt*).
_CLR_ALIAS = {"tx1": "dk1", "tx2": "dk2", "bg1": "lt1", "bg2": "lt2"}


def _fill_hex(
    fill: etree._Element, palette: dict[str, str] | None
) -> str | None:
    """hex первого цвета a:solidFill: srgbClr@val напрямую, schemeClr
    через declared-палитру темы слайда (alias tx*/bg* → dk*/lt*),
    sysClr через lastClr. Трансформы (lumMod/tint/alpha) в v0 не
    применяем — честно возвращаем базовый цвет слота."""
    for child in fill:
        tag = etree.QName(child).localname
        if tag == "srgbClr" and child.get("val"):
            return child.get("val").upper()
        if tag == "sysClr" and child.get("lastClr"):
            return child.get("lastClr").upper()
        if tag == "schemeClr" and child.get("val") and palette:
            slot = _CLR_ALIAS.get(child.get("val"), child.get("val"))
            hexv = palette.get(slot)
            if hexv:
                return hexv
        return None
    return None


def _observed_colors(
    zf: zipfile.ZipFile,
    names: list[str],
    palettes_by_part: dict[str, dict[str, str]],
    top: int = 10,
) -> dict[str, int]:
    """Observed-палитра: частота hex-цветов solid fill на слайдах.

    Считаются только a:solidFill — прямые дети *spPr (заливка фигур;
    градиенты, паттерны, blipFill и заливки текста/линий пропускаем —
    v0 честно покрывает простой solid fill). schemeClr резолвится в hex
    через declared-палитру темы конкретного слайда.
    """
    counter: Counter[str] = Counter()
    slide_theme: dict[str, str | None] = {}
    for name in names:
        if not _SLIDE_RE.match(name):
            continue
        if name not in slide_theme:
            slide_theme[name] = _slide_theme_part(zf, names, name)
        palette = palettes_by_part.get(slide_theme[name] or "")
        root = etree.fromstring(zf.read(name))
        for fill in root.iter(f"{{{A}}}solidFill"):
            parent = fill.getparent()
            if parent is None or etree.QName(parent).localname != "spPr":
                continue
            hexv = _fill_hex(fill, palette)
            if hexv:
                counter[hexv] += 1
    return dict(counter.most_common(top))


def _layout_usage(zf: zipfile.ZipFile, names: list[str]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for name in names:
        m = _SLIDE_RE.match(name)
        if not m:
            continue
        rels_name = f"ppt/slides/_rels/slide{m.group(1)}.xml.rels"
        layout_id = "none"
        if rels_name in names:
            rels = etree.fromstring(zf.read(rels_name))
            for rel in rels.iter():
                target = rel.get("Target", "")
                lm = _RELS_LAYOUT_RE.search(target)
                if lm:
                    layout_id = lm.group(1)
                    break
        usage[layout_id] = usage.get(layout_id, 0) + 1
    return dict(sorted(usage.items(), key=lambda kv: -kv[1]))


def _count_tables(zf: zipfile.ZipFile, names: list[str]) -> int:
    total = 0
    for name in names:
        if not _SLIDE_RE.match(name):
            continue
        root = etree.fromstring(zf.read(name))
        total += len(root.findall(f".//{{{A}}}tbl"))
    return total


def analyze_template(path: str | Path) -> TemplateForensics:
    path = Path(path)
    f = TemplateForensics(path=str(path))
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        f.slides = _count_part(names, r"^ppt/slides/slide\d+\.xml$")
        f.masters = _count_part(names, r"^ppt/slideMasters/slideMaster\d+\.xml$")
        f.layouts = _count_part(names, r"^ppt/slideLayouts/slideLayout\d+\.xml$")
        f.themes = _count_part(names, r"^ppt/theme/theme\d+\.xml$")
        f.media = sum(1 for n in names if n.startswith("ppt/media/"))
        f.charts = _count_part(names, r"^ppt/charts/chart\d+\.xml$")
        f.embeddings = sum(1 for n in names if n.startswith("ppt/embeddings/"))
        f.layout_usage = _layout_usage(zf, names)
        f.declared_fonts = _declared_fonts(zf, names)
        f.observed_fonts = dict(_observed_fonts(zf, names).most_common())
        f.theme_palettes = _theme_palettes(zf, names)
        f.observed_colors = _observed_colors(
            zf, names, _theme_palettes_by_part(zf, names)
        )
        f.tables = _count_tables(zf, names)
    return f
