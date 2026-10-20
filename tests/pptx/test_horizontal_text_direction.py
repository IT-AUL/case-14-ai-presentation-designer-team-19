"""OOXML explicitly horizontal text is normal content, not vertical decoration."""
from pathlib import Path

import pytest
from deckdna.contracts.deck_plan import ContentUnit, DeckPlan, Kind, Purpose
from deckdna.pptx.composing.minimal import generate_deck
from deckdna.pptx.composing.text_replace import _body_is_decorative, replace_text_runs
from lxml import etree
from pptx import Presentation
from pptx.util import Inches, Pt

A = 'http://schemas.openxmlformats.org/drawingml/2006/main'


def _donor(direction):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(7), Inches(3))
    box.text = 'Original donor content'
    box.text_frame.paragraphs[0].runs[0].font.size = Pt(10)
    if direction:
        box.text_frame._txBody.bodyPr.set('vert', direction)
    return prs,slide,box


@pytest.mark.parametrize('direction', [None, 'horz'])
def test_horizontal_body_accepts_authored_content(direction):
    prs,slide,box = _donor(direction)
    assert not _body_is_decorative(box.text_frame._txBody)
    xml,report = replace_text_runs(etree.tostring(slide._element), ['Actual authored content'],
                                   slide_size=(prs.slide_width,prs.slide_height))
    assert b'Actual authored content' in xml
    assert b'Original donor content' not in xml
    assert report.bodies_filled == 1 and report.texts_dropped == 0


@pytest.mark.parametrize('direction', ['vert','vert270','eaVert','wordArtVert'])
def test_vertical_artwork_remains_decorative(direction):
    _,_,box = _donor(direction)
    assert _body_is_decorative(box.text_frame._txBody)


def test_unseen_explicit_horizontal_template_keeps_and_grows_actual_content(tmp_path):
    prs,_,_ = _donor('horz')
    template = tmp_path/'unseen.pptx'
    prs.save(template)
    plan = DeckPlan.model_validate_json(
        (Path(__file__).parents[1]/'fixtures/content/poc_deck_plan.json').read_text())
    text = 'Результат подтверждён исходными данными'
    slide = plan.slides[0].model_copy(update={'purpose':Purpose.benefits,
        'title_intent':'Результат', 'content_units':[
            ContentUnit(kind=Kind.title,role='title',text='Результат'),
            ContentUnit(kind=Kind.bullet,role='body',text=text)]})
    out=tmp_path/'out.pptx'
    report=generate_deck(template,plan.model_copy(update={'slides':[slide]}),out)
    result=Presentation(out)
    body=next(s for s in result.slides[0].shapes if s.has_text_frame and s.text==text)
    assert min(r.font.size.pt for p in body.text_frame.paragraphs for r in p.runs)>=16
    assert not report['slides'][0]['dropped_units']


def test_generated_heading_backdrop_does_not_cover_authored_body(tmp_path):
    test_unseen_explicit_horizontal_template_keeps_and_grows_actual_content(tmp_path)
    shapes = Presentation(tmp_path/'out.pptx').slides[0].shapes
    body = next(s for s in shapes if s.has_text_frame and
                s.text=='Результат подтверждён исходными данными')
    heading = next(s for s in shapes if s.name=='DeckDNA heading')
    backdrop = next(s for s in shapes if s.name=='DeckDNA heading backdrop')
    assert heading.top+heading.height <= body.top
    assert backdrop.top+backdrop.height <= heading.top+heading.height
