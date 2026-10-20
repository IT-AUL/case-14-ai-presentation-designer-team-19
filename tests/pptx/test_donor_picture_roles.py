"""Inset figures are donor content, even when their area resembles a background."""
import io
import json
from pathlib import Path

import pytest
from deckdna.contracts.variant_spec import Strategy
from deckdna.pptx.composing.minimal import generate_deck
from deckdna.pptx.composing.native_compose import slide_frame
from deckdna.pptx.opc.package import OpcPackage
from lxml import etree
from PIL import Image
from pptx import Presentation
from pptx.util import Inches, Pt

P = 'http://schemas.openxmlformats.org/presentationml/2006/main'


def _template(tmp_path):
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[6])
    buf = io.BytesIO()
    Image.new('RGB', (30, 20), '#AB7834').save(buf, format='PNG')
    # More than 85% area, but inset below the title: a figure, not canvas art.
    s.shapes.add_picture(io.BytesIO(buf.getvalue()), -prs.slide_width // 40,
                         prs.slide_height // 7, int(prs.slide_width * 1.05),
                         int(prs.slide_height * .84))
    s.shapes.add_picture(io.BytesIO(buf.getvalue()), 0, 0, Inches(.2), Inches(.2))
    for y, text in [(.2, 'Donor title'), (1, 'Donor body')]:
        sh = s.shapes.add_textbox(Inches(.5), Inches(y), Inches(8), Inches(.6))
        sh.text = text
        sh.text_frame.paragraphs[0].runs[0].font.size = Pt(24)
    path = tmp_path / 'template.pptx'
    prs.save(path)
    return path, prs.slide_width, prs.slide_height


@pytest.mark.parametrize('purpose,strategy', [('title', Strategy.balanced),
                                            ('custom', Strategy.visual)])
def test_inset_donor_figure_cannot_survive_cover_or_native_recomposition(
    tmp_path, purpose, strategy,
):
    template, sw, sh = _template(tmp_path)
    plan = json.loads(Path('tests/fixtures/content/poc_deck_plan.json').read_text())
    plan['slides'] = plan['slides'][:1]
    plan['slides'][0].update(purpose=purpose, title_intent='Авторский результат', content_units=[
        {'kind': 'title', 'text': 'Авторский результат', 'role': 'title'},
        {'kind': 'paragraph', 'text': 'Сохранённый авторский тезис', 'role': 'body'},
    ])
    out = tmp_path / 'out.pptx'
    generate_deck(template, plan, out, strategy=strategy, protected_slide_indices=[0])
    pkg = OpcPackage.open(out)
    # Protected attribution/source is never cleaned.
    original = OpcPackage.open(template)
    assert pkg.parts['ppt/slides/slide1.xml'] == original.parts['ppt/slides/slide1.xml']
    root = etree.fromstring(pkg.parts['ppt/slides/slide2.xml'])
    pics = list(root.iter(f'{{{P}}}pic'))
    assert len(pics) == 1  # small brand mark remains
    assert b'\xd0\xa1\xd0\xbe\xd1\x85' in pkg.parts['ppt/slides/slide2.xml']


def test_native_frame_does_not_promote_large_inset_picture_to_background(tmp_path):
    template, sw, sh = _template(tmp_path)
    pkg = OpcPackage.open(template)
    xml, _, _ = slide_frame(pkg.parts['ppt/slides/slide1.xml'], sw, sh)
    root = etree.fromstring(xml)
    assert len(list(root.iter(f'{{{P}}}pic'))) == 1
