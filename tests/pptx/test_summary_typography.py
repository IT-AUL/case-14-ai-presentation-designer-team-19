"""A large donor card needs readable short copy without touching chrome."""
from pathlib import Path

import pytest
from deckdna.contracts.deck_plan import ContentUnit, DeckPlan, Kind, Purpose
from deckdna.pptx.composing.minimal import generate_deck
from pptx import Presentation
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt


def _template(tmp_path):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.33), Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = 'Donor heading'
    for text, x, w, size in [('Donor short fact', .5, 4.2, 36),
                               ('Donor second fact', 5.2, 6.5, 10)]:
        box = slide.shapes.add_textbox(Inches(x), Inches(2), Inches(w), Inches(4))
        box.text = text
        box.text_frame.paragraphs[0].runs[0].font.size = Pt(size)
    footer = slide.shapes.add_textbox(Inches(1), Inches(6.7), Inches(5), Inches(.4))
    footer.text = 'Company footer'
    footer.text_frame.paragraphs[0].runs[0].font.size = Pt(8)
    ph = OxmlElement('p:ph')
    ph.set('type', 'ftr')
    footer._element.nvSpPr.nvPr.append(ph)
    path = tmp_path / 'unseen.pptx'
    prs.save(path)
    return path


@pytest.mark.parametrize('purpose', [Purpose.overview, Purpose.data])
def test_large_overview_fact_grows_but_footer_and_other_purposes_keep_scale(tmp_path, purpose):
    plan = DeckPlan.model_validate_json(
        (Path(__file__).parents[1] / 'fixtures/content/poc_deck_plan.json').read_text())
    facts = ['Выручка выросла за период', 'Категория: 61% выручки']
    units = [ContentUnit(role='title', kind=Kind.title, text='Выводы'),
             *[ContentUnit(role='bullet', kind=Kind.bullet, text=t) for t in facts]]
    slide = plan.slides[0].model_copy(update={
        'purpose': purpose, 'title_intent': 'Выводы', 'content_units': units})
    out = tmp_path / 'out.pptx'
    generate_deck(_template(tmp_path), plan.model_copy(update={'slides': [slide]}), out)
    result = Presentation(out).slides[0]
    short = next(s for s in result.shapes if s.has_text_frame and s.text == facts[1])
    size = min(r.font.size.pt for p in short.text_frame.paragraphs for r in p.runs)
    assert size >= 28 if purpose == Purpose.overview else size <= 20
    footer = next(s for s in result.shapes if s.has_text_frame and s.text == 'Company footer')
    assert footer.text_frame.paragraphs[0].runs[0].font.size.pt == 8
    assert result.shapes.title.text == 'Выводы'
