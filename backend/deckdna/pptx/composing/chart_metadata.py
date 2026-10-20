"""Source-grounded legacy chart metadata with runtime colors and native text."""

from __future__ import annotations

from lxml import etree

from deckdna.contracts.content_pack import Chart

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_AFTER_TITLE = {
    "numFmt",
    "majorTickMark",
    "minorTickMark",
    "tickLblPos",
    "spPr",
    "txPr",
    "crossAx",
    "crosses",
    "crossesAt",
    "crossBetween",
    "auto",
    "lblAlgn",
    "lblOffset",
    "tickLblSkip",
    "tickMarkSkip",
    "noMultiLvlLbl",
}


def _title(label: str, ink: str | None, size: int, vertical: bool = False):
    title = etree.Element(f"{{{C}}}title")
    rich = etree.SubElement(etree.SubElement(title, f"{{{C}}}tx"), f"{{{C}}}rich")
    attrs = {"rot": "-5400000", "vert": "horz"} if vertical else {}
    etree.SubElement(rich, f"{{{A}}}bodyPr", **attrs)
    etree.SubElement(rich, f"{{{A}}}lstStyle")
    run = etree.SubElement(etree.SubElement(rich, f"{{{A}}}p"), f"{{{A}}}r")
    props = etree.SubElement(run, f"{{{A}}}rPr", sz=str(size))
    fill = etree.SubElement(props, f"{{{A}}}solidFill")
    etree.SubElement(fill, f"{{{A}}}srgbClr" if ink else f"{{{A}}}schemeClr", val=ink or "tx1")
    etree.SubElement(props, f"{{{A}}}latin", typeface="+mn-lt")
    etree.SubElement(run, f"{{{A}}}t").text = label
    etree.SubElement(title, f"{{{C}}}overlay", val="0")
    return title


def complete_chart_metadata(xml: bytes, chart: Chart, ink: str | None, size: int = 1200) -> bytes:
    root = etree.fromstring(xml)
    single = chart.series[0].name if len(chart.series) == 1 else None
    value = single or "Значение"
    if chart.unit:
        value += ", " + chart.unit
    labels = {
        "catAx": chart.category_axis_title or "Категория",
        "dateAx": chart.category_axis_title or "Период",
        "valAx": chart.value_axis_title or value,
    }
    changed = False
    owner = root.find(f"{{{C}}}chart")
    existing_title = owner.find(f"{{{C}}}title") if owner is not None else None
    if existing_title is not None and not chart.title:
        owner.remove(existing_title)
        changed = True
    title_text = (chart.title or "").strip()
    if (
        owner is not None
        and title_text
        and (existing_title is None or "".join(existing_title.itertext()) != title_text)
    ):
        if existing_title is not None:
            owner.remove(existing_title)
        owner.insert(0, _title(title_text, ink, size))
        changed = True
    deleted = owner.find(f"{{{C}}}autoTitleDeleted") if owner is not None else None
    if deleted is not None and deleted.get("val") != ("0" if title_text else "1"):
        deleted.set("val", "0" if title_text else "1")
        changed = True
    for kind, label in labels.items():
        for axis in root.iter(f"{{{C}}}{kind}"):
            existing = axis.find(f"{{{C}}}title")
            if existing is not None:
                if "".join(existing.itertext()) == label:
                    continue
                axis.remove(existing)
            pos = axis.find(f"{{{C}}}axPos")
            title = _title(label, ink, size, pos is not None and pos.get("val") in ("l", "r"))
            index = next(
                (i for i, node in enumerate(axis) if etree.QName(node).localname in _AFTER_TITLE),
                len(axis),
            )
            axis.insert(index, title)
            changed = True
    owner = root.find(f"{{{C}}}chart")
    plot = root.find(f".//{{{C}}}plotArea")
    labeled = any(
        node.get("val") == "1"
        for labels in root.iter(f"{{{C}}}dLbls")
        for node in labels.iter()
        if etree.QName(node).localname.startswith("show")
    )
    if (
        owner is not None
        and plot is not None
        and plot.find(f".//{{{C}}}ser") is not None
        and owner.find(f"{{{C}}}legend") is None
        and not labeled
    ):
        legend = etree.Element(f"{{{C}}}legend")
        etree.SubElement(legend, f"{{{C}}}legendPos", val="b")
        etree.SubElement(legend, f"{{{C}}}layout")
        etree.SubElement(legend, f"{{{C}}}overlay", val="0")
        tx = etree.SubElement(legend, f"{{{C}}}txPr")
        etree.SubElement(tx, f"{{{A}}}bodyPr")
        etree.SubElement(tx, f"{{{A}}}lstStyle")
        props = etree.SubElement(
            etree.SubElement(etree.SubElement(tx, f"{{{A}}}p"), f"{{{A}}}pPr"),
            f"{{{A}}}defRPr",
            sz=str(size),
        )
        fill = etree.SubElement(props, f"{{{A}}}solidFill")
        etree.SubElement(fill, f"{{{A}}}srgbClr" if ink else f"{{{A}}}schemeClr", val=ink or "tx1")
        etree.SubElement(props, f"{{{A}}}latin", typeface="+mn-lt")
        tail = {"plotVisOnly", "dispBlanksAs", "showDLblsOverMax", "extLst"}
        index = next(
            (i for i, node in enumerate(owner) if etree.QName(node).localname in tail), len(owner)
        )
        owner.insert(index, legend)
        changed = True
    if owner is not None and any(v is None for series in chart.series for v in series.values):
        gaps = owner.find(f"{{{C}}}dispBlanksAs")
        if gaps is None:
            gaps = etree.Element(f"{{{C}}}dispBlanksAs", val="gap")
            index = next(
                (
                    i
                    for i, node in enumerate(owner)
                    if etree.QName(node).localname in {"showDLblsOverMax", "extLst"}
                ),
                len(owner),
            )
            owner.insert(index, gaps)
            changed = True
        elif gaps.get("val") != "gap":
            gaps.set("val", "gap")
            changed = True
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8") if changed else xml
