"""Table region styles from the runtime package (no built-in template assumptions)."""

from __future__ import annotations

from lxml import etree

from deckdna.pptx.exporting.html_style import A, child


def table_style(table):
    props = table._tbl.tblPr
    style_id = child(props, "a:tableStyleId")
    for rel in table._graphic_frame.part.package.presentation_part.rels.values():
        if rel.reltype.endswith("/tableStyles") and not rel.is_external:
            root = etree.fromstring(rel.target_part.blob)
            identity = style_id.text if style_id is not None else root.get("def")
            return next((s for s in root if s.get("styleId") == identity), None)
    return None


def cell_regions(style, table, row, col):
    names = ["wholeTbl"]
    if table.horz_banding:
        names.append("band1H" if (row - int(table.first_row)) % 2 == 0 else "band2H")
    if table.vert_banding:
        names.append("band1V" if (col - int(table.first_col)) % 2 == 0 else "band2V")
    if table.first_col and col == 0:
        names.append("firstCol")
    if table.last_col and col == len(table.columns) - 1:
        names.append("lastCol")
    if table.first_row and row == 0:
        names.append("firstRow")
    if table.last_row and row == len(table.rows) - 1:
        names.append("lastRow")
    return [child(style, "a:" + name) for name in reversed(names)]


def text_defaults(regions):
    defaults = []
    for region in regions:
        source = child(region, "a:tcTxStyle")
        if source is None:
            continue
        props = etree.Element(f"{{{A}}}defRPr")
        for key in ("b", "i"):
            if source.get(key):
                props.set(key, "1" if source.get(key) in ("on", "1", "true") else "0")
        font = child(source, "a:font/a:latin")
        if font is not None:
            props.append(etree.fromstring(etree.tostring(font)))
        for color in source:
            if etree.QName(color).localname.endswith("Clr"):
                fill = etree.SubElement(props, f"{{{A}}}solidFill")
                fill.append(etree.fromstring(etree.tostring(color)))
        defaults.append(props)
    return defaults
