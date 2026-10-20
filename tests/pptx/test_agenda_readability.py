"""Unseen donor with a thin column must not wrap agenda words vertically."""
from pathlib import Path

from deckdna.contracts.deck_plan import ContentUnit, DeckPlan, Kind, Purpose
from deckdna.pptx.composing.minimal import generate_deck
from pptx import Presentation
from pptx.util import Inches, Pt


def test_narrow_agenda_uses_readable_native_boxes(tmp_path):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(16), Inches(9)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    for text, x, y, w, h in [("Title", 1, .5, 12, 1), ("Body text", 8, 2, 2, 5)]:
        frame = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame
        frame.text = text
        frame.paragraphs[0].runs[0].font.size = Pt(16)
    template = tmp_path / "unseen.pptx"
    prs.save(template)
    plan = DeckPlan.model_validate_json(
        (Path(__file__).parents[1] / "fixtures/content/poc_deck_plan.json").read_text())
    entries = ["Проблема процесса", "Решение команды", "Проверка результатов"]
    agenda = plan.slides[0].model_copy(update={
        "purpose": Purpose.agenda, "title_intent": "Обсуждение",
        "content_units": [ContentUnit(kind=Kind.title, role="title", text="Обсуждение"),
                          *[ContentUnit(kind=Kind.bullet, role="agenda", text=t)
                            for t in entries]],
    })
    out = tmp_path / "out.pptx"
    generate_deck(template, plan.model_copy(update={"slides": [agenda]}), out)
    result = Presentation(out)
    for entry in entries:
        shape = next(s for s in result.slides[0].shapes
                     if s.has_text_frame and entry in s.text)
        assert shape.width > prs.slide_width * .22
        assert min(r.font.size.pt for p in shape.text_frame.paragraphs for r in p.runs) >= 20
