"""Unreadable donor cards need a complete readable native fallback."""

from pathlib import Path

from deckdna.audit.basic import audit_deck
from deckdna.contracts.deck_plan import ContentUnit, DeckPlan, Kind, Purpose
from deckdna.pptx.composing.minimal import generate_deck
from pptx import Presentation

ROOT = Path(__file__).parents[2]


def test_overflowing_existing_cards_do_not_block_readable_fallback(tmp_path):
    plan = DeckPlan.model_validate_json(
        (ROOT / "tests/fixtures/content/poc_deck_plan.json").read_text()
    )
    texts = [
        "Детерминированный аудит на каждую колоду",
        "Typed repair только с подтверждением пользователя",
        "Quality Passport фиксирует метрики",
    ]
    slide = plan.slides[0].model_copy(
        update={
            "purpose": Purpose.overview,
            "title_intent": "Надёжность",
            "content_units": [
                ContentUnit(kind=Kind.title, role="title", text="Надёжность"),
                *[ContentUnit(kind=Kind.bullet, role="body", text=t) for t in texts],
            ],
        }
    )
    out = tmp_path / "out.pptx"
    report = generate_deck(
        ROOT / "tests/fixtures/pptx/lct2026_submission.pptx",
        plan.model_copy(update={"slides": [slide]}),
        out,
        exemplar_overrides={slide.id: "ppt/slides/slide25.xml"},
    )
    result = Presentation(out)
    assert not any(
        i.rule_code in ("text.overflow", "accessibility.contrast") for i in audit_deck(out)
    )
    for text in texts:
        shape = next(s for s in result.slides[0].shapes if s.has_text_frame and s.text == text)
        assert min(r.font.size.pt for p in shape.text_frame.paragraphs for r in p.runs) >= 14
    assert not report["slides"][0]["dropped_units"]
