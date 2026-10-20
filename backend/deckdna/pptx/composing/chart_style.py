"""Стиль графика-донора шаблона (ADR-033 §6).

Если в шаблоне есть хоть один график, из него берётся профиль стиля и
применяется к графикам **любого** типа: кегль подписей осей и легенды,
сетка, положение легенды, зазор столбцов, толщина линии и палитра серий.
Без донора стиль идёт из Design DNA (тема: шрифт, акценты по порядку).

Цвета донора проходят ту же проверку контраста с фоном слайда, что и
акценты темы (``chart_colors``): сливающиеся с фоном отбрасываются."""

from __future__ import annotations

import re
from dataclasses import dataclass

from lxml import etree

C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_CHART_PART = re.compile(r"^ppt/charts/chart\d+\.xml$")
_MIN_SZ, _MAX_SZ = 1000, 1600


@dataclass(frozen=True)
class DonorStyle:
    label_sz: int | None = None
    gridlines: bool | None = None
    legend_pos: str | None = None
    gap_width: int | None = None
    line_w: int | None = None
    # ("scheme", "accent2") | ("srgb", "1F77B4")
    series: tuple[tuple[str, str], ...] = ()
    source: str | None = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "label_sz": self.label_sz,
            "gridlines": self.gridlines,
            "legend_pos": self.legend_pos,
            "gap_width": self.gap_width,
            "line_w": self.line_w,
            "series": [f"{k}:{v}" for k, v in self.series],
        }


def _color(fill: etree._Element | None) -> tuple[str, str] | None:
    if fill is None:
        return None
    scheme = fill.find(f"{{{A}}}schemeClr")
    if scheme is not None and scheme.get("val"):
        return ("scheme", scheme.get("val"))
    srgb = fill.find(f"{{{A}}}srgbClr")
    if srgb is not None and srgb.get("val"):
        return ("srgb", srgb.get("val").upper())
    return None


def _profile(xml: bytes, name: str) -> DonorStyle | None:
    try:
        root = etree.fromstring(xml)
    except etree.XMLSyntaxError:
        return None
    plot = root.find(f".//{{{C}}}plotArea")
    if plot is None:
        return None
    sizes = []
    for tx in [*plot.iter(f"{{{C}}}txPr"), *root.iterfind(f".//{{{C}}}legend/{{{C}}}txPr")]:
        rpr = tx.find(f".//{{{A}}}defRPr")
        if rpr is not None and (rpr.get("sz") or "").isdigit():
            sizes.append(int(rpr.get("sz")))
    label_sz = max(_MIN_SZ, min(_MAX_SZ, sizes[0])) if sizes else None
    val_ax = plot.find(f"{{{C}}}valAx")
    gridlines = None if val_ax is None else val_ax.find(f"{{{C}}}majorGridlines") is not None
    legend = root.find(f".//{{{C}}}chart/{{{C}}}legend/{{{C}}}legendPos")
    gap = plot.find(f".//{{{C}}}gapWidth")
    series: list[tuple[str, str]] = []
    line_w = None
    for ser in plot.iter(f"{{{C}}}ser"):
        sp = ser.find(f"{{{C}}}spPr")
        if sp is None:
            continue
        color = _color(sp.find(f"{{{A}}}solidFill")) or _color(
            sp.find(f"{{{A}}}ln/{{{A}}}solidFill")
        )
        if color and color not in series:
            series.append(color)
        ln = sp.find(f"{{{A}}}ln")
        if ln is not None and (ln.get("w") or "").isdigit() and line_w is None:
            line_w = int(ln.get("w"))
    return DonorStyle(
        label_sz=label_sz,
        gridlines=gridlines,
        legend_pos=legend.get("val") if legend is not None else None,
        gap_width=int(gap.get("val"))
        if gap is not None and (gap.get("val") or "").isdigit()
        else None,
        line_w=line_w,
        series=tuple(series[:6]),
        source=name,
    )


def donor_style(parts: dict[str, bytes]) -> DonorStyle | None:
    """Профиль первого разбираемого графика шаблона (порядок частей
    стабилен: chart1, chart2, …)."""
    names = sorted(
        (n for n in parts if _CHART_PART.match(n)),
        key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)),
    )
    for name in names:
        style = _profile(parts[name], name)
        if style is not None and (style.series or style.label_sz):
            return style
    return None


def resolve_series(style: DonorStyle, theme: dict[str, str], cmap: dict[str, str]) -> list[str]:
    """Цвета серий донора в HEX (схемные — через тему и карту цветов)."""
    out = []
    aliases = {"tx1": "dk1", "bg1": "lt1", "tx2": "dk2", "bg2": "lt2"}
    for kind, val in style.series:
        if kind == "srgb":
            out.append(val)
        else:
            slot = cmap.get(val, aliases.get(val, val)) if val in aliases else val
            if theme.get(slot):
                out.append(theme[slot])
    return out


__all__ = ["DonorStyle", "donor_style", "resolve_series"]
