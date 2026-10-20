"""Native local geometry edits; fail if a safe improvement is unavailable."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from deckdna.errors import DeckDNAError
from deckdna.pptx.composing.table_fill import _free_space_host
from deckdna.pptx.opc.package import OpcPackage

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"


def enlarge_chart(path: Path) -> None:
    pkg = OpcPackage.open(path)
    part = "ppt/slides/slide1.xml"
    root = etree.fromstring(pkg.parts[part])
    tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    charts = [e for e in tree if e.find(f".//{{{C}}}chart") is not None]
    if len(charts) != 1:
        raise DeckDNAError("invalid_input", "select a slide with one native chart", http_status=422)
    chart = charts[0]
    transform = chart.find(f"{{{P}}}xfrm")
    if transform is None:
        raise DeckDNAError("invalid_input", "chart has no editable geometry", http_status=422)
    off, ext = transform.find(f"{{{A}}}off"), transform.find(f"{{{A}}}ext")
    size = etree.fromstring(pkg.parts["ppt/presentation.xml"]).find(f"{{{P}}}sldSz")
    area = int(ext.get("cx")) * int(ext.get("cy"))
    free = _free_space_host(root, int(size.get("cx")), int(size.get("cy")),
                            ignore=frozenset({id(chart)}))
    if free is None or free[0] < area * 1.08:
        raise DeckDNAError("invalid_input", "no room to enlarge the chart without covering content",
                           http_status=422)
    _, x, y, width, height, _ = free
    off.set("x", str(x))
    off.set("y", str(y))
    ext.set("cx", str(width))
    ext.set("cy", str(height))
    pkg.parts[part] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    pkg.save(path)


def update_existing_notes(deck: bytes, position: int, text: str) -> bytes:
    """Update the edited slide's notes without reserializing other slide XML."""
    import io
    import zipfile

    from deckdna.pptx.composing.slide_swap import slide_parts
    from deckdna.pptx.opc.package import resolve_target

    with zipfile.ZipFile(io.BytesIO(deck)) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    package = OpcPackage(parts)
    slide = slide_parts(parts)[position - 1]
    notes = next((resolve_target(slide, rel.target) for rel in package.rels(slide)
                  if rel.type_name == "notesSlide"), None)
    if notes is None:
        return deck
    root = etree.fromstring(parts[notes])
    body = next((shape.find(f"{{{P}}}txBody") for shape in root.iter(f"{{{P}}}sp")
                 if shape.find(f"{{{P}}}nvSpPr/{{{P}}}nvPr/{{{P}}}ph[@type='body']")
                 is not None), None)
    if body is None:
        return deck
    for paragraph in list(body.findall(f"{{{A}}}p")):
        body.remove(paragraph)
    paragraph = etree.SubElement(body, f"{{{A}}}p")
    run = etree.SubElement(paragraph, f"{{{A}}}r")
    etree.SubElement(run, f"{{{A}}}t").text = text
    parts[notes] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return output.getvalue()
