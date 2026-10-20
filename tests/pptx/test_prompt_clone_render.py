"""Clearing layout prompts must preserve LibreOffice theme/master resolution."""
from pathlib import Path

import pymupdf
from deckdna.pptx.cloning.single_slide import build_multi_slide_deck
from deckdna.pptx.composing.placeholders import clear_layout_prompts
from deckdna.pptx.exporting.render import render_pdf
from deckdna.pptx.opc.package import OpcPackage


def test_prompt_layout_clone_keeps_white_panel_in_real_render(tmp_path):
    # Fixture-only reproduction. Runtime registration has no template names,
    # layout indices or colors: this source has an explicit white theme panel.
    source = OpcPackage.open(Path(__file__).parents[1]/'fixtures/pptx/lct2026_submission.pptx')
    part = 'ppt/slides/slide11.xml'
    original = dict(source.parts)
    out = build_multi_slide_deck(source, [(part,source.parts[part])])
    assert clear_layout_prompts(out,set()) > 0
    assert source.parts == original
    deck = tmp_path/'clone.pptx'
    out.save(deck)
    pdf = pymupdf.open(render_pdf(deck,tmp_path/'clone.pdf'))
    pix = pdf[0].get_pixmap()
    # The same point rendered black when the cloned layout was absent from
    # sldLayoutIdLst, even though its slide/master relationships were readable.
    assert min(pix.pixel(int(pix.width*.25),int(pix.height*.7))) >= 240
    assert 'Техническая суть решения' in pdf[0].get_text()
