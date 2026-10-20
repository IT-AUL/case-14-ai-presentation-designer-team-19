"""An authored heading without a placeholder still has a real title role."""
from deckdna.pptx.composing.title_fit import fit_title_above_content
from lxml import etree
from pptx import Presentation
from pptx.util import Inches, Pt

P = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'


def test_empty_title_frame_space_is_trimmed_before_a_caption_without_shrinking_text():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    title = slide.shapes.add_textbox(Inches(.4), Inches(.3), Inches(6.4), Inches(1))
    title.text_frame.text = 'Электроника: 61% выручки'
    title.text_frame.paragraphs[0].runs[0].font.size = Pt(24)
    caption = slide.shapes.add_textbox(Inches(.4), Inches(.9), Inches(3), Inches(.5))
    caption.text_frame.text = 'Почти вся выручка сосредоточена в трёх категориях.'
    xml = etree.tostring(slide._element)
    # The old placeholder-only route leaves an overlapping empty frame.
    assert fit_title_above_content(xml, int(prs.slide_width), int(prs.slide_height))[0] == xml
    new, changed = fit_title_above_content(xml, int(prs.slide_width), int(prs.slide_height),
                                          title_text=title.text)
    assert changed
    shape = etree.fromstring(new).find(f'{P}cSld/{P}spTree/{P}sp')
    off = shape.find(f'{P}spPr/{A}xfrm/{A}off')
    ext = shape.find(f'{P}spPr/{A}xfrm/{A}ext')
    assert int(off.get('y')) + int(ext.get('cy')) < caption.top
    assert next(shape.iter(A+'t')).text == title.text
    assert next(shape.iter(A+'rPr')).get('sz') == '2400'
