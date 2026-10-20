"""Small native SVG chart renderer. Unsupported plots use object-level fallback."""

from __future__ import annotations

import math
from html import escape

from lxml import etree

from deckdna.pptx.exporting.html_style import A

C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
NS = {"c": C}


def _at(node, path, default=None):
    el = node.find(path, NS)
    return el.get("val", default) if el is not None else default


def _cache(ser, name):
    root = ser.find(f"c:{name}", NS)
    if root is None:
        return []
    caches = root.xpath(".//c:numCache | .//c:strCache | .//c:numLit | .//c:strLit", namespaces=NS)
    if not caches:
        return []
    cache = caches[0]
    points = {
        int(p.get("idx")): p.findtext("c:v", namespaces=NS) for p in cache.findall("c:pt", NS)
    }
    count = max(int(_at(cache, "c:ptCount", 0)), max(points, default=-1) + 1)
    return [points.get(i) for i in range(count)]


def _number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def chart_svg(chart, theme, width, height):
    """Returns SVG from cached chart data, or None for unsupported semantics."""
    root = etree.fromstring(etree.tostring(chart._chartSpace))
    area = root.find(".//c:plotArea", NS)
    if area is None:
        return None
    plots = [p for p in area if etree.QName(p).localname.endswith("Chart")]
    supported = {"barChart", "lineChart", "pieChart", "doughnutChart", "scatterChart"}
    if len(plots) != 1 or etree.QName(plots[0]).localname not in supported:
        return None
    plot = plots[0]
    kind = etree.QName(plot).localname
    # Stacked/percent-stacked require a different scale; retain the exact PDF crop.
    if _at(plot, "c:grouping") in ("stacked", "percentStacked"):
        return None
    series = plot.findall("c:ser", NS)
    data = [_cache(s, "yVal" if kind == "scatterChart" else "val") for s in series]
    numbers = [[_number(v) for v in values] for values in data]
    if not numbers or not any(v is not None for row in numbers for v in row):
        return None
    categories = _cache(series[0], "cat")
    count = max(map(len, numbers))
    categories = [
        (categories[i] if i < len(categories) else None) or str(i + 1) for i in range(count)
    ]
    names = [
        s.findtext("c:tx/c:strRef/c:strCache/c:pt/c:v", namespaces=NS)
        or s.findtext("c:tx/c:v", namespaces=NS)
        or f"Серия {i + 1}"
        for i, s in enumerate(series)
    ]
    colors = [
        theme.color(
            s.find(f"c:spPr/{{{A}}}solidFill", NS),
            theme.palette.get(f"accent{i % 6 + 1}", "#4472C4"),
        )
        for i, s in enumerate(series)
    ]
    # Use the chart's own title, not the shape name, for accessible labels.
    title = " ".join(
        root.xpath(
            ".//c:title//a:t/text()",
            namespaces={**NS, "a": "http://schemas.openxmlformats.org/drawingml/2006/main"},
        )
    )
    label = title or ("Круговая диаграмма" if kind in ("pieChart", "doughnutChart") else "График")
    w, h = 800, max(220, 800 * height / max(1, width))
    ink = theme.palette.get(theme.cmap.get("tx1", "dk1"), "#222222")
    label_props = root.find(f"c:txPr/{{{A}}}p/{{{A}}}pPr/{{{A}}}defRPr", NS)
    # Chart text properties override the slide theme, including on dark slides.
    # Keep source styling rather than guessing a contrasting runtime color.
    face = None
    if label_props is not None:
        ink = theme.color(label_props.find(f"{{{A}}}solidFill"), ink)
        latin = label_props.find(f"{{{A}}}latin")
        face = latin.get("typeface") if latin is not None else None
    font = escape(theme.font(face), quote=True)
    label_size = float(label_props.get("sz", 1200) if label_props is not None else 1200)
    label_size = w * label_size * 127 / max(1, width)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h:.3f}" '
        f'role="img" aria-label="{escape(label, quote=True)}">',
        f"<title>{escape(label)}</title>",
        "<desc>"
        + escape(
            "; ".join(
                f"{name}: " + ", ".join(f"{categories[i]}={v}" for i, v in enumerate(row))
                for name, row in zip(names, data, strict=True)
            )
        )
        + "</desc>",
        f'<g font-family="{font}" font-size="{label_size:.4f}" fill="{ink}">',
    ]
    if title:
        out.append(
            f'<text x="400" y="{label_size * 1.2}" text-anchor="middle">{escape(title)}</text>'
        )
    horizontal = kind == "barChart" and _at(plot, "c:barDir") == "bar"
    x0 = max(72, label_size * 3.5)
    if horizontal:
        label_width = max(map(len, categories)) * label_size * 0.6
        x0 = max(x0, min(w * 0.45, label_width + 10))
    y0 = label_size * 2.2 if title else max(24, label_size)
    # End tick labels extend beyond their anchor; keep their trailing half
    # inside the SVG instead of clipping the last month/value at the edge.
    right_margin = max(28, label_size * 3)
    if not horizontal:
        right_margin = max(right_margin, max(map(len, categories)) * label_size * 0.35)
    right_margin = min(w * 0.25, right_margin)
    pw, ph = w - x0 - right_margin, max(1, h - y0 - max(70, label_size * 2.3))

    def text(x, y, value, anchor="middle"):
        return f'<text x="{x:.3f}" y="{y:.3f}" text-anchor="{anchor}">{escape(str(value))}</text>'

    if kind in ("pieChart", "doughnutChart"):
        vals = [max(0, v or 0) for v in numbers[0]]
        total = sum(vals)
        if not total:
            return None
        cx, cy, radius = 300, y0 + ph / 2, max(1, min(210, ph / 2 - 5))
        angle = math.radians(float(_at(plot, "c:firstSliceAng", 0)) - 90)
        hole = float(_at(plot, "c:holeSize", 50)) / 100 if kind == "doughnutChart" else 0
        for i, val in enumerate(vals):
            if not val:
                continue
            # Two arcs also handle a one-point pie (a full 360-degree circle).
            end = angle + val / total * 2 * math.pi
            mid = (angle + end) / 2

            def point(a, r=radius):
                return f"{cx + r * math.cos(a):.4f},{cy + r * math.sin(a):.4f}"

            d = (
                f"M {point(angle)} A {radius},{radius} 0 0 1 {point(mid)} "
                f"A {radius},{radius} 0 0 1 {point(end)}"
            )
            if hole:
                r = radius * hole
                d += (
                    f" L {point(end, r)} A {r},{r} 0 0 0 {point(mid, r)} "
                    f"A {r},{r} 0 0 0 {point(angle, r)} Z"
                )
            else:
                d += f" L {cx},{cy} Z"
            color = theme.palette.get(f"accent{i % 6 + 1}", colors[0])
            # A point may override the series/theme color.
            for pt in series[0].findall("c:dPt", NS):
                if int(_at(pt, "c:idx", -1)) == i:
                    color = theme.color(
                        pt.find(
                            "c:spPr/{http://schemas.openxmlformats.org/drawingml/2006/main}solidFill",
                            NS,
                        ),
                        color,
                    )
            out.append(
                f'<path d="{d}" fill="{color}">'
                f"<title>{escape(categories[i])}: {val:g}</title></path>"
            )
            out.append(f'<rect x="550" y="{y0 + i * 25}" width="14" height="14" fill="{color}"/>')
            out.append(text(574, y0 + i * 25 + 13, f"{categories[i]}: {val:g}", "start"))
            angle = end
    else:
        finite = [v for row in numbers for v in row if v is not None]
        lo, hi = min(0, min(finite)), max(0, max(finite))
        valax = area.find("c:valAx", NS)
        if valax is not None:
            lo = float(_at(valax, "c:scaling/c:min", lo))
            hi = float(_at(valax, "c:scaling/c:max", hi))
        if hi == lo:
            hi = lo + 1
        orientation = _at(area, "c:catAx/c:scaling/c:orientation", "minMax")
        reverse_categories = orientation == ("minMax" if horizontal else "maxMin")

        def category_slot(i):
            return count - 1 - i if reverse_categories else i

        shown_categories = set(range(count))
        if not horizontal and kind != "scatterChart" and count > 1:
            label_width = max(map(len, categories)) * label_size * 0.6 + label_size
            capacity = max(2, int(pw / max(1, label_width)))
            if count > capacity:
                # Thin tick labels only; all points, tooltips and accessible data
                # remain. Uniform sampling keeps both endpoints visible.
                shown_categories = {
                    round(i * (count - 1) / (capacity - 1)) for i in range(capacity)
                }

        def y(v):
            return y0 + ph * (1 - (v - lo) / (hi - lo))

        def xval(v):
            return x0 + pw * (v - lo) / (hi - lo)

        for step in range(5):
            v = lo + (hi - lo) * step / 4
            if horizontal:
                x = xval(v)
                out.append(f'<path d="M{x},{y0}v{ph}" stroke="#CCCCCC" stroke-width="1"/>')
                out.append(text(x, y0 + ph + 24, f"{v:.4g}"))
            else:
                yy = y(v)
                out.append(f'<path d="M{x0},{yy}h{pw}" stroke="#CCCCCC" stroke-width="1"/>')
                out.append(text(x0 - 10, yy + 5, f"{v:.4g}", "end"))
        for i, cat in enumerate(categories):
            if kind != "scatterChart" and i in shown_categories:
                slot = category_slot(i)
                out.append(
                    text(
                        x0 - 10 if horizontal else x0 + (slot + 0.5) * pw / count,
                        y0 + (slot + 0.5) * ph / count + 5 if horizontal else y0 + ph + 24,
                        cat,
                        "end" if horizontal else "middle",
                    )
                )
        scatter_x = (
            [[_number(v) for v in _cache(s, "xVal")] for s in series]
            if kind == "scatterChart"
            else []
        )
        xs = [v for row in scatter_x for v in row if v is not None]
        xmin, xmax = (min(xs), max(xs)) if xs else (0, 1)
        if xmax == xmin:
            xmax = xmin + 1
        if kind == "scatterChart":
            for i in range(5):
                v = xmin + (xmax - xmin) * i / 4
                out.append(text(x0 + pw * i / 4, y0 + ph + 24, f"{v:.4g}"))
        for j, row in enumerate(numbers):
            segment = []
            for i, value in enumerate(row):
                if value is None:
                    segment.append(None)
                    continue
                slot = category_slot(i)
                xx = x0 + (slot + 0.5) * pw / count
                yy = y(value)
                if kind == "barChart":
                    span = (ph if horizontal else pw) / count
                    thickness = span * 0.75 / len(series)
                    pos = (y0 if horizontal else x0) + slot * span + span * 0.125 + j * thickness
                    if horizontal:
                        x, yy, ww, hh = (
                            min(xval(0), xval(value)),
                            pos,
                            abs(xval(value) - xval(0)),
                            thickness,
                        )
                    else:
                        x, yy, ww, hh = pos, min(y(0), y(value)), thickness, abs(y(value) - y(0))
                    out.append(
                        f'<rect x="{x:.4f}" y="{yy:.4f}" width="{ww:.4f}" '
                        f'height="{hh:.4f}" fill="{colors[j]}">'
                        f"<title>{escape(names[j])}, {escape(categories[i])}: "
                        f"{value:g}</title></rect>"
                    )
                else:
                    if kind == "scatterChart":
                        vx = scatter_x[j][i] if i < len(scatter_x[j]) else None
                        if vx is None:
                            segment.append(None)
                            continue
                        xx = x0 + pw * (vx - xmin) / (xmax - xmin)
                    segment.append((xx, yy))
                    out.append(
                        f'<circle cx="{xx:.4f}" cy="{yy:.4f}" r="4" fill="{colors[j]}">'
                        f"<title>{escape(names[j])}: {value:g}</title></circle>"
                    )
            if kind == "lineChart" or (
                kind == "scatterChart" and _at(plot, "c:scatterStyle", "marker") != "marker"
            ):
                path, pen = [], "M"
                for pt in segment:
                    if pt is None:
                        pen = "M"
                    else:
                        path.append(f"{pen}{pt[0]:.4f},{pt[1]:.4f}")
                        pen = "L"
                out.append(
                    f'<path d="{" ".join(path)}" fill="none" '
                    f'stroke="{colors[j]}" stroke-width="3"/>'
                )
        if root.find(".//c:legend", NS) is not None:
            for j, name in enumerate(names):
                out.append(
                    f'<rect x="{x0 + j * pw / len(names)}" y="{h - 20}" '
                    f'width="12" height="12" fill="{colors[j]}"/>'
                )
                out.append(text(x0 + j * pw / len(names) + 18, h - 9, name, "start"))
    return "".join(out) + "</g></svg>"
