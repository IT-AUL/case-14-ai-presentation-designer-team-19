"""Native donor cards gain readable width without replacing the authored prose."""
import io
import zipfile
from pathlib import Path

from deckdna.contracts.deck_plan import ContentUnit, DeckPlan, Kind, Purpose
from deckdna.pptx.composing.card_reflow import clone_cards
from deckdna.pptx.composing.minimal import generate_deck
from lxml import etree
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from test_card_reflow import SH, SW, _boxes, _grid_deck

A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'


def _one_row():
    xml, prs = _grid_deck({0, 1, 2}, numbers=False)
    root = etree.fromstring(xml)
    tree = root.find('.//{http://schemas.openxmlformats.org/presentationml/2006/main}spTree')
    for shape in list(tree):
        offset = shape.find('.//' + A + 'off')
        if offset is not None and int(offset.get('y')) >= .55 * SH:
            tree.remove(shape)
    return etree.tostring(root), prs


def test_five_cloned_prose_cards_gain_two_rows_and_keep_donor_geometry():
    xml, _ = _one_row()
    baseline, _ = clone_cards(xml, 5, SW, SH)
    new, added = clone_cards(xml, 5, SW, SH, max_columns=3)
    before = [b for b in _boxes(baseline) if b[0] == 'roundRect']
    after = [b for b in _boxes(new) if b[0] == 'roundRect']
    assert len({b[1] for b in before}) == 5
    assert added == 2
    assert len(after) == 5 and len({b[1] for b in after}) == 3
    assert len({b[2] for b in after}) == 2
    assert all(b[3] > SW * .25 for b in after)


def test_card_rows_do_not_expand_over_a_foreign_footer():
    xml, prs = _one_row()
    footer = prs.slides[0].shapes.add_textbox(0, int(SH * .75), SW, int(SH * .1))
    footer.text_frame.text = 'Неподвижный колонтитул'
    root = etree.fromstring(xml)
    root.find('.//{http://schemas.openxmlformats.org/presentationml/2006/main}spTree').append(
        etree.fromstring(etree.tostring(footer._element)))
    new, _ = clone_cards(etree.tostring(root), 5, SW, SH, max_columns=3)
    boxes = _boxes(new)
    assert all(b[2] + b[4] < SH * .75 for b in boxes if b[0] == 'roundRect')
    unchanged = next(b for b in boxes if b[5] == 'Неподвижный колонтитул')
    assert unchanged[2] == int(SH * .75)


def test_cleared_invisible_slot_does_not_reserve_space_below_the_body():
    from deckdna.pptx.composing.card_reflow import _Box, _box, _Card, _expand_bodies

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    container = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, 0, 0, 3000000, 2000000)
    body = slide.shapes.add_textbox(100000, 500000, 2800000, 500000)
    body.text_frame.text = 'Полный текст'
    cleared = slide.shapes.add_textbox(100000, 1500000, 2800000, 100000)
    marker = slide.shapes.add_shape(MSO_SHAPE.OVAL, 100000, 100000, 100000, 100000)
    members = [(s._element, _box(s._element)) for s in (body, cleared, marker)]
    _expand_bodies([_Card(container._element, _Box(0, 0, 3000000, 2000000), members)])
    assert _box(body._element).b == 1880000


def test_compiler_keeps_full_prose_and_uses_readable_font(tmp_path):
    xml, prs = _one_row()
    memory = io.BytesIO()
    prs.save(memory)
    template = tmp_path / 'unseen.pptx'
    with zipfile.ZipFile(memory) as source, zipfile.ZipFile(template, 'w') as target:
        for name in source.namelist():
            target.writestr(name, xml if name == 'ppt/slides/slide1.xml' else source.read(name))
    plan = DeckPlan.model_validate_json(
        (Path(__file__).parents[1] / 'fixtures/content/poc_deck_plan.json').read_text())
    texts = [f'Полное содержание пункта {i} сохраняется при изменении сетки без потери слов.'
             for i in range(5)]
    slide = plan.slides[0].model_copy(update={
        'purpose': Purpose.benefits, 'title_intent': 'Результаты',
        'content_units': [ContentUnit(role='body', kind=Kind.bullet, text=t) for t in texts],
    })
    out = tmp_path / 'out.pptx'
    generate_deck(template, plan.model_copy(update={'slides': [slide]}), out)
    shapes = Presentation(out).slides[0].shapes
    for text in texts:
        shape = next(s for s in shapes if s.has_text_frame and s.text == text)
        assert shape.width > SW * .20
        assert min(r.font.size.pt for p in shape.text_frame.paragraphs for r in p.runs) >= 16
