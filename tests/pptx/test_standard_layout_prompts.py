"""Ordinary layout prompts are editor content, even without hasCustomPrompt."""

from io import BytesIO
from zipfile import ZipFile

from deckdna.pptx.cloning.single_slide import build_multi_slide_deck
from deckdna.pptx.composing.placeholders import clear_layout_prompts
from deckdna.pptx.opc.package import OpcPackage
from lxml import etree
from pptx import Presentation

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def test_standard_prompts_are_cleared_on_private_layouts_without_touching_brand_text():
    prs = Presentation()
    prs.slides.add_slide(prs.slide_layouts[1])
    stream = BytesIO()
    prs.save(stream)
    with ZipFile(stream) as z:
        pkg = OpcPackage({n: z.read(n) for n in z.namelist()})
    source = "ppt/slides/slide1.xml"
    layout = next(t for r, t in pkg.internal_dependencies(source) if r.type_name == "slideLayout")
    root = etree.fromstring(pkg.parts[layout])
    for shape in root.iter(f"{{{P}}}sp"):
        ph = shape.find(f"{{{P}}}nvSpPr/{{{P}}}nvPr/{{{P}}}ph")
        if ph is not None and ph.get("type") in {None, "obj", "body"}:
            ph.attrib.pop("hasCustomPrompt", None)
            for text in shape.iter(f"{{{A}}}t"):
                text.text = "EDITOR BODY PROMPT"
    # This is a fixed brand label, not a placeholder, and must survive.
    tree = root.find(f"{{{P}}}cSld/{{{P}}}spTree")
    brand = etree.SubElement(tree, f"{{{P}}}sp")
    nv = etree.SubElement(brand, f"{{{P}}}nvSpPr")
    etree.SubElement(nv, f"{{{P}}}cNvPr", id="1000", name="Brand")
    etree.SubElement(nv, f"{{{P}}}cNvSpPr")
    etree.SubElement(nv, f"{{{P}}}nvPr")
    etree.SubElement(brand, f"{{{P}}}spPr")
    body = etree.SubElement(brand, f"{{{P}}}txBody")
    etree.SubElement(body, f"{{{A}}}bodyPr")
    etree.SubElement(body, f"{{{A}}}lstStyle")
    text = etree.SubElement(body, f"{{{A}}}p")
    etree.SubElement(etree.SubElement(text, f"{{{A}}}r"), f"{{{A}}}t").text = "Company Brand"
    pkg.parts[layout] = etree.tostring(root)
    assert b"EDITOR BODY PROMPT" in pkg.parts[layout]
    out = build_multi_slide_deck(pkg, [(source, pkg.parts[source])] * 2)
    protected = out.parts[source]
    assert clear_layout_prompts(out, {0}) > 0
    assert out.parts[layout] == pkg.parts[layout]
    assert out.parts[source] == protected
    clone = next(
        t
        for r, t in out.internal_dependencies("ppt/slides/slide2.xml")
        if r.type_name == "slideLayout"
    )
    assert clone != layout
    assert b"EDITOR BODY PROMPT" not in out.parts[clone]
    assert b"Company Brand" in out.parts[clone]
    snapshot = dict(out.parts)
    assert clear_layout_prompts(out, {0}) == 0
    assert out.parts == snapshot
