"""Нативные графики выбранного типа (ADR-033 §6).

``chart_fill`` раньше умел создавать только сгруппированные столбцы. Для
данных тип выбирает код (``analysis/recommend.py``), здесь он строится как
родной редактируемый OOXML-график:

- ``line`` — ``lineChart``, без маркеров при длинном ряде, пропуски —
  разрывом (``dispBlanksAs=gap``), подпись последней точки;
- ``bar`` / ``bar_horizontal`` / ``bar_stacked`` / ``bar_stacked_100`` /
  ``histogram`` — ``barChart`` (``barDir``, ``grouping``; у гистограммы
  ``gapWidth=0``); горизонтальные столбцы — крупнейший сверху;
- ``pie`` / ``doughnut`` — доли снаружи без переноса; при >4 секторах
  подписи трёх крупнейших, все категории в легенде справа;
- ``scatter`` — ``scatterChart`` из пар значений.

Стиль — из Design DNA шаблона через тему: шрифт ``+mn-lt``, цвета серий
``accent1..6``, подписи не мельче 12 pt. Подписи осей с единицами,
легенда только при ≥ 2 сериях (у долей — всегда)."""

from __future__ import annotations

from contextvars import ContextVar
from xml.sax.saxutils import escape

from deckdna.contracts.content_pack import Chart, ChartSeries

C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

LABEL_SZ = 1200  # 12 pt — подписи осей, легенды и данных
TYPED = {
    "line",
    "bar",
    "bar_horizontal",
    "bar_stacked",
    "bar_stacked_100",
    "histogram",
    "pie",
    "doughnut",
    "scatter",
    "area",
    "waterfall",
}


def _esc(text: object) -> str:
    return escape(str(text))


def _num(v: float) -> str:
    return f"{v:g}"


def _col(i: int) -> str:
    s = ""
    while i > 0:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


_INK = ContextVar("chart_ink", default=None)
_GRID = ContextVar("chart_grid", default=None)
_SERIES = ContextVar("chart_series", default=())
_MUTED = ContextVar("chart_muted", default=None)
_STYLE = ContextVar("chart_style", default=None)


def _label_sz() -> int:
    return _style("label_sz", LABEL_SZ)


def _style(attr: str, default):
    style = _STYLE.get()
    value = getattr(style, attr, None) if style is not None else None
    return default if value is None else value


def _ink_fill(lum: tuple[int, int] = (75000, 25000)) -> str:
    ink = _INK.get()
    if ink:
        return f'<a:solidFill><a:srgbClr val="{ink}"/></a:solidFill>'
    return (
        f'<a:solidFill><a:schemeClr val="tx1"><a:lumMod val="{lum[0]}"/>'
        f'<a:lumOff val="{lum[1]}"/></a:schemeClr></a:solidFill>'
    )


def _text_props(
    sz: int | None = None, rot: int | None = None, no_wrap: bool = False,
) -> str:
    sz = sz or _style("label_sz", LABEL_SZ)
    attrs = f' rot="{rot}" vert="horz"' if rot is not None else ''
    if no_wrap:
        attrs += ' wrap="none"' + (' vert="horz"' if rot is None else '')
    body = f'<a:bodyPr{attrs}/>'
    return (
        f'<c:txPr>{body}<a:lstStyle/><a:p><a:pPr><a:defRPr sz="{sz}" b="0">'
        + _ink_fill()
        + '<a:latin typeface="+mn-lt"/><a:cs typeface="+mn-cs"/>'
        '</a:defRPr></a:pPr><a:endParaRPr lang="ru-RU"/></a:p></c:txPr>'
    )


def _title(text: str, rot: int | None = None) -> str:
    body = f'<a:bodyPr rot="{rot}" vert="horz"/>' if rot is not None else "<a:bodyPr/>"
    return (
        f"<c:title><c:tx><c:rich>{body}<a:lstStyle/><a:p><a:pPr>"
        f'<a:defRPr sz="{_label_sz()}" b="0"/></a:pPr><a:r>'
        f'<a:rPr lang="ru-RU" sz="{_label_sz()}" b="0">'
        + _ink_fill()
        + '<a:latin typeface="+mn-lt"/></a:rPr>'
        f"<a:t>{_esc(text)}</a:t></a:r></a:p></c:rich></c:tx>"
        '<c:overlay val="0"/></c:title>'
    )


def _grid_fill(lum: tuple[int, int]) -> str:
    grid = _GRID.get()
    if grid:
        return f'<a:solidFill><a:srgbClr val="{grid}"/></a:solidFill>'
    return (
        f'<a:solidFill><a:schemeClr val="tx1"><a:lumMod val="{lum[0]}"/>'
        f'<a:lumOff val="{lum[1]}"/></a:schemeClr></a:solidFill>'
    )


def _gridlines() -> str:
    return (
        f'<c:majorGridlines><c:spPr><a:ln w="6350">{_grid_fill((15000, 85000))}'
        "</a:ln></c:spPr></c:majorGridlines>"
    )


def _axis_line() -> str:
    return f'<c:spPr><a:ln w="9525">{_grid_fill((25000, 75000))}</a:ln></c:spPr>'


def _cat_ref(categories: list[str]) -> str:
    n = len(categories)
    if not n:
        return ""
    pts = "".join(f'<c:pt idx="{i}"><c:v>{_esc(c)}</c:v></c:pt>' for i, c in enumerate(categories))
    return (
        f"<c:cat><c:strRef><c:f>Sheet1!$A$2:$A${n + 1}</c:f><c:strCache>"
        f'<c:ptCount val="{n}"/>{pts}</c:strCache></c:strRef></c:cat>'
    )


def _num_ref(tag: str, col: str, values: list[float | None], fmt: str = "General") -> str:
    n = len(values)
    pts = "".join(
        f'<c:pt idx="{i}"><c:v>{_num(v)}</c:v></c:pt>'
        for i, v in enumerate(values)
        if v is not None
    )
    return (
        f"<c:{tag}><c:numRef><c:f>Sheet1!${col}$2:${col}${n + 1}</c:f><c:numCache>"
        f'<c:formatCode>{_esc(fmt)}</c:formatCode><c:ptCount val="{n}"/>{pts}'
        f"</c:numCache></c:numRef></c:{tag}>"
    )


def _tx(col: str, name: str | None) -> str:
    if not name:
        return ""
    return (
        f'<c:tx><c:strRef><c:f>Sheet1!${col}$1</c:f><c:strCache><c:ptCount val="1"/>'
        f'<c:pt idx="0"><c:v>{_esc(name)}</c:v></c:pt></c:strCache></c:strRef></c:tx>'
    )


def _fill(j: int) -> str:
    series = _SERIES.get()
    if series:
        return f'<a:solidFill><a:srgbClr val="{series[j % len(series)]}"/></a:solidFill>'
    return f'<a:solidFill><a:schemeClr val="accent{(j % 6) + 1}"/></a:solidFill>'


def _muted_fill() -> str:
    """Приглушённая серия/точка: акцент, сдвинутый к фону (``_mix`` в
    ``chart_colors``), иначе нейтральный серый."""
    muted = _MUTED.get()
    if muted:
        return f'<a:solidFill><a:srgbClr val="{muted}"/></a:solidFill>'
    return (
        '<a:solidFill><a:schemeClr val="tx1"><a:lumMod val="60000"/>'
        '<a:lumOff val="40000"/></a:schemeClr></a:solidFill>'
    )


_OTHER_LABEL = ("прочие", "остальные")


def _scatter_trendline(xs: list[float | None], ys: list[float | None]) -> str:
    """Линейный тренд по парам без пропусков, когда связь заметна
    (|r| ≥ 0.5 — тот же порог, что у вывода «correlation» в insights)."""
    pairs = [
        (x, y) for x, y in zip(xs, ys, strict=False) if x is not None and y is not None
    ]
    if len(pairs) < 3:
        return ""
    from deckdna.analysis.insights import pearson

    r = pearson([p[0] for p in pairs], [p[1] for p in pairs])
    if r is None or abs(r) < 0.5:
        return ""
    return (
        '<c:trendline><c:trendlineType val="linear"/>'
        '<c:dispRSqr val="0"/><c:dispEq val="0"/></c:trendline>'
    )


def _bar_dpts(chart: Chart) -> str:
    """Pre-attentive focus для одиночного ряда: лидер — акцент, прочие
    столбцы приглушены; сводная строка «Прочие/Остальные» — нейтральная."""
    if len(chart.series) != 1:
        return ""
    s = chart.series[0]
    leader = max(
        (i for i, v in enumerate(s.values) if v is not None),
        key=lambda i: s.values[i],
        default=None,
    )
    if leader is None:
        return ""
    parts = []
    for i, cat in enumerate(chart.categories):
        if str(cat).strip().casefold().split(" ")[0] in _OTHER_LABEL:
            color = _muted_fill()
        elif i == leader:
            color = _fill(0)
        else:
            color = _muted_fill()
        parts.append(
            f'<c:dPt><c:idx val="{i}"/><c:invertIfNegative val="0"/>'
            f'<c:bubble3D val="0"/><c:spPr>{color}<a:ln><a:noFill/></a:ln></c:spPr></c:dPt>'
        )
    return "".join(parts)


def _dlbls(
    show_val: bool = False,
    show_pct: bool = False,
    pos: str | None = None,
    only_idx: int | None = None,
    fmt: str | None = None,
    only_indices: list[int] | None = None,
    no_wrap: bool = False,
) -> str:
    text_props = _text_props(no_wrap=no_wrap)
    flags = (
        '<c:showLegendKey val="0"/>'
        f'<c:showVal val="{int(show_val)}"/><c:showCatName val="0"/>'
        f'<c:showSerName val="0"/><c:showPercent val="{int(show_pct)}"/>'
        '<c:showBubbleSize val="0"/>'
    )
    num_fmt = f'<c:numFmt formatCode="{_esc(fmt)}" sourceLinked="0"/>' if fmt else ""
    pos_xml = f'<c:dLblPos val="{pos}"/>' if pos else ""
    if only_idx is not None:
        only_indices = [only_idx]
    if only_indices is not None:
        off = (
            '<c:showLegendKey val="0"/><c:showVal val="0"/><c:showCatName val="0"/>'
            '<c:showSerName val="0"/><c:showPercent val="0"/><c:showBubbleSize val="0"/>'
        )
        labels = ''.join(
            f'<c:dLbl><c:idx val="{index}"/>{num_fmt}<c:spPr><a:noFill/>'
            f"<a:ln><a:noFill/></a:ln></c:spPr>{text_props}{pos_xml}{flags}</c:dLbl>"
            for index in only_indices
        )
        return f"<c:dLbls>{labels}{off}</c:dLbls>"
    return (
        f"<c:dLbls>{num_fmt}<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr>"
        f"{text_props}{pos_xml}{flags}</c:dLbls>"
    )


def _value_format(chart: Chart) -> str:
    values = [abs(v) for s in chart.series for v in s.values if v is not None]
    if not values:
        return "General"
    if chart.chart_type == "bar_stacked_100":
        return "0%"
    if all(float(v).is_integer() for v in values) or max(values) >= 100:
        return "#,##0"
    return "#,##0.0"


def _cat_axis(
    ax_id: int,
    cross: int,
    pos: str,
    title: str | None,
    *,
    delete: bool = False,
    rot: int | None = None,
    n_labels: int = 0,
) -> str:
    # длинный ряд: подпись через одну-две точки, иначе месяцы слипаются
    skip = (n_labels + 11) // 12 if n_labels > 12 else 0
    return (
        f'<c:catAx><c:axId val="{ax_id}"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
        f'<c:delete val="{int(delete)}"/><c:axPos val="{pos}"/>'
        + (_title(title, rot) if title else "")
        + '<c:numFmt formatCode="General" sourceLinked="1"/>'
        '<c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/>'
        + _axis_line()
        + _text_props()
        + f'<c:crossAx val="{cross}"/><c:crosses val="autoZero"/><c:auto val="1"/>'
        '<c:lblAlgn val="ctr"/><c:lblOffset val="100"/>'
        + (f'<c:tickLblSkip val="{skip}"/><c:tickMarkSkip val="{skip}"/>' if skip else "")
        + '<c:noMultiLvlLbl val="0"/></c:catAx>'
    )


def _val_axis(
    ax_id: int,
    cross: int,
    pos: str,
    title: str | None,
    fmt: str,
    *,
    grid: bool = True,
    rot: int | None = None,
    between: str = "between",
    min_zero: bool = False,
) -> str:
    return (
        f'<c:valAx><c:axId val="{ax_id}"/><c:scaling><c:orientation val="minMax"/>'
        + ('<c:min val="0"/>' if min_zero else "")
        + "</c:scaling>"
        f'<c:delete val="0"/><c:axPos val="{pos}"/>'
        + (_gridlines() if grid and _style("gridlines", True) else "")
        + (_title(title, rot) if title else "")
        + f'<c:numFmt formatCode="{_esc(fmt)}" sourceLinked="0"/>'
        '<c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/>'
        "<c:spPr><a:ln><a:noFill/></a:ln></c:spPr>"
        + _text_props()
        + f'<c:crossAx val="{cross}"/><c:crosses val="autoZero"/>'
        f'<c:crossBetween val="{between}"/></c:valAx>'
    )


def _legend(pos: str = "b") -> str:
    pos = _style("legend_pos", pos) if pos == "b" else pos
    return f'<c:legend><c:legendPos val="{pos}"/><c:overlay val="0"/>{_text_props()}</c:legend>'


def oriented(chart: Chart) -> Chart:
    """Горизонтальные столбцы рисуются снизу вверх: чтобы крупнейшее было
    сверху, порядок категорий разворачивается (данные те же)."""
    if chart.chart_type != "bar_horizontal":
        return chart
    return chart.model_copy(
        update={
            "categories": list(reversed(chart.categories)),
            "series": [
                s.model_copy(update={"values": list(reversed(s.values))}) for s in chart.series
            ],
        }
    )


def _waterfall_series(chart: Chart) -> list[tuple[str, list[float | None]]]:
    """Мост «старт → факторы → итог» как 4 ряда накопительного столбца:
    невидимая база, итоги, рост и снижение. Совместимость со старым OOXML:
    классический stacked вместо нативного ``c:waterfallChart``."""
    values = [v if v is not None else 0.0 for v in chart.series[0].values]
    n = len(values)
    base: list[float | None] = []
    totals: list[float | None] = []
    up: list[float | None] = []
    down: list[float | None] = []
    running = 0.0
    for i, v in enumerate(values):
        if i == 0 or i == n - 1:
            base.append(None)
            totals.append(v)
            up.append(None)
            down.append(None)
            if i == 0:
                running = v
            continue
        prev, running = running, running + v
        base.append(min(prev, running))
        if v >= 0:
            up.append(v)
            down.append(None)
        else:
            up.append(None)
            down.append(-v)
        totals.append(None)
    return [("База", base), ("Итоги", totals), ("Рост", up), ("Снижение", down)]


def _srgb_fill(color: str | None, fallback_slot: str) -> str:
    if color:
        return f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
    return f'<a:solidFill><a:schemeClr val="{fallback_slot}"/></a:solidFill>'


def build_typed_chart_xml(
    chart: Chart, wb_rid: str | None, colors=None, style=None,
) -> bytes:
    """``chartN.xml`` для ``chart.chart_type`` (см. ``TYPED``). *colors* —
    ``chart_colors.ChartColors`` слайда: цвета подписей, сетки и серий по
    фону; без них — цвета темы (``tx1``, ``accent1..6``)."""
    tokens = (
        _INK.set(getattr(colors, "ink", None)),
        _GRID.set(getattr(colors, "grid", None)),
        _SERIES.set(tuple(getattr(colors, "series", ()) or ())),
        _MUTED.set(getattr(colors, "muted", None)),
        _STYLE.set(style),
    )
    try:
        return _build(chart, wb_rid)
    finally:
        _INK.reset(tokens[0])
        _GRID.reset(tokens[1])
        _SERIES.reset(tokens[2])
        _MUTED.reset(tokens[3])
        _STYLE.reset(tokens[4])


def _build(chart: Chart, wb_rid: str | None) -> bytes:
    kind = chart.chart_type or "bar"
    chart = oriented(chart)
    cats = _cat_ref(chart.categories)
    fmt = _value_format(chart)
    n_series = len(chart.series)
    many = n_series >= 2
    legend = ""
    plot = ""
    axes = ""
    if kind == "line" or kind == "area":
        points = len(chart.categories)
        sers = []
        for j, s in enumerate(chart.series):
            col = _col(2 + j)
            last = max((i for i, v in enumerate(s.values) if v is not None), default=None)
            gaps = any(v is None for v in s.values)
            marker = (
                '<c:marker><c:symbol val="none"/></c:marker>'
                if points > 12 and not gaps
                else f'<c:marker><c:symbol val="circle"/><c:size val="6"/><c:spPr>{_fill(j)}'
                "</c:spPr></c:marker>"
            )
            sp = (
                f'<c:spPr><a:ln w="{_style("line_w", 31750)}" cap="rnd">'
                f'{_fill(j)}<a:round/></a:ln></c:spPr>'
                if kind == "line"
                else f"<c:spPr>{_fill(j)}</c:spPr>"
            )
            lbl = _dlbls(show_val=True, pos="t", only_idx=last, fmt=fmt) if last is not None else ""
            sers.append(
                f'<c:ser><c:idx val="{j}"/><c:order val="{j}"/>{_tx(col, s.name)}{sp}'
                + (marker if kind == "line" else "")
                + lbl
                + cats
                + _num_ref("val", col, s.values, fmt)
                + ('<c:smooth val="0"/>' if kind == "line" else "")
                + "</c:ser>"
            )
        tag = "lineChart" if kind == "line" else "areaChart"
        plot = (
            f'<c:{tag}><c:grouping val="standard"/><c:varyColors val="0"/>{"".join(sers)}'
            + ('<c:marker val="1"/>' if kind == "line" else "")
            + '<c:axId val="500001"/><c:axId val="500002"/>'
            f"</c:{tag}>"
        )
        axes = _cat_axis(
            500001, 500002, "b", chart.category_axis_title, n_labels=len(chart.categories)
        ) + _val_axis(500002, 500001, "l", chart.value_axis_title, fmt, rot=-5400000)
        legend = _legend() if many else ""
    elif kind == "waterfall":
        palette = _SERIES.get()
        specs = _waterfall_series(chart)
        fills = [
            "<a:noFill/>",
            _muted_fill(),
            _srgb_fill(palette[0] if palette else None, "accent1"),
            _srgb_fill(palette[1] if len(palette) > 1 else None, "accent3"),
        ]
        sers = []
        for j, ((name, vals), fill) in enumerate(zip(specs, fills, strict=True)):
            # у ряда «Снижение» хранятся модули — минус ставит формат
            ser_fmt = f"\\-{fmt}" if j == 3 and fmt else fmt
            sers.append(
                f'<c:ser><c:idx val="{j}"/><c:order val="{j}"/>{_tx(_col(2 + j), name)}'
                f'<c:spPr>{fill}<a:ln><a:noFill/></a:ln></c:spPr>'
                '<c:invertIfNegative val="0"/>'
                + (_dlbls(show_val=True, pos="ctr", fmt=ser_fmt) if j else "")
                + cats
                + _num_ref("val", _col(2 + j), vals, fmt)
                + "</c:ser>"
            )
        plot = (
            '<c:barChart><c:barDir val="col"/><c:grouping val="stacked"/>'
            f'<c:varyColors val="0"/>{"".join(sers)}'
            '<c:gapWidth val="60"/><c:overlap val="100"/>'
            '<c:axId val="500001"/><c:axId val="500002"/></c:barChart>'
        )
        axes = _cat_axis(
            500001, 500002, "b", chart.category_axis_title, n_labels=len(chart.categories)
        ) + _val_axis(
            500002, 500001, "l", chart.value_axis_title, fmt, rot=-5400000, min_zero=True
        )
        # невидимая база — не строка легенды
        legend = (
            '<c:legend><c:legendEntry><c:idx val="0"/><c:delete val="1"/></c:legendEntry>'
            f'<c:legendPos val="b"/><c:overlay val="0"/>{_text_props()}</c:legend>'
        )
    elif kind in ("bar", "bar_horizontal", "bar_stacked", "bar_stacked_100", "histogram"):
        horizontal = kind == "bar_horizontal"
        grouping = {"bar_stacked": "stacked", "bar_stacked_100": "percentStacked"}.get(
            kind, "clustered"
        )
        show_values = len(chart.categories) <= 12 and kind not in ("bar_stacked_100",)
        sers = []
        for j, s in enumerate(chart.series):
            col = _col(2 + j)
            sers.append(
                f'<c:ser><c:idx val="{j}"/><c:order val="{j}"/>{_tx(col, s.name)}'
                + _bar_dpts(chart)
                + f"<c:spPr>{_fill(j)}<a:ln><a:noFill/></a:ln></c:spPr>"
                '<c:invertIfNegative val="0"/>'
                + (
                    _dlbls(
                        show_val=True, pos="outEnd" if grouping == "clustered" else "ctr", fmt=fmt
                    )
                    if show_values and (not many or grouping != "clustered")
                    else ""
                )
                + cats
                + _num_ref("val", col, s.values, fmt)
                + "</c:ser>"
            )
        gap = 0 if kind == "histogram" else _style("gap_width", 60 if horizontal else 80)
        overlap = '<c:overlap val="100"/>' if grouping != "clustered" else ""
        plot = (
            f'<c:barChart><c:barDir val="{"bar" if horizontal else "col"}"/>'
            f'<c:grouping val="{grouping}"/><c:varyColors val="0"/>{"".join(sers)}'
            f'<c:gapWidth val="{gap}"/>{overlap}<c:axId val="500001"/><c:axId val="500002"/>'
            "</c:barChart>"
        )
        axes = _cat_axis(
            500001,
            500002,
            "l" if horizontal else "b",
            chart.category_axis_title,
            rot=-5400000 if horizontal else None,
            n_labels=0 if horizontal else len(chart.categories),
        ) + _val_axis(
            500002,
            500001,
            "b" if horizontal else "l",
            chart.value_axis_title,
            fmt,
            rot=None if horizontal else -5400000,
        )
        # без подписей данных (много столбцов, гистограмма) серию называет
        # легенда — иначе график без метаданных (аудит chart.metadata)
        legend = _legend() if many or not show_values else ""
    elif kind in ("pie", "doughnut"):
        s = chart.series[0]
        pts = "".join(
            f'<c:dPt><c:idx val="{i}"/><c:bubble3D val="0"/><c:spPr>'
            + (
                _muted_fill()
                if str(chart.categories[i]).strip().casefold().split(" ")[0] in _OTHER_LABEL
                else _fill(i)
            )
            + '<a:ln w="12700"><a:solidFill><a:schemeClr val="bg1"/></a:solidFill></a:ln></c:spPr>'
            "</c:dPt>"
            for i in range(len(chart.categories))
        )
        # Callouts on small neighboring sectors collide even with no-wrap.
        # Bound visible callouts, never the sectors, names or workbook data.
        label_indices = None
        if len(s.values) > 4:
            label_indices = sorted(sorted(
                range(len(s.values)), key=lambda i: (-(s.values[i] or 0), i),
            )[:3])
        ser = (
            f'<c:ser><c:idx val="0"/><c:order val="0"/>{_tx("B", s.name)}{pts}'
            + _dlbls(show_pct=True, pos="outEnd", fmt="0%",
                     only_indices=label_indices, no_wrap=True)
            + cats
            + _num_ref("val", "B", s.values, fmt)
            + "</c:ser>"
        )
        tag = "pieChart" if kind == "pie" else "doughnutChart"
        tail = '<c:firstSliceAng val="0"/>' + (
            '<c:holeSize val="55"/>' if kind == "doughnut" else ""
        )
        plot = f'<c:{tag}><c:varyColors val="1"/>{ser}{tail}</c:{tag}>'
        legend = _legend("r")
    elif kind == "scatter":
        s = chart.series[0]
        xs = []
        for c in chart.categories:
            try:
                xs.append(float(str(c).replace(" ", "").replace(" ", "").replace(",", ".")))
            except ValueError:
                xs.append(None)
        ser = (
            f'<c:ser><c:idx val="0"/><c:order val="0"/>{_tx("B", s.name)}'
            '<c:spPr><a:ln w="19050"><a:noFill/></a:ln></c:spPr>'
            f'<c:marker><c:symbol val="circle"/><c:size val="7"/><c:spPr>{_fill(0)}</c:spPr>'
            "</c:marker>"
            + _scatter_trendline(xs, s.values)
            + _num_ref("xVal", "A", xs)
            + _num_ref("yVal", "B", s.values, fmt)
            + '<c:smooth val="0"/></c:ser>'
        )
        plot = (
            f'<c:scatterChart><c:scatterStyle val="lineMarker"/><c:varyColors val="0"/>{ser}'
            '<c:axId val="500001"/><c:axId val="500002"/></c:scatterChart>'
        )
        axes = _val_axis(
            500001, 500002, "b", chart.category_axis_title, "General", grid=False, between="midCat"
        ) + _val_axis(
            500002, 500001, "l", chart.value_axis_title, fmt, rot=-5400000, between="midCat"
        )
        legend = _legend()  # серию точек называет легенда (иначе chart.metadata)
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f'<c:chartSpace xmlns:c="{C}" xmlns:a="{A}" xmlns:r="{R_NS}">'
        '<c:date1904 val="0"/><c:lang val="ru-RU"/><c:roundedCorners val="0"/>'
        '<c:chart><c:autoTitleDeleted val="1"/><c:plotArea><c:layout/>'
        f"{plot}{axes}"
        "<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr></c:plotArea>"
        f'{legend}<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart>'
        "<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr>"
        + _text_props()
        + (
            f'<c:externalData r:id="{wb_rid}"><c:autoUpdate val="0"/></c:externalData>'
            if wb_rid
            else ""
        )
        + "</c:chartSpace>"
    )
    return xml.encode("utf-8")


def typed_workbook(chart: Chart) -> bytes:
    """Встроенная книга того же вида: категории в A2:, серии с B1."""
    from io import BytesIO

    import openpyxl

    chart = oriented(chart)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.cell(row=1, column=1, value=chart.category_axis_title or "")
    for i, cat in enumerate(chart.categories, start=2):
        value: object = cat
        if chart.chart_type == "scatter":
            try:
                value = float(str(cat).replace(" ", "").replace(" ", "").replace(",", "."))
            except ValueError:
                value = cat
        ws.cell(row=i, column=1, value=value)
    if chart.chart_type == "waterfall":
        # в книге — те же 4 синтетических ряда, что в XML: при обновлении
        # кэша Excel мост останется собранным
        series_list = [
            ChartSeries(name=name, values=vals) for name, vals in _waterfall_series(chart)
        ]
    else:
        series_list = list(chart.series)
    for j, series in enumerate(series_list):
        ws.cell(row=1, column=2 + j, value=series.name or f"Ряд {j + 1}")
        for i, value in enumerate(series.values, start=2):
            ws.cell(row=i, column=2 + j, value=value)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def chart_type_of(xml: bytes) -> str | None:
    """Тип графика в ``chartN.xml`` (для тестов инвариантности)."""
    from lxml import etree

    root = etree.fromstring(xml)
    plot = root.find(f".//{{{C}}}plotArea")
    if plot is None:
        return None
    for el in plot:
        name = etree.QName(el).localname
        if name == "barChart":
            bar_dir = el.find(f"{{{C}}}barDir")
            grouping = el.find(f"{{{C}}}grouping")
            gap = el.find(f"{{{C}}}gapWidth")
            g = grouping.get("val") if grouping is not None else "clustered"
            if gap is not None and gap.get("val") == "0":
                return "histogram"
            if g == "stacked":
                return "bar_stacked"
            if g == "percentStacked":
                return "bar_stacked_100"
            return (
                "bar_horizontal" if bar_dir is not None and bar_dir.get("val") == "bar" else "bar"
            )
        if name in ("lineChart", "line3DChart"):
            return "line"
        if name in ("pieChart", "pie3DChart"):
            return "pie"
        if name == "doughnutChart":
            return "doughnut"
        if name == "scatterChart":
            return "scatter"
        if name == "areaChart":
            return "area"
    return None


__all__ = ["TYPED", "build_typed_chart_xml", "chart_type_of", "oriented", "typed_workbook"]
