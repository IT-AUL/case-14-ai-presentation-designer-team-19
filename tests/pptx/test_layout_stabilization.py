"""Прикладные регрессии плейсхолдеров и пустого оформления."""

from pathlib import Path

import pytest
from deckdna.pptx.composing.orphans import remove_orphans
from deckdna.pptx.composing.text_replace import count_content_slots, replace_text_runs
from deckdna.pptx.opc.package import OpcPackage
from lxml import etree

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
FIXTURES = Path("tests/fixtures/pptx")


def slide(*shapes):
    return (f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>'
            + "".join(shapes) + '</p:spTree></p:cSld></p:sld>').encode()


def shape(sid, text, ph=""):
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="S{sid}"/>'
            f'<p:cNvSpPr/><p:nvPr>{ph}</p:nvPr></p:nvSpPr><p:spPr>'
            '<a:xfrm><a:off x="914400" y="914400"/>'
            '<a:ext cx="2743200" cy="914400"/></a:xfrm></p:spPr>'
            f'<p:txBody><a:bodyPr/><a:p>{text}</a:p></p:txBody></p:sp>')


@pytest.mark.parametrize("ph,field", [
    ("sldNum", "slidenum"), ("dt", "datetime1"), ("ftr", ""), ("hdr", ""),
    ("", "slidenum"),
])
def test_system_placeholder_or_field_never_receives_prose(ph, field):
    metadata = (f'<a:fld id="{{00000000-0000-0000-0000-000000000001}}" type="{field}">'
                '<a:t>7</a:t></a:fld>' if field else '<a:r><a:t>Footer</a:t></a:r>')
    xml = slide(shape(1, metadata, f'<p:ph type="{ph}"/>' if ph else ""),
                shape(2, '<a:r><a:t>Body</a:t></a:r>'))
    assert count_content_slots(xml) == 1
    out, report = replace_text_runs(xml, ["Authored body"])
    root = etree.fromstring(out)
    texts = [[t.text for t in s.iter(f"{{{A}}}t")] for s in root.iter(f"{{{P}}}sp")]
    assert texts == [["7" if field else "Footer"], ["Authored body"]]
    assert report.bodies_eligible == 1 and report.texts_dropped == 0


def test_empty_placeholder_icons_are_removed_including_inner_pictures():
    pkg = OpcPackage.open(FIXTURES / "vk_tech_template.pptx")
    # Образец с пустыми body-плейсхолдерами, подложками и значками.
    xml = pkg.parts["ppt/slides/slide7.xml"]
    out, _ = replace_text_runs(xml, [], title_text="Спасибо")
    out, count = remove_orphans(xml, out, 9144000, 5143500)
    root = etree.fromstring(out)
    assert count == 12  # шесть пустых подложек + шесть значков
    assert len(list(root.iter(f"{{{P}}}pic"))) == 1  # крупная фирменная иллюстрация
    assert len(list(root.iter(f"{{{P}}}ph"))) == 7  # пустые ph подавляют подсказки макета


def test_cleared_placeholder_keeps_its_empty_shape_to_mask_layout_prompt():
    xml = slide(shape(1, '<a:r><a:t>Описание проблемы</a:t></a:r>',
                      '<p:ph type="body" idx="1"/>'))
    root = etree.fromstring(xml)
    sp = next(root.iter(f"{{{P}}}sp"))
    etree.SubElement(sp.find(f"{{{P}}}spPr"), f"{{{A}}}solidFill")
    xml = etree.tostring(root)
    after, _ = replace_text_runs(xml, [])
    out, _ = remove_orphans(xml, after, 9144000, 5143500)
    assert next(etree.fromstring(out).iter(f"{{{P}}}ph"), None) is not None
    assert not any(t.text for t in etree.fromstring(out).iter(f"{{{A}}}t"))


def test_layout_prompts_are_cleared_only_for_generated_slides():
    from deckdna.pptx.cloning.single_slide import build_multi_slide_deck
    from deckdna.pptx.composing.placeholders import clear_layout_prompts

    pkg = OpcPackage.open(FIXTURES / "lct2026_submission.pptx")
    layout = next(p for p, b in pkg.parts.items() if p.startswith("ppt/slideLayouts/")
                  and p.endswith(".xml") and b'hasCustomPrompt="1"' in b)
    source = next(p for p in pkg.parts if p.startswith("ppt/slides/") and p.endswith(".xml")
                  and any(t == layout for r, t in pkg.internal_dependencies(p)
                          if r.type_name == "slideLayout"))
    out = build_multi_slide_deck(pkg, [(source, pkg.parts[source])] * 2)
    protected = out.parts["ppt/slides/slide1.xml"]
    count = clear_layout_prompts(out, {0})
    assert count > 0
    assert out.parts[layout] == pkg.parts[layout]
    assert out.parts["ppt/slides/slide1.xml"] == protected
    target = next(t for r, t in out.internal_dependencies("ppt/slides/slide2.xml")
                  if r.type_name == "slideLayout")
    assert target != layout
    master = next(t for r,t in out.internal_dependencies(target) if r.type_name=='slideMaster')
    registered = {t for r,t in out.internal_dependencies(master) if r.type_name=='slideLayout'}
    assert target in registered
    master_xml = etree.fromstring(out.parts[master])
    entries = master_xml.findall(f'{{{P}}}sldLayoutIdLst/{{{P}}}sldLayoutId')
    ids = [e.get('id') for e in entries]
    assert len(ids)==len(set(ids))
    linked = {r.id:t for r,t in out.internal_dependencies(master) if r.type_name=='slideLayout'}
    rel_ns = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    assert target in {linked[e.get(f'{{{rel_ns}}}id')] for e in entries}
    assert b"hasCustomPrompt" not in out.parts[target]
    for sp in etree.fromstring(pkg.parts[layout]).iter(f"{{{P}}}sp"):
        if sp.find(f'.//{{{P}}}ph[@hasCustomPrompt="1"]') is not None:
            for text in sp.iter(f"{{{A}}}t"):
                if text.text:
                    assert text.text.encode() not in out.parts[target]
    snapshot = dict(out.parts)
    assert clear_layout_prompts(out, {0}) == 0
    assert out.parts == snapshot
    assert not out.check_rel_integrity()
    assert not out.content_type_coverage(set(out.parts))


@pytest.mark.parametrize("name", ["vk_tech_template.pptx", "synthetic_unseen.pptx"])
def test_offline_bookends_use_detected_template_roles(name):
    from datetime import UTC, datetime

    from deckdna.contracts.deck_plan import Brief
    from deckdna.ingestion.content_parsers import parse_file
    from deckdna.planning.story_director import plan_deck
    from deckdna.pptx.composing.minimal import plan_exemplar_choices
    from deckdna.template.autopsy import analyze_template
    from deckdna.template.dna import build_design_dna

    path = FIXTURES / name
    f = analyze_template(path)
    dna = build_design_dna(
        path.read_bytes(), f, "t", "a", {i: str(i) for i in range(f.slides)},
        datetime.now(UTC), "1.0",
    ).design_dna
    source = parse_file(Path("tests/fixtures/content/poc_article.md"))
    plan = plan_deck(source, Brief(purpose="Материал", audience="команда", language="ru",
                                   target_slide_count=5))
    choices = plan_exemplar_choices(path, plan)
    roles = {ex.part: ex.role for ex in dna.exemplars}
    if "title" in roles.values():
        assert roles[choices[0].slide_part] == "title"


def test_small_overlay_leaves_with_the_removed_donor_illustration():
    from deckdna.pptx.composing.minimal import _remove_unmatched_artwork

    pkg = OpcPackage.open(FIXTURES / "vk_tech_template.pptx")
    xml = pkg.parts["ppt/slides/slide22.xml"]
    root = etree.fromstring(xml)
    pictures = list(root.iter(f"{{{P}}}pic"))
    # Крестик — маленькая картинка поверх большой демо-иллюстрации.
    from deckdna.pptx.composing.card_reflow import _box

    small = [p for p in pictures if (b := _box(p)).w * b.h < 0.04 * 9144000 * 5143500]
    assert len(small) == 1
    out, _ = _remove_unmatched_artwork(xml, 9144000 * 5143500)
    assert not list(etree.fromstring(out).iter(f"{{{P}}}pic"))

    # Маленький фирменный знак вне сцены остаётся.
    from copy import deepcopy

    logo = deepcopy(small[0])
    off = logo.find(f"{{{P}}}spPr/{{{A}}}xfrm/{{{A}}}off")
    off.set("x", "100000")
    off.set("y", "4800000")
    root.find(f"{{{P}}}cSld/{{{P}}}spTree").append(logo)
    out, _ = _remove_unmatched_artwork(etree.tostring(root), 9144000 * 5143500)
    assert len(list(etree.fromstring(out).iter(f"{{{P}}}pic"))) == 1


def test_donor_chart_frame_without_content_data_is_removed():
    from deckdna.pptx.composing.minimal import _remove_chart_frames

    frame = ('<p:graphicFrame><a:graphic><a:graphicData '
             'uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
             '<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"/>'
             '</a:graphicData></a:graphic></p:graphicFrame>')
    out, count = _remove_chart_frames(slide(frame))
    assert count == 1
    assert not list(etree.fromstring(out).iter(f"{{{P}}}graphicFrame"))


def test_contextual_issue_provenance_uses_the_loaded_prompt_version():
    from deckdna.audit.contextual import PROMPT_NAME, PROMPT_REF
    from deckdna.providers.prompts import load_prompt

    assert PROMPT_REF == f"contextual_slide_audit@{load_prompt(PROMPT_NAME).version}"
