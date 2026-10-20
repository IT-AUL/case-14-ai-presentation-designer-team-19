"""Compiler drafts preserve filled text and OOXML dependencies."""

from pathlib import Path

import pytest
from deckdna.contracts.deck_plan import Brief
from deckdna.ingestion.content_parsers import parse_file
from deckdna.planning.story_director import plan_deck
from deckdna.pptx.composing.minimal import generate_deck
from deckdna.pptx.opc.package import OpcPackage
from pptx import Presentation

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("template", ["vk_tech_template.pptx", "synthetic_unseen_43.pptx"])
def test_callback_receives_real_independent_single_slide_before_final_save(tmp_path, template):
    content = ROOT / "tests/fixtures/content/poc_article.md"
    plan = plan_deck(parse_file(content), Brief(
        purpose="Обзор", audience="команда", language="ru", target_slide_count=5,
    ))
    output = tmp_path / "deck.pptx"
    drafts = []

    def compiled(index: int, slide_id: str, package: OpcPackage):
        assert not output.exists()
        assert index == len(drafts)
        path = tmp_path / f"draft-{index}.pptx"
        package.save(path)
        presentation = Presentation(path)
        assert len(presentation.slides) == 1
        texts = "\n".join(s.text for s in presentation.slides[0].shapes if s.has_text_frame)
        assert texts.strip()
        drafts.append((slide_id, package))

    report = generate_deck(
        ROOT / "tests/fixtures/pptx" / template, plan, output,
        on_slide_compiled=compiled,
    )
    assert [slide_id for slide_id, _ in drafts] == [s["id"] for s in report["slides"]]
    final = OpcPackage.open(output)
    for index, (_, package) in enumerate(drafts, 1):
        assert package.parts["ppt/slides/slide1.xml"] == final.parts[f"ppt/slides/slide{index}.xml"]
    # Owning independent dictionaries prevents later final mutations leaking in.
    before = final.parts["ppt/slides/slide1.xml"]
    drafts[0][1].parts["ppt/slides/slide1.xml"] = b"changed"
    assert final.parts["ppt/slides/slide1.xml"] == before
