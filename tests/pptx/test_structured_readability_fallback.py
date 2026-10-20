"""Faithful structured slides still need readable captions and chart frames."""

from io import BytesIO
from pathlib import Path

from deckdna.audit.basic import audit_deck
from deckdna.contracts.content_pack import Chart, ChartSeries, Diagram, SourceRef
from deckdna.contracts.deck_plan import ContentUnit, DeckPlan, Kind, Purpose
from deckdna.contracts.variant_spec import Strategy
from deckdna.pptx.composing.minimal import generate_deck
from PIL import Image
from pptx import Presentation
from pptx.util import Inches

ROOT = Path(__file__).parents[2]


def _plan(units):
    p = DeckPlan.model_validate_json(
        (ROOT / "tests/fixtures/content/poc_deck_plan.json").read_text()
    )
    slide = p.slides[0].model_copy(
        update={"purpose": Purpose.solution, "title_intent": "Процесс", "content_units": units}
    )
    return p.model_copy(update={"slides": [slide]})


def test_diagram_caption_overflow_uses_native_readable_layout(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    shape = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(0.7), Inches(0.5))
    shape.text = "A long donor caption"
    source = tmp_path / "template.pptx"
    prs.save(source)
    caption = "Каждый шаг процесса имеет ответственную команду"
    plan = _plan(
        [
            ContentUnit(kind=Kind.bullet, role="body", text=caption),
            ContentUnit(kind=Kind.diagram, role="body", diagram_ref="process"),
        ]
    )
    out = tmp_path / "out.pptx"
    r = generate_deck(
        source,
        plan,
        out,
        strategy=Strategy.faithful,
        diagrams=[
            Diagram(
                id="process",
                steps=["Сбор требований", "Анализ", "План", "Релиз", "Поддержка"],
                source_ref=SourceRef(artifact_id="input.md"),
            )
        ],
    )
    assert not r["slides"][0]["dropped_units"]
    assert r["slides"][0]["diagrams"]["diagrams_created"] == 1
    assert not any(i.rule_code == "text.overflow" for i in audit_deck(out))
    result = Presentation(out)
    shape = next(s for s in result.slides[0].shapes if s.has_text_frame and caption in s.text)
    assert min(run.font.size.pt for p in shape.text_frame.paragraphs for run in p.runs) >= 14


def test_untyped_chart_creation_obeys_the_same_readability_gate(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Source title"
    image = BytesIO()
    Image.new("RGB", (90, 30), "red").save(image, format="PNG")
    image.seek(0)
    slide.shapes.add_picture(image, Inches(0.5), Inches(1), Inches(9), Inches(3))
    slide.shapes.add_textbox(Inches(0.5), Inches(4), Inches(9), Inches(2.5)).text = "Chart content"
    source = tmp_path / "template.pptx"
    prs.save(source)
    plan = _plan([ContentUnit(kind=Kind.chart, role="body", chart_ref="chart")])
    out = tmp_path / "out.pptx"
    r = generate_deck(
        source,
        plan,
        out,
        strategy=Strategy.faithful,
        charts=[
            Chart(
                id="chart",
                categories=["A", "B"],
                series=[ChartSeries(name="Value", values=[1, 2])],
                source_ref=SourceRef(artifact_id="input.md"),
            )
        ],
    )
    assert not r["slides"][0]["dropped_units"]
    assert r["slides"][0]["charts"]["charts_filled"] == 1
    assert not any(i.rule_code == "chart.readability" for i in audit_deck(out))


def test_empty_diagram_host_cannot_overlap_a_filled_caption(tmp_path):
    from deckdna.pptx.composing.diagram_fill import fill_slide_diagrams
    from deckdna.pptx.opc.package import OpcPackage

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    caption = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(8), Inches(3))
    caption.text = "Source caption must survive intact"
    slide.shapes.add_textbox(0, Inches(4.45), Inches(10), Inches(1.4))
    source = tmp_path / "source.pptx"
    prs.save(source)
    pkg = OpcPackage.open(source)
    part = "ppt/slides/slide1.xml"
    xml, report = fill_slide_diagrams(
        pkg.parts[part],
        [
            Diagram(
                id="flow",
                steps=["One", "Two", "Three"],
                source_ref=SourceRef(artifact_id="input.md"),
            )
        ],
        used_hosts=set(),
        slide_size=(int(prs.slide_width), int(prs.slide_height)),
    )
    assert report.diagrams_created == 1
    pkg.parts[part] = xml
    out = tmp_path / "out.pptx"
    pkg.save(out)
    result = Presentation(out)
    group = next(s for s in result.slides[0].shapes if hasattr(s, "shapes"))
    assert group.top >= caption.top + caption.height
    assert not any(i.rule_code == "layout.unintended_overlap" for i in audit_deck(out))
