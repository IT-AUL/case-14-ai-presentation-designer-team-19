"""Regression tests for BE6: font scale (K3), slide occupancy (K4), and visual cards (F2/K10)."""

from __future__ import annotations

from pathlib import Path

from deckdna.audit.basic import audit_deck
from deckdna.pptx.composing.chart_fill import add_source_note
from deckdna.pptx.composing.font_scale import (
    SYNTHETIC_FONT_SCALE,
    balance_slide_occupancy,
    effective_font_scale,
    fit_oversized_body_before_data,
    in_font_scale,
    measure_xml_occupancy,
    snap_deck_font_scale,
)
from deckdna.pptx.composing.native_compose import compose, slide_frame
from deckdna.pptx.composing.text_grow import grow_text
from deckdna.pptx.composing.text_replace import replace_text_runs
from deckdna.pptx.opc.package import OpcPackage
from lxml import etree
from pptx import Presentation
from pptx.util import Inches, Pt

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
SW, SH = 12_192_000, 6_858_000


def _minimal_slide_xml(body_Shapes_xml: str = "") -> bytes:
    return (
        f'<p:sld xmlns:p="{P[1:-1]}" xmlns:a="{A[1:-1]}">'
        f"<p:cSld><p:spTree>"
        f'<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        f"<p:grpSpPr/>"
        f"{body_Shapes_xml}"
        f"</p:spTree></p:cSld></p:sld>"
    ).encode()


def test_font_scale_snapping_declared_and_synthetic(tmp_path: Path) -> None:
    scale = [12.0, 16.0, 20.0, 28.0]
    fallback = effective_font_scale(declared=[28.0], observed=[])
    assert tuple(fallback) == SYNTHETIC_FONT_SCALE

    donor_xml = _minimal_slide_xml(
        '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr>'
        '<p:ph type="title"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="600000" y="400000"/>'
        '<a:ext cx="10000000" cy="900000"/></a:xfrm></p:spPr>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr sz="2500"/>'
        "<a:t>Заголовок</a:t></a:r></a:p></p:txBody></p:sp>"
        '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Body"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="600000" y="1600000"/>'
        '<a:ext cx="10000000" cy="3600000"/></a:xfrm></p:spPr>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr sz="1350"/>'
        "<a:t>Старый текст</a:t></a:r></a:p></p:txBody></p:sp>"
    )
    replaced_xml, _ = replace_text_runs(
        donor_xml,
        ["Короткий тезис для проверки привязки кегля к шкале шаблона."],
        title_text="Новый заголовок слайда",
        slide_size=(SW, SH),
        font_scale=scale,
    )
    grown_xml, _ = grow_text(replaced_xml, font_scale=scale)
    root = etree.fromstring(grown_xml)
    for rpr in root.xpath("//a:rPr[@sz]", namespaces={"a": A[1:-1]}):
        sz_pt = int(rpr.get("sz")) / 100.0
        assert in_font_scale(sz_pt, scale), f"{sz_pt}pt not in {scale}"

    frame_xml, region, tokens = slide_frame(donor_xml, SW, SH)
    tokens["font_scale"] = scale
    composed_xml = compose(
        frame_xml,
        region,
        tokens,
        "cards",
        "Нативная композиция карточек по шкале",
        [
            {
                "heading": "Первое направление",
                "body": "Рост выручки в сегменте B2B",
                "value": "42%",
            },
            {"heading": "Второе направление", "body": "Снижение времени отклика", "value": "18%"},
            {
                "heading": "Третье направление",
                "body": "Покрытие регионов присутствия",
                "value": "85%",
            },
        ],
        SW,
        SH,
    )
    c_root = etree.fromstring(composed_xml)
    for rpr in c_root.xpath("//a:rPr[@sz]", namespaces={"a": A[1:-1]}):
        sz_pt = int(rpr.get("sz")) / 100.0
        if sz_pt < 36.0:
            assert in_font_scale(sz_pt, scale), f"composed {sz_pt}pt not in {scale}"

    chart_root = etree.fromstring(
        f'<p:sld xmlns:p="{P[1:-1]}" xmlns:a="{A[1:-1]}"><p:cSld><p:spTree>'
        '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="9" name="Chart 9"/>'
        "<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>"
        '<p:xfrm><a:off x="800000" y="1600000"/><a:ext cx="9000000" cy="4000000"/></p:xfrm>'
        "</p:graphicFrame></p:spTree></p:cSld></p:sld>".encode()
    )
    frame = chart_root.find(f".//{P}graphicFrame")
    add_source_note(chart_root, frame, "Источник: отчёт компании", font_scale=scale)
    note_sz = [
        int(el.get("sz")) / 100.0
        for el in chart_root.xpath("//p:sp//a:rPr[@sz]", namespaces={"p": P[1:-1], "a": A[1:-1]})
    ]
    assert note_sz and all(in_font_scale(s, scale) for s in note_sz)

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    s1 = prs.slides.add_slide(prs.slide_layouts[6])
    for i, pt in enumerate((12, 16, 20, 28)):
        tb = s1.shapes.add_textbox(Inches(1), Inches(1 + i * 0.8), Inches(8), Inches(0.6))
        run = tb.text_frame.paragraphs[0].add_run()
        run.text = f"Эталон {pt}"
        run.font.size = Pt(pt)
        tb2 = s1.shapes.add_textbox(Inches(5), Inches(1 + i * 0.8), Inches(4), Inches(0.6))
        run2 = tb2.text_frame.paragraphs[0].add_run()
        run2.text = f"Повтор {pt}"
        run2.font.size = Pt(pt)
    s2 = prs.slides.add_slide(prs.slide_layouts[6])
    off_tb = s2.shapes.add_textbox(Inches(1), Inches(1.5), Inches(9), Inches(2))
    off_run = off_tb.text_frame.paragraphs[0].add_run()
    off_run.text = "Нестандартный кегль 14.5pt должен притянуться к шкале шаблона"
    off_run.font.size = Pt(14.5)
    deck_path = tmp_path / "off_scale.pptx"
    prs.save(str(deck_path))

    pkg = OpcPackage.open(deck_path)
    snapped = snap_deck_font_scale(pkg)
    assert snapped >= 1
    snapped_path = tmp_path / "snapped.pptx"
    pkg.save(snapped_path)
    issues = [i for i in audit_deck(snapped_path) if i.rule_code == "template.font_scale"]
    assert not issues


def test_balance_slide_occupancy_materializes_inherited_xfrm_and_fixes_bounds() -> None:
    inherited_xml = _minimal_slide_xml(
        '<p:sp><p:nvSpPr><p:cNvPr id="10" name="Title 1"/><p:cNvSpPr/><p:nvPr>'
        '<p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr/>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr sz="2400"/>'
        "<a:t>Региональная динамика продаж</a:t></a:r></a:p></p:txBody></p:sp>"
        '<p:sp><p:nvSpPr><p:cNvPr id="11" name="Content Placeholder 2"/><p:cNvSpPr/><p:nvPr>'
        '<p:ph idx="1"/></p:nvPr></p:nvSpPr><p:spPr/>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr sz="1400"/>'
        "<a:t>Выручка филиалов выросла на 24% за счёт автоматизации каналов сбыта.</a:t>"
        "</a:r></a:p></p:txBody></p:sp>"
    )
    hints = {
        "10": {"box": (609600, 365760, 10972800, 731520), "w": 10789920, "h": 640080, "sz": 24.0},
        "11": {"box": (609600, 1463040, 5000000, 900000), "w": 4817120, "h": 808560, "sz": 14.0},
    }
    before_total, _ = measure_xml_occupancy(etree.fromstring(inherited_xml), SW, SH)
    assert before_total == 0.0

    balanced_xml, changed = balance_slide_occupancy(
        inherited_xml, SW, SH, hints, font_scale=[12.0, 14.0, 16.0, 18.0, 24.0], purpose="body"
    )
    assert changed
    after_total, after_content = measure_xml_occupancy(etree.fromstring(balanced_xml), SW, SH)
    assert 0.25 <= after_total <= 0.85
    assert after_content <= 0.75

    oversized_xml = _minimal_slide_xml(
        '<p:sp><p:nvSpPr><p:cNvPr id="20" name="Title"/><p:cNvSpPr/><p:nvPr>'
        '<p:ph type="title"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="400000" y="300000"/>'
        '<a:ext cx="11000000" cy="700000"/></a:xfrm></p:spPr>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr sz="2400"/>'
        "<a:t>Сводка по кварталу</a:t></a:r></a:p></p:txBody></p:sp>"
        '<p:sp><p:nvSpPr><p:cNvPr id="21" name="GiantBody"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="300000" y="1100000"/>'
        '<a:ext cx="11500000" cy="5600000"/></a:xfrm></p:spPr>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr sz="1600"/>'
        "<a:t>Короткий вывод в две строки внутри огромной рамки на 80% слайда.</a:t>"
        "</a:r></a:p></p:txBody></p:sp>"
    )
    _, over_content = measure_xml_occupancy(etree.fromstring(oversized_xml), SW, SH)
    assert over_content > 0.75

    shrunk_xml, shrunk_changed = balance_slide_occupancy(
        oversized_xml, SW, SH, {}, font_scale=[12.0, 16.0, 24.0], purpose="body"
    )
    assert shrunk_changed
    s_total, s_content = measure_xml_occupancy(etree.fromstring(shrunk_xml), SW, SH)
    assert 0.25 <= s_total <= 0.85
    assert s_content <= 0.75


def test_fit_oversized_body_before_data_frees_space_for_chart() -> None:
    slide_xml = _minimal_slide_xml(
        '<p:sp><p:nvSpPr><p:cNvPr id="30" name="Title"/><p:cNvSpPr/><p:nvPr>'
        '<p:ph type="title"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="600000" y="400000"/>'
        '<a:ext cx="10500000" cy="800000"/></a:xfrm></p:spPr>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr sz="2400"/>'
        "<a:t>Динамика выручки и рентабельности</a:t></a:r></a:p></p:txBody></p:sp>"
        '<p:sp><p:nvSpPr><p:cNvPr id="31" name="Body"/><p:cNvSpPr/><p:nvPr>'
        '<p:ph idx="1"/></p:nvPr></p:nvSpPr>'
        '<p:spPr><a:xfrm><a:off x="600000" y="1500000"/>'
        '<a:ext cx="10500000" cy="4600000"/></a:xfrm></p:spPr>'
        '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr sz="1600"/>'
        "<a:t>Рост выручки составил 28% при сохранении целевой маржинальности.</a:t>"
        "</a:r></a:p></p:txBody></p:sp>"
    )
    fitted_xml, changed = fit_oversized_body_before_data(slide_xml, SW, SH, {})
    assert changed
    root = etree.fromstring(fitted_xml)
    body_ext = root.xpath(
        "//p:sp[p:nvSpPr/p:cNvPr/@id='31']/p:spPr/a:xfrm/a:ext",
        namespaces={"p": P[1:-1], "a": A[1:-1]},
    )[0]
    assert int(body_ext.get("cy")) <= int(SH * 0.28)
