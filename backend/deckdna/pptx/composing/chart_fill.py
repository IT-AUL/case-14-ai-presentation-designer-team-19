"""Fill native `c:chart` parts with ContentPack Chart data.

Chart data in OOXML lives twice: `c:numCache`/`c:strCache` point caches
inside chartN.xml (what every renderer draws) and the embedded xlsx
workbook behind the chart's rels (what PowerPoint's «Edit Data» opens).
This module rewrites the point caches and, when an embedded workbook
is present and openpyxl-parseable, syncs the cells the `c:f` formulas
reference so both views agree.

Narrow practical scope, honestly bounded:
- one `c:ser` per ChartSeries; data series beyond the chart's own
  `c:ser` count are truncated (`series_dropped`), surplus `c:ser`
  elements keep their stock data (dropping them is safe but left out
  of scope);
- two output slides cloned from the same chart-bearing exemplar
  share one physical chart part (the dependency closure is a set);
  ``unshare_chart_parts`` duplicates the part (+ its workbook) for
  the later slide, so each carries its own data set — when the
  source bytes are missing the second unit still drops;
- the workbook sync is best-effort: any openpyxl failure keeps the
  cache rewrite and counts `workbook_errors`;
- a slide whose rels carry no `c:chart` part gets a NEW native
  chart (`_add_slide_chart`, same pattern as `add_table`/
  `add_picture` in the sibling fill modules): minimal valid
  clustered `barChart` with theme `schemeClr` series colors,
  an openpyxl workbook in `ppt/embeddings/` (same layout
  ``_sync_workbook`` writes), rels/content-type plumbing and a
  `p:graphicFrame` in the largest emptied text body — the frame
  is removed from used hosts via the shared `used_hosts` set.
  Typed limitation: only `barChart` is creatable
  (`created_types`); no series / no rels part / no host → the
  unit still drops honestly.

Markdown convention (for content ingestion wiring, see
`parse_chart_block`):

    ```chart id=ch-1
    categories: 2021, 2022, 2023, 2024
    Ряд 1: 4.3, 2.5, 3.5, 4.5
    Ряд 2: 2.4, 4.4, 1.5, 2.8
    ```
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field, replace
from io import BytesIO

from lxml import etree

from deckdna.contracts.content_pack import Chart, ChartSeries

# Markdown-конвенция описана в ingestion/chart_block.py; парсер живёт
# там же (слой ingestion), re-export для обратной совместимости.
from deckdna.ingestion.chart_block import parse_chart_block  # noqa: F401
from deckdna.pptx.composing.table_fill import _empty_body_hosts, _next_shape_id
from deckdna.pptx.opc.package import (
    CONTENT_TYPES,
    Relationship,
    parse_rels,
    rels_name_for,
    resolve_target,
    serialize_rels,
)

C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CHART_REL = f"{R_NS}/chart"
_PACKAGE_REL = f"{R_NS}/package"
_CHART_CT = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
_XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_CHART_PART_RE = re.compile(r"^ppt/charts/chart(\d+)\.xml$")
_XLSX_PART_RE = re.compile(r"^ppt/embeddings/[^/]*?(\d+)\.xlsx$")
# Минимумы host-бокса для созданного чарта: ~2.2" × ~1.6" — меньше
# делает barChart+легенду нечитаемым (строже table anchor,
# сопоставимо с diagram host).
_MIN_CHART_W_EMU = 2_000_000
_MIN_CHART_H_EMU = 1_500_000
_EMPTY_RELS = (
    b"<Relationships xmlns='http://schemas.openxmlformats.org/"
    b"package/2006/relationships'/>"
)


@dataclass
class ChartFillReport:
    charts_found: int = 0
    charts_filled: int = 0
    charts_created: int = 0
    units_dropped: int = 0
    series_dropped: int = 0
    points_written: int = 0
    workbook_errors: int = 0
    chart_ids: list[str] = field(default_factory=list)
    # Тип созданных from-scratch чартов — единственный поддержанный вид
    # fallback'а: clustered barChart. Типизированное ограничение, не
    # скрытое дефолтирование под любой Chart.
    created_types: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "charts_found": self.charts_found,
            "charts_filled": self.charts_filled,
            "charts_created": self.charts_created,
            "units_dropped": self.units_dropped,
            "series_dropped": self.series_dropped,
            "points_written": self.points_written,
            "workbook_errors": self.workbook_errors,
            "chart_ids": self.chart_ids,
            "created_types": self.created_types,
        }


def _fmt_value(v) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _num(v: str) -> float | str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return int(f) if f == int(f) else f


def _set_pts(cache: etree._Element, values: list, numeric: bool) -> int:
    """Replace a `c:numCache`/`c:strCache`/`c:numLit`/`c:strLit` point
    list with *values*, keeping formatCode. Returns points written."""
    for pt in cache.findall(f"{{{C}}}pt"):
        cache.remove(pt)
    count = cache.find(f"{{{C}}}ptCount")
    if count is None:
        count = etree.SubElement(cache, f"{{{C}}}ptCount")
        cache.insert(0, count)
    count.set("val", str(len(values)))
    for i, value in enumerate(values):
        pt = etree.SubElement(cache, f"{{{C}}}pt")
        pt.set("idx", str(i))
        v = etree.SubElement(pt, f"{{{C}}}v")
        v.text = _fmt_value(value) if numeric else str(value)
    return len(values)


def _sync_formula(ref: etree._Element, n_points: int) -> None:
    """Extend/shrink the trailing row of `Sheet1!$B$2:$B$N`-style `c:f`
    to match the new point count — best-effort, formula only edited
    when it matches the simple range shape."""
    f_el = ref.find(f"{{{C}}}f")
    if f_el is None or not f_el.text:
        return
    m = re.match(r"^(.*!\$[A-Z]+\$)(\d+)(:\$[A-Z]+\$)(\d+)$", f_el.text.strip())
    if not m:
        return
    start_row = int(m.group(2))
    f_el.text = f"{m.group(1)}{start_row}{m.group(3)}{start_row + n_points - 1}"


def _fill_ser(ser: etree._Element, categories: list[str], series: ChartSeries) -> int:
    """Write one series: categories into `c:cat` cache, values into
    `c:val` cache, name into `c:tx` cache. Returns points written."""
    written = 0
    cat = ser.find(f"{{{C}}}cat")
    if cat is not None:
        for ref in cat:
            if not isinstance(ref.tag, str):
                continue
            cache = ref.find(f"{{{C}}}numCache")
            if cache is None:
                cache = ref.find(f"{{{C}}}strCache")
            if cache is None and ref.tag in (
                f"{{{C}}}numLit",
                f"{{{C}}}strLit",
            ):
                cache = ref
            if cache is not None:
                numeric = "numCache" in cache.tag or "numLit" in cache.tag
                written += _set_pts(
                    cache,
                    [_num(c) for c in categories] if numeric else categories,
                    numeric=numeric,
                )
                _sync_formula(ref, len(categories))
    val = ser.find(f"{{{C}}}val")
    if val is not None:
        for ref in val:
            if not isinstance(ref.tag, str):
                continue
            cache = ref.find(f"{{{C}}}numCache")
            if cache is None and ref.tag == f"{{{C}}}numLit":
                cache = ref
            if cache is not None:
                written += _set_pts(cache, series.values, numeric=True)
                _sync_formula(ref, len(series.values))
    if series.name:
        tx = ser.find(f"{{{C}}}tx")
        if tx is not None:
            ref = tx.find(f"{{{C}}}strRef")
            cache = ref.find(f"{{{C}}}strCache") if ref is not None else None
            if cache is None:
                cache = tx.find(f"{{{C}}}strLit")
            if cache is not None:
                _set_pts(cache, [series.name], numeric=False)
    return written


def _sync_workbook(
    pkg_parts: dict[str, bytes], chart_part: str, chart: Chart, rels
) -> bool:
    """Rewrite the embedded workbook cells the chart formulas point at.

    Narrow: categories → column A from row 2, series → columns B.. with
    the series name in row 1 — the layout PowerPoint/python-pptx emit.
    Returns True when a workbook part was found and rewritten."""
    chart_rels = [
        rel for rel in rels
        if rel.type_name == "package" or rel.target.lower().endswith(".xlsx")
    ]
    if not chart_rels:
        return False
    xlsx_part = resolve_target(chart_part, chart_rels[0].target)
    raw = pkg_parts.get(xlsx_part)
    if raw is None:
        return False
    import openpyxl

    wb = openpyxl.load_workbook(BytesIO(raw))
    ws = wb.worksheets[0]
    # Clear the previous data block (categories + up to 8 series, 512 rows).
    for row in ws.iter_rows(min_row=1, max_row=512, min_col=1, max_col=9):
        for cell in row:
            cell.value = None
    # Категории — строки по контракту `list[str]`: числовая ячейка
    # заставляет Excel отрисовать «2021» как serial-date.
    for i, cat in enumerate(chart.categories, start=2):
        ws.cell(row=i, column=1, value=cat)
    for j, series in enumerate(chart.series):
        col = 2 + j
        if series.name:
            ws.cell(row=1, column=col, value=series.name)
        for i, value in enumerate(series.values, start=2):
            ws.cell(row=i, column=col, value=value)
    buf = BytesIO()
    wb.save(buf)
    pkg_parts[xlsx_part] = buf.getvalue()
    return True


def _fill_chart_xml(xml: bytes, chart: Chart, report: ChartFillReport) -> bytes:
    root = etree.fromstring(xml)
    sers = [s for s in root.iter(f"{{{C}}}ser")]
    for ser, series in zip(sers, chart.series, strict=False):
        report.points_written += _fill_ser(ser, chart.categories, series)
    report.series_dropped += max(0, len(chart.series) - len(sers))
    report.charts_filled += 1
    report.chart_ids.append(chart.id)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def fill_slide_charts(
    pkg_parts: dict[str, bytes],
    slide_part: str,
    charts: list[Chart],
    slide_rels,
    used_parts: set[str],
    used_hosts: set[tuple[int, int, int, int]] | None = None,
) -> ChartFillReport:
    """Pour *charts* into the chart parts reachable from *slide_part*'s
    rels, mutating entries of *pkg_parts*. *slide_rels* is the parsed
    relationship list of the slide part; *used_parts* tracks chart
    parts already filled on another output slide (a shared part can't
    carry two different data sets).

    Units beyond the slide's chart parts create a native `c:chart`
    part + `p:graphicFrame` in the largest emptied text body
    (:func:`_add_slide_chart`, the add_table/add_picture analogue)
    when *used_hosts* is provided — the same set table/diagram/image
    creation consumes, so all four never fight over one host slot."""
    report = ChartFillReport()
    if not charts:
        return report
    chart_parts = [
        resolve_target(slide_part, rel.target)
        for rel in slide_rels
        if rel.type == _CHART_REL and not rel.is_external
    ]
    report.charts_found = len(chart_parts)
    part_iter = iter(chart_parts)
    for chart in charts:
        part = next(
            (p for p in part_iter if p not in used_parts and p in pkg_parts),
            None,
        )
        if part is None:
            if used_hosts is not None and _add_slide_chart(
                pkg_parts, slide_part, chart, slide_rels, used_hosts, report
            ):
                continue
            report.units_dropped += 1
            continue
        xml = pkg_parts[part]
        pkg_parts[part] = _fill_chart_xml(xml, chart, report)
        used_parts.add(part)
        rels_name = rels_name_for(part)
        chart_rels = parse_rels(pkg_parts.get(rels_name, _EMPTY_RELS))
        try:
            _sync_workbook(pkg_parts, part, chart, chart_rels)
        except Exception:
            report.workbook_errors += 1
    return report



def _relativize(owner_part: str, target_part: str) -> str:
    """Target string for a rel owned by *owner_part* — `../charts/x.xml`
    style, matching what slide .rels already use."""
    return posixpath.relpath(target_part, posixpath.dirname(owner_part))


def _content_type_of(parts: dict[str, bytes], part_name: str) -> str | None:
    """Per-part Override content type, when the package declares one."""
    for el in etree.fromstring(parts[CONTENT_TYPES]):
        if (
            etree.QName(el).localname == "Override"
            and el.get("PartName") == f"/{part_name}"
        ):
            return el.get("ContentType")
    return None


def _add_override(parts: dict[str, bytes], part_name: str, content_type: str) -> None:
    root = etree.fromstring(parts[CONTENT_TYPES])
    for el in root:
        if (
            etree.QName(el).localname == "Override"
            and el.get("PartName") == f"/{part_name}"
        ):
            return  # already declared
    el = etree.SubElement(root, "Override")
    el.set("PartName", f"/{part_name}")
    el.set("ContentType", content_type)
    parts[CONTENT_TYPES] = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def _unique_part_name(parts: dict[str, bytes], part_name: str, tag: str) -> str:
    """*part_name* with `_<tag>` before the extension, bumped until free."""
    stem, dot, ext = part_name.rpartition(".")
    candidate = f"{stem}_{tag}{dot}{ext}"
    n = 2
    while candidate in parts:
        candidate = f"{stem}_{tag}_{n}{dot}{ext}"
        n += 1
    return candidate


def _duplicate_chart_part(
    out_parts: dict[str, bytes],
    src_parts: dict[str, bytes],
    chart_part: str,
    tag: str,
) -> str | None:
    """Copy *chart_part* under a fresh name so another output slide can
    carry a different data set.

    Bytes come from *src_parts* — the unmutated template package — so
    the copy starts pristine even after sibling slides already filled
    their own parts. The chart's embedded workbook is duplicated too
    (the sync mutates cells); style/colour deps stay shared — fill
    never writes them. Returns the new part name, or None when the
    source bytes are missing from *src_parts*.
    """
    raw = src_parts.get(chart_part)
    if raw is None:
        return None
    new_part = _unique_part_name(out_parts, chart_part, tag)
    out_parts[new_part] = raw

    crels = parse_rels(src_parts.get(rels_name_for(chart_part), _EMPTY_RELS))
    if crels:
        new_rels = []
        for rel in crels:
            # Same detection as _sync_workbook: the package/xlsx rel is
            # the one data sink the fill rewrites per chart.
            if rel.is_external or not (
                rel.type_name == "package"
                or rel.target.lower().endswith(".xlsx")
            ):
                new_rels.append(rel)
                continue
            wtgt = resolve_target(chart_part, rel.target)
            wraw = src_parts.get(wtgt)
            if wraw is None:
                new_rels.append(rel)
                continue
            wdup = _unique_part_name(out_parts, wtgt, tag)
            out_parts[wdup] = wraw
            wct = _content_type_of(out_parts, wtgt)
            if wct:
                _add_override(out_parts, wdup, wct)
            new_rels.append(
                replace(rel, target=_relativize(new_part, wdup))
            )
        out_parts[rels_name_for(new_part)] = serialize_rels(new_rels)

    ct = _content_type_of(out_parts, chart_part) or (
        "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
    )
    _add_override(out_parts, new_part, ct)
    return new_part


def unshare_chart_parts(
    out_parts: dict[str, bytes],
    src_parts: dict[str, bytes],
    slide_part: str,
    used_parts: set[str],
    tag: str,
) -> None:
    """Repoint *slide_part*'s chart rels at fresh duplicates of chart
    parts already claimed by an earlier output slide.

    Two output slides cloned from the same chart-bearing exemplar
    share one physical part — the dependency closure is a set. Call
    before :func:`fill_slide_charts` for each output slide; parts not
    yet in *used_parts* are left alone (the slide keeps the first
    claim), missing source bytes keep the shared part (the unit drops
    downstream, as before).
    """
    rels_name = rels_name_for(slide_part)
    rels = parse_rels(out_parts.get(rels_name, _EMPTY_RELS))
    changed = False
    new_rels = []
    dup_i = 0
    for rel in rels:
        if rel.type != _CHART_REL or rel.is_external:
            new_rels.append(rel)
            continue
        target = resolve_target(slide_part, rel.target)
        if target not in used_parts:
            new_rels.append(rel)
            continue
        dup_i += 1
        dup = _duplicate_chart_part(
            out_parts, src_parts, target, f"{tag}_{dup_i}"
        )
        if dup is None:
            new_rels.append(rel)
            continue
        new_rels.append(
            replace(rel, target=_relativize(slide_part, dup))
        )
        changed = True
    if changed:
        out_parts[rels_name] = serialize_rels(new_rels)


# --- From-scratch native chart creation (no c:chart exemplar) ---
#
# Аналог add_table/add_picture: когда у слайда нет chart-парта, юнит
# создаёт минимальный ВАЛИДНЫЙ clustered barChart с нуля — свой
# chartN.xml + embedded workbook + rels + content-type Override +
# p:graphicFrame в крупнейшем опустевшем теле. Типизированное
# ограничение: единственный создаваемый вид — barChart (barDir=col,
# clustered); данные кладутся и в numCache/strCache (рендерят все), и
# в workbook (PowerPoint «Edit Data»). Цвета серий — schemeClr
# accent1..6 темы шаблона, т.е. палитра берётся из theme, не
# хардкодится. Не создаём (честный drop): нет пустого host-тела, нет
# серий, нет rels-файла слайда.

def _col_letter(i: int) -> str:
    """1-based Excel column letter: 1→A, 27→AA."""
    s = ""
    while i > 0:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s



def _next_indexed_part(
    parts: dict[str, bytes], pattern: re.Pattern, prefix: str, ext: str
) -> str:
    """First free `prefix<N><ext>` part name — scan-based, не
    конфликтует с уже занятыми chartN/workbook-партами пакета."""
    idx = max(
        (int(m.group(1)) for name in parts if (m := pattern.match(name))),
        default=0,
    )
    while f"{prefix}{idx + 1}{ext}" in parts:
        idx += 1
    return f"{prefix}{idx + 1}{ext}"


def _next_rid(rels) -> str:
    idx = max(
        (
            int(rel.id[3:])
            for rel in rels
            if rel.id.startswith("rId") and rel.id[3:].isdigit()
        ),
        default=0,
    )
    return f"rId{idx + 1}"


def _ser_xml(
    j: int, col: str, name: str | None, cat_ref: str, n_val: int, values: list
) -> str:
    """One `c:ser` — tx strRef→header cell, cat shared ref, val
    numRef→series column. Цвет — schemeClr темы (accent1..6 по кругу)."""
    tx = ""
    if name:
        tx = (
            f'<c:tx><c:strRef><c:f>Sheet1!${col}$1</c:f><c:strCache>'
            f'<c:ptCount val="1"/><c:pt idx="0"><c:v>{_esc(name)}</c:v></c:pt>'
            f"</c:strCache></c:strRef></c:tx>"
        )
    pts = "".join(
        f'<c:pt idx="{i}"><c:v>{_fmt_value(v)}</c:v></c:pt>'
        for i, v in enumerate(values)
    )
    return (
        f"<c:ser><c:idx val=\"{j}\"/><c:order val=\"{j}\"/>{tx}"
        f'<c:spPr><a:solidFill><a:schemeClr val="accent{(j % 6) + 1}"/>'
        f"</a:solidFill><a:ln><a:noFill/></a:ln><a:effectLst/></c:spPr>"
        f"<c:invertIfNegative val=\"0\"/>{cat_ref}"
        f'<c:val><c:numRef><c:f>Sheet1!${col}$2:${col}${n_val + 1}</c:f>'
        f'<c:numCache><c:formatCode>General</c:formatCode>'
        f'<c:ptCount val="{n_val}"/>{pts}</c:numCache></c:numRef></c:val>'
        f"</c:ser>"
    )


def _build_chart_xml(chart: Chart, wb_rid: str | None) -> bytes:
    """Minimal valid `chartN.xml`: clustered barChart + cat/val axes +
    externalData на embedded workbook (только при wb_rid). Категории —
    всегда strRef/strCache: контракт `categories: list[str]`, числовая
    коэрция ломает ось (Excel показывает «2021» как serial-date)."""
    n_cat = len(chart.categories)
    cat_pts = "".join(
        f'<c:pt idx="{i}"><c:v>{_esc(_fmt_value(c))}</c:v></c:pt>'
        for i, c in enumerate(chart.categories)
    )
    cat_ref = "" if n_cat == 0 else (
        f"<c:cat><c:strRef><c:f>Sheet1!$A$2:$A${n_cat + 1}</c:f>"
        f"<c:strCache><c:ptCount val=\"{n_cat}\"/>{cat_pts}"
        f"</c:strCache></c:strRef></c:cat>"
    )
    # Пустые категории — валидный OOXML: c:cat просто опускается
    # (серии рисуются по индексам 1..n), выдумывать метки нельзя.
    sers = "".join(
        _ser_xml(j, _col_letter(2 + j), s.name, cat_ref, len(s.values), s.values)
        for j, s in enumerate(chart.series)
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        f'<c:chartSpace xmlns:c="{C}" xmlns:a="{A}" xmlns:r="{R_NS}">'
        f'<c:lang val="ru-RU"/><c:chart><c:autoTitleDeleted val="1"/>'
        f"<c:plotArea><c:layout/>"
        f'<c:barChart><c:barDir val="col"/><c:grouping val="clustered"/>'
        f'<c:varyColors val="0"/>{sers}<c:gapWidth val="150"/>'
        f'<c:axId val="100001"/><c:axId val="100002"/></c:barChart>'
        f'<c:catAx><c:axId val="100001"/>'
        f'<c:scaling><c:orientation val="minMax"/></c:scaling>'
        f'<c:delete val="0"/><c:axPos val="b"/>'
        f'<c:tickLblPos val="nextTo"/><c:crossAx val="100002"/>'
        f'<c:crosses val="autoZero"/><c:crossBetween val="between"/></c:catAx>'
        f'<c:valAx><c:axId val="100002"/>'
        f'<c:scaling><c:orientation val="minMax"/></c:scaling>'
        f'<c:delete val="0"/><c:axPos val="l"/><c:majorGridlines/>'
        f'<c:numFmt formatCode="General" sourceLinked="1"/>'
        f'<c:tickLblPos val="nextTo"/><c:crossAx val="100001"/>'
        f'<c:crosses val="autoZero"/><c:crossBetween val="between"/></c:valAx>'
        f"</c:plotArea>"
        f'<c:legend><c:legendPos val="b"/><c:overlay val="0"/></c:legend>'
        f'<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/>'
        f"</c:chart>"
        + (
            f'<c:externalData r:id="{wb_rid}"><c:autoUpdate val="0"/>'
            f"</c:externalData>"
            if wb_rid
            else ""
        )
        + "</c:chartSpace>"
    )
    return xml.encode("utf-8")


def _build_chart_workbook(chart: Chart) -> bytes:
    """Embedded xlsx — тот же layout что _sync_workbook пишет: категории
    в A2:, имена серий в строке 1 из колонки B, значения ниже."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for i, cat in enumerate(chart.categories, start=2):
        ws.cell(row=i, column=1, value=cat)
    for j, series in enumerate(chart.series):
        col = 2 + j
        if series.name:
            ws.cell(row=1, column=col, value=series.name)
        for i, value in enumerate(series.values, start=2):
            ws.cell(row=i, column=col, value=value)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _build_chart_frame(
    shape_id: int, rid: str, x: int, y: int, cx: int, cy: int
) -> etree._Element:
    """Standalone `p:graphicFrame` с `c:chart` — тот же каркас что
    PowerPoint/python-pptx add_chart эмитит."""
    xml = (
        f'<p:graphicFrame xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R_NS}">'
        f'<p:nvGraphicFramePr><p:cNvPr id="{shape_id}" name="Chart {shape_id}"/>'
        f'<p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr>'
        f"<p:nvPr/></p:nvGraphicFramePr>"
        f'<p:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></p:xfrm>'
        f'<a:graphic><a:graphicData uri="{C}">'
        f'<c:chart xmlns:c="{C}" r:id="{rid}"/>'
        f"</a:graphicData></a:graphic></p:graphicFrame>"
    )
    return etree.fromstring(xml)


def _add_slide_chart(
    pkg_parts: dict[str, bytes],
    slide_part: str,
    chart: Chart,
    slide_rels,
    used_hosts: set[tuple[int, int, int, int]],
    report: ChartFillReport,
) -> bool:
    """Create a native `c:chart` from scratch — the add_table analogue.

    New `ppt/charts/chartN.xml` (clustered barChart, theme accent
    colors), embedded `ppt/embeddings/Microsoft_Excel_WorksheetN.xlsx`,
    chart-part rels → workbook, slide rel → chart part, content-type
    Overrides, `p:graphicFrame` in the largest emptied text body
    (min ~2.2"×1.6" — меньше делает chart+legend нечитаемым).
    Bounded validation: серия без единого значения пропускается
    (series_dropped), ноль содержательных серий / нет host / нет
    rels-парта → False (caller counts honest drop); пустые категории —
    валидный OOXML, `c:cat` опускается (индексы 1..n), метки не
    выдумываются; длина категорий/значений не обязана совпадать —
    кэши эмитят фактические точки (ptCount=len), Excel покажет
    пустые ячейки, данные не дополняются.
    Workbook failure keeps the cache-only chart (externalData просто
    не эмитится — dangling rel не создаётся)."""
    series = [s for s in chart.series if s.values]
    report.series_dropped += len(chart.series) - len(series)
    if not series:
        return False
    chart = chart.model_copy(update={"series": series})
    rels_part = rels_name_for(slide_part)
    if rels_part not in pkg_parts:
        return False
    root = etree.fromstring(pkg_parts[slide_part])
    sp_tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    if sp_tree is None:
        return False
    host = next(
        (
            h
            for h in _empty_body_hosts(root)
            if h[1:5] not in used_hosts
            and h[3] >= _MIN_CHART_W_EMU
            and h[4] >= _MIN_CHART_H_EMU
        ),
        None,
    )
    if host is None:
        return False
    _, x, y, cx, cy, _sp = host

    wb_bytes: bytes | None
    try:
        wb_bytes = _build_chart_workbook(chart)
    except Exception:
        wb_bytes = None
        report.workbook_errors += 1

    chart_part = _next_indexed_part(
        pkg_parts, _CHART_PART_RE, "ppt/charts/chart", ".xml"
    )
    rid = _next_rid(slide_rels)
    if wb_bytes is not None:
        wb_part = _next_indexed_part(
            pkg_parts, _XLSX_PART_RE,
            "ppt/embeddings/Microsoft_Excel_Worksheet", ".xlsx",
        )
        pkg_parts[wb_part] = wb_bytes
        pkg_parts[rels_name_for(chart_part)] = serialize_rels(
            [
                Relationship(
                    id="rId1",
                    type=_PACKAGE_REL,
                    target=_relativize(chart_part, wb_part),
                )
            ]
        )
        _add_override(pkg_parts, wb_part, _XLSX_CT)
        chart_xml = _build_chart_xml(chart, "rId1")
    else:
        # Workbook недоступен — чарт без externalData: кэши всё равно
        # рендерят, но «Edit Data» честно отсутствует (workbook_errors).
        chart_xml = _build_chart_xml(chart, None)
    pkg_parts[chart_part] = chart_xml
    _add_override(pkg_parts, chart_part, _CHART_CT)

    slide_rels.append(
        Relationship(
            id=rid, type=_CHART_REL, target=_relativize(slide_part, chart_part)
        )
    )
    pkg_parts[rels_part] = serialize_rels(slide_rels)
    used_hosts.add(host[1:5])
    sp_tree.append(_build_chart_frame(_next_shape_id(root), rid, x, y, cx, cy))
    pkg_parts[slide_part] = etree.tostring(
        root, xml_declaration=True, standalone=True
    )
    report.charts_filled += 1
    report.charts_created += 1
    report.created_types.append("barChart")
    report.points_written += len(chart.categories) + sum(
        len(s.values) for s in chart.series
    )
    report.chart_ids.append(chart.id)
    return True


def _esc(text) -> str:
    """XML text escape для значений, попадающих в chart XML."""
    from xml.sax.saxutils import escape

    return escape(str(text))
